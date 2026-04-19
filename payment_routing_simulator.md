# Payment Routing Simulator — Routing Intelligence Engine (+ OSS Mock Gateway)

## Product Summary

Payment Routing Simulator is a **routing intelligence engine** that powers two things: (a) the Payment Data Chatbot's `query_routing_intelligence` tool — so merchants can ask "which provider should I route USD→BRL Visa transactions through right now?" and get a ranked recommendation with projected approval rate, latency, and fee — and (b) a standalone CLI / PyPI package / REST API that simulates any payment processor's behavior for integration testing (feed it provider + params, get back realistic response codes, latency distributions, 3DS flows, retry semantics, decline reasons).

**Platform role**: This is the second module of the unified AI chatbot portfolio (see `plan.md`). The simulator engine also exposes a `/recommend-route` endpoint that ranks providers by projected net approval rate for a given `{country, card_brand, amount}` — the chatbot calls this when users ask routing questions. The standalone simulator API still targets integration engineers and QA teams; the chatbot wrapper targets merchants and payment ops.

**Ship date**: May 28 (Phase 2, weeks 4-7 code + deck)
**Build effort**: 19 hours code across 10 sessions (9 × 2h + 1h QA) + deck sessions
**Primary role target**: Solutions Engineer
**Secondary role target**: Amazon PMT L6 (product thinking — identified gap, built for users)

## The Problem

When a company integrates a new payment processor, testing is painful:

- **Stripe's test mode always returns success unless you use magic card numbers.** You can't test "what happens when Adyen returns a soft decline 05 for a Brazilian Visa transaction at $300?" because test mode doesn't simulate real decline distributions.
- **Each processor's test environment works differently.** Stripe test keys, Adyen test credentials, Checkout.com sandbox — different APIs, different behaviors, different limitations. No consistency.
- **Edge cases are invisible until production.** 3DS challenge flows, latency spikes, country-specific decline patterns, retry behavior after a soft decline — you can't reproduce these in test mode. Teams discover them when merchants start complaining.
- **QA can't regression-test payment flows.** If you change your routing logic, you need to verify it works against every provider's behavior. With test mode, you're testing against fake behavior that doesn't match production.

**Who has this problem**: Integration engineers building payment flows, QA teams testing payment edge cases, product managers evaluating new providers before signing contracts, solutions engineers demoing multi-provider routing to merchants.

**How often**: Every new provider integration (takes 2-6 weeks), every routing logic change, every new market launch. A company processing through 3-5 providers tests payment flows weekly.

**What it costs**: Production incidents from untested edge cases. A major PSP had a 3-hour outage because their routing fallback logic wasn't tested against realistic decline patterns — test mode said everything worked. Integration timelines stretch because developers can't verify behavior until they have real traffic.

## What the User Sees

Three ways to use it:

**CLI** (for developers): `payreplay simulate --provider adyen --country BR --card visa --amount 300 --3ds` → get back a realistic response: decline code 05, latency 215ms, 3DS challenged, pares_status Y. Run it 1000 times and see the actual distribution — 88% approval, 12% decline with realistic code frequencies.

**REST API** (for integration testing): `POST /simulate` with JSON body → get back a response that looks exactly like a real processor response. Wire it into your test suite. Full lifecycle: authorize → capture → refund, with webhooks fired to your callback URL on each state change.

**PyPI package** (for CI/CD): `pip install payreplay`, import it in your test suite, run simulations programmatically. Every commit tests your routing logic against realistic provider behavior.

## Why Any Team Would Build This

- **Test against real behavior, not happy paths**: Decline code distributions, latency percentiles, and 3DS challenge rates all match what you'd see in production — derived from actual transaction patterns.
- **Test new providers before signing**: Want to evaluate Nuvei for LATAM? Run 10,000 simulated transactions through the Nuvei profile and see projected approval rates, latency, and decline patterns for each country. No contract needed.
- **Regression-test routing logic**: Change your cascade rules → run the test suite → verify that the new logic actually improves net approval rate against realistic provider behavior, not mocks that always return 200.
- **Reproduce production incidents**: "We had 15% declines from Adyen in Germany last Tuesday." Simulate that exact scenario, find the root cause, deploy the fix, re-simulate to verify.
- **One tool, every provider**: Instead of maintaining 5 different mock servers, maintain one YAML file per provider. Add a new provider in 30 minutes by writing a config file.

Provider profiles are derived from distribution patterns across 30 countries and 5 archetypes in `products/Claude files/routing_transactions.csv` (108,339 rows, 128 columns) — generated by `generate_routing_transactions.py` (seed=42, deterministic) and validated against a 150-pattern ASSERT gate + 652-pattern non-contradiction scan. See `products/Claude files/DATA_DECISIONS.md` for per-pattern encoding.

## Technical Solution

### What you're building

A Python package (`pip install payreplay`) that simulates any payment processor's full behavior — not just approve/decline, but the complete payment lifecycle with webhooks, idempotency, and event streaming. Three interfaces: (1) CLI — `payreplay simulate --provider stripe --country BR --card visa --amount 150`, (2) REST API with OpenAPI docs — `POST /simulate`, `POST /capture`, `POST /refund`, and (3) Python import — `from payreplay import simulate`. Each provider is defined in YAML with approval rates, decline distributions, latency percentiles, and 3DS configs — all derived from `routing_transactions.csv`. The simulator persists every transaction in PostgreSQL with a state machine (pending → authorized → captured → voided → refunded), publishes events to Kafka, and delivers webhook notifications to merchant callback URLs with HMAC signatures and Celery-powered retry. Idempotency keys prevent duplicate charges. API key authentication mirrors Stripe's model.

### Architecture

```
CLI command or API request (provider, country, card_brand, amount)
  → API key validation (publishable or secret key check)
    → Idempotency check (Redis lookup: has this key been processed?)
      → If duplicate: return cached response immediately
      → If new: proceed to simulation
        → Provider Loader reads providers/{name}.yaml
          → State Machine: create transaction record (state: PENDING) in PostgreSQL
            → Simulation Engine:
              1. Approval probability: base_rate × country_modifier × card_brand_modifier × amount_modifier
              2. Random draw: approved or declined
              3. If approved: response_code "00", generate latency → transition to AUTHORIZED
              4. If declined: Decline Engine selects code from weighted distribution → transition to DECLINED
                 - P(decline_code | country, card_brand, amount_bucket) from YAML
                 - Classifies soft vs hard, adds merchant_advice_code, retry guidance
              5. If --3ds: 3DS Engine determines challenge/frictionless, pares_status, version
              6. Latency: log-normal draw from YAML p50/p95/p99 + country modifier
            → Persist result to PostgreSQL (transactions + state_transitions tables)
              → Publish event to Kafka topic (payment.authorized / payment.declined / etc.)
                → Celery task: deliver webhook to merchant callback URL
                  → HMAC-SHA256 signed payload
                    → Retry on failure: exponential backoff (1s, 2s, 4s, 8s, 16s), max 5 attempts
                      → Log each delivery attempt in PostgreSQL (webhook_deliveries table)
          → Return ProviderResponse (JSON)

Additional API endpoints for lifecycle operations:
  POST /capture (idempotency-key) → AUTHORIZED → CAPTURED → Kafka event → webhook
  POST /void (idempotency-key)    → AUTHORIZED → VOIDED → Kafka event → webhook
  POST /refund (idempotency-key)  → CAPTURED → REFUNDED → Kafka event → webhook
```

### Key files to create

| File | What it does |
|------|-------------|
| `payreplay/cli.py` | Click CLI: `simulate`, `compare`, `list-providers`, `server` commands |
| `payreplay/models.py` | Pydantic + dataclass models: TransactionRequest, ProviderResponse, CompareResult |
| `payreplay/engine.py` | Core `simulate()` — orchestrates approval → decline → 3DS → latency → state transition |
| `payreplay/state_machine.py` | Payment lifecycle: PENDING → AUTHORIZED → CAPTURED → VOIDED → REFUNDED, validates transitions |
| `payreplay/provider_loader.py` | Reads + validates YAML profiles, `load_provider(name)`, `list_providers()` |
| `payreplay/response_codes.py` | `ISO_8583_CODES` dict (~40 codes), `MERCHANT_ADVICE_CODES` dict, provider-specific mappings |
| `payreplay/decline_engine.py` | `select_decline_code(provider, country, card_brand, amount)` — weighted random from YAML |
| `payreplay/threeds.py` | `simulate_3ds()` — challenge/frictionless/pares_status based on provider + country + amount |
| `payreplay/latency.py` | `generate_latency()` — log-normal draw from YAML p50/p95, country modifiers |
| `payreplay/webhooks.py` | Webhook delivery: HMAC-SHA256 signing, payload construction, delivery status tracking |
| `payreplay/idempotency.py` | Idempotency key handling: check Redis for existing key, store result on completion |
| `payreplay/auth.py` | API key management (publishable/secret pairs), middleware validation |
| `payreplay/kafka_producer.py` | Publish payment lifecycle events to Kafka topic `payment.events` |
| `payreplay/celery_app.py` | Celery worker config, webhook delivery task with retry backoff |
| `payreplay/api.py` | FastAPI: POST /simulate, /capture, /void, /refund, /compare, GET /providers, /health, /metrics |
| `payreplay/db.py` | SQLAlchemy models (transactions, state_transitions, webhook_deliveries), PostgreSQL connection |
| `payreplay/providers/*.yaml` | 5 provider profiles: Stripe, Adyen, Nuvei, Checkout.com, Braintree |
| `scripts/derive_profiles.py` | Reads routing_transactions.csv → computes per-archetype stats → outputs YAML files |
| `migrations/` | Alembic database migration scripts |
| `loadtest/` | k6 load test scripts (simulate 100/500/1000 concurrent users) |
| `Dockerfile` | Multi-stage build for containerized deployment |
| `docker-compose.yml` | FastAPI + PostgreSQL + Redis + Kafka + Celery worker + Grafana |
| `terraform/` | Terraform configs for DigitalOcean deployment |
| `grafana/dashboards/` | Pre-built Grafana dashboard JSON |
| `.github/workflows/ci.yml` | GitHub Actions: lint → test → build → publish to PyPI on tag |

### Data layer

The CSV columns used to derive provider profiles:
- **Approval rates**: `provider` × `country` × `card_brand` × `transaction_status` → grouped approval rates per segment
- **Decline codes**: `iso8583_response_code` × `provider` → decline code frequency distribution per provider
- **Latency**: `provider_integration_time` × `provider` × `country` → p50/p95/p99 percentiles
- **3DS**: `three_ds_version` × `three_ds_has_challenge` × `three_ds_pares_status` × `provider` → challenge rates, version distribution
- **Response codes**: `response_code` × `provider_response_code` × `merchant_advice_code` → code mapping tables

`scripts/derive_profiles.py` reads the CSV once, computes these aggregates, and writes them into YAML files. After that, the simulator never touches the CSV again — it only reads YAML.

### Key decisions

- **YAML for provider profiles, not code**: Anyone can add a new provider by writing a YAML file. No Python needed. Extensible by design.
- **PostgreSQL for transaction history**: Every simulated transaction is persisted with full state transition history. Enables analytics, audit trails, and demonstrates database skills.
- **Payment state machine with strict transitions**: PENDING → AUTHORIZED → CAPTURED/VOIDED → REFUNDED. Invalid transitions are rejected. This mirrors how real payment processors work — the #1 domain concept in SE interviews.
- **Webhooks with HMAC-SHA256 and Celery retry**: Like real Stripe — when a payment event happens, POST to the merchant's URL with a signed payload. Celery handles async delivery with exponential backoff. This is the most commonly asked SE interview topic at Stripe and Adyen.
- **Idempotency keys via Redis**: Duplicate API requests return the cached response instead of processing twice. Prevents double charges. The most important payments API concept — Stripe's blog post on idempotency is required reading for their SE interviews.
- **Kafka for event streaming**: Every payment state change publishes to a Kafka topic. The ML Payment Recovery Engine consumes `payment.declined` events for real-time retry decisions. This connects the portfolio products into one system.
- **API key auth (publishable + secret)**: Mirrors Stripe's pattern — publishable keys for client-side, secret keys for server-side.
- **Terraform for deployment**: Infrastructure-as-code — the droplet, firewall rules, and DNS are all defined in .tf files. Reproducible, version-controlled deployment instead of clicking through a console.
- **Grafana over custom dashboards**: Pre-built dashboard visualizes Prometheus metrics. Graphs simulation volume, latency distributions, webhook delivery rates. Industry-standard monitoring, not a bespoke tool.
- **CI/CD with GitHub Actions**: Every push runs lint + tests. Tagged releases automatically publish to PyPI. Demonstrates professional development practices.
- **k6 for load testing**: Measures performance under realistic load. Documents actual throughput numbers (not theoretical claims).
- **Log-normal for latency**: Real payment API latency is right-skewed. Log-normal captures this. Parameters from dataset percentiles.
- **CLI-first, API second**: CLI gets adopted faster by developers. REST API is for integration testing and the web demo.

## Tech Stack

- Python 3.12
- Click (CLI framework — `payreplay simulate`, `payreplay compare`, `payreplay server`)
- FastAPI (REST API with OpenAPI/Swagger auto-generated docs)
- PostgreSQL (transaction history, state persistence, webhook delivery log)
- Redis (idempotency key cache, rate limiting, provider profile cache)
- Kafka (publish payment lifecycle events: payment.authorized, payment.declined, payment.captured, etc.)
- Celery + Redis broker (async webhook delivery with exponential backoff retry)
- SQLAlchemy + Alembic (ORM + database migrations)
- YAML (provider profile configuration — Stripe, Adyen, Nuvei, Checkout.com, Braintree)
- PyPI packaging (pip install payreplay)
- API key authentication (publishable/secret key pairs, like Stripe's pk_test/sk_test)
- HMAC-SHA256 (webhook payload signing for merchant verification)
- Pydantic (request/response validation)
- numpy (log-normal latency distributions, probability sampling)
- pytest (unit + integration tests)
- k6 (load testing — measure performance at 100/500/1000 req/sec)
- Docker + docker-compose (FastAPI + PostgreSQL + Redis + Kafka + Celery worker + Grafana)
- Terraform (infrastructure-as-code for DigitalOcean deployment)
- GitHub Actions (CI/CD: lint → test → build → publish to PyPI)
- Prometheus (metrics: simulation_count, latency_histogram, webhook_deliveries, error_rate)
- Grafana (pre-built metrics dashboards)
- OpenTelemetry (distributed tracing across API → engine → Kafka → webhook)
- Structured logging (JSON format with request correlation IDs)

## Key Features

### 1. Provider Profiles (YAML configs)
Each processor gets a YAML definition specifying:
- Approval rates by country and card brand
- Decline code distribution (weighted by frequency)
- Latency percentiles: P50, P95, P99
- Supported currencies and payment methods
- Region-specific behavior overrides

### 2. Response Code Engine
- Maps ISO 8583 response codes to provider-specific codes and back
- Conditional probability: decline code distribution varies by country, card brand, amount bucket
- Supports both raw provider codes and normalized reason categories

### 3. 3DS Simulation
- Configurable challenge rates per card brand and country
- `pares_status` distribution (Y/N/A/U/R)
- 3DS version selection (1.0, 2.1, 2.2)
- Frictionless vs challenged flow with configurable ratios

### 4. Latency Modeling
- Log-normal distributions fitted from dataset per provider per region
- Configurable P50/P95/P99 targets
- Simulates realistic tail latency (not just uniform random)

### 5. Retry Semantics
- Soft-decline identification and retry eligibility
- Configurable retry success curves (exponential decay)
- Max retry count and backoff configuration per provider

### 6. REST API (FastAPI)
- `POST /simulate` — full transaction simulation
- `POST /batch` — batch simulation for load testing
- `GET /providers` — list available provider profiles
- `GET /providers/{name}` — provider profile details
- Health check, OpenAPI docs at `/docs`

### 7. CLI Mode
```bash
payreplay simulate --provider adyen --country BR --card visa --amount 150
payreplay simulate --provider nuvei --country MX --card mastercard --amount 500 --3ds
payreplay server --port 8080  # start REST API
payreplay list-providers
```

### 8. Payment State Machine
Full payment lifecycle modeled as a finite state machine:
- **PENDING** — initial state when request received
- **AUTHORIZED** — payment approved, funds reserved
- **CAPTURED** — funds transferred (from AUTHORIZED only)
- **VOIDED** — authorization cancelled (from AUTHORIZED only)
- **DECLINED** — payment rejected (from PENDING only)
- **REFUNDED** — funds returned (from CAPTURED only)

Invalid transitions (e.g., DECLINED → CAPTURED) return 409 Conflict. Every transition logged in `state_transitions` table with timestamp and trigger.

### 9. Webhook Callbacks
When a payment event occurs, the simulator POSTs a JSON payload to the merchant's registered callback URL:
- Payload: `{event_type, transaction_id, provider, amount, currency, status, timestamp}`
- HMAC-SHA256 signature in `X-Signature-256` header — merchant verifies with their secret key
- Celery delivers asynchronously — API response returns immediately, webhook fires in background
- Retry on failure: exponential backoff (1s → 2s → 4s → 8s → 16s), max 5 attempts
- Delivery log in PostgreSQL: every attempt recorded with status code, timestamp, retry count

### 10. Idempotency Keys
Every mutating API request accepts an `Idempotency-Key` header:
- On first request: process normally, cache result in Redis (TTL: 24h) keyed by idempotency key
- On duplicate request (same key): return cached response without re-processing
- Prevents double charges from network retries — the most critical safety feature in payment APIs

### 11. Kafka Event Streaming
Every payment state change publishes an event to the `payment.events` Kafka topic:
- Event types: `payment.authorized`, `payment.declined`, `payment.captured`, `payment.voided`, `payment.refunded`
- Payload: full transaction context (provider, country, amount, card_brand, response_code, etc.)
- The ML Payment Recovery Engine consumes `payment.declined` events for retry/cascade decisions
- Creates an event-driven architecture connecting the portfolio products

### 12. API Key Authentication
Two key types (mirrors Stripe's model):
- **Publishable key** (`pk_test_...`): client-side, limited permissions (simulate only)
- **Secret key** (`sk_test_...`): server-side, full permissions (capture, void, refund, webhooks)
- Keys stored in PostgreSQL, validated on every request

### 13. CI/CD Pipeline (GitHub Actions)
Automated pipeline on every push:
1. **Lint**: ruff check + ruff format
2. **Test**: pytest with PostgreSQL test database
3. **Build**: python -m build
4. **Publish** (on git tag only): upload to PyPI via twine
- Pull requests require all checks to pass before merge

### 14. Load Testing (k6)
Performance benchmarks documented with k6 scripts:
- Scenario 1: 100 concurrent users, sustained 60 seconds → target: <50ms p95
- Scenario 2: 500 concurrent users, sustained 60 seconds → identify bottleneck
- Scenario 3: 1000 req/sec spike → measure degradation
- Results include: throughput, p50/p95/p99 latency, error rate

### 15. Observability
- Structured JSON logging: every request, simulation, webhook delivery logged with correlation ID
- Prometheus `/metrics`: simulation_count (by provider, country), simulation_latency_histogram, webhook_delivery_total (labels: status), active_connections_gauge
- OpenTelemetry traces: API request → simulation engine → PostgreSQL persist → Kafka publish → Celery webhook delivery

## Build Plan

### Session 1 (2h): Project scaffold + CLI + response codes + BIN resolver
**Build**:
1. Create repo `ivanpayments/payment-routing-simulator`, init venv, `pyproject.toml` with all dependencies, entry point `payreplay = payreplay.cli:main`
2. Package structure: `payreplay/` with `__init__.py`, `cli.py`, `models.py`, `engine.py`, `providers/`, `api.py`. Pydantic/dataclass models.
3. Click CLI in `cli.py`: `simulate`, `compare`, `list-providers`, `server` commands with all flags
4. `response_codes.py`: `ISO_8583_CODES` dict (~40 codes), `MERCHANT_ADVICE_CODES`, provider-specific mappings
5. BIN lookup table: 50 BIN ranges → card_brand, card_type, issuing_country
6. `provider_loader.py`: `load_provider(name)`, `list_providers()`, YAML schema validation
7. `scripts/derive_profiles.py` skeleton

**Done when**: `payreplay --help` works. BIN lookups correct. Provider YAML schema validates.

---

### Session 2 (2h): Stripe + Adyen + engine core + derive_profiles
**Build**:
1. `derive_profiles.py`: read `routing_transactions.csv` → compute per-provider aggregates → generate YAML files
2. `providers/stripe.yaml`: approval rates by country, 30+ response codes with weights, latency p50/p95/p99, 3DS config, supported currencies
3. `providers/adyen.yaml`: different profile — higher 3DS rates, more granular decline codes, different latency
4. `engine.py`: core `simulate(request, provider)` — load provider → approval probability (base_rate x country_modifier x card_brand_modifier x amount_modifier) → random draw → decline engine or success
5. `decline_engine.py`: `select_decline_code()` — weighted random selection from YAML distribution, conditional on country + card_brand + amount_bucket

**Done when**: `payreplay simulate --provider stripe --country BR --card visa --amount 150` returns realistic response. Stripe and Adyen give different results.

---

### Session 3 (2h): Remaining providers + 3DS + latency
**Build**:
1. `providers/nuvei.yaml`, `providers/checkout.yaml`, `providers/braintree.yaml`
2. `threeds.py`: `simulate_3ds()` — challenge rate from YAML x country modifier (EU higher per PSD2) x amount modifier x card brand modifier. pares_status distribution (Y/N/A/U/R). Version selection (3DS2.2/2.1/1.0).
3. `latency.py`: log-normal from YAML p50/p95 (`mu = ln(p50)`, `sigma = (ln(p95) - mu) / 1.645`), country cross-border modifier, 1-2% chance of p99+ tail latency
4. Wire 3DS + latency into engine. `--3ds` CLI flag.
5. `payreplay compare --country BR --card visa --amount 150` runs all 5 providers and outputs comparison table

**Done when**: 5 providers listed. 3DS challenge rate higher for EU. Latency histogram right-skewed.

---

### Session 4 (2h): PostgreSQL + payment state machine
**Build**:
1. PostgreSQL schema: `transactions` table (id, idempotency_key, provider, country, card_brand, amount, currency, state, response_code, created_at), `state_transitions` table (transaction_id, from_state, to_state, triggered_by, timestamp), `webhook_deliveries` table (transaction_id, url, attempt, status_code, response_body, timestamp)
2. SQLAlchemy models + Alembic init + first migration
3. `state_machine.py`: define valid transitions, `transition(transaction_id, to_state)` — validates + persists + returns new state. Rejects invalid transitions (409 Conflict).
4. `POST /capture`, `POST /void`, `POST /refund` endpoints that invoke state machine

**Done when**: Full lifecycle works: simulate (→ AUTHORIZED) → capture (→ CAPTURED) → refund (→ REFUNDED). Invalid transition (DECLINED → CAPTURED) returns 409.

---

### Session 5 (2h): Webhooks + HMAC + Celery
**Build**:
1. Webhook registration: `POST /webhooks/register {url, events, secret}` — merchant registers callback URL
2. `webhooks.py`: construct JSON payload, sign with HMAC-SHA256 using merchant's secret, set `X-Signature-256` header
3. Celery app with Redis broker: `deliver_webhook` task
4. Retry logic: on HTTP error or timeout → exponential backoff (1s, 2s, 4s, 8s, 16s), max 5 attempts
5. Log every delivery attempt in `webhook_deliveries` table

**Done when**: Register webhook URL → simulate payment → receive POST at callback URL within 2 seconds. Failed delivery retries.

---

### Session 6 (2h): Idempotency + API key auth + rate limiting
**Build**:
1. `idempotency.py`: on request with `Idempotency-Key` header → check Redis for existing key → if found, return cached response (200) → if new, process request, store result in Redis (TTL: 24h)
2. `auth.py`: generate API key pairs (pk_test_... / sk_test_...), store hashed in PostgreSQL, validate on every request. Publishable keys: simulate only. Secret keys: full access (capture, void, refund, webhooks).
3. Rate limiting: sliding window per API key, configurable limits (default: 100 req/min for publishable, 1000 for secret). Redis ZADD + ZRANGEBYSCORE.
4. FastAPI middleware: extract API key from `Authorization: Bearer` header, validate, apply rate limit

**Done when**: Duplicate request with same Idempotency-Key returns identical response. Invalid API key returns 401. Rate limit triggers at threshold.

---

### Session 7 (2h): Kafka + FastAPI REST API
**Build**:
1. `kafka_producer.py`: publish events to `payment.events` topic. Event schema: `{event_type, transaction_id, provider, country, card_brand, amount, currency, response_code, timestamp, metadata}`
2. Wire into state machine: every state transition publishes corresponding event
3. FastAPI API: `POST /simulate`, `POST /capture/{id}`, `POST /void/{id}`, `POST /refund/{id}`, `POST /compare`, `GET /providers`, `GET /providers/{name}`, `GET /transactions/{id}`, `GET /transactions/{id}/transitions`, `GET /health`
4. OpenAPI/Swagger docs auto-generated at `/docs`
5. Pydantic request/response models with validation (ISO country codes, card brand enum, amount > 0)

**Done when**: Full API works via curl. Kafka receives events on every state change. `/docs` shows interactive API documentation.

---

### Session 8 (2h): Docker + Terraform + Grafana + observability
**Build**:
1. `Dockerfile`: multi-stage (build dependencies → slim runtime)
2. `docker-compose.yml`: `api` (FastAPI), `db` (PostgreSQL), `cache` (Redis), `kafka` (Kafka/KRaft — no Zookeeper), `celery-worker` (Celery), `grafana` (Grafana) — NO celery-beat (no periodic tasks)
3. Structured JSON logging via structlog: request_id correlation across API → engine → Kafka → webhook
4. Prometheus `/metrics`: simulation_count (labels: provider, country, outcome), simulation_latency_histogram, webhook_delivery_total (labels: status), active_connections_gauge
5. OpenTelemetry: instrument FastAPI + Celery + Kafka producer. Trace: API request → simulation → DB persist → Kafka publish → webhook delivery.
6. Grafana: pre-built dashboard with simulation counts by provider, latency distributions, webhook delivery rates panels
7. Terraform config: DigitalOcean droplet, firewall rules, DNS

**Done when**: `docker-compose up` starts full stack. Grafana dashboard loads. Logs are JSON with correlation IDs. `/metrics` returns Prometheus format.

---

### Session 9 (2h): CI/CD + load testing + PyPI + deploy
**Build**:
1. `.github/workflows/ci.yml`: on push → ruff lint → ruff format check → pytest (with PostgreSQL service container) → build wheel. On tag (v*) → publish to PyPI via twine.
2. `loadtest/basic.js` (k6): 100 concurrent virtual users, 60s duration, POST /simulate with random params
3. `loadtest/spike.js` (k6): ramp to 1000 req/sec, measure degradation
4. Run benchmarks, document results: throughput, p50/p95/p99, error rate
5. `pyproject.toml` finalization: version, classifiers, README rendering
6. Deploy via Terraform to DigitalOcean, Caddy reverse proxy at `/routing-simulator/*`
7. Website page: hero section with live comparison table, technical details, architecture diagram
8. README: architecture diagram, quick start, API docs summary, load test results, "Try it live" link

**Done when**: CI passes. `pip install payreplay` works from PyPI. Load test results documented. `ivanantonov.com/routing-simulator` loads.

---

### Session 10 (1h): QA + chatbot integration + case-study deliverable
End-to-end: full payment lifecycle (simulate → capture → refund), webhook delivery + retry, idempotency duplicate handling, all 5 providers, 3DS flow, API key auth, rate limiting, load test baseline. Fix bugs.

**Chatbot integration**:
1. Add `POST /recommend-route` endpoint — input `{country, card_brand, amount, currency}` → output ranked list of providers with projected approval rate, latency p50, fee estimate, weighted score
2. Add `query_routing_intelligence` tool function in Payment Data Chatbot (`tools.py`) that HTTP-calls this endpoint and formats the response as a markdown table
3. Update chatbot system prompt to route routing-related questions to this tool
4. Verify end-to-end via chatbot: "Which provider is best for US MasterCard $200?" → chatbot returns ranked recommendation with reasoning

**Case-study deliverable** (Amazon Bar-Raiser "Deliver Results" anchor):
5. Generate `case_study_book.yaml` — fixed simulated merchant persona (mid-market DTC, $50M GMV, 10K txn/day, $180 AOV, 30% international) seeded from `routing_transactions.csv`. Emitted by `scripts/derive_profiles.py` alongside the 5 archetype profiles.
6. Write `scripts/run_case_study.py` — replays `case_study_book.yaml` through **baseline** config (single global-acquirer, no retry, no NT, no SCA exemptions) and **intervention** config (smart routing + retry cascade + NT preference + SCA exemption optimization). Computes delta; emits `case_study_output.json` + `case_study_chart.png`.
7. Write `products/case_study.md` (3 pages, rendered to `case_study.pdf` via WeasyPrint): headline **"$X recovered per $1M processed"** on the simulated book + 3-row decomposition (auth-rate lift pp, retry-salvage rate %, effective fee delta bps) + the baseline/intervention chart + a "show the math" appendix tracing each contributor to CSV columns + pattern IDs.
8. Update landing page hero (built in Block G) to display the headline $ number + a toggle to expand the decomposition. Live compare widget moves below the fold.

---

### Deck Sessions
After code ships, 10 deck sessions build company-specific presentations referencing the simulator's architecture, provider profiles, and demo flow. See schedule.md for dates.

## Deliverables

- [ ] `pip install payreplay` on PyPI
- [ ] GitHub repo with comprehensive README + architecture diagram
- [ ] Live REST API at `ivanantonov.com/routing-simulator/api` with OpenAPI docs
- [ ] 5 provider profiles: Stripe, Adyen, Nuvei, Checkout.com, Braintree
- [ ] Full payment lifecycle state machine (authorize → capture → void → refund)
- [ ] Webhook callbacks with HMAC-SHA256 signatures and Celery retry
- [ ] Idempotency key handling (Redis-backed)
- [ ] Kafka event streaming (payment.events topic)
- [ ] API key authentication (publishable + secret keys)
- [ ] Terraform infrastructure-as-code for DigitalOcean deployment
- [ ] Grafana dashboard for real-time metrics visualization
- [ ] Docker-compose: full stack in one command
- [ ] GitHub Actions CI/CD pipeline with automated PyPI publishing
- [ ] k6 load test results with p50/p95/p99 benchmarks
- [ ] Prometheus metrics + OpenTelemetry tracing
- [ ] **Case study**: `case_study.md`/`.pdf` + `case_study_book.yaml` + `scripts/run_case_study.py` — headline "$X recovered per $1M processed" on simulated mid-market DTC book, with 3-line decomposition (auth lift / retry salvage / fee delta) and full math traceable to CSV columns + pattern IDs. Landing-page hero number. Primary Amazon "Deliver Results" anchor.

## Provider Profiles Shipping at Launch

| Provider | Approval Rate Range | Latency P50 | Key Behavior |
|----------|-------------------|-------------|--------------|
| Adyen | 85-95% by country | ~200ms | Strong 3DS2, detailed decline codes |
| Stripe | 90-97% | ~150ms | Simple retry logic, clean error codes |
| Checkout.com | 87-94% | ~180ms | Aggressive 3DS challenge rates |
| Nuvei | 75-90% (LATAM) | ~350ms | High variance by country, FX delays |
| Braintree | 80-92% | ~250ms | PayPal ecosystem, strong in NA/EU |

## Interview Talking Points

**For Solutions Engineer roles**:
- "I built a mock payment gateway that simulates the full payment lifecycle — authorize, capture, void, refund — with a state machine that rejects invalid transitions, just like a real processor."
- "Webhooks work like Stripe's: HMAC-SHA256 signed payloads, async delivery via Celery, exponential backoff retry up to 5 attempts, full delivery log. I can walk you through designing a reliable webhook system."
- "Idempotency keys prevent duplicate charges — same pattern Stripe uses. The key is cached in Redis for 24 hours, so retried requests return the same response."
- "I load-tested it with k6 — [X] requests per second at p95 < 50ms. Here's where the bottleneck is and what I'd do to scale it."

**For Amazon PMT / PM roles**:
- "This is a product that solves a real ecosystem gap — no universal mock payment gateway existed. I identified the user (integration engineers), built for their workflow (CLI for quick testing, API for automation), and made it extensible (YAML profiles)."
- "The state machine enforces business rules — you can't capture a declined payment, you can't refund an uncaptured payment. This is product thinking about error states."
- "Kafka connects this to the ML Payment Recovery Engine — declined payment events flow to the retry prediction model in real time."

**For TPM roles**:
- "Full CI/CD: GitHub Actions runs lint + tests on every push, publishes to PyPI on tagged release. Zero manual deployment steps."
- "Docker-compose runs the full stack: API, PostgreSQL, Redis, Kafka, Celery worker — five services orchestrated together."
- "Observability: structured JSON logs with correlation IDs trace a request from API entry through simulation, database persistence, Kafka publish, and webhook delivery."

## Coverage Matrix

| Role | Relevance | Signal |
|------|-----------|--------|
| Solutions Engineer | PRIMARY | Deep processor knowledge, integration testing, multi-provider architecture |
| Amazon PMT L6 | Strong | Product judgment — identified ecosystem gap, built for real users, extensible design |
| AI Engineer | Moderate | Data-driven parameter fitting, distribution modeling |

## Data Source

All statistical parameters derived from `products/Claude files/routing_transactions.csv` (108,339 rows × 128 cols, seed=42):
- 108,339 transactions (deterministic, regeneratable)
- 128 columns
- 500 synthetic merchants, 30 countries, 5 processor archetypes (`global-acquirer-a/b`, `regional-bank-mx/br/in/ae`, `apm-specialist-sepa/latam/in`, `fx-cross-border`, `orchestrator-high-risk`)
- Safe for public repos (fully synthetic, legal-reviewed)
- 150-pattern ASSERT gate + 652-pattern non-contradiction scan both PASS

## Case Study (primary "Deliver Results" anchor)

**Headline metric: "$X recovered per $1M processed"** on a simulated mid-market DTC book. Target band $8k–$25k per $1M (80–250 bps). One number, fully reproducible, every dollar traces to a CSV column + pattern ID.

### Simulated book (`case_study_book.yaml`)

Fixed persona so the number is comparable across runs:
- Mid-market DTC, $50M annualized GMV, 10K txn/day, $180 AOV
- 30% international mix: US home (70%), EU (12%), LATAM (10%), APAC (6%), MEA (2%)
- Card-brand mix per public industry benchmarks
- Seeded subset of `routing_transactions.csv` — same rows produce same number

### Baseline vs intervention

| Config | Routing | Retry | Network tokens | SCA exemptions |
|---|---|---|---|---|
| **Baseline** | Single global-acquirer, no fallback | None | PAN only | None applied |
| **Intervention** | Smart routing: archetype selected per corridor | Cascade: soft-decline → 2nd attempt @ 4h, 3rd @ 24h (codes 05/51/61/91) | Preferred when supported | TRA/LVP/recurring applied in EEA |

### Decomposition (3 supporting metrics)

1. **Blended auth-rate lift (pp)** — primary driver, target +1.5 to +3.0 pp
2. **Retry-salvage rate (%)** — of soft-declines recovered within 24h, target 15–30%
3. **Effective fee delta (bps)** — interchange + scheme + FX markup, target -10 to -40 bps

Each shown as a contributing line in the $ stack. Landing-page hero: the $ number with a "show the math" toggle that expands the decomposition and links each line to the pattern IDs + CSV columns it draws from.

### Deliverables

- `case_study_book.yaml` (emitted by `scripts/derive_profiles.py`)
- `scripts/run_case_study.py` (replays book through both configs, emits JSON + PNG)
- `products/case_study.md` + `case_study.pdf` (3 pages via WeasyPrint)
- Landing-page hero (Block G) displays the number; compare widget moves below fold
- 6 company decks lead slide 1 with the $ number, slide 2 with decomposition, slide 3 with "how I'd adapt this to {Nuvei/Interac/Shopify/Stripe/Brex/Wise}"
