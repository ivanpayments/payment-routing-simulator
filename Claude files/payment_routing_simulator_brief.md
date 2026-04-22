# Payment Routing Simulator — Product Brief

*Pre-build brief for Project 2 of the Apr–Jul 2026 job-search portfolio. Ships May 30, 2026. Anchor deliverable for 6 company decks: Nuvei, Interac, Shopify, Stripe, Brex, Wise.*

---

## Section A — Value proposition (PM lens)

### A.1 What it is

The Payment Routing Simulator is a multi-**archetype** payment-gateway simulator. One tool emulates five processor archetypes — `global-acquirer`, `regional-bank-processor`, `apm-specialist`, `cross-border-fx-specialist`, and `high-risk-or-orchestrator` — with realistic response codes, latency distributions, 3DS flows, decline-reason distributions, and retry semantics. It is delivered in three forms: a Python CLI (`prs`), a REST API with HMAC-signed webhook callbacks, and a PyPI package (`pip install payment-routing-simulator`). The runtime is backed by the same building blocks a real processor exposes: a payment state machine (PENDING → AUTHORIZED → CAPTURED / VOIDED → REFUNDED), idempotency keys, API-key authentication, Kafka event streaming, Celery-driven webhook delivery with exponential backoff.

Archetype behavior is configured in YAML files with distributions fitted from a 10,000-transaction, 72-column synthetic dataset — so approval rates, decline distributions, latency percentiles, and 3DS challenge rates are statistically realistic within each archetype rather than "200 OK every time."

**Why archetypes, not brand names.** Routing decisions are archetype-level, not brand-level. A merchant choosing between Stripe and Checkout.com is choosing between instances of the same archetype; a merchant choosing between Stripe and a Mexican bank processor is choosing between archetypes. Making that explicit is the product-thinking artifact. It also keeps the project clear of trademark friction with the very companies Ivan wants to interview at, and is intellectually honest about what the YAMLs actually represent (synthetic distributions, not reverse-engineered PSP internals).

### A.2 Why it's needed

Every merchant integrating a new payment processor hits the same wall: **test environments do not reflect production.**

- **Stripe's test mode always approves** unless you use magic card numbers. You cannot test "what happens when a global-acquirer returns a soft decline 05 for a Brazilian Visa at $300?" because the sandbox returns the response you ask for, not the response you would actually receive.
- **Each processor's sandbox behaves differently.** Different APIs, different coverage, different quirks. A QA team maintaining a multi-processor integration maintains five different test harnesses.
- **Edge cases are invisible until production.** 3DS challenge flows, latency spikes, country-specific decline patterns, retry behavior after soft declines, webhook-delivery races with idempotency — none of these are reproducible in sandbox. Teams discover them when customers complain.
- **QA cannot regression-test routing changes.** Every routing-logic change (e.g., "cascade declined Brazil Visas from the global-acquirer to the regional-bank-processor") needs validation against realistic behavior. Sandbox says it works; production doesn't.

The cost is concrete: a major PSP had a three-hour outage because its routing-fallback logic wasn't tested against realistic decline patterns; integration timelines stretch from 2 days to 2–6 weeks because developers cannot verify behavior until live traffic arrives.

### A.3 Who uses it

| Persona | Primary need |
|---|---|
| **Integration engineer** at a merchant | Test a new PSP integration before signing the contract; verify the cascade works before shipping |
| **QA engineer** | Regression-test routing logic against realistic archetype behavior in CI |
| **Solutions engineer** at a PSP | Demo multi-provider routing to prospects; simulate "what if you added us as a fallback to the global-acquirer archetype" |
| **Product manager** evaluating providers | Run 10k simulated transactions through a candidate archetype profile before contract signing |
| **AI / data engineer** | Generate realistic decline-rate training data; validate ML retry models against distribution-matched synthetic traffic |

### A.4 How they use it

**Local dev (CLI).** A developer runs `prs simulate --archetype global-acquirer --country BR --card visa --amount 300 --3ds` and gets back a realistic JSON response — decline code 05, latency 215ms, 3DS challenged, pares_status Y. Running 1,000 simulations shows the actual distribution (88% approval, 12% decline with realistic code frequencies).

**CI/CD.** Pin `payment-routing-simulator` in `requirements-dev.txt`:
```python
from payment_routing_simulator import simulate
def test_cascade_triggers_on_br_visa_decline():
    result = simulate(archetype="global-acquirer", country="BR", card_brand="visa", amount=300)
    if result.response_code == "05":
        fallback = simulate(archetype="regional-bank-processor", ...)
        assert fallback.status == "authorized"
```

**Integration testing (REST API).** QA points its test suite at `https://ivanantonov.com/routing-simulator/api`. They register a webhook URL, receive HMAC-signed callbacks, test full payment lifecycles (authorize → capture → refund) including the 409 Conflict path on invalid state transitions.

**Chatbot-driven merchant routing advice.** Project 1's Payment Data Chatbot calls the simulator's `POST /recommend-route` endpoint. A merchant asks "Which archetype should I route USD→BRL Visa $200 through?"; the chatbot returns a ranked table: global-acquirer 92% approval / 180ms p50 / $0.29 fee; cross-border-fx-specialist 88% / 150ms / $0.30; etc.

**Scope boundary — heuristic vs ML.** `/recommend-route` is heuristic ranking: for a given transaction, run the simulator across all 5 archetypes and rank by expected approval × latency × fee. It is rule-based and driven entirely by the YAML profile distributions — no training, no labels. ML-driven retry/cascade/abandon decisions (with confidence scores and SHAP explanations, trained on outcome labels with a custom `net_retry_value` objective) live in the ML Payment Recovery Engine (Project 3), which uses this simulator as its replay harness. Clean split: Project 2 simulates and ranks; Project 3 decides and explains.

### A.5 How they deploy it

| Mode | Effort | Use case |
|---|---|---|
| **Self-serve CLI** | `pip install payment-routing-simulator` | Individual developer local testing |
| **CI/CD dependency** | Pin in `requirements-dev.txt`; run via pytest | Automated regression testing in PR pipelines |
| **Shared service (docker-compose)** | `docker compose up -d` brings up API + Postgres + Redis + Kafka + Celery + Grafana in one command | Team-shared test environment on any VM |
| **Production-lite (client)** | Behind internal load balancer with per-team API keys, webhooks wired into Slack for 3DS/decline alerts | Real-client staging replacing sandboxes |

### A.6 Metrics to track

| Category | Metric | MVP target | Mature target |
|---|---|---|---|
| **Adoption** | PyPI downloads / week | 10 | 500+ |
| | GitHub stars | 5 | 200+ |
| | Docker image pulls / week | 2 | 50+ |
| **Engagement** | API requests / day | 100 | 10,000+ |
| | Unique API keys active / week | 1 (Ivan) | 20+ teams |
| | Avg simulations per session | 5 | 50+ |
| **Coverage** | % transaction parameters matched by an archetype profile | 95% of dataset combos | 99%+ |
| | Time to add a new archetype (YAML only) | 30 min | 30 min (unchanged) |
| **Quality** | p95 API response time | < 50 ms | < 30 ms |
| | Webhook delivery success (first attempt) | 95% | 99.5% |
| | Idempotency cache hit rate | n/a (low traffic) | 2–5% |
| | Archetype-vs-real accuracy (when comparable) | not measured | 90%+ approval-rate match per country |
| **Business (at client)** | Integration time saved vs sandbox | n/a | 40–70% reduction |
| | Bugs caught pre-production | n/a | 3–8 per routing change |
| | Routing changes deployed / week | n/a | 2–5 |

### A.7 Publication model (job-search-first OSS)

The project is published as real open source on GitHub and PyPI — anyone can `pip install payment-routing-simulator` and use it — but the **primary purpose is interview/portfolio signal**, not building a user community. Concretely:

| What is committed to | What is NOT committed to |
|---|---|
| Public GitHub repo, MIT license, clear README | Active issue-triage rotation, support SLAs |
| Works out of the box (`docker compose up`) | Community forum, Discord, office hours |
| Semantic versioning, published CI status | Long-term maintenance beyond Ivan's job hunt |
| Clear scope disclaimer ("archetypes not brands") | Feature roadmap driven by external requests |

This stance is baked into the README. It is also why the archetype framing matters — archetypes are a *framework* that delivers value immediately on read (interview signal) even if no external team ever adopts the package.

### A.8 What's WORKING on day one (post-May 30 launch)

- 5 archetype profiles (global-acquirer, regional-bank-processor, apm-specialist, cross-border-fx-specialist, high-risk-or-orchestrator) derived from `synthetic_transactions.csv` (10k rows, 72 cols — **synthetic**, not real production data)
- Full payment state machine with Postgres persistence and strict transition validation
- HMAC-SHA256 webhook delivery with Celery exponential-backoff retry (1-2-4-8-16s, max 5 attempts)
- Redis-backed idempotency keys (24h TTL)
- API-key authentication (publishable `pk_test_...` + secret `sk_test_...`)
- Rate limiting (sliding window per API key)
- Kafka `payment.events` topic streaming every state transition
- Prometheus `/metrics` + Grafana dashboard
- OpenTelemetry distributed tracing end-to-end
- structlog JSON logs with request correlation IDs
- GitHub Actions CI: lint + test on every push, PyPI publish on tagged release
- k6 load-test scripts and documented benchmarks
- Live REST API at `ivanantonov.com/routing-simulator/api` with OpenAPI at `/docs`
- Landing page at `ivanantonov.com/routing-simulator/` with a live comparison widget
- `pip install payment-routing-simulator` works from PyPI
- Integration into Project 1 chatbot via `POST /recommend-route` + `query_routing_intelligence` tool

### A.9 What needs to be ADDED for real client deployment

The MVP is calibrated on synthetic data and Ivan-scale traffic. A real enterprise client would require:

1. **Real-data-calibrated archetype profiles.** Derive YAMLs from the client's own transaction history — ideally ≥100,000 transactions per archetype per country segment over a 3–6 month window.
2. **Processor-specific overrides on top of archetypes.** Clients with real PSP relationships want profile overlays (e.g., "our Stripe instance on top of the global-acquirer archetype"). This adds a two-layer YAML inheritance model not present in MVP.
3. **Enterprise authentication.** SSO (Okta / Azure AD), role-based access control, audit logging. API keys alone do not satisfy a regulated buyer's security review.
4. **Multi-tenancy.** Team namespaces, per-team webhook URLs, per-team rate limits, per-team usage metering for chargeback.
5. **Accuracy-validation harness.** Continuously compare simulator output against real processor responses via shadow-traffic replay. Drift detection with a weekly accuracy report per archetype / country.
6. **Profile-refresh pipeline.** Scheduled job rebuilds YAML profiles from the latest 30 days of real data; alerts on distribution shift beyond a configurable threshold.
7. **Compliance review.** PCI DSS scope assessment (likely out-of-scope since no PAN is stored, but requires formal written confirmation). GDPR for webhook payloads. SOC 2 Type II if sold as SaaS.
8. **SLA and on-call.** PagerDuty integration, explicit uptime target (e.g., 99.9% for API), on-call rota, runbook for the top 10 failure modes.
9. **High availability.** Managed Postgres (RDS / Cloud SQL), managed Kafka (MSK / Confluent Cloud), multi-AZ, automated failover. Current setup is single-node Docker on one droplet.
10. **Data retention policy.** Transaction history TTL, automated purging, encrypted backups, point-in-time restore.
11. **Hardened 3DS model.** Real 3DS responses include ARes codes, DS signals, SCA exemption flags (TRA, low-value, MIT) — not in the MVP.
12. **Extended archetype set.** Real merchants may benefit from splitting (e.g., "acquirer with proprietary network" as a sixth archetype, separating Amex/Discover).
13. **Regulatory-specific behaviors.** PSD2 SCA exemptions, TRA, low-value rules, MIT vs CIT distinctions, India recurring-mandate rules.
14. **Customer-facing archetype-to-processor mapping service.** "Given the processors I already have contracts with, which archetypes am I covered on, and which am I missing?" — net-new product.

### A.10 Concrete case study — the headline number

**Headline: $X recovered per $1M processed** — this is the portfolio's Amazon-Bar-Raiser "Deliver Results" anchor. One number. Fully reproducible. Every dollar traces to a CSV column and a pattern ID. Target band: $8,000–$25,000 per $1M processed (80–250 bps), depending on corridor mix.

**The simulated book** (`case_study_book.yaml`, committed). Mid-market DTC apparel merchant — $50M annualized GMV, 10K txn/day, $180 AOV, 30% international (US home 70% / EU 12% / LATAM 10% / APAC 6% / MEA 2%), card-brand mix per public industry benchmarks. Rows seeded deterministically from `routing_transactions.csv`, so the number is identical on every replay.

**The two configurations.**

| Config | Routing | Retry | Network tokens | SCA exemptions |
|---|---|---|---|---|
| **Baseline** | Single global-acquirer; no fallback | None | PAN only | None applied |
| **Intervention** | Smart routing: per-corridor archetype pick | Soft-decline cascade (2nd @ 4h, 3rd @ 24h; codes 05/51/61/91) | Preferred when supported | TRA/LVP/recurring applied in EEA |

**The decomposition** (3 contributing lines, summed to the headline):

| Contributor | Target | Source |
|---|---|---|
| Blended auth-rate lift (pp) | +1.5 to +3.0 pp | Pattern cluster AD001–AD100 applied to book; delta measured at simulator layer |
| Retry-salvage rate (%) | 15–30% of soft-declines recovered within 24h | Pattern cluster RC001–RC050 applied; measured on is_retry=True rows |
| Effective fee delta (bps) | -10 to -40 bps | Pattern cluster AF050–AF110 (interchange + scheme + FX markup); measured on `processor_fee_bps + scheme_fee_bps + fx_markup_bps` |

Each line is clickable on the landing page ("show the math" toggle expands to the exact pattern IDs, CSV columns, and the SQL query that produced the number).

**Commands that produce the number.**

```
$ scripts/derive_profiles.py         # emits 5 YAML profiles + case_study_book.yaml
$ scripts/run_case_study.py          # replays book through both configs
  → case_study_output.json           # full decomposition
  → case_study_chart.png             # baseline vs intervention bar chart
$ weasyprint products/case_study.md products/case_study.pdf
```

**Why this is the portfolio's anchor.** Amazon Bar-Raiser feedback specifically calls out the need for a second "Deliver Results" story beyond the Payment Data Chatbot. A defensible dollar number — reproducible on any laptop in 30 seconds, with the math exposed — is that story. The simulator's infra depth (state machine, webhooks, Kafka, idempotency) becomes the supporting "how I built this rigorously" narrative.

**Landing page hero (Block G).** The number lives above the fold. A "show the math" disclosure expands the decomposition table. The live compare widget (archetype fingerprint by corridor) moves below the fold — it's the proof, not the hook.

**Deck lead slide** (6 decks: Nuvei, Interac, Shopify, Stripe, Brex, Wise). Slide 1 = the number. Slide 2 = the decomposition. Slide 3 = "how I'd adapt this to {target company}" — e.g. for Wise, the FX-markup contributor dominates; for Shopify, the auth-lift contributor dominates.

---

## Section B — Technical architecture

### B.1 Component diagram

```
  [CLI]    [Python SDK]    [Chatbot (Project 1)]    [Merchant app]
    \          |                   |                     /
     \         v                   v                    /
      +------------------------------------------------+
      |          FastAPI  (uvicorn, port 8082)         |   ← Caddy reverse-proxies
      |   • API-key auth  • idempotency check          |     at /routing-simulator/api/*
      |   • Pydantic validation  • Redis rate-limit    |
      +---+-------+-------+-------+-------+------------+
          |       |       |       |       |
          |       |       |       |       +→  [Simulation engine]
          |       |       |       |             ↓
          |       |       |       |       [Archetype YAML profiles]
          |       |       |       |         - global-acquirer.yaml
          |       |       |       |         - regional-bank-processor.yaml
          |       |       |       |         - apm-specialist.yaml
          |       |       |       |         - cross-border-fx-specialist.yaml
          |       |       |       |         - high-risk-or-orchestrator.yaml
          |       |       |       |             ↑
          |       |       |       |       scripts/derive_profiles.py
          |       |       |       |       (reads synthetic_transactions.csv once,
          |       |       |       |        aggregates by archetype mapping, writes YAML)
          |       |       |       |
          |       |       |       +→  [PostgreSQL]
          |       |       |             • transactions
          |       |       |             • state_transitions
          |       |       |             • webhook_deliveries
          |       |       |             • api_keys
          |       |       |             PENDING → AUTHORIZED → CAPTURED / VOIDED → REFUNDED
          |       |       |
          |       |       +→  [Redis]   idempotency cache (24h TTL)
          |       |                     rate-limit window (sorted-set ZADD)
          |       |                     Celery broker
          |       |
          |       +→  [Kafka (KRaft mode, single-node)]
          |             topic: payment.events
          |             partition key: transaction_id
          |                 ↓
          |           [Celery workers] → merchant webhook URLs
          |                              (HMAC-SHA256, exponential backoff, max 5 attempts)
          |           [ML Recovery Engine consumer — Project 3]
          |                              (consumes payment.declined)
          |
          +→  [Prometheus /metrics]  →  [Grafana dashboard]
          +→  [OpenTelemetry traces]    (API → engine → DB → Kafka → webhook)
          +→  [structlog JSON logs]     (correlation IDs throughout)
```

### B.2 The five archetypes

Archetypes are chosen to span the routing decisions that actually matter: geography, acquiring model, payment-method mix, risk posture, and merchant-type fit. Any real PSP a merchant deals with maps cleanly onto one primary archetype.

| Archetype | One-line | Characteristic behavior | Reference processors (in docs / README only — nominative fair use) |
|---|---|---|---|
| **`global-acquirer`** | Multi-country direct acquiring, card-dominant | Approval 88–95% by country; latency p50 150–200ms; sophisticated decline taxonomy (ISO 8583 + merchant advice codes); strong 3DS2; stored-credential-aware | Stripe, Adyen, Checkout.com |
| **`regional-bank-processor`** | Single-country/region, tight issuer-acquirer proximity | Higher local auth (90–96%), weak cross-border (<70%); basic decline granularity; lower latency domestically; often better pricing in-country | Banorte/Prosa (MX), Cielo (BR), Interac (CA debit), iyzico (TR) |
| **`apm-specialist`** | Alternative payment methods, non-card rails | PIX/iDEAL/OXXO/UPI/SEPA-DD; country-locked; different failure modes (voucher expiry, bank redirect drop-off); no CVV/3DS concept | Mercado Pago, Trustly, dLocal, EBANX |
| **`cross-border-fx-specialist`** | Multi-currency settlement, local entities in many markets | FX-margin-transparent; marketplace-split capable; high-risk-tolerant; settlement logic for 30+ currencies | Nuvei, Worldpay, dLocal, Payoneer |
| **`high-risk-or-orchestrator`** | Two sub-modes: (a) high-risk vertical specialist; (b) pure orchestration layer | Mode (a): chargeback-tolerant, aggressive 3DS, higher fees. Mode (b): no acquiring — routes to the other four archetypes | Nuvei (high-risk side), Yuno, Primer, Gr4vy, Spreedly |

**Coverage dimensions the archetypes span:**
- Global vs regional (1 vs 2)
- Card vs APM (1 vs 3)
- Cross-border vs domestic (4 vs 2)
- Mainstream vs edge (1 vs 5a)
- Acquirer vs orchestrator (1 vs 5b)

### B.3 Stack choices — decisions and rationale

| Layer | Choice | Why this | Why NOT the alternative |
|---|---|---|---|
| Language | Python 3.12 | Ivan's strongest language; fastest path to ship; numpy/scipy for distribution sampling; shared with Projects 1 and 3 | Go would be faster at runtime but slower to ship, duplicates no tooling |
| CLI framework | Click | Python-CLI de facto standard; auto-generated help; composes with `prs server` subcommand | argparse: verbose; Typer: less mature |
| API framework | FastAPI | Pydantic validation built in; OpenAPI auto-generated; async-ready | Flask: no validation; Django: heavy; Starlette: too low-level |
| Profile config | YAML | Human-editable; contributors add archetypes without writing Python; matches SE tooling patterns | JSON: no comments; TOML: awkward nesting; dataclasses: Python-only |
| Persistence | PostgreSQL 16 + Alembic | State-machine needs ACID; Alembic for versioned migrations; Postgres is the default at every PSP on the interview list | SQLite: single-writer breaks webhook concurrency; MongoDB: schemaless loses invariants |
| Cache + broker | Redis 7 | Sub-ms idempotency lookups; also Celery broker + rate-limit sliding window — one component, three uses | Memcached: no sorted sets; RabbitMQ: broker only |
| Message bus | Kafka (KRaft mode) | Payment-industry standard; Project 3 consumes `payment.declined`; KRaft removes Zookeeper complexity | Redis Pub/Sub: no durability/replay; RabbitMQ: weaker replay + weaker interview signal |
| Async work | Celery | Webhook delivery must survive API restarts; durable queue + built-in exponential backoff | asyncio bg tasks: lost on restart; Dramatiq: less tooling |
| Webhook signing | HMAC-SHA256 | Exact pattern used by Stripe, Adyen, GitHub — interview signal | RSA: slower; mTLS: infra-heavy |
| API auth | Key pairs (`pk_test_...` / `sk_test_...`) | Mirrors Stripe's model; stateless | OAuth2: heavy for a simulator; JWT: key mgmt infra |
| Latency model | Log-normal | Real payment latency right-skewed; fits well from p50/p95 | Gaussian: undershoots p99; exponential: wrong shape at p50 |
| Observability | Prometheus + Grafana + OpenTelemetry + structlog | Industry standard; no lock-in; runs anywhere | Datadog / New Relic: paid, slower to demo |
| Container | Docker + docker-compose | Single-command spin-up; reproducible | Kubernetes: over-engineering for single droplet |
| IaC | Terraform | Droplet + firewall + DNS as code; TPM/SE platform signal | Manual console: not reviewable |
| CI/CD | GitHub Actions | Free for public repos; matrix testing; PyPI publish action | CircleCI: extra account; Jenkins: self-host overhead |
| Packaging | `pyproject.toml` + wheel + PyPI | PEP 517/518 modern standard | `setup.py`: deprecated |
| Load test | k6 | Clean p50/p95/p99 reports; Go runtime handles high concurrency | Locust: Python GIL distorts at high RPS |
| Hosting | DigitalOcean droplet (existing, 209.38.71.25) | Already operates OpenClaw + dashboard + portfolio here; cheapest path to live | AWS/GCP: complexity not worth it for demo |

### B.4 Non-obvious design decisions

**Archetypes, not brand names.** Routing decisions are archetype-level; interviewing AT Stripe/Adyen/Nuvei makes real-name profiles a legal and reputational risk; the YAMLs are synthetic-data-derived, so brand naming would also be factually misrepresenting the content. Archetype framing is the single strongest product-thinking signal in the portfolio. Real processors appear in README documentation only, as illustrative references under nominative fair use.

**Kafka present even at MVP.** The simulator itself doesn't strictly need Kafka. It ships in the MVP because **Project 3 (ML Recovery Engine) consumes `payment.declined` events** for retry decisions. Deploying Kafka in Project 2 means Project 3 plugs in with zero infra work.

**Payment state machine, not just a response generator.** A naive mock returns `{"status": "approved"}`. This simulator maintains full lifecycle state in Postgres; every transition is validated; every invalid transition returns 409 Conflict. #1 topic asked in PSP SE interviews.

**Idempotency on every endpoint, including `/simulate`.** Matches Stripe's actual API contract. Teaches callers the right pattern from the first request.

**Single-node Kafka via KRaft.** Production would be 3-node managed; MVP runs single Confluent-image container, no Zookeeper. Flagged in A.9 for client deployment.

**Log-normal latency fitted from p50/p95 only.** `mu = ln(p50)`, `sigma = (ln(p95) - mu) / 1.645`. Enough to reproduce shape without over-fitting. 1–2% chance of fat-tail multiplier per draw keeps p99 realistic.

### B.5 Data flow traces

**Trace 1: successful simulate → authorize → capture → webhook.**
```
1. POST /simulate
   headers: Authorization: Bearer sk_test_xxx, Idempotency-Key: idem_abc123
   body:    {archetype: "global-acquirer", country: "BR", card_brand: "visa", amount: 300}

2. Middleware:
   auth.validate_key(sk_test_xxx)                               → 1ms
   idempotency.check(idem_abc123) in Redis                      → not found
   rate_limit.check(sk_test_xxx) in Redis sorted-set            → under limit

3. Engine:
   archetype_loader.load("global-acquirer")                     → YAML cache hit, 0ms
   approval_prob = 0.87 * country_mod["BR"] * brand_mod["visa"] * amount_mod(300)
                 = 0.87 * 0.94 * 1.00 * 0.98 = 0.801
   random.uniform(0,1) = 0.312 < 0.801                          → APPROVED
   latency_ms = lognormal(mu=ln(0.180), sigma=0.42) * country_mod["BR"]  → 215ms
   three_ds.simulate(...)                                       → challenged=True, pares_Y, v2.2

4. DB:
   INSERT INTO transactions (id, idempotency_key, archetype, ..., state='PENDING')
   state_machine.transition(txn, to='AUTHORIZED')
   INSERT INTO state_transitions (txn_id, from='PENDING', to='AUTHORIZED', ts=now())

5. Kafka:
   kafka_producer.send('payment.events', {event: 'payment.authorized', txn_id, archetype, ...})

6. Celery (async):
   deliver_webhook.delay(url='https://merchant.com/hooks/payments', payload, secret)
   → signed payload posted, 200 received, logged to webhook_deliveries

7. Response (sync): 200 OK, {txn_id, status: 'authorized', response_code: '00', latency_ms: 215, ...}
   idempotency.store(idem_abc123, response, ttl=24h) in Redis

8. Merchant follows up:
   POST /capture/txn_01HXK... with Idempotency-Key: idem_cap_xyz
   → state_machine.transition(txn, to='CAPTURED')
   → kafka_producer.send('payment.captured', ...)
   → webhook delivered
```

**Trace 2: declined simulate triggers cross-archetype cascade.**
```
1. POST /simulate {archetype: "global-acquirer", country: "BR", card_brand: "visa", amount: 300}
   → engine: random.uniform(0,1) = 0.91 > 0.801 → DECLINED
   → decline_engine.select_code(archetype='global-acquirer', country='BR', card='visa', amount=300)
     Reads YAML:
       global-acquirer.decline_codes.BR.visa:
         "05": weight=0.42
         "51": weight=0.18
         "14": weight=0.11
         ...
     Weighted random → "05" selected
   → response: 200 OK, {status: 'declined', response_code: '05', merchant_advice: 'retry_later'}
   → Kafka: payment.declined event published
   → Webhook fired with signed payload

2. Client cascade logic:
   if result.response_code == "05" and result.country == "BR":
       fallback = POST /simulate {archetype: "regional-bank-processor", ...same params}
       → engine: random.uniform(0,1) = 0.22 < 0.93 (regional BR visa) → APPROVED
```

**Trace 3: duplicate request hits idempotency cache.**
```
1. POST /simulate ... Idempotency-Key: idem_abc123  [first request, as in Trace 1]
2. Same request retried 3s later (client network glitch):
   POST /simulate ... Idempotency-Key: idem_abc123
   → Middleware: idempotency.check(idem_abc123) in Redis → HIT
   → Return cached response immediately. 200 OK. Not re-processed.
   → No new transaction row. No duplicate Kafka event. No duplicate webhook.
```

---

## Section C — End-product description: what you actually see and do

Three surfaces, each reachable by a different persona. Below is exactly what each one looks like after Block H.

### C.1 Surface 1 — Landing page (Ivan's primary interview demo)

**URL:** `https://ivanantonov.com/routing-simulator/`

1. **Hero block.**
   > *"Payment Routing Simulator"*
   > *"Offline mock gateway that models five processor archetypes — global-acquirer, regional-bank-processor, APM-specialist, cross-border-FX-specialist, and high-risk/orchestrator — with realistic response codes, latency, 3DS flows, decline reasons, and retry semantics."*
   > `pip install payment-routing-simulator`   *[copy button]*
   > [ GitHub ]  [ PyPI ]  [ OpenAPI docs ]

2. **Live comparison widget.** Dropdowns: **Country** (15 ISO codes), **Card brand** (visa/mastercard/amex), **Amount** (slider, $1–$5000). Click **"Compare archetypes."** Live call to `POST /api/compare`:

   | Archetype | Approval | p50 lat | p95 lat | Top decline | Est. fee |
   |---|---|---|---|---|---|
   | global-acquirer | 91.3% | 198 ms | 412 ms | 05 (do not honor) | $0.283 |
   | regional-bank-processor | 88.7% | 152 ms | 331 ms | 51 (insufficient funds) | $0.301 |
   | apm-specialist | n/a (PIX) | 341 ms | 780 ms | voucher-expired | $0.241 |
   | cross-border-fx-specialist | 89.1% | 183 ms | 389 ms | 05 | $0.274 |
   | high-risk-or-orchestrator | 87.4% | 247 ms | 523 ms | 51 | $0.289 |

3. **Archetype reference table** (from Section B.2) — which real processors each archetype is modeled after.

4. **Architecture diagram** (rendered SVG of the Section B.1 diagram).

5. **"Try it now" block.** Copy-pasteable terminal snippet:
   ```
   pip install payment-routing-simulator
   prs simulate --archetype global-acquirer --country BR --card visa --amount 300 --3ds
   ```

6. **Footer links.** GitHub repo, PyPI page, OpenAPI docs at `/routing-simulator/api/docs`, README, BENCHMARKS.md.

### C.2 Surface 2 — The CLI (developer experience)

Install:
```
$ pip install payment-routing-simulator
Successfully installed payment-routing-simulator-0.1.0
```

List archetypes:
```
$ prs list-archetypes
global-acquirer              — Multi-country direct acquiring, card-dominant
regional-bank-processor      — Single-country, issuer-acquirer proximity
apm-specialist               — Alternative payment methods (PIX, iDEAL, OXXO, UPI)
cross-border-fx-specialist   — Multi-currency settlement, marketplace splits
high-risk-or-orchestrator    — High-risk vertical OR pure routing layer
```

Single simulation:
```
$ prs simulate --archetype global-acquirer --country BR --card visa --amount 300 --3ds
{
  "transaction_id": "txn_01HXK5Z3RQH7DBA1E3V0MZPK8Y",
  "status": "authorized",
  "archetype": "global-acquirer",
  "country": "BR",
  "card_brand": "visa",
  "amount": 300.00,
  "currency": "BRL",
  "response_code": "00",
  "merchant_advice": null,
  "latency_ms": 215,
  "three_ds": {
    "challenged": true,
    "pares_status": "Y",
    "version": "2.2",
    "challenge_method": "otp"
  },
  "fee_estimate_usd": 0.287,
  "created_at": "2026-05-31T14:22:09.113Z"
}
```

Compare across archetypes:
```
$ prs compare --country MX --card mastercard --amount 500
┌──────────────────────────────┬──────────┬─────────┬──────────┬───────────────────┬───────────┐
│ Archetype                    │ Approval │ p50 lat │ p95 lat  │ Top decline       │ Est. fee  │
├──────────────────────────────┼──────────┼─────────┼──────────┼───────────────────┼───────────┤
│ global-acquirer              │ 91.3%    │ 198 ms  │ 412 ms   │ 05 (do not honor) │ $0.28     │
│ regional-bank-processor      │ 94.2%    │  98 ms  │ 221 ms   │ 51 (insufficient) │ $0.21     │
│ apm-specialist               │ n/a      │ n/a     │ n/a      │ n/a (no card)     │ n/a       │
│ cross-border-fx-specialist   │ 89.1%    │ 183 ms  │ 389 ms   │ 05                │ $0.27     │
│ high-risk-or-orchestrator    │ 87.4%    │ 247 ms  │ 523 ms   │ 51                │ $0.29     │
└──────────────────────────────┴──────────┴─────────┴──────────┴───────────────────┴───────────┘

Recommendation: regional-bank-processor (highest approval, lowest fee — issuer proximity wins for MX domestic)
```

Start a local REST server:
```
$ prs server --port 8080
[INFO] FastAPI starting on http://localhost:8080
[INFO] OpenAPI docs at http://localhost:8080/docs
[INFO] Connected to Postgres, Redis, Kafka
[INFO] Ready to accept requests
```

### C.3 Surface 3 — The REST API (integration / QA experience)

**Step 1 — Get an API key.** Self-serve form at `/routing-simulator/keys`:
```
publishable_key: pk_test_<EXAMPLE>
secret_key:      sk_test_<EXAMPLE>
```

**Step 2 — Register a webhook URL.**
```
curl -X POST https://ivanantonov.com/routing-simulator/api/webhooks/register \
  -H "Authorization: Bearer sk_test_..." \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://my-test-server.com/webhooks/payments",
    "events": ["payment.authorized", "payment.declined", "payment.captured", "payment.refunded"],
    "secret": "whsec_my_webhook_secret"
  }'

→ 201 Created   {"webhook_id": "wh_01HXK..."}
```

**Step 3 — POST /simulate with idempotency key.**
```
curl -X POST https://ivanantonov.com/routing-simulator/api/simulate \
  -H "Authorization: Bearer sk_test_..." \
  -H "Idempotency-Key: idem_integration_test_001" \
  -H "Content-Type: application/json" \
  -d '{"archetype":"global-acquirer","country":"BR","card_brand":"visa","amount":300,"three_ds":true}'

→ 200 OK
  {
    "transaction_id": "txn_01HXK5...",
    "archetype": "global-acquirer",
    "status": "authorized",
    "response_code": "00",
    "latency_ms": 215,
    ...
  }
```

**Step 4 — Receive signed webhook within 2 seconds.**
```
POST https://my-test-server.com/webhooks/payments
Headers:
  X-Signature-256: sha256=9a7f...
  X-Event-Type: payment.authorized
  Content-Type: application/json

Body:
{
  "event_type": "payment.authorized",
  "transaction_id": "txn_01HXK5...",
  "archetype": "global-acquirer",
  "amount": 300.00,
  "currency": "BRL",
  "timestamp": "2026-05-31T14:22:09.113Z"
}
```
Merchant verifies HMAC with `secret` from registration.

**Step 5 — Progress the transaction.**
```
curl -X POST .../api/capture/txn_01HXK5... -H "Authorization: Bearer sk_test_..." -H "Idempotency-Key: idem_cap_001"
→ 200 OK   {"transaction_id": "...", "status": "captured"}

curl -X POST .../api/refund/txn_01HXK5... -H "Authorization: Bearer sk_test_..." -H "Idempotency-Key: idem_ref_001"
→ 200 OK   {"transaction_id": "...", "status": "refunded"}
```

**Step 6 — Assert 409 on invalid transition.**
```
curl -X POST .../api/capture/txn_declined_xyz -H "Authorization: Bearer sk_test_..." -H "Idempotency-Key: idem_cap_err_001"
→ 409 Conflict
  {
    "error": "invalid_transition",
    "message": "Cannot transition from DECLINED to CAPTURED",
    "current_state": "DECLINED",
    "attempted_state": "CAPTURED",
    "valid_transitions": []
  }
```

**Inspect transition history:**
```
curl .../api/transactions/txn_01HXK5.../transitions
→ 200 OK
  [
    {"from": "PENDING",    "to": "AUTHORIZED", "at": "2026-05-31T14:22:09.113Z"},
    {"from": "AUTHORIZED", "to": "CAPTURED",   "at": "2026-05-31T14:23:44.202Z"},
    {"from": "CAPTURED",   "to": "REFUNDED",   "at": "2026-05-31T14:25:01.981Z"}
  ]
```

### C.4 How Ivan uses the finished product in the job search

1. **Portfolio website card.** Third card on `ivanantonov.com/portfolio` alongside the Turkish Airlines case and Payment Data Chatbot. Headline: *"Payment Routing Simulator — open-source archetype-based multi-processor mock gateway."* Click-through to `/routing-simulator/`.

2. **CV entry.** Under "Projects":
   > **Payment Routing Simulator** — open-source mock gateway modeling 5 processor archetypes (global-acquirer, regional-bank-processor, APM-specialist, cross-border-FX-specialist, high-risk/orchestrator). Python + FastAPI + Postgres + Redis + Kafka + Celery. Published to PyPI, deployed at ivanantonov.com/routing-simulator. Full payment state machine, HMAC-signed webhooks, idempotency, API-key auth, Prometheus metrics, k6-benchmarked.

3. **Interview screen-share script (15 min)** for SE interviews at Stripe, Adyen, Nuvei, Checkout.com:
   - (2 min) Landing page + live comparison widget.
   - (2 min) *The archetype framing decision*: "I modeled archetypes, not brands. Routing decisions are archetype-level — Stripe vs Checkout is same archetype; Stripe vs a Mexican bank processor is cross-archetype. Making that explicit is the product decision."
   - (2 min) OpenAPI docs, state machine walkthrough.
   - (3 min) docker-compose service graph — "Redis for three things, Kafka for Project 3 handoff, Celery so webhook delivery survives restarts."
   - (2 min) `webhooks.py` — HMAC signing + Celery retry backoff.
   - (2 min) `idempotency.py` — Redis ZADD + 24h TTL.
   - (2 min) `BENCHMARKS.md` — k6 p50/p95/p99 at 100/500/1000 concurrent users.

4. **Deck slide template for 6 target companies.** Each gets one slide mapping the company to its archetype(s):
   - **Nuvei** → cross-border-FX-specialist + high-risk sub-mode. "Here's how I'd extend the archetype for your LatAm + high-risk-vertical surface: add X, Y, Z fields."
   - **Interac** → regional-bank-processor (CA debit specialist). "Here's how I'd model Interac e-Transfer and push-to-card as an archetype extension."
   - **Shopify** → global-acquirer + orchestrator hybrid. "Shopify Payments + Shop Pay + Stripe Connect marketplace splits live at the intersection."
   - **Stripe** → canonical global-acquirer + orchestrator. "Here's what I'd change in the archetype to add Payment Intents + SCA confirmation flow."
   - **Brex** → global-acquirer with commercial-card extension. "L2/L3 data + interchange tiers as archetype overlay."
   - **Wise** → cross-border-FX-specialist. "FX corridor modeling per simulation — here's the YAML extension."

5. **"What would you build next?"** Point at Project 3 (ML Payment Recovery Engine), which consumes `payment.declined` events from this simulator's Kafka topic. "The two products form one system — the simulator generates realistic failures across archetypes; the recovery engine learns which archetype to retry into. Project 3 ships June 27."

6. **Interview one-liner for the archetype decision:**
   > "I chose archetypes over real names because routing decisions are archetype-level. A merchant choosing between Stripe and Checkout is choosing between instances of the same archetype, but choosing between Stripe and a Mexican bank processor is choosing between archetypes. The framework made that explicit — and it kept the project clear of trademark friction with the very companies I want to work at."

---

*End of brief. Next step: approved PDF → start code Block A.*
