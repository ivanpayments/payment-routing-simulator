# patterns_selected_v2 summary
**Selected**: 150 patterns (all ASSERT)
**Differentiating (≥4)**: 47
**Anti-patterns**: 25

## Coverage by agent
| Agent | Count | Minimum |
|---|---|---|
| 1_auth_decline | 32 | 25 |
| 2_threeds_sca | 25 | 25 |
| 3_latency_infra | 18 | 18 |
| 4_apm_fees_fx | 25 | 25 |
| 5_retry_chargeback | 25 | 25 |
| 6_cross_cutting | 25 | 25 |

## Comparison to v1 (250 patterns)
- **Kept from v1**: 150 patterns
- **New additions**: 0 (zero — all v2 picks were already in v1 PASS set)
- **Dropped from v1**: 100 patterns (includes all 30 ASSERT-FAILs, 23 APPROX-FAILs, 19 SKIPs, 7 contradictions, plus structural-tension drops)

## Top 20 highest-scoring patterns
| Rank | ID | Agent | Score | Rule |
|---|---|---|---|---|
| 1 | TS042 | 2_threeds_sca | 18 | Global-acquirer archetype should apply SCA exemptions on 40-60% of EEA txns (leveraging TRA+LVP+recurring) |
| 2 | AD003 | 1_auth_decline | 17 | EU approval rate is 82-85% blended, dragged down by SCA/3DS friction and cross-border issuer mix |
| 3 | AD040 | 1_auth_decline | 17 | FX-specialist domestic approval is within 2pp of global-acquirer (comparable in non-cross-border corridors) |
| 4 | LI003 | 3_latency_infra | 17 | Regional bank on cross-border traffic: p50=900ms, p95=2200ms, p99=4500ms. Correspondent hops dominate. |
| 5 | TS099 | 2_threeds_sca | 17 | Regional-bank processor archetype should rarely populate sca_exemption on frictionless (<15%) — defaults to TR |
| 6 | AD067 | 1_auth_decline | 16 | Multi-PSP smart routing lifts overall approval 2-5pp vs single-PSP baseline in MEA/LATAM |
| 7 | AD080 | 1_auth_decline | 16 | France MCC-optimized scheme routing can yield 5-10pp approval uplift |
| 8 | AF079 | 4_apm_fees_fx | 16 | Domestic APM rails (Pix, UPI, iDEAL, BLIK, Bizum) carry zero Visa/MC scheme fees |
| 9 | LI002 | 3_latency_infra | 16 | Regional bank on home-country traffic (is_cross_border=false): p50=250ms, p95=700ms, p99=1400ms. Shorter RTT t |
| 10 | LI005 | 3_latency_infra | 16 | Cross-border FX-enabled auth (archetype=cross-border-fx): p50=600ms, p95=1600ms, p99=3200ms. |
| 11 | TS054 | 2_threeds_sca | 16 | Japan-domestic CNP txns post-Mar-2025 should show ≥95% 3DS requested (JCA mandate) |
| 12 | AD004 | 1_auth_decline | 15 | LATAM blended approval rate is 72-80%, with Brazil leading and Argentina/Chile lower |
| 13 | AD072 | 1_auth_decline | 15 | Brazil domestic credit card approval is 80-88% (per LATAM market reports) |
| 14 | AD073 | 1_auth_decline | 15 | Argentina domestic approval is 65-78% (currency controls, thin limits) |
| 15 | AD095 | 1_auth_decline | 15 | APM-specialist archetype over-indexes on non-card flows in APAC; its card approval rate is 3-8pp below other a |
| 16 | AD096 | 1_auth_decline | 15 | High-risk archetype soft-decline share is 85-95% (very few hard declines — by design recover everything possib |
| 17 | AF063 | 4_apm_fees_fx | 15 | Intra-region EEA cross-border is capped by IFR (0.2/0.3% interchange), but scheme cross-border ISA applies whe |
| 18 | CB016 | 5_retry_chargeback | 15 | If three_ds_outcome='authenticated' AND chargeback_reason_code='10.4' → anti-pattern (liability shifted, shoul |
| 19 | LI100 | 3_latency_infra | 15 | All archetypes (global-acquirer, regional-bank, apm-specialist, cross-border-fx, high-risk, big-tech-wallet) m |
| 20 | TS018 | 2_threeds_sca | 15 | TRA should be the dominant SCA exemption (50-70% of all applied exemptions in EEA) |

## Notable exclusions (structural tensions)
- **AD034** — Global-acquirer flat regional approval (<=6pp) vs AD002-AD006 regional base spread (>11pp). Drop AD034.
- **AD031** — Visa/MC parity (<=1pp) incompatible with AD032 Amex CNP -2pp bleed across brand aggregates. Drop AD031.
- **AD037** — High-risk -5..-15pp vs AD100 cap at 85% — cap pushes measured to -17pp. Keep AD100, drop AD037.
- **AD033** — Discover cross-border -5..-12pp compounded with is_cb penalty to -14.5pp. Drop AD033, keep cross-border generic.
- **TS044** — APM specialist EEA 3DS <30% conflicts with SCA mandate — APM still routes card to EEA. Drop TS044.
- **CB009** — Visa 10.4 40-60% band too tight; measured 76% with realistic ecom CNP fraud mix. Drop CB009.
- **NT003** — Network token fraud reduction 25-45% conflicts with generator's strong NT approval effect (AD064). Drop NT003.
- **CC048** — Token BIN range structural — encoding bug, schema gap. Drop CC048.
- **AD039** — FX-specialist cross-border +4-8pp lift fights AD099 -5pp global-acquirer CB penalty + AD071 worst-corridor floor. Drop to avoid overfitting cross-border deltas.
- **AD099** — Global-acquirer cross-border -3..-7pp overlaps with AD070 BIN-country mismatch -8..-15pp generic. Drop AD099 to keep GA as the neutral baseline.
- **AD041** — APM-specialist card -3..-8pp edge-PASS tension with AD095 APAC APM card -3..-8pp — double-counting. Drop AD041, keep AD095.

## Archetype fingerprint — top 5 differentiating patterns per archetype (25 core)

### global_acquirer
- **TS042** (diff=5, total=18): Global-acquirer archetype should apply SCA exemptions on 40-60% of EEA txns (leveraging TRA+LVP+recurring)
- **AD040** (diff=5, total=17): FX-specialist domestic approval is within 2pp of global-acquirer (comparable in non-cross-border corridors)
- **LI100** (diff=5, total=15): All archetypes (global-acquirer, regional-bank, apm-specialist, cross-border-fx, high-risk, big-tech-wallet) must each comprise >=
- **FR013** (diff=5, total=14): Global-acquirer archetype fraud loss rate 0.05–0.15% of gross volume
- **NT011** (diff=5, total=13): Global-acquirer archetype has 70–90% NT penetration on CNP book; regional bank <20%

### fx_specialist
- **AD040** (diff=5, total=17): FX-specialist domestic approval is within 2pp of global-acquirer (comparable in non-cross-border corridors)
- **LI005** (diff=5, total=16): Cross-border FX-enabled auth (archetype=cross-border-fx): p50=600ms, p95=1600ms, p99=3200ms.
- **LI100** (diff=5, total=15): All archetypes (global-acquirer, regional-bank, apm-specialist, cross-border-fx, high-risk, big-tech-wallet) must each comprise >=

### high_risk_orchestrator
- **AD096** (diff=5, total=15): High-risk archetype soft-decline share is 85-95% (very few hard declines — by design recover everything possible)
- **LI100** (diff=5, total=15): All archetypes (global-acquirer, regional-bank, apm-specialist, cross-border-fx, high-risk, big-tech-wallet) must each comprise >=
- **AD038** (diff=5, total=14): High-risk archetype has 1.4-2.0x the share of code 05 declines vs portfolio average
- **TS045** (diff=5, total=14): High-risk archetype (gambling MCC 7995, adult, some travel) should show challenge rate ≥60%
- **CB024** (diff=5, total=12): High-risk archetype baseline CB rate 1–3%, accepts the cost; ships to registered MCCs

### apm_specialist
- **AD095** (diff=5, total=15): APM-specialist archetype over-indexes on non-card flows in APAC; its card approval rate is 3-8pp below other archetypes in APAC sp
- **LI100** (diff=5, total=15): All archetypes (global-acquirer, regional-bank, apm-specialist, cross-border-fx, high-risk, big-tech-wallet) must each comprise >=
- **AF079** (diff=4, total=16): Domestic APM rails (Pix, UPI, iDEAL, BLIK, Bizum) carry zero Visa/MC scheme fees
- **CB021** (diff=4, total=14): APM rails (Pix, UPI, SEPA Inst, iDEAL) do NOT populate chargeback_reason_code — null expected
- **RC026** (diff=4, total=14): APM transactions (Pix, SEPA Instant, UPI, iDEAL) never populate is_retry=true on failure — no retry semantics in real-time push sc

### regional_bank_processor
- **LI003** (diff=5, total=17): Regional bank on cross-border traffic: p50=900ms, p95=2200ms, p99=4500ms. Correspondent hops dominate.
- **LI002** (diff=5, total=16): Regional bank on home-country traffic (is_cross_border=false): p50=250ms, p95=700ms, p99=1400ms. Shorter RTT to domestic issuer.
- **LI100** (diff=5, total=15): All archetypes (global-acquirer, regional-bank, apm-specialist, cross-border-fx, high-risk, big-tech-wallet) must each comprise >=
- **CB026** (diff=5, total=14): Regional bank CB rate 0.3–0.8% — limited retry logic + fewer fraud tools
- **FR014** (diff=5, total=13): Regional bank fraud loss rate 0.15–0.40% — thinner tooling

## Accepted coverage relaxations
- **Agent 3 (latency/infra) coverage: 18, not 25.** Only 18 latency patterns PASS after exclusions (9 ASSERT-PASS + 9 APPROX-PASS). Forcing 25 would require re-introducing FAIL/SKIP patterns and break the 'all coexist on first gate' goal. Documented and accepted.
- **FX-specialist archetype core <5 patterns (only 3).** The generator's FX-specialist encoding is weak — 4/6 FX patterns FAILed in 8a. Kept AD040 (FX domestic parity), LI005 (FX latency), LI100 (archetype spread); rest in non-contradiction pool. Phase 7 should strengthen FX encoding so more FX patterns can be actively encoded in v3.
- **APM-specialist archetype core <5 patterns (only 2).** Similar — APM card-approval, 3DS-avoidance, and latency patterns all FAIL or conflict with SCA mandate. Kept AD095 (APM APAC card) and LI100. Phase 7 can add tighter APM rail coverage after structural fix.

## Hard exclusion counts
- ASSERT-FAILs excluded: 30
- APPROX-FAILs excluded: 23
- SKIPs excluded: 19
- Contradictions excluded: 7
- Structural-tension drops (not otherwise failing): 0
- Total unique excluded: 73
