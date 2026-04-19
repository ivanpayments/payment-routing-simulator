# payment-router

A payment provider routing simulator for integration testing.

Simulate realistic approval rates, decline codes, latency distributions, 3DS flows, and soft-decline retry cascades — without touching a real acquirer. Think of it as WireMock for payment routing.

**Live API:** `https://ivanantonov.com/router/` — health endpoint is public; `/simulate`, `/compare`, `/recommend`, `/query` require an `sk_test_...` bearer key.

**Scope:**
- 5 processor archetypes (11 variants): global-acquirer, cross-border-fx-specialist, high-risk-or-orchestrator, regional-bank-processor, regional-card-specialist
- Card rails only (no APMs)
- 6 card brands: Visa, Mastercard, Amex, UnionPay, Discover, JCB

**Use cases:**
- Test retry cascade logic before going live with a new acquirer
- Reproduce country-specific decline patterns
- Benchmark routing strategies on a synthetic transaction book
- Give the Payment Data Chatbot a live routing intelligence backend

---

## Install

```bash
git clone https://github.com/ivanpayments/payment-routing-simulator
cd payment-routing-simulator
pip install -e .
```

Requires Python 3.12+.

---

## CLI quickstart

**Simulate a single transaction:**
```bash
payment-router simulate -p global-acquirer-a -c BR --card visa --card-type credit -a 300

  Provider : global-acquirer-a
  Status   : APPROVED
  Code     : 00 - Approved
  Latency  : 312 ms
```

**Compare all providers for a transaction profile:**
```bash
payment-router compare -c BR --card visa -a 300

  Comparing 11 providers - BR / visa / 300.0 USD

  Provider                        Approval   p50 ms   p95 ms
  ----------------------------------------------------------
  global-acquirer-a                 86.0%      317      1006
  regional-card-specialist-b        84.5%      402      1198
  cross-border-fx-specialist-a      82.1%      540      1500
  ...
```

**Route with soft-decline retry cascade:**
```bash
payment-router route -p global-acquirer-a -p regional-bank-processor-a -c BR --card visa -a 300

  Attempt 1  global-acquirer-a      APPROVED  code=00  312ms
  Outcome  : SUCCESS
```

**Cross-border issuer penalty:**
```bash
payment-router simulate -p global-acquirer-a -c US --issuer-country NG --card visa -a 100 --runs 200

  Runs     : 200
  Approved : 148/200 (74.0%)   # vs ~86% domestic — Tier 3 issuer penalty applied
```

**List available providers:**
```bash
payment-router list-providers

  global-acquirer-a             Global Acquirer A
  global-acquirer-b             Global Acquirer B
  cross-border-fx-specialist-a  Cross-Border FX Specialist A (APAC corridor)
  cross-border-fx-specialist-b  Cross-Border FX Specialist B (Europe corridor)
  high-risk-or-orchestrator-a   High-Risk / Orchestrator A
  high-risk-or-orchestrator-b   High-Risk / Orchestrator B
  regional-bank-processor-a     Regional Bank Processor A (LATAM)
  regional-bank-processor-b     Regional Bank Processor B (Europe)
  regional-bank-processor-c     Regional Bank Processor C (APAC)
  regional-card-specialist-a    Regional Card Specialist A (Europe)
  regional-card-specialist-b    Regional Card Specialist B (LATAM)
```

---

## REST API

Start the server:
```bash
payment-router server --port 8090
```

**Compare providers:**
```bash
curl -s -X POST http://localhost:8090/compare \
  -H "Content-Type: application/json" \
  -d '{"country":"BR","card_brand":"visa","card_type":"credit","amount":300,"currency":"USD"}' \
  | jq '.[0]'
```

**Routing intelligence (used by chatbot):**
```bash
curl -s -X POST http://localhost:8090/query \
  -H "Content-Type: application/json" \
  -d '{"country":"IN","amount":150,"card_brand":"visa","card_type":"debit","use_3ds":true}'
```

Returns `recommended_provider`, `retry_order`, plain-English `reasoning`, and a `key_insight` (cross-border penalty, 3DS challenge variance, latency spread, etc.).

Full API docs at `http://localhost:8090/docs` (Swagger UI).

---

## Provider profiles

Five archetypes (11 variants) derived from a 106,739-row synthetic transaction dataset:

| Archetype | Variants | Notes |
|---|---|---|
| `global-acquirer` | a, b | Broadest country coverage (~20 countries) |
| `cross-border-fx-specialist` | a (APAC), b (Europe) | Widest currency coverage |
| `high-risk-or-orchestrator` | a (Europe-leaning), b (Americas-leaning) | Low approval, high 3DS challenge |
| `regional-bank-processor` | a (LATAM), b (Europe), c (APAC) | Fastest regional processor |
| `regional-card-specialist` | a (Europe/DACH), b (LATAM) | Strong card-rail focus |

Per-country overrides (base approval, card brand modifiers, 3DS challenge rate, latency) are stored in `payment_router/providers/*.yaml`.

Regenerate YAMLs from source data:
```bash
python scripts/derive_profiles.py --csv "Claude files/routing_transactions.csv"
```

---

## Benchmark

Compare a naive strategy (always route to `global-acquirer-a`) against smart routing with soft-decline retry:

```bash
python scripts/benchmark.py --runs 2000

  Strategy A  global-acquirer-a (always)
  Strategy B  smart routing + soft-decline retry

  Metric                       Strategy A   Strategy B    Delta
  ----------------------------------------------------------------
  Blended approval rate            84.2%        95.0%    +10.8%
  Latency p50 (ms)                   317          391      +74
```

---

## Architecture

- **No network calls** — fully local, deterministic per seed, no external dependencies at runtime
- **Synthetic data only** — approval rates and latency derived from a public synthetic dataset; no real transaction data
- **YAML config layer** — provider profiles in `payment_router/providers/*.yaml`; swap or extend without touching engine code
- **Issuer-country model** — 3-tier modifier (Tier 1 = 1.00×, Tier 2 = 0.94×, Tier 3 = 0.87×) applied when issuer country differs from merchant country
- **3DS simulation** — challenge vs frictionless per provider/country, ECI codes, PaRes status, liability shift
- **Retry cascade** — soft declines (retryable codes) cascade to next provider; hard declines stop immediately

---

## Tests

```bash
pip install -e ".[dev]"
pytest
```

45 tests covering simulation correctness, retry logic, cross-border penalties, 3DS liability shift, provider comparison, API auth, idempotency, rate limiting, state machine, webhook signing, and Kafka production.
