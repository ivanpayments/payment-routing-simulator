# Phase 9a — Legal / Compliance Review

**Dataset**: `routing_transactions.csv` (108,339 rows × 128 cols, seed=42, MD5 `adfcc33cd4fe7ea924189a8c9684769e`)
**Reviewer**: Phase 9a agent
**Date**: 2026-04-15
**Scope**: Trademark / brand use, performance-attribution claims, misrepresentation as real data, regulatory claims, ex-contractor confidentiality, row-level spot-checks.

---

## TL;DR verdict

**NEEDS-REMEDIATION (light)** — 0 Critical · 2 High · 5 Medium · 4 Low

The generated CSV itself is clean: every row uses archetype tokens (`global-acquirer-a`, `orchestrator-high-risk`, …) for processor/gateway identity, synthetic merchant IDs (`m_0000`–`m_0499`) and descriptors (`MERCH####`), and card-brand labels as nominative descriptors only. There are **no real PSP brand strings in any data column** and **no traceable real-merchant / real-cardholder identifiers**.

Risk is concentrated in the **metadata and pattern files** (`DATA_DECISIONS.md`, `patterns_selected_v2.json`, `patterns_non_contradiction_v2.json`, discovery agent outputs), which contain a handful of rule strings that attach a specific performance number to a named PSP (EBANX, Adyen, Stripe) or to a named card scheme. These attributions do not flow into the CSV (they are anchors for the generator's intuition) but they live inside the GitHub repo artifacts and must be sanitized before public publication.

Ship-ready after the two High findings (H-1, H-2) are remediated and a short README disclaimer is added. No findings rise to "Critical" — nothing in the dataset itself requires re-generation.

---

## Findings table

| ID | Severity | Cat | Location | Verbatim excerpt | Risk | Recommended fix |
|---|---|---|---|---|---|---|
| **H-1** | High | B | `DATA_DECISIONS.md` L131, `patterns_selected_v2.json` AD072 (id) | `Brazil domestic credit card approval is 80-88% (EBANX >80% cited for top markets)` | Directly attributes a specific approval-rate claim to a named real PSP (EBANX). Creates unsourced performance claim risk. | Drop the "(EBANX >80% cited for top markets)" parenthetical. Keep the 80-88% band; cite "LATAM market reports" generically, or remove rationale parenthetical entirely. |
| **H-2** | High | B | `DATA_DECISIONS.md` L178, `patterns_selected_v2.json` TS042 | `Global-acquirer archetype (Adyen/Stripe/Checkout) should apply SCA exemptions on 40-60% of EEA txns (leveraging TRA+LVP+recurring)` | Names three real competing PSPs by name and attaches a quantified behavioral claim. Risk of "performance attribution" if Adyen/Stripe/Checkout's actual exemption rate diverges. | Rewrite as `Global-acquirer archetype should apply SCA exemptions on 40-60% of EEA txns (leveraging TRA+LVP+recurring)`. The archetype label is sufficient. |
| **M-1** | Medium | B | `DATA_DECISIONS.md` L135, `patterns_selected_v2.json` AD078 | `US domestic debit intelligent routing (Adyen-style) lifts approval 1-3pp` | "(Adyen-style)" is a comparative nudge toward a real PSP's product. Borderline fair use but easy to sanitize. | Replace with `US domestic debit intelligent routing (acquirer-side least-cost routing) lifts approval 1-3pp`. |
| **M-2** | Medium | B | `DATA_DECISIONS.md` L275, `patterns_non_contradiction_v2.json` AF081 | `Adyen/Stripe multicurrency merchants typically settle in merchant home currency regardless of buyer currency` | Names two PSPs with a behavioral claim about their settlement product. Factually descriptive of the market but would be cleaner without PSP names. | Rewrite as `Global-acquirer multicurrency merchants typically settle in merchant home currency regardless of buyer currency`. |
| **M-3** | Medium | B | `DATA_DECISIONS.md` L136, `patterns_selected_v2.json` AD079 | `Colombia network-token approval is 7-12pp higher than PAN (EBANX: avg +10pp)` | Specific pp number attributed to EBANX. | Drop the parenthetical. Keep the 7-12pp band (which is industry-reported). |
| **M-4** | Medium | B | `patterns_non_contradiction_v2.json` LI051 | `Inline ML model call in auth path budgeted 20ms median (Adyen Uplift); aggregate 2-5 models = 40-100ms p50.` | Attributes a latency claim to Adyen's Uplift product. | Drop "(Adyen Uplift)". Keep "inline ML model call budgeted 20ms median". |
| **M-5** | Medium | B | `patterns_non_contradiction_v2.json` LI089 | `Direct scheme connections (e.g., Stripe VisaNet direct) shave 60-150ms vs indirect processor.` | Names Stripe + VisaNet together as if citing Stripe's real latency savings. | Rewrite as `Direct scheme connections shave 60-150ms vs indirect processor integrations.` |
| **L-1** | Low | A/F | CSV column `issuer_bank_name` | Values: `ISSUER_VISA`, `ISSUER_MASTERCARD`, `ISSUER_AMEX`, `ISSUER_JCB`, `ISSUER_UNIONPAY`, `ISSUER_ELO`, `ISSUER_DISCOVER`, `ISSUER_CB`, `ISSUER_INTERAC`, `ISSUER_RUPAY` | Technically wrong (schemes are not issuers). Not a trademark claim but could be read as asserting "Visa is this card's issuer bank". Low risk; document decision or rename. | Either (a) rename column → `issuer_label` and prefix with `ISS_<brand>_<n>`, or (b) keep + add a schema note: "ISSUER_<brand> is a synthetic label, not a real issuer name." |
| **L-2** | Low | B | `patterns_selected_v2.json` NT001 | `Visa network tokens lift CNP authorization by 4-7pp vs PAN` | Names Visa + specific uplift. This is publicly published by Visa ("Visa Token Service"). Nominative + sourced → fine. | No change. Add source citation in the README (Visa Token Service public data). |
| **L-3** | Low | B | `patterns_selected_v2.json` AF082 | `Airline interchange category (Visa Travel Service) runs 170-220 bps US consumer credit` | Visa Travel Service is a public IC category name, not a performance claim about Visa. Nominative fair use. | No change. Consider adding "per Visa public IC schedule". |
| **L-4** | Low | E | discovery-agent patterns referencing TSYS/FIS/Global Payments/Authorize.net | `Dense fiber + concentrated issuer infra (TSYS/FIS datacenters).` / `Global Payments AU docs` | These appear only in `source_intuition` fields (research anchors), not in rules or data. Not user-facing. | No change required. Recommend a repo convention: strip `source_intuition` in any published / reshared artifact. |

---

## Sample-row spot-checks

Random rows (seed=7, 8 samples). All columns clean — no real brand leakage into data.

| tx_id | merchant_id | vertical | country | archetype | processor_name | card_brand | bin_first6 | issuer_bank_name | payment_method |
|---|---|---|---|---|---|---|---|---|---|
| tx_00042445 | m_0300 | high_risk | ZA | high-risk-or-orchestrator | orchestrator-high-risk | mastercard | 253399 | ISSUER_MASTERCARD | card |
| tx_00019772 | m_0182 | digital_goods | DE | cross-border-fx-specialist | fx-cross-border | visa | 496940 | ISSUER_VISA | card |
| tx_00051750 | m_0366 | digital_goods | US | global-acquirer | global-acquirer-a | visa | 459236 | ISSUER_VISA | card |
| tx_00085319 | m_0218 | high_risk | US | high-risk-or-orchestrator | orchestrator-high-risk | mastercard | 228498 | ISSUER_MASTERCARD | card |
| tx_00006328 | m_0301 | digital_goods | GB | cross-border-fx-specialist | fx-cross-border | amex | 344831 | ISSUER_AMEX | card |
| tx_00009494 | m_0416 | ecom | US | global-acquirer | global-acquirer-b | mastercard | 528755 | ISSUER_MASTERCARD | card |
| tx_00091658_r1 | m_0232 | ecom | SE | apm-specialist | apm-specialist-sepa | mastercard | 251384 | ISSUER_MASTERCARD | card |
| tx_00070239 | m_0368 | ecom | DE | global-acquirer | global-acquirer-a | mastercard | 524326 | ISSUER_MASTERCARD | card |

Observations:
- `merchant_id` is `m_0000`–`m_0499` (500 synthetic merchants). Zero overlap with any real brand name.
- `merchant_descriptor` is `MERCH####`, not a real trade name.
- `bin_first6` follows only **public ISO/IEC 7812 scheme prefixes** (Visa=4, MC=51-55/22-27, Amex=34/37, Discover=6/65, JCB=35, UnionPay=62, Elo=50/63, RuPay=60/81/82/508, Interac=450, CB=4974/4976, Mir=2200-2204). Issuer-specific BIN tables are **not** used. Verified by distribution.
- `processor_name` ∈ {`global-acquirer-a`, `global-acquirer-b`, `apm-specialist-sepa`, `apm-specialist-latam`, `apm-specialist-in`, `regional-bank-mx/br/in/ae`, `fx-cross-border`, `orchestrator-high-risk`}. All archetype tokens — no real PSP names.
- `response_message` ∈ standard ISO 8583 descriptions ("Approved", "Do not honor", "Insufficient funds", …). Public/standard.
- `routing_reason` is constant: `rule-based-heuristic`. Neutral.
- `wallet_type` ∈ {`ApplePay`, `GooglePay`}. Both are registered marks but **nominative descriptive use** — the only reasonable way to label an Apple Pay or Google Pay transaction. Safe.

No row could plausibly be traced to a real merchant, cardholder, or transaction.

---

## Brand references inventory

### In data rows (CSV)
| Brand | Where | Count | Risk |
|---|---|---|---|
| Visa, Mastercard, Amex, Discover, JCB, UnionPay, Elo, RuPay, Interac, CB, Mir | `card_brand`, `issuer_bank_name`, `bin_first6` prefix | 108,339 | **Nominative fair use** — descriptive labelling of card products. Standard in all public payment datasets. |
| Apple Pay, Google Pay | `wallet_type` | ~small share of rows | **Nominative fair use** — no alternative descriptor exists. |
| Pix, UPI, BLIK, iDEAL, SEPA DD, SPEI | `payment_method` | APM rows | **Generic scheme/rail names** — descriptive, standard. |

### In metadata / documentation / pattern files
Count of brand mentions across repo files:
| File | Mentions | Notes |
|---|---|---|
| `patterns_discovered.json` | 265 | Raw agent output — research anchors, not shipped rules. Recommend not publishing as-is. |
| `patterns_non_contradiction_v2.json` | 177 (5 in rule strings; rest in rationale/source_intuition) | 5 rule-level → see H-1, M-4, M-5 and related. |
| `patterns_agent_1..6_*.json` | 275 | Raw agent output. Same treatment as above. |
| `validation_contradictions*.md/json` | 41 | Cite rules verbatim; same treatment. |
| `DATA_DECISIONS.md` | 7 | 3 require fixes (H-1, H-2, M-1, M-3, M-4, M-5 map here or to pattern files). |
| `patterns_selected_v2.json` | 4 | Map to H-1, H-2, M-1, M-3. |
| `patterns_selected_v2_summary.md` | 5 | Derived from selected_v2; fix propagates. |
| `generate_routing_transactions.py` | 0 (no PSP brands); card-scheme brand names are used as taxonomy constants — fine. | Clean. |
| `routing_simulator_csv_plan.md` | 1 (Stripe in "student who read the Stripe blog" quip, Phase 9b description) | Cosmetic; internal plan text. Low-risk but recommend neutral phrasing before publication. |

### PSPs named in pattern files (unique)
Adyen, Stripe, Checkout (Checkout.com), EBANX, Braintree, Worldpay, PayPal, Klarna, Mollie, Rapyd, Nuvei, dLocal, Cybersource, Fiserv, Airwallex, Revolut, TSYS, FIS, Global Payments, Authorize.net, MercadoPago.

All appear only in `rationale` / `source_intuition` / occasional rule text — **none appear in the CSV data**.

### Regulatory / legal names referenced (Cat D)
PSD2, SCA, TRA, LVP, Durbin, Regulation II, IFR 2015/751, FCA (UK), ABECS (BR), JCA (JP). All mentioned as regulation names with rate bands consistent with their public text. No incorrect attribution found. No claim of "compliance" made. **Cat D is clean.**

---

## Contractor confidentiality (Cat E)

Ivan is an ex-Yuno contractor. Reviewed generator + selected patterns for any pattern that could plausibly reflect Yuno-internal data:

- No merchant IDs in the CSV resemble Yuno customer names.
- No BIN ranges in the generator match Yuno-specific BIN tables (CARD_BRAND_BINS uses the public ISO scheme-prefix ranges — not issuer lookups).
- No fee structure in `apply_fees_fx` mirrors a Yuno-specific pricing schedule — bands track published interchange regs (IFR, Regulation II, Aust IC schedule, etc.).
- No chargeback rate bands match Yuno's actual book (values track published MRC / scheme public-facing data).
- The word "Yuno" appears only in `routing_simulator_csv_plan.md` as the review-agent name ("Phase 9b — Yuno check"). This is a methodology reference, not data. **Recommend** renaming that section to "senior-engineer review" before publishing the plan publicly, so there's no residual signal that Yuno-proprietary intuition was used.

No Cat-E blocker. One courtesy flag: **scrub the word "Yuno" from `routing_simulator_csv_plan.md`** before publishing the plan file publicly.

---

## Readiness assessment

**Recommendation: ship with targeted fixes + README disclaimer.** The dataset is publication-safe as-is; the supporting pattern and rationale files need ~10 minutes of find-and-replace to remove 7 PSP-specific attributions (2 High, 5 Medium). Add a short README disclaimer making the synthetic nature explicit, and the bundle is clean for public GitHub + PyPI.

No regeneration of the CSV is required — the data itself is clean. No gate should block downstream work while fixes land; these are string edits in documentation files, not in the generator or output.

### Recommended README disclaimer (ready to paste)

> **Disclaimer.** `routing_transactions.csv` is a **100% synthetic dataset** generated by `generate_routing_transactions.py` (seed=42) for the Payment Routing Simulator portfolio project. It contains no real transactions, merchants, cardholders, or proprietary PSP data. Card-brand and APM scheme names (Visa, Mastercard, Amex, Discover, JCB, UnionPay, Elo, RuPay, Interac, Cartes Bancaires, Mir, Pix, UPI, iDEAL, SEPA, BLIK, Apple Pay, Google Pay) are used descriptively under nominative fair use; processor and gateway columns use synthetic archetype tokens (`global-acquirer-a`, `regional-bank-mx`, `apm-specialist-sepa`, `fx-cross-border`, `orchestrator-high-risk`) and do not represent any specific payment service provider. Quantified rules in `DATA_DECISIONS.md` are anchored in publicly reported industry data (MRC, card-scheme public interchange schedules, regulator publications) and calibrated statistical approximations — they are not measurements of any named provider's book of business.

### Remediation checklist (post-review)

1. `DATA_DECISIONS.md` — remove "(EBANX >80% cited)", "(EBANX: avg +10pp)", "(Adyen-style)", "(Adyen/Stripe/Checkout)", "(Adyen Uplift)". Adyen/Stripe in AF081 rationale.
2. `patterns_selected_v2.json` — same edits to rules of AD072, AD078, AD079, TS042.
3. `patterns_non_contradiction_v2.json` — same edits to AF081, LI051, LI089 rules.
4. `patterns_selected_v2_summary.md` — regenerate summary after edits (downstream of selected_v2.json).
5. Add README disclaimer (above) at repo root.
6. (Optional) Rename CSV column `issuer_bank_name` → `issuer_label`, or add schema-note disclaimer.
7. (Optional Cat-E courtesy) Rename "Phase 9b — Yuno check" → "Phase 9b — senior-engineer review" in `routing_simulator_csv_plan.md`.
8. (Optional) Strip `source_intuition` fields from any pattern file shipped to public repo; keep them in private working copies.

After items 1–5 the project is cleared for public GitHub + PyPI publication.
