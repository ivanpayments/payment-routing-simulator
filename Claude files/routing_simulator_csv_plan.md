# Plan — Rebuild `synthetic_transactions.csv` for Payment Routing Simulator (Project 2)

Mirrors the SaaS-chatbot CSV methodology (Apr 14–15, plans `curious-pondering-crane.md` + `twinkly-toasting-orbit.md`): clean generator rewrite + deliberate pattern encoding + 2-stage verification.

## Context

The existing `C:\Users\ivana\synthetic_transactions.csv` (10K × 72 cols) has unknown provenance — Ivan can't defend "where did the distributions come from?" in an interview. Rebuild from scratch using the exact same two-script + patterns-first approach just executed for the Payments bots SaaS CSV.

**Key difference from Project 1's SaaS CSV:**
- P1 (SaaS) = **one persona** (Head of Payments / RevOps at a ~$2B-ARR SaaS, Notion/Intercom-scale), one parent, ~100 SKUs, 14 PSPs, 30 countries. Rows = billing attempts. Monetization patterns (dunning, NRR, trial conversion) are the story.
- P2 (routing simulator) = **no single persona**. Multi-vertical, multi-archetype breadth. The CSV's job is to make the 5 archetypes (`global-acquirer`, `regional-bank-processor`, `apm-specialist`, `cross-border-fx-specialist`, `high-risk-or-orchestrator`) produce materially different simulated responses for identical inputs. Archetype-fingerprint patterns are the story.

**Intended outcome:** Defensible, deterministic, fully synthetic dataset. `scripts/derive_profiles.py` consumes it, emits 5 YAML profiles. Every distribution traces back to a deliberately-chosen generator parameter documented in `DATA_DECISIONS.md`. Ivan can explain the whole pipeline on an interview screen-share.

## Decisions locked

| Decision | Choice | Mirrors SaaS CSV |
|---|---|---|
| Volume | **100K rows** | ✓ same scale |
| Generator style | Clean rewrite (~700 lines), not cruft on top of prior 10K CSV | ✓ same (700 vs 1665 airline) |
| Schema approach | Keep a broad column set (~120 cols) for downstream tooling compatibility; emit NULL/False on inapplicable rows rather than deleting columns | ✓ same philosophy (SaaS kept all 156 airline cols + 14 new) |
| Row = | One **auth attempt** (not an invoice, not a billing line). Retry attempts are separate rows joined by `original_transaction_id` | differs from SaaS (row=billing attempt); reflects P2's per-txn simulation focus |
| Archetype ground-truth | `archetype` column is authoritative. `processor_name` uses descriptive tokens (`global-acquirer-a`, `regional-bank-mx`, `apm-specialist-sepa`, `fx-cross-border`, `orchestrator-high-risk`) — no real brand names in the data | new (P2-specific) |
| Verticals | 6: DTC ecom, marketplace, SaaS, travel, digital goods, high-risk. ~500 merchants distributed | breadth vs SaaS's 1 vertical |
| Country coverage | 30 countries × 6 regions (NA, LATAM, EU, UK, APAC, MEA), realistic PSP volume weighting | ✓ same 30 |
| Reproducibility | Fixed seed (42), numpy + python random both seeded, deterministic regeneration | ✓ same |
| File layout | Generator + verifier in `products/Claude files/`, output CSV in same folder, then symlinked/copied to wherever `derive_profiles.py` expects | ✓ same structure |
| Iteration model | Part-A verification (selected patterns PASS/APPROX) + Part-B master (full pattern list, zero contradictions). Patch cycle allowed before declaring done | ✓ same two-gate model |

## Critical files to create

| File | Purpose | SaaS analog |
|---|---|---|
| `products/Claude files/generate_routing_transactions.py` | Main generator. `--rows`, `--seed`, `--output` flags. Emits ~120-col CSV | `generate_transactions.py` |
| `products/Claude files/verify_routing_patterns.py` | Part-A selected-pattern assertions + Part-B master-list contradiction scan | `verify_patterns.py` |
| `products/Claude files/DATA_DECISIONS.md` | Every generator parameter documented: why archetype X has p50 latency Y band, why LATAM auth sits in band Z, what real-world intuition anchors each number | new, but mirrors SaaS's inline-documented approach |
| `products/Claude files/PATTERN_MASTER.md` | Full ~200-pattern master list (Part-B gate input). Most are "must NOT contradict" expectations; ~30 are hard asserted in Part A | mirrors SaaS's ~500 master pattern list |
| `products/Claude files/routing_transactions.csv` | Output. 100K rows. Committed to jobsearch repo | `transactions.csv` |

## Schema (columns, ~120 total)

Grouped for readability. Column-level spec lives in `DATA_DECISIONS.md`.

**Identity & time (6):** `transaction_id`, `timestamp`, `merchant_id`, `merchant_vertical`, `merchant_mcc`, `merchant_country`

**Routing ground truth (3):** `archetype`, `processor_name`, `routing_reason` (why this archetype was selected — for learning, always "rule-based heuristic" in this dataset)

**Transaction core (12):** `amount`, `amount_usd`, `currency`, `card_brand`, `card_type`, `card_country`, `is_cross_border`, `bin_first6`, `card_funding_source`, `is_token`, `token_type`, `present_mode` (ecom / moto / pos)

**Auth outcome (8):** `auth_status`, `response_code` (ISO 8583), `response_message`, `decline_bucket` (issuer_hard / issuer_soft / network / processor / risk), `is_soft_decline`, `approved_amount`, `auth_code`, `scheme_response_code`

**3DS (6):** `three_ds_requested`, `three_ds_outcome`, `three_ds_version`, `three_ds_flow` (frictionless/challenge), `three_ds_eci`, `sca_exemption`

**Latency (4):** `latency_ms`, `latency_auth_ms`, `latency_3ds_ms`, `latency_bucket`

**Retry context (5):** `is_retry`, `original_transaction_id`, `retry_attempt_num`, `retry_reason`, `hours_since_original`

**Fees & FX (6):** `processor_fee_bps`, `interchange_estimate_bps`, `scheme_fee_bps`, `fx_applied`, `fx_rate`, `settlement_currency`

**Risk (5):** `risk_score`, `is_chargeback`, `chargeback_reason_code`, `fraud_flag`, `risk_model_version`

**Geography / BIN resolution (4):** `billing_country`, `shipping_country`, `ip_country`, `issuer_country`

**PSP metadata (kept for downstream tooling, often NULL per row) (~60 cols):** the broad set of PSP/payment fields the existing 72-col legacy file expects — emit NULL/False where not applicable rather than remove. Preserves compatibility with any analyst scripts Ivan writes against the data later.

## Phase 5 — Pattern discovery (high-effort parallel agents)

**Do not hand-write the pattern list.** Launch **5–6 high-effort agents in parallel**, each tasked with discovering patterns in a distinct payments-domain dimension. Combined target: **500–600 candidate patterns**.

Proposed agent assignments (each returns 80–120 patterns with rationale + quantified bands):

| # | Agent focus | Dimension coverage |
|---|---|---|
| 1 | **Auth & decline behavior** | Approval rates by country/archetype/card-brand/amount-band, decline-code distributions (ISO 8583), soft/hard/fraud split, issuer vs network vs processor decline attribution, decline reasons by cross-border pairs. |
| 2 | **3DS / SCA / authentication** | EU PSD2 exemption thresholds, 3DSv1 vs v2 adoption, frictionless vs challenge mix by country, low-value/TRA/corporate/MIT exemption flows, Amex Safekey quirks, US vs UK vs EU authentication realities. |
| 3 | **Latency & infra fingerprints** | p50/p95/p99 by archetype, log-normal parameters, regional infra variance, 3DS-added latency, FX-added latency, issuer-side vs acquirer-side breakdown, timeout + retry-on-timeout cascades. |
| 4 | **APMs, fees, interchange, FX** | SEPA/iDEAL/Pix/SPEI/UPI availability & mix, interchange bps by card brand/region/commercial code, scheme fees, FX markups, settlement currency economics, dynamic currency conversion patterns. |
| 5 | **Retry, chargeback, fraud, network tokens** | Retry cascade success curves (time-of-day, hours-since-first, soft-decline type), chargeback rates by MCC/vertical/card-type, fraud rate by corridor, network token lift on auth, account updater recovery. |
| 6 | **Cross-cutting realism & anti-patterns** | Card-present vs CNP expectations, BIN→issuer country reality, regional currency pairings, merchant-country ≠ settlement-country cases, token vs raw-PAN distributions, PIN-at-POS only, impossible combinations (the things that MUST NOT appear). |

**Agent briefing format** (identical per agent):
- Scope of dimension.
- Target 80–120 patterns.
- Each pattern returned as: `{id, dimension, rule, quantified_band_or_threshold, rationale, source_intuition, test_method}`.
- Bias toward patterns an experienced payments person would immediately recognize; avoid textbook trivia.
- Flag any pattern that could be controversial (e.g. pins a brand claim) for legal review in Phase 8.

**Output:** single consolidated `patterns_discovered.json` (~550 entries).

## Phase 6 — Prioritization & selection (1 high-effort agent)

Input: `patterns_discovered.json` (~550 candidates).

Agent tasks:
1. **Dedupe** overlapping patterns across the 6 discovery dimensions.
2. **Score** each pattern on (a) payments-domain signal strength, (b) testability with the generator's column set, (c) differentiation between archetypes, (d) interview storytelling value.
3. **Select 250 patterns** to target in the regenerated CSV — these become the "applied target set".
4. Classify each selected pattern as:
   - **ASSERT** (hard PASS required in Part-A) — expected count: ~150.
   - **APPROX** (within 2pp / 10% of band — soft PASS) — expected count: ~100.
5. **Keep the remaining ~300 candidates** as Part-B "must-not-contradict" set.

**Output:** `patterns_selected.md` (250 applied + ~300 non-contradiction = ~550 total tracked).

## Phase 7 — Regeneration

Update the generator (`generate_routing_transactions.py`) to encode the 250 selected patterns as explicit parameters. Not one line of generator logic should exist that isn't traceable to a selected pattern or a schema requirement.

1. Rewrite matrices / distributions / auth engine / 3DS / latency / retry/chargeback layers keyed to the selected pattern IDs.
2. Produce `DATA_DECISIONS.md` keyed 1:1 to pattern IDs (every pattern has a documented encoding + rationale).
3. Regenerate `routing_transactions.csv` (100K × ~120 cols, deterministic seed).

## Phase 8 — Validation (2 high-effort parallel agents)

**Agent 8a — Applied-patterns check:**
- Input: `routing_transactions.csv` + `patterns_selected.md` (ASSERT + APPROX set).
- Task: run pandas/duckdb queries to verify each of the ~250 applied patterns.
- Gate: **≥150 patterns must PASS** (others may APPROX); zero FAIL on the ASSERT subset.
- Output: `validation_applied.md` with per-pattern PASS/APPROX/FAIL + the query used.

**Agent 8b — Contradiction scan:**
- Input: `routing_transactions.csv` + full `patterns_discovered.json` (all ~600).
- Task: scan the CSV for contradictions against the full 600-pattern set (including the ~300 not selected for encoding — they still must not contradict).
- Gate: **zero contradictions** across all 600. "Cannot verify from schema" allowed but flagged.
- Output: `validation_contradictions.md`.

If either agent reports FAIL: iterate — update generator → regenerate → rerun agents. Standard patch cycle.

## Phase 9 — Legal check + senior-engineer review (2 high-effort agents)

Only runs after Phase 8 passes.

**Agent 9a — Legal check:**
- Scope: scan `patterns_selected.md`, `DATA_DECISIONS.md`, and sample CSV rows for anything that could misrepresent a named processor, infringe trademarks (real brand names in data rows rather than archetypes), or claim performance numbers attributed to a specific PSP.
- Gate: zero findings flagged as "likely legal risk". All processor references in data rows use archetype-variant tokens only; real brand names appear only in illustrative README mapping tables under nominative fair use.
- Output: `legal_review.md`.

**Agent 9b — senior-engineer review:**
- Scope: simulate a senior payments engineer reviewing this dataset. Does it pass the "is this the work of someone who has actually run payments, or a student who read a payments blog?" test?
- Check: are the pattern bands realistic, do edge cases reflect lived experience (BIN mismatches, issuer timeouts, cascading 3DS fallbacks, etc.), is anything textbook-obvious or missing the interesting edges?
- Gate: "would pass a senior technical screen without follow-up challenges on data quality."
- Output: `senior_review.md` with flagged issues + recommended patches.

If either surfaces a blocker, loop back to Phase 7 with targeted fixes.

## Execution order (matches the phase flow above)

1. **Phase 1–2: Context + decisions** — already done in this plan file.
2. **Phase 3: Generator skeleton** (~1h) — argparse, seed, column list, empty DataFrame, CSV writer.
3. **Phase 4: Schema** (~1h) — ~120 cols, PSP metadata kept for downstream compat.
4. **Phase 5: Discovery** (~6 parallel agents, wall-clock ~20min, total agent effort ~3h equivalent). Produces `patterns_discovered.json`.
5. **Phase 6: Prioritization** (~1 agent, ~20min wall-clock). Produces `patterns_selected.md`.
6. **Phase 7: Regeneration** (~3h Claude work) — encode 250 patterns in generator + DATA_DECISIONS.md + regenerate CSV.
7. **Phase 8: Validation** (~2 parallel agents, ~20min wall-clock + patch cycles). Produces validation reports.
8. **Phase 9: Legal + senior-engineer review** (~2 parallel agents, ~20min). Produces review reports.
9. **Phase 10: Wire into Project 2 build** (~0.5h) — point `scripts/derive_profiles.py` at new CSV; update `payment_routing_simulator.md` + `snug-dreaming-bubble.md` Block A to reference generator as prerequisite.

**Total: ~9h Claude work + ~1.5h wall-clock for parallel agents.**

## Learning checkpoints (per `snug-dreaming-bubble.md` learning protocol)

Ivan reviews agent outputs at each phase boundary. Before starting Phase 7 regeneration, Claude asks Socratic questions on the 3–5 most interesting encoded patterns:
- "Agent 1 found that `regional-bank-processor` over-indexes on response code 91 in cross-region — why would that be? What's the infra story?"
- "Agent 4 distinguished SEPA-core vs SEPA-instant latency. Which matters for routing decisions and why?"
- "Agent 5 flagged network-token lift as +4–8pp. What exactly is a network token and why would an issuer treat it preferentially?"

Ivan answers in his own words; gaps filled; code proceeds.

## Verification (end-to-end)

**Gate flow:** Phase 5 → Phase 6 → Phase 7 → Phase 8a PASS (≥150 applied) → Phase 8b PASS (0 contradictions on 600) → Phase 9a PASS (legal clean) → Phase 9b PASS (senior-engineer-grade).

**Downstream verification (after all gates pass):**
1. `derive_profiles.py` emits 5 YAML profiles with visibly differentiated distributions.
2. `prs simulate --archetype regional-bank-processor --country FR --card visa --amount 200` returns low approval + high latency (FR outside home region).
3. `prs compare --country MX --card mastercard --amount 500` returns a ranked table where archetype fingerprints are visible to end users.
4. Ivan can walk through the whole pipeline in <2 min on a screen-share: 6 discovery agents → 550 patterns → 250 selected → generator encodes → 150 applied + 600 non-contradictory → legal + senior-engineer review cleared → 5 YAML profiles → simulator.

## Deliberately out of scope

- Real payment data. The whole point is defensible synthetic provenance.
- Matching any specific real processor's actual numbers. Archetypes are composites.
- More than 30 countries or 6 verticals — diminishing returns on per-cell distribution stability.
- Labeled retry-outcome features for ML training (that's Project 3's separate dataset).
- Per-row FX conversion math — `amount` stored in source currency only; FX realism lives in archetype profiles.
- Network tokenization economics, L2/L3 data, commercial interchange, OCT/push-to-card (orthogonal to routing decision surface).
