# payment-router

A local payment provider routing simulator for integration testing.

Simulate realistic approval rates, decline codes, latency distributions, 3DS flows, and soft-decline retry cascades — without touching a real acquirer. Think of it as WireMock for payment routing.

**Use cases:**
- Test retry cascade logic before going live with a new acquirer
- Reproduce country-specific decline patterns (BR Pix latency, IN 3DS friction, NG cross-border penalty)
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

  Comparing 6 providers - BR / visa / 300.0 USD

  Provider         Approval   p50 ms   p95 ms
  ------------------------------------------------
  apm-specialist     88.0%      421      1142  ##################
  global-acquirer-a  86.0%      317      1006  #################
  ...
```

**Route with soft-decline retry cascade:**
```bash
payment-router route -p apm-specialist -p global-acquirer-a -c BR --card visa -a 300

  Attempt 1  apm-specialist         APPROVED  code=00  438ms
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

  global-acquirer-a    Global Acquirer A
  global-acquirer-b    Global Acquirer B
  regional-bank        Regional Bank
  apm-specialist       APM Specialist
  fx-cross-border      FX Cross-Border
  orchestrator-high-risk  Orchestrator / High-Risk
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

Six archetypes derived from a 108K-row synthetic transaction dataset:

| Provider | Approval | Latency p50 | Notes |
|---|---|---|---|
| `global-acquirer-a` | 86% | 317ms | Broadest country coverage |
| `global-acquirer-b` | 86% | 316ms | Similar profile, different decline mix |
| `regional-bank` | 82% | 257ms | Fastest; strong in AE, BR, IN, MX |
| `apm-specialist` | 88% | 421ms | Best approval; strong in LATAM, SEPA, IN |
| `fx-cross-border` | 83% | 602ms | Widest currency coverage; slowest |
| `orchestrator-high-risk` | 65% | 651ms | Low approval; 70% 3DS challenge rate |

Per-country overrides (base approval, card brand modifiers, 3DS challenge rate, latency) are stored in `payment_router/providers/*.yaml`.

Regenerate YAMLs from source data:
```bash
python scripts/derive_profiles.py --csv path/to/routing_transactions.csv
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

28 tests covering simulation correctness, retry logic, cross-border penalties, 3DS liability shift, and provider comparison.
