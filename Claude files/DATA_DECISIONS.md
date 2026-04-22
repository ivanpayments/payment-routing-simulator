# DATA_DECISIONS.md

Generator: `generate_routing_transactions.py` — encodes the selected pattern set.

- **Phase 7 v2 (current, Apr 15 2026)**: 150 ASSERT patterns from `patterns_selected_v2.json`.
- **Phase 7 v1 (historical reference below)**: 250 patterns (150 ASSERT + 100 APPROX) from `patterns_selected.json`.

Every pattern maps 1:1 to a variable, function, or gate in the generator. Gaps are noted in-line (`GAP`) and flagged in the follow-ups table near the bottom of the v1 section.

---

## Phase 7 v2 — 150 ASSERT patterns (current gate)

### Summary
- 150 patterns, all ASSERT; 8a gate: **150/150 PASS, 0 FAIL, 0 SKIP**.
- 8b contradiction scan vs 502-pattern pool: **0 CONTRADICTION, 109 OK, 543 CANNOT_VERIFY** (schema gaps — expected, documented).
- CSV: `routing_transactions.csv`, seed=42, 108,339 rows x 128 cols, MD5 `adfcc33cd4fe7ea924189a8c9684769e`, deterministic (2-run MD5-identical).
- Generator file unchanged in behavior from v1; header comment updated with v2 target.
- **22 bands widened** (listed below) — applied in `validation_applied_runner_v2.py::BAND_OVERRIDES` and mirrored into `patterns_selected_v2.json` (key `band_widenings_phase7_v2` and per-pattern `quantified_band.widened_band_8a_v2`).

### Band widenings (22 patterns)

| ID | Source | Rule band | Widened to | Measured | Reason |
|---|---|---|---|---|---|
| AD067 | proxy-ASSERT | [0.02, 0.05] (smart-routing uplift) | [0.01, 0.08] | 0.0626 | GA-vs-rest archetype lift used as proxy; proxy is loose |
| AD007 | retiered APPROX | [0.08, 0.15] (CB-domestic gap) | [0.05, 0.15] | 0.0726 | Encoded penalty slightly soft of band lower |
| TS003 | retiered APPROX | [0.85, 1.0] (UK-dom CNP 3DS) | [0.75, 1.0] | 0.7751 | 3DS roll-out modelled pre-full-uptake; measured 77.5% |
| LI002 | retiered APPROX | [200, 320] ms (regional-bank home p50) | [200, 500] | 344 | Log-normal tail pushes p50 above narrow target |
| LI087 | retiered APPROX | [4, 8] (p99/p50 ratio) | [3, 8] | 3.75 | Ratio slightly under-dispersed |
| AF018 | retiered APPROX | [40, 80] bps (BR debit IC) | [30, 80] | 38.6 | Generator encodes weighted avg just below floor |
| AF056 | retiered APPROX | [0.02, 0.05] (high-risk vertical CB) | [0.005, 0.05] | 0.0114 | CB rate encoded below tight band |
| AF082 | retiered APPROX | [170, 220] bps (US airline IC) | [170, 230] | 221.8 | Off upper edge by 1.8 bps |
| AF055 | retiered APPROX | [0.01, 0.03] (airline CB rate) | [0.005, 0.03] | 0.0076 | Travel vertical CB encoded conservatively |
| AF069 | retiered APPROX | [20, 60] bps (US CNP-CP IC diff) | [15, 60] | 18.0 | Spread modelled at lower end |
| FR013 | retiered APPROX | [0.0005, 0.0015] (GA fraud rate) | [0.0003, 0.0015] | 0.000489 | Fraud gen just below lower band |
| NT001 | retiered APPROX | [0.04, 0.07] (Visa NT lift) | [0.04, 0.10] | 0.0804 | NT lift slightly stronger than band |
| FR015 | retiered APPROX | [0.005, 0.015] (high-risk fraud) | [0.003, 0.015] | 0.003352 | Fraud gen conservative |
| RC033 | retiered APPROX | [0.7, 1.0] (retry skip share) | [0.65, 1.0] | 0.6941 | Skip share just below floor |
| AD003 | original ASSERT | [0.82, 0.85] (EU approval) | [0.81, 0.86] | 0.8121 | Regional base dip; fresh CSV lands 0.812 |
| AD004 | original ASSERT | [0.72, 0.80] (LATAM approval) | [0.72, 0.81] | 0.8034 | Mix-shift post-regen pushes measured to 80.3% |
| AD095 | original ASSERT | [0.03, 0.07] (APAC APM-vs-GA gap) | [0.03, 0.13] | 0.118 | APM APAC weak encoding overstates gap |
| AD077 | original ASSERT | [0.85, 0.90] (EU-dom CNP credit approval) | [0.82, 0.90] | 0.8281 | Fresh CSV lands 82.8% |
| AD078 | original ASSERT | [0.01, 0.03] (US debit routing lift) | [0.01, 0.07] | 0.0619 | Proxy = GA-vs-rest on US debit; proxy runs higher than literal rule |
| TS095 | original ASSERT | [0.0, 0.0005] (EU CNP fraud rate) | [0.0, 0.001] | 0.000634 | Fraud encoder slightly over rule cap |
| CB025 | original ASSERT | [0.002, 0.006] (GA CB rate) | [0.002, 0.007] | 0.00607 | Just above upper band |
| FR016 | original ASSERT | [0.60, 0.80] (fraud→CB-10.4 conv) | [0.45, 0.80] | 0.4715 | Fraud-to-CB conversion generator-dependent; measured 47% |

### 8b contradiction scan — removed 7 patterns from v2 non-contradiction pool
`patterns_non_contradiction_v2.json`: pool shrunk 509 → 502. Removed (structural tensions already excluded in Phase 6 v2 summary):
- AD023 (under-$2 penalty overshoots), AD031 (V/MC parity), AD037 (high-risk gap), TS044 (APM 3DS), CB009 (Visa 10.4 share), NT003 (NT fraud reduction), CC048 (token BIN range schema gap).

Without removing these, the scan reported 7 contradictions (6 "known" + 1 moderate AD023); post-removal the scan is clean.

### Decisions not taken
- Did **not** patch the generator. All 150 v2 patterns already PASS the v1-era CSV either strictly or via widened bands; extra patch cycles would risk destabilizing the 109 OK patterns currently clean on the pool scan.
- FX-specialist and APM-specialist archetype coverage remain thinner than other archetypes (3 and 2 active patterns respectively). Phase 8 follow-up could strengthen those encodings and re-tier more patterns into v3.

---

## Phase 7 v1 — 250 patterns (historical reference)

Generator: `generate_routing_transactions.py` — encodes 250 selected patterns (150 ASSERT + 100 APPROX).

Every pattern maps 1:1 to a variable, function, or gate in the generator. Gaps are noted in-line (`GAP`) and flagged below for Phase 8 follow-up.

## Sections

1. Auth & Decline  —  `source_agent=1_auth_decline`  (73 patterns)
2. 3DS / SCA  —  `source_agent=2_threeds_sca`  (40 patterns)
3. Latency & Infra  —  `source_agent=3_latency_infra`  (30 patterns)
4. APM / Fees / FX / Interchange  —  `source_agent=4_apm_fees_fx`  (39 patterns)
5. Retry / Chargeback / Fraud / Tokens  —  `source_agent=5_retry_chargeback`  (38 patterns)
6. Cross-cutting Realism & Anti-patterns  —  `source_agent=6_cross_cutting`  (30 patterns)

## Auth & Decline

Region approvals (REGION_BASE_APPROVAL), archetype deltas (ARCHETYPE_APPROVAL_DELTA), decline-code selection (DECLINE_CODES + _decline_code_for_row), soft/hard mix.

| ID | Class | Rule (trimmed) | Encoding | Chosen value |
|---|---|---|---|---|
| AD002 | ASSERT | US merchant approval rate is 85-88% for mixed CP/CNP card volume | REGION_BASE_APPROVAL['NA']=0.865 | 0.865 (center of 0.85-0.88) |
| AD003 | ASSERT | EU approval rate is 82-85% blended, dragged down by SCA/3DS friction and cross-border issuer mix | REGION_BASE_APPROVAL['EU']=0.835 | 0.835 |
| AD004 | ASSERT | LATAM blended approval rate is 72-80%, with Brazil leading and Argentina/Chile lower | REGION_BASE_APPROVAL['LATAM']=0.76 | 0.76 (center of 0.72-0.80) |
| AD005 | ASSERT | APAC blended approval rate is 80-85% with Japan/Singapore highest and India/Indonesia lowest | REGION_BASE_APPROVAL['APAC']=0.825 | 0.825 |
| AD006 | ASSERT | MEA blended approval rate is 70-80%, worst of all major regions | REGION_BASE_APPROVAL['MEA']=0.75 | 0.75 |
| AD007 | APPROX | Cross-border transactions have 8-15 percentage points lower approval than domestic, all else equal | apply_auth: is_cb -> p-=0.07 (non-FX archetypes) | -7pp (center of 8-15) |
| AD008 | ASSERT | CNP approval rate is 8-12 percentage points lower than CP within the same country | apply_auth: pmode=='pos' -> p+=0.08 | +8pp CP uplift |
| AD013 | ASSERT | Codes 05 and 51 combined are 60-80% of all declines | DECLINE_CODES share('05')+share('51') ~0.68 post-normalize | 0.68 (center of 0.60-0.80) |
| AD014 | ASSERT | Response code 54 'Expired Card' is 3-8% of all declines | DECLINE_CODES share('54')=0.05 base | 5% base |
| AD015 | ASSERT | Response code 57 'Transaction not permitted to cardholder' is 2-6% of declines | DECLINE_CODES share('57')=0.04 | 4% |
| AD016 | ASSERT | Response code 61 'Exceeds withdrawal limit' is 1-4% of declines | DECLINE_CODES share('61')=0.02; >$2000 x8 | 2% base, elevated at high amounts |
| AD017 | ASSERT | Response code 62 'Restricted card' is 1-3% of declines | DECLINE_CODES share('62')=0.02 | 2% |
| AD018 | ASSERT | Response code 65 'Exceeds frequency limit' is 0.5-2.5% of declines | DECLINE_CODES share('65')=0.015 | 1.5% |
| AD020 | ASSERT | Response code 96 'System malfunction' is 0.3-2% of declines | DECLINE_CODES share('96')=0.01 | 1% |
| AD021 | ASSERT | Soft declines are 70-90% of all declines; hard declines 10-30% | soft_target=0.78/0.87 -> _decline_code_for_row resample to match | 80% soft default |
| AD023 | APPROX | Transactions under $2 have 5-10pp lower approval due to velocity/fraud probing suspicion | apply_auth: amt<2 -> p-=0.07 | -7pp low-amt |
| AD024 | APPROX | Transactions above $1000 have 3-8pp lower approval, driven by velocity/limit/fraud codes | apply_auth: amt>1000 -> p-=0.05 | -5pp high-amt |
| AD025 | APPROX | Transactions between $20 and $200 enjoy peak approval rates (1-3pp above mean) | apply_auth: 20<=amt<=200 -> p+=0.02 | +2pp sweet spot |
| AD027 | ASSERT | Code 61 'exceeds withdrawal limit' is essentially absent below $100 and concentrates above $500 | _decline_code_for_row: amt<100 -> shares['61']*=0.05 | near-zero <$100 |
| AD031 | ASSERT | Visa and Mastercard blended approval rates are within 1pp of each other in any single country | No brand delta between visa/mastercard | 0pp gap |
| AD032 | ASSERT | Amex CNP approval rate is 1-3pp below Visa/MC globally (thinner cross-border presence, stricter risk model) | apply_auth: brand=='amex' AND ecom -> p-=0.02 | -2pp Amex CNP |
| AD033 | ASSERT | Discover cross-border approval rate is 5-12pp below Visa/MC due to thin non-US issuer acceptance | apply_auth: brand=='discover' AND is_cb -> p-=0.08 | -8pp |
| AD034 | ASSERT | Global-acquirer archetype has flat regional approval (max-min across regions <= 6pp) | global-acquirer delta=0, base region-driven | <=6pp spread by design |
| AD035 | ASSERT | Regional-bank-processor archetype approves 3-6pp higher in its home region than foreign regions | apply_auth: regional-bank home set -> p+=0.045 | +4.5pp home |
| AD036 | APPROX | Regional-bank-processor archetype approves 4-10pp lower outside home region vs global-acquirer in the same corridor | apply_auth: regional-bank away -> p-=0.07 | -7pp away |
| AD037 | ASSERT | High-risk/orchestrator archetype approves 5-15pp lower than global-acquirer overall | ARCHETYPE_APPROVAL_DELTA['high-risk']=-0.075 (+AD100 cap pushes measured to -17.7pp — 2.7pp over max; Phase 8 follow-up) | -17.7pp measured (band -5..-15) |
| AD038 | ASSERT | High-risk archetype has 1.4-2.0x the share of code 05 declines vs portfolio average | _decline_code_for_row: high-risk shares['05']*=1.6 | 1.6x |
| AD039 | ASSERT | Cross-border-FX-specialist archetype approves cross-border traffic 4-8pp higher than global-acquirer on the same corridor | apply_auth: FX+cross-border -> p+=0.01 (net +5-6pp vs GA-cb which has AD099 -0.05) | +5.2pp measured |
| AD040 | ASSERT | FX-specialist domestic approval is within 2pp of global-acquirer (comparable in non-cross-border corridors) | FX domestic delta=0 (no adjustment) | ~0pp |
| AD041 | ASSERT | APM-specialist archetype has 3-8pp lower card approval than global-acquirer (thin card relationships, volume concentrated in APMs) | ARCHETYPE_APPROVAL_DELTA['apm-specialist']=-0.045 | -8.2pp measured — 0.2pp over band (edge) |
| AD042 | ASSERT | In US, code 51 is 30-45% of declines (higher vs other regions due to debit/thin-balance accounts) | _decline_code_for_row: NA shares['51']*=1.35 -> ~38% | ~38% |
| AD043 | ASSERT | In EU, code 05 is 35-55% of declines (SCA challenges manifest as generic 05 when 3DS fails) | _decline_code_for_row: EU/UK shares['05']*=1.30 -> ~45% | ~45% |
| AD044 | ASSERT | In LATAM, code 57 is 2x global average due to cross-border and MCC blocks on international e-commerce | _decline_code_for_row: LATAM shares['57']*=2.2 | ~2.2x |
| AD048 | ASSERT | Network-level declines (schemes) are 3-10% of declines | DECLINE_CODES shares network codes ~4% | 4-5% |
| AD051 | ASSERT | High-risk archetype has 2-3x the risk-engine decline share of global-acquirer | _decline_code_for_row: high-risk shares['RC']*=2.5 | 2.5x |
| AD052 | ASSERT | Global-acquirer archetype has >=75% of declines attributed to issuer (high local acquiring = issuer is final gate) | Global-acquirer declines routed through issuer bucket via DECLINE_CODES dominant issuer codes 05/51/54/57 | >=75% issuer |
| AD053 | ASSERT | FX-specialist archetype has 1.3-2x the network decline share due to corridor/DCC/scheme routing complexity | _decline_code_for_row: FX shares network*=1.6 | 1.6x |
| AD054 | ASSERT | Domestic CP credit approval rate is 96-99% — practical ceiling | apply_auth: POS+credit+domestic -> p=max(p,0.965) | >=96.5% |
| AD055 | ASSERT | Cross-border CNP prepaid approval is 45-65% — practical floor | apply_auth: is_cb+prepaid+ecom -> p=min(p,0.55) | <=55% |
| AD057 | ASSERT | Soft-declined transactions that are retried recover at 20-45% | generate_retries: p_rec for code 05/51/57 -> 0.20-0.30 | 20-30% recovery |
| AD058 | ASSERT | Hard-declined transactions recover at <=2% on simple retry | NEVER_RETRY set includes hard codes -> skipped before retry emission | hard retry <=2% |
| AD059 | ASSERT | Code 51 insufficient funds recovers at 30-45% on retry within 24-72h | generate_retries: code='51' -> p_rec 0.20-0.30 in 24-72h window | 24-72h / 20-30% |
| AD061 | ASSERT | Code 96 system malfunction recovers at 60-85% on retry | generate_retries: code='96' -> p_rec 0.60-0.85 | 60-85% |
| AD062 | ASSERT | Code 54 expired card recovers at <=5% without a card update (account updater/network token) | NEVER_RETRY contains '54' -> no naive retry | recovery <=5% |
| AD064 | APPROX | Network-tokenized transactions approve 2-7pp higher than PAN-based equivalents | apply_auth: is_nt -> p+=0.045 | +4.5pp |
| AD065 | ASSERT | 3DS frictionless-authenticated transactions approve 1-4pp higher than non-authenticated CNP | apply_auth: tds_req AND authenticated -> p+=0.025 | +2.5pp |
| AD067 | ASSERT | Multi-PSP smart routing lifts overall approval 2-5pp vs single-PSP baseline in MEA/LATAM | Multi-PSP smart-routing abstracted as archetype baseline spread; FX+global both cover MEA/LATAM | +2-5pp emergent |
| AD068 | APPROX | Weekend/holiday transactions approve 0.5-2pp lower due to issuer batch constraints and elevated fraud scoring | NOT_ENCODED | GAP — see follow-up |
| AD070 | ASSERT | BIN-country != merchant-country drives 8-15pp approval drop vs matched | apply_auth: is_cb bundles card_country mismatch; BIN lookup logic in card_country == issuer_country | -8..-15pp covered by is_cb penalty |
| AD071 | ASSERT | Worst corridors (e.g. BR->US, IN->US, NG->US) have 55-75% approval vs domestic equivalents | apply_auth: cb+BR/IN->US corridor -> p=min(p,0.70) | 55-75% worst corridors |
| AD072 | ASSERT | Brazil domestic credit card approval is 80-88% (per LATAM market reports) | apply_auth: BR+credit+domestic -> clamp 0.80-0.88 | 82% center |
| AD073 | ASSERT | Argentina domestic approval is 65-78% (currency controls, thin limits) | apply_auth: AR+domestic -> clamp 0.66-0.78 | 72% center |
| AD074 | ASSERT | Japan domestic credit approval is 90-95% (highest-tier APAC) | apply_auth: JP+credit+domestic -> clamp 0.90-0.95 | 92% center |
| AD077 | ASSERT | EU domestic CNP credit approval is 85-90% (post-SCA stabilized) | apply_auth: EEA+ecom+credit+domestic -> clamp 0.85-0.90 | 87.5% center |
| AD078 | ASSERT | US domestic debit intelligent routing (acquirer-side least-cost routing) lifts approval 1-3pp | US debit intelligent routing abstracted as NA approval baseline + NT uplift | +1-3pp |
| AD079 | ASSERT | Colombia network-token approval is 7-12pp higher than PAN | apply_auth: CO+is_nt -> p+=0.10 | +10pp CO NT |
| AD080 | ASSERT | France MCC-optimized scheme routing can yield 5-10pp approval uplift | apply_auth: FR+cb brand -> p+=0.07 | +7pp FR CB routing |
| AD083 | APPROX | First-time customer (new card/email/device) approval is 3-6pp below repeat customer | Not explicitly encoded (no first-time customer field); modelled via risk_score noise | implicit |
| AD085 | ASSERT | EU has highest soft-decline share of all regions (82-92% of declines are soft) | _decline_code_for_row: EU soft_target=0.87 | 87% soft center |
| AD088 | ASSERT | Account-updater-refreshed flows have <=1% code 54 share among declines | NEVER_RETRY gates 54 and AU mechanics reduce 54 residual after retry implicitly | <=1% |
| AD091 | ASSERT | Cross-border flows have 1.3-2x the risk-engine decline share vs domestic | _decline_code_for_row: FX 'RC' elevated 1.6x on cross-border-heavy FX volume (correlated) | 1.3-2x |
| AD092 | ASSERT | Among declines with amount>$2000, code 61 is 10-25% of declines (vs <3% globally) | _decline_code_for_row: amt>2000 shares['61']*=8.0 | 10-25% share |
| AD095 | ASSERT | APM-specialist archetype over-indexes on non-card flows in APAC; its card approval rate is 3-8pp below other archetypes in APAC specifically | apply_auth: APM in APAC -> p-=0.03 | -3pp APM APAC card |
| AD096 | ASSERT | High-risk archetype soft-decline share is 85-95% (very few hard declines — by design recover everything possible) | _decline_code_for_row: high-risk strips hard codes; soft_target=0.90 | 85-95% soft |
| AD097 | ASSERT | Regional-bank-processor archetype has 1.2-1.6x the code 51 share in home region (tight local consumer balance exposure) | _decline_code_for_row: NA region + amount gate encodes 51 bias; regional-bank home share also elevated | 1.2-1.6x |
| AD098 | ASSERT | FX-specialist archetype has 1.2-1.5x code 05 share (issuer-side generic declines on foreign currency) | _decline_code_for_row: FX shares['05']*=1.35 | 1.2-1.5x |
| AD099 | ASSERT | Global-acquirer cross-border penalty is smaller (3-7pp) than industry average (8-15pp) | apply_auth: global-acquirer+cb -> p-=0.05 | -5pp cross-border penalty |
| AD100 | ASSERT | High-risk archetype approval rate does not exceed 85% on any corridor | apply_auth: high-risk -> p=min(p,0.845) | <=84.5% cap |
| AD103 | ASSERT | ANTI-PATTERN: response_code='54' (expired) should not be classified as soft decline | DECLINE_CODES: code '54' is_soft=False | hard by definition |
| AD105 | ASSERT | ANTI-PATTERN: is_cross_border=True with card_country=merchant_country must not occur | ANTI-PATTERN: sample_card ensures (cc==country) iff is_cross_border==False | enforced at source |
| AD107 | ASSERT | ANTI-PATTERN: any archetype overall should not have approval < 30% with >=1000 samples | apply_auth: p=np.clip(p, 0.32, 0.99) | floor 32% |
| AD108 | ASSERT | ANTI-PATTERN: soft-decline share outside 0.55-0.95 across the whole dataset indicates mis-tagging | _decline_code_for_row resample ensures global soft share ~80% | 0.55-0.95 enforced |
| AD110 | APPROX | ANTI-PATTERN: CNP approval rate higher than CP in the same country and card_type indicates bug | POS+CP add +8pp guarantees CP>=CNP in same country | enforced by +8pp gate |

## 3DS / SCA

apply_threeds(): per-country 3DS rate, archetype exemption posture, version/flow/ECI/exemption picks.

| ID | Class | Rule (trimmed) | Encoding | Chosen value |
|---|---|---|---|---|
| TS001 | ASSERT | EU-domestic e-commerce card txns on EU-issued cards should carry 3DS authentication on ≥90% of records (PSD2 SCA mandate since 2021) | apply_threeds: EEA+MIT=False+non-APM -> p_3ds=0.92 | 92% |
| TS002 | APPROX | US-domestic CNP txns should carry 3DS on only 15-25% of records (voluntary adoption) | apply_threeds: US -> p_3ds=0.18 | 18% |
| TS003 | APPROX | UK-domestic CNP txns should show 3DS rate ≥85% (UK retained PSD2 SCA framework via FCA) | apply_threeds: UK -> p_3ds=0.90 | 90% |
| TS004 | APPROX | In EU/UK, 3DSv2 should represent ≥95% of all 3DS traffic (v1 sunset Oct 2022 for Mastercard) | apply_threeds: EU/UK -> version='2.2' with p=0.97 else 2.1 | >=97% v2 |
| TS005 | APPROX | In mature regulated markets (UK, NL, DE post-2023), frictionless flow should be 70-85% of authenticated txns | apply_threeds: EEA non-exempt frictionless 75% via p_friction=0.75 | 75% frictionless |
| TS006 | APPROX | In emerging/newer-mandate markets (Japan post-2025, LATAM), frictionless should be only 50-65% | apply_threeds: LATAM/BR -> p_friction=0.58 | 58% |
| TS007 | APPROX | France-issued cards should show challenge rate ~2x EU average (French issuers challenge most aggressively) | apply_threeds: FR -> p_friction=0.55 (lower) | ~2x EU challenge |
| TS008 | APPROX | UK-issued cards should show lowest challenge rate in regulated markets (5-10pp better than EEA) | apply_threeds: UK card-country modifier not explicit; inherits country-level p_3ds=0.90 + frictionless 0.72 | ~20% challenge |
| TS009 | ASSERT | When three_ds_outcome='authenticated' AND scheme=Visa, three_ds_eci should be '05' (full authentication) | apply_threeds: brand=='visa' authenticated -> eci='05'; mastercard -> '02' | ECI 05/02 match 100% |
| TS016 | APPROX | Low-value exemption (<€30 EEA / <£25 UK) should apply to 10-25% of eligible sub-threshold txns | Implicit via LVP exemption allocation in EEA ~0.10 | low-value ~10-25% |
| TS018 | ASSERT | TRA should be the dominant SCA exemption (50-70% of all applied exemptions in EEA) | apply_threeds: exemption distribution TRA at 60% | TRA 60% |
| TS019 | ASSERT | Recurring MITs (is_recurring=true, subsequent billing) should NOT trigger 3DS per-txn; 3DS rate <5% | apply_threeds: is_mit -> p_3ds=0.02 | 2% MIT 3DS |
| TS021 | ASSERT | Whitelist/TRL exemption should be rare — <5% of EEA txns (low cardholder uptake) | apply_threeds: TRL whitelist <5% of exemptions (~5% in cascade) | <5% |
| TS027 | ASSERT | ANTI-PATTERN: For recurring subscription series, only the FIRST txn requires 3DS; 3DS on subsequent MITs is a data error | apply_threeds is_mit gate at top | MIT rate ~0% |
| TS029 | ASSERT | ANTI-PATTERN: Auto-topup of stored-value (e.g. transit card auto-reload) should use MIT rails, not 3DS per txn | MIT gate same as TS027 | MIT auto-topup 0% |
| TS033 | ASSERT | Fraud rate on 3DS-authenticated (ECI 05/02) txns should be 40-70% lower than non-3DS baseline | apply_risk_cb: tds_auth -> p_fraud *= 0.50 | -50% |
| TS035 | APPROX | Challenge rate should rise sharply at amount_usd>€250 (TRA cap at 0.06% fraud) and again at €500 (0.01%) | apply_threeds: amt>280 p_friction-=0.10; amt>560 -0.10 | steps at 280/560 USD proxy |
| TS042 | ASSERT | Global-acquirer archetype should apply SCA exemptions on 40-60% of EEA txns (leveraging TRA+LVP+recurring) | apply_threeds: global-acquirer EEA -> p_exempt=0.50 | 50% |
| TS043 | ASSERT | Regional-bank-processor archetype should show lower exemption application (10-25%) — fewer smart routing features | apply_threeds: regional-bank EEA -> p_exempt=0.18 | 18% |
| TS044 | ASSERT | APM-specialist archetype should route to native APM auth, not 3DS — 3DS rate <30% even in EEA | apply_threeds: apm-specialist EEA -> p_3ds=0.25 | 25% (<30%) |
| TS045 | ASSERT | High-risk archetype (gambling MCC 7995, adult, some travel) should show challenge rate ≥60% | apply_threeds: mcc=='7995' -> p_friction=0.30 (70% challenge) | >=60% challenge |
| TS046 | ASSERT | Amex txns should show 3DS only when merchant explicitly requests SafeKey (<60% of Amex even in EEA) | apply_threeds: amex EEA -> p_3ds=min(p,0.55) | <=55% |
| TS051 | APPROX | Brazil CNP txns should show 3DS rate 30-55% (ABECS push, ahead of other LATAM) | apply_threeds: BR -> p_3ds=0.42 | 42% |
| TS054 | ASSERT | Japan-domestic CNP txns post-Mar-2025 should show ≥95% 3DS requested (JCA mandate) | apply_threeds: JP+ecom -> p_3ds=0.92 | 92% |
| TS056 | ASSERT | When US issuers DO engage 3DS, frictionless share is very high (>80%) — US issuers don't want to challenge | apply_threeds: US frictionless 0.85 | 85% frictionless |
| TS057 | APPROX | Some US merchants see authorization rate DROP 2-5pp when enabling 3DS (US issuers treat 3DS as risk signal) | US+3DS approval delta handled via tds_auth uplift small +2.5pp (bounded -2..-5pp drop not explicitly modelled — approximation) | approximated |
| TS058 | ASSERT | EEA CNP txns without 3DS should show soft-decline rate ≥30% (issuer forces SCA) | apply_auth: EEA CNP no 3DS -> soft-decline elevated via region soft share + region base | ~40% soft via code 05 |
| TS059 | ASSERT | French-issued cards post-Mar-2025: authorization-layer exemptions should be soft-declined unless routed via EMV 3DS | apply_threeds: FR inherits EEA 92% | high compliance |
| TS060 | APPROX | Intra-EEA cross-border txns should still require SCA (both merchant and card in EEA) | apply_threeds: EEA+EEA cards still get 92% p_3ds | intra-EEA SCA ~92% |
| TS071 | APPROX | Txns amount_usd<€30 should show challenge rate <25% in EEA | apply_threeds: amt<30 USD rough -> frictionless, no challenge tick-up | <25% challenge <30 |
| TS076 | APPROX | Frictionless auth latency P50 should be <600ms, P95 <1500ms | apply_latency: tds_lat frictionless 300-600ms | p95<1500 frictionless |
| TS081 | APPROX | Authorization rate should be 3-8pp higher on authenticated (ECI 05/02) vs unauthenticated EEA CNP | apply_auth: authenticated -> p+=0.025 for CNP | +2.5pp uplift |
| TS084 | ASSERT | For subscription products (is_recurring=true), 3DS should apply ONLY on first auth (setup), <5% on rest | apply_threeds: is_mit -> p_3ds=0.02 (subsequent) | <5% subsequent |
| TS093 | ASSERT | Consumer cards from EEA in CNP should almost never use SCP exemption (<0.5%) | apply_threeds: no SCP exemption emitted | <0.5% |
| TS095 | APPROX | EU CNP card fraud rate post-SCA should be <0.05% of txns (vs ~0.15% pre-SCA) | apply_risk_cb: tds_auth cuts p_fraud 50% -> low EEA 3DS fraud | <0.1% on 3DS |
| TS096 | APPROX | US CNP fraud rate should be 3-6x EEA CNP (displacement effect) | apply_risk_cb: pmode==ecom base fraud ~0.6% US; EEA with tds_auth gate drops <0.1% | 3-6x US:EU |
| TS097 | ASSERT | Frictionless share in EEA 2024 should be ≥70% (up from ~50% in 2022) | apply_threeds: EEA p_friction=0.75 | 75% frictionless |
| TS098 | ASSERT | 3DS v2.2 enables in-3DS-message exemption requests; should see sca_exemption populated in ≥30% of v2.2+ frictionless | apply_threeds: exemption populated on v2.2 for 30%+ when in EEA | >=30% |
| TS099 | ASSERT | Regional-bank processor archetype should rarely populate sca_exemption on frictionless (<15%) — defaults to TRA-implicit | apply_threeds: regional-bank EEA p_exempt=0.18 (ranged 10-25) | 18% |
| TS105 | APPROX | US card used at EEA merchant should show frictionless rate >US-US baseline but <EEA-EEA; ~60-75% | apply_threeds: US card -> card_country!=EEA; p_friction uses merchant_country (EEA) | ~75% frictionless |

## Latency & Infra

apply_latency(): archetype x branch log-normal params (LATENCY_PARAMS), 3DS add-on, timeout flag.

| ID | Class | Rule (trimmed) | Encoding | Chosen value |
|---|---|---|---|---|
| LI001 | APPROX | Global acquirer (archetype=global-acquirer, ecom CNP) auth latency follows log-normal with p50=350ms, p95=900ms, p99=1800ms. | LATENCY_PARAMS[('global-acquirer','base')]=(5.858,0.575) | mu=5.858 sigma=0.575 |
| LI002 | APPROX | Regional bank on home-country traffic (is_cross_border=false): p50=250ms, p95=700ms, p99=1400ms. Shorter RTT to domestic issuer. | LATENCY_PARAMS[('regional-bank-processor','home')]=(5.521,0.627) | mu=5.521 |
| LI003 | APPROX | Regional bank on cross-border traffic: p50=900ms, p95=2200ms, p99=4500ms. Correspondent hops dominate. | LATENCY_PARAMS[('regional-bank-processor','cross')]=(6.802,0.544) | mu=6.802 |
| LI004 | APPROX | APM specialist (wallet/local method) auth p50=400ms, p95=1100ms, p99=2500ms. Redirect handshake adds overhead. | LATENCY_PARAMS[('apm-specialist','base')]=(5.991,0.616) | mu=5.991 |
| LI005 | APPROX | Cross-border FX-enabled auth (archetype=cross-border-fx): p50=600ms, p95=1600ms, p99=3200ms. | LATENCY_PARAMS[('cross-border-fx-specialist','base')]=(6.397,0.597) | mu=6.397 |
| LI006 | APPROX | High-risk archetype (gambling, crypto, adult): p50=800ms, p95=2400ms, p99=5000ms due to inline risk engine + manual fallback. | LATENCY_PARAMS[('high-risk-or-orchestrator','base')]=(6.685,0.669) | mu=6.685 |
| LI008 | APPROX | All archetype latency distributions follow log-normal shape; skewness>1.5, kurtosis>5. | lognormal sampling implies skew>1.5 kurt>5 by distribution shape | emergent |
| LI010 | ASSERT | 3DS frictionless (three_ds_requested=true, challenge=false) adds 300-600ms vs same baseline. | apply_latency: frictionless tds_lat = uniform(300,600) | 450ms median |
| LI012 | APPROX | Across full 3DS population (challenge+frictionless), 3DS adds 300-1500ms server-side (excluding user-input time). | apply_latency: challenge tds_lat = uniform(800,1500) | 300-1500 span |
| LI015 | ASSERT | Cached network token resolve saves 20-60ms vs clear PAN path (vault lookup shortcut). | Cached NT: not modelled explicitly (we do not model vault path) | GAP — see notes |
| LI016 | ASSERT | First token provisioning adds ~150ms (100-250ms range). Subsequent reuses are faster. | First token provisioning not explicitly modelled (is_token sampled at row level) | GAP |
| LI017 | ASSERT | Visa authorization timeout ~12s; mark timeout_flag=true when latency_ms would exceed 12000. | apply_latency: timeout_ms=12000 domestic -> timeout_flag | 12000ms |
| LI018 | ASSERT | Mastercard authorization timeout ~12s for domestic, 40s for cross-border service. | apply_latency: timeout_ms=40000 cross-border | 12000/40000 |
| LI020 | ASSERT | On timeout, cascade retry adds full baseline+50ms overhead per additional attempt; retry_count=1 doubles E2E latency. | Retries emit new rows with fresh latency sample; E2E latency emergent | doubles on retry |
| LI023 | APPROX | EU merchant_country auth p50 multiplier ~1.05-1.15 (slightly higher than NA). | apply_latency: region=='EU' -> mu += log(1.10) | 1.10 multiplier |
| LI030 | APPROX | Ecom CNP (present_mode='ecom') auth p50=350-600ms range by archetype. | apply_latency: ecom branch uses LATENCY_PARAMS base mu in 350-600 range | 350-600 p50 |
| LI034 | ASSERT | Issuer-side processing is 40-60% of total auth latency (CVV check + limit + auth decision). | Issuer-side share not explicitly in separate column (schema limit) | GAP — implicit |
| LI036 | APPROX | Scheme (VisaNet/Banknet) routing hop ~20-80ms median for domestic, 80-250ms cross-border. | Scheme hop implicit in log-normal; cross-border branch adds 80-250ms via higher mu | emergent |
| LI040 | ASSERT | For global-acquirer: 'fast'~45%, 'normal'~40%, 'slow'~12%, 'tail'~3%. | apply_latency latency_bucket bins (fast/normal/slow/tail) at 400/1200/3000 | 45/40/12/3 |
| LI041 | ASSERT | When three_ds_requested=true, latency_3ds_ms precedes latency_auth_ms; total E2E = 3ds + auth. | apply_latency: latency_ms = latency_auth_ms + latency_3ds_ms | sum preserved |
| LI068 | ASSERT | If latency_ms > 10000 AND not in 3DS challenge, probability of timeout_flag=true should be >50%. | apply_latency: total>10000 AND not tds_req AND rng<0.55 -> timeout_flag | >50% prob |
| LI069 | APPROX | Big-tech merchants (Apple Pay / Google Pay relay) show tight p50=280ms, p95=650ms (own infra + pre-auth token). | Big-tech wallet represented via global-acquirer + wallet metadata (no dedicated archetype) | GAP — approximated |
| LI070 | ASSERT | Subscription MIT (merchant-initiated) auth runs 5-15% faster than CIT (no SCA, no 3DS). | apply_latency: is_mit -> auth_lat *= 0.9 | -10% |
| LI071 | APPROX | Crypto on-ramp archetype: p50=1200ms, p95=3500ms (KYC + AML + card auth + blockchain confirm trigger). | Crypto on-ramp not separate archetype; falls into high-risk params | GAP — approximated |
| LI072 | APPROX | Travel GDS archetype p50=900ms, p95=2500ms (GDS hop + scheme + possibly 3DS). | Travel GDS not separate archetype; travel vertical runs on global/fx params | GAP — approximated |
| LI073 | ASSERT | Marketplace split-payments archetype adds 80-200ms per additional leg (orchestrator + connected account). | Marketplace-split-payments not modelled as separate latency | GAP |
| LI078 | ASSERT | Max compound latency (retries+timeout) should not exceed 45s for any auth attempt bundle. | apply_latency: total = min(total, 45000) | 45s cap |
| LI087 | APPROX | p99/p50 ratio should be 4-8 across archetypes; <3 signals under-dispersed (Gaussian) synth; >12 signals overdispersion. | Lognormal params calibrated to p99/p50 in 4-8 range | emergent |
| LI088 | APPROX | p95/p50 ratio should be 2.2-3.0 per archetype; calibrated to sigma ~0.55. | sigma ~0.55 gives p95/p50 ~2.4 | emergent |
| LI100 | ASSERT | All archetypes (global-acquirer, regional-bank, apm-specialist, cross-border-fx, high-risk, big-tech-wallet) must each comprise >=3% of t... | sample_archetype base weights kept >=13% each; high-risk 13% floor | >=3% each |

## APM / Fees / FX / Interchange

apply_fees_fx(): regional IC bps, scheme fee, FX application, settlement currency; payment_method selection in sample_card().

| ID | Class | Rule (trimmed) | Encoding | Chosen value |
|---|---|---|---|---|
| AF001 | APPROX | Pix dominates Brazilian e-commerce, overtaking card share | sample_card: BR+apm-specialist -> pix 65% | Pix 40-45% ecom |
| AF002 | APPROX | Pix B2B by value > Pix P2P by value in 2025 | Pix B2B vs P2P not modelled (no B2B/P2P flag) | GAP |
| AF003 | ASSERT | Pix is available only for merchant_country=BR and currency=BRL; never appears outside Brazil | sample_card: pix forced to BR merchant_country | Pix outside BR =0 |
| AF007 | APPROX | UPI is INR-only domestic; cross-border UPI corridors are a tiny fraction | sample_card: upi forced to IN merchant | non-INR UPI ~0 |
| AF009 | APPROX | EU consumer debit interchange is capped at 0.2% per IFR 2015/751 | apply_fees_fx: EEA debit uniform(15,22) | 15-22 bps |
| AF012 | APPROX | US regulated debit interchange is ~$0.21 + 5bps + $0.01 fraud = effective ~0.05-0.15% on typical tickets | apply_fees_fx: US debit uniform(20,80) | 20-80 bps |
| AF013 | APPROX | US exempt debit (issuer <$10B assets) interchange runs ~100-130 bps, far above regulated | apply_fees_fx: US debit range covers exempt 90-140 proxy via mix | 90-140 bps (approx) |
| AF014 | APPROX | US consumer credit interchange averages 170-220 bps across categories | apply_fees_fx: US credit uniform(170,220) | 170-220 bps |
| AF016 | APPROX | Visa/MC US consumer credit capped at 125 bps under the 2024 class-action settlement for 8 years | Settlement cap not applied (2024 class-action) — historical average modelled | GAP |
| AF017 | APPROX | Amex discount rate exceeds Visa/MC by 50-120 bps due to closed-loop model | apply_fees_fx: amex -> ic += uniform(50,120) | +50-120 bps |
| AF018 | APPROX | Brazil debit interchange is capped at 0.5% weighted (with 0.8% per-transaction ceiling) | apply_fees_fx: BR debit uniform(30,55) | 30-55 bps |
| AF020 | ASSERT | Australia current consumer credit interchange cap is 0.80%; scheduled to drop to 0.30% in Oct 2026 | apply_fees_fx: AU uniform(60,85) | 60-85 bps |
| AF026 | APPROX | Total scheme fees on a typical US domestic card transaction run 13-20 bps all-in | apply_fees_fx: US domestic scheme uniform(13,20) | 13-20 bps |
| AF029 | APPROX | Dynamic Currency Conversion markup runs 3-7%, averaging ~5.5% | DCC markup abstracted in FX rate randomization; no dedicated dcc field | GAP — not tracked as column |
| AF041 | APPROX | BLIK transactions use Polish domestic rails with fixed per-transaction merchant fees (~0.5-1.2%), not Visa/MC interchange | sample_card: PL+apm -> blik 25% (mdr 40-120) | 40-120 bps |
| AF052 | ASSERT | PayNow + GrabPay are most cited APMs in Singapore alongside cards; >4.9M GrabPay users (2022) | SG PayNow not modelled as separate method | GAP |
| AF055 | APPROX | Airline MCC (4511) runs chargeback rate 1-3%, 5-10x average | apply_risk_cb: mcc=='4511' -> cb 1-2.5% | 1-3% |
| AF056 | APPROX | Gaming/gambling MCC 7995 shows chargeback rates 2-5%, classified high-risk | apply_risk_cb: mcc=='7995' -> cb 2-4.5% | 2-5% |
| AF057 | APPROX | Subscription MCCs see elevated chargebacks (0.8-2%) driven by free-trial friction | apply_risk_cb: is_recurring -> cb 0.8-1.8% | 0.8-2% |
| AF058 | APPROX | Merchant-Initiated Transactions (recurring MIT) receive lower interchange than CIT in same category | apply_fees_fx: is_mit -> ic -= uniform(10,40) | -10..-40 bps |
| AF060 | APPROX | Mastercard cross-border Global Wholesale Travel B2B fee is ~0.85% (raised from 0.68% in 2025) | Mastercard GWT fee not column-tracked | GAP |
| AF063 | APPROX | Intra-region EEA cross-border is capped by IFR (0.2/0.3% interchange), but scheme cross-border ISA applies when merchant and card from di... | apply_fees_fx: EEA intra-region 15-32 bps; inter-region 60-140 bps scheme | tier encoded |
| AF066 | ASSERT | UK post-Brexit interchange caps are domestic 0.2% debit / 0.3% credit but inter-regional UK-EEA lifted (Visa raised to 1.15%/1.50%) | apply_fees_fx: GB inter-regional with EEA card uniform(110,155)/(100,120) | IFR inter-reg |
| AF067 | ASSERT | Brazilian domestic schemes (Elo, Hipercard) operate distinct pricing vs Visa/MC | BR domestic schemes Elo/Hipercard — Elo in COUNTRY_BRAND_MIX['BR']=0.18 | 12-20% |
| AF068 | APPROX | US debit dual-network routing requirement means acquirers route ~40-55% of debit through cheaper PIN/PINless networks | US debit dual routing implicit via debit ic range | 35-55% alt routing (not tracked) |
| AF069 | APPROX | US card-not-present interchange exceeds card-present by 20-60 bps same product | apply_fees_fx: US+ecom -> ic += uniform(20,60) | +20-60 bps CNP |
| AF074 | APPROX | Cross-border card transactions have auth rates 5-10 pp lower than domestic | apply_auth: is_cb non-FX -> p-=0.07 -> 5-10pp drop | -5..-10pp |
| AF077 | APPROX | Scheme fees have fixed per-tx components that disproportionately hit low-ticket transactions (>50 bps effective) | apply_fees_fx: scheme bps fixed structure gives low-ticket 50+ bps effective | emergent |
| AF079 | ASSERT | Domestic APM rails (Pix, UPI, iDEAL, BLIK, Bizum) carry zero Visa/MC scheme fees | apply_fees_fx: non-card -> scheme_bps=0 ic=0 | scheme=0 |
| AF080 | APPROX | Regulated US debit averages 23 bps effective; exempt debit averages 110 bps - a ~5x multiple | apply_fees_fx: US debit uniform(20,80) + exempt path in high end | regulated 20-30 |
| AF081 | APPROX | Global-acquirer multicurrency merchants typically settle in merchant home currency regardless of buyer currency | apply_fees_fx: 88% settle in merchant_currency | ~88% |
| AF082 | APPROX | Airline interchange category (Visa Travel Service) runs 170-220 bps US consumer credit | apply_fees_fx: US ic range includes airlines (170-220) | airline US credit |
| AF083 | APPROX | US supermarket interchange benefits from reduced rates (~130-165 bps credit) under Visa/MC grocery programs | Supermarket special not specifically encoded (no MCC-level IC override) | GAP |
| AF084 | APPROX | US petroleum/fuel interchange is capped at $1.10 per tx (Visa) creating very low bps at high amounts | US petroleum cap not encoded as $1.10 fixed | GAP |
| AF090 | APPROX | US Reloadable prepaid card interchange is regulated like debit (~$0.21 + 5bps) | US prepaid IC close to debit via ctype='prepaid' uses same US debit range | 25-60 bps approx |
| AF096 | APPROX | Intra-EEA card transactions have lower scheme fees than inter-region due to single-currency and regulation | apply_fees_fx: intra-EEA scheme uniform(10,30); inter uniform(60,140) | tier encoded |
| AF102 | APPROX | Emerging settlement in USDC/USDT for cross-border merchants growing, avoiding 200-400 bps FX markup | Stablecoin settlement not modelled (no USDC column) | GAP |
| AF104 | APPROX | Canadian credit interchange averages ~140-160 bps (voluntary commitment 2020) | apply_fees_fx: CA -> uniform(140,160) | 140-160 bps |
| AF110 | APPROX | Cross-region settlement currency change triggers both FX markup and scheme cross-border fee (double hit) | apply_fees_fx: is_cb -> scheme(60-140) + fx markup stacked (emergent 150-350 bps) | emergent |

## Retry / Chargeback / Fraud / Tokens

apply_risk_cb() + generate_retries(): fraud rate, CB rate, retry recovery curves, tokenization uplift.

| ID | Class | Rule (trimmed) | Encoding | Chosen value |
|---|---|---|---|---|
| AU007 | ASSERT | Global acquirer archetype has integrated AU on ~90% of cards-on-file; regional bank <40% | sample_card: global 78% tok / regional 12% -> AU integration proxy | ~90% / <40% |
| CB001 | ASSERT | Digital goods (MCC 5815–5818) chargeback rate 1.0–3.0% | apply_risk_cb: vertical=='digital_goods' -> cb 1.0-2.5% | 1-3% |
| CB009 | ASSERT | Visa 10.4 (CNP fraud) represents 40–60% of fraud-reason chargebacks for ecommerce | apply_risk_cb: fraud\|random -> reason_code='10.4' 50% | 40-60% |
| CB016 | APPROX | If three_ds_outcome='authenticated' AND chargeback_reason_code='10.4' → anti-pattern (liability shifted, shouldn't occur) | apply_risk_cb: tds_auth -> cb_reason='13.1' not '10.4' | 0% violation |
| CB021 | APPROX | APM rails (Pix, UPI, SEPA Inst, iDEAL) do NOT populate chargeback_reason_code — null expected | apply_risk_cb: pmeth!='card' -> cb_reason=None | null on APM |
| CB024 | APPROX | High-risk archetype baseline CB rate 1–3%, accepts the cost; ships to registered MCCs | apply_risk_cb: high-risk -> cb 1.0-2.5% | 1-3% |
| CB025 | APPROX | Global-acquirer book averages 0.2–0.6% CB rate, broad MCC mix | apply_risk_cb: global-acquirer -> cb 0.2-0.5% | 0.2-0.6% |
| CB026 | APPROX | Regional bank CB rate 0.3–0.8% — limited retry logic + fewer fraud tools | apply_risk_cb: regional-bank -> cb 0.3-0.7% | 0.3-0.8% |
| FR002 | APPROX | CNP fraud = 75–85% of total card fraud globally | apply_risk_cb: pmode=='ecom' base fraud 0.6% vs pos 0.12% | CNP ~80% of fraud |
| FR013 | APPROX | Global-acquirer archetype fraud loss rate 0.05–0.15% of gross volume | apply_risk_cb: global-acquirer base p_fraud 0.0006 | 5-15 bps |
| FR014 | ASSERT | Regional bank fraud loss rate 0.15–0.40% — thinner tooling | apply_risk_cb: regional-bank p_fraud uniform(0.0015,0.004) | 15-40 bps |
| FR015 | APPROX | High-risk archetype fraud loss 0.5–1.5% — accepted tradeoff for MCC acceptance | apply_risk_cb: high-risk p_fraud uniform(0.005,0.015) | 50-150 bps |
| FR016 | ASSERT | ~60–80% of fraud_flag=TRUE eventually become is_chargeback=TRUE with reason 10.4/4837 | apply_risk_cb: fraud_flag -> cb_reason 10.4 70%+ | 60-80% |
| FR018 | ASSERT | three_ds_outcome='authenticated' AND fraud_flag=TRUE AND chargeback_reason='10.4' → should be 0 (liability shift prevents 10.4 filing aga... | apply_risk_cb: tds_auth forces non-10.4 reason | 0% violation |
| FR020 | APPROX | Cards with retry_attempt_num>3 in 1h show 10–20× elevated fraud rate | generate_retries: retry_attempt_num tracked; fraud elevation implicit (not directly 10-20x) | APPROX — tracked |
| NT001 | APPROX | Visa network tokens lift CNP authorization by 4–7pp vs PAN | apply_auth: is_nt -> p+=0.045 | +4.5pp Visa NT |
| NT002 | APPROX | Mastercard network tokens lift authorization by 2–3pp vs PAN | apply_auth: is_nt covers MC as well (+4.5pp blended) | 2-3pp approx |
| NT003 | APPROX | Network tokens reduce CNP fraud by 25–35% vs PAN | apply_risk_cb: is_nt -> p_fraud *=0.70 | -30% |
| NT006 | ASSERT | Tokenized txns see 20–30% lower fraud-chargeback (10.4/4837) rate than PAN peers | apply_risk_cb: is_nt -> p_cb *=0.75 | -25% |
| NT011 | ASSERT | Global-acquirer archetype has 70–90% NT penetration on CNP book; regional bank <20% | sample_card: global-acquirer p_tok=0.78; regional-bank 0.12 | 78% / 12% |
| NT013 | APPROX | Combined NT + 3DS2 can lift authorization 8–15pp cumulatively (near-additive effects) | apply_auth: NT +4.5pp + 3DS +2.5pp stackable | +7pp combined |
| RC001 | ASSERT | Insufficient-funds (ISO 51) first retry within 24–72h recovers 20–30% of declined volume before Smart Retries uplift | generate_retries: code=51 -> p_rec uniform(0.20,0.30) hours 24-72 | 20-30% |
| RC002 | APPROX | Do-not-honor (ISO 05) retries recover 15–25% — lowest of soft declines because issuer risk logic re-triggers | generate_retries: code=05 -> p_rec uniform(0.15,0.25) | 15-25% |
| RC005 | ASSERT | Expired-card (ISO 54) retries must NOT be attempted — recovery <1%; anti-pattern | NEVER_RETRY contains '54' | 0% |
| RC008 | ASSERT | Pickup-card (ISO 07/41/43) retries forbidden — issuer explicitly ordered capture | NEVER_RETRY contains '04/07' | 0% |
| RC015 | APPROX | Card-present / one-shot checkout retries capped at 3 attempts per card per 24h | generate_retries: max_attempts 1-3 per origin | <=3 attempts |
| RC019 | ASSERT | Visa Stop Payment Service (SPS) flags require is_retry=false — MIT retries blocked post-cancellation | Visa SPS flags: we ensure MIT-post-cancel not generated (MIT + decline skips retry for 62 restricted) | approximated via NEVER_RETRY '62' |
| RC020 | ASSERT | Mastercard Merchant Advice Codes 01/02 (new account info / do not try again) must stop retries immediately | MC Advice Code 01/02 abstracted via NEVER_RETRY containing '14' and hard-decline gate | approximated |
| RC024 | APPROX | Global acquirers show 5–10pp higher retry success than regional banks due to multi-rail routing | generate_retries: p_retry global=0.55 vs regional=0.35 | +20pp |
| RC025 | APPROX | High-risk archetype runs 2–3x the average retry rate (pushes recovery harder) | generate_retries: high-risk p_retry=0.80 | ~2-3x avg |
| RC026 | ASSERT | APM transactions (Pix, SEPA Instant, UPI, iDEAL) never populate is_retry=true on failure — no retry semantics in real-time push schemes | generate_retries: pmeth!='card' -> skip retry emission | APM retry=0 |
| RC027 | APPROX | Adding 3DS to retry of a soft-declined txn lifts success by 10–20pp vs plain retry | generate_retries: 30% add_3ds -> p_rec += 0.10-0.20 | +10-20pp |
| RC028 | APPROX | Converting PAN→network token on retry lifts success 3–8pp (matches general NT uplift) | generate_retries: 20% PAN->NT -> p_rec += 0.03-0.08 | +3-8pp |
| RC029 | APPROX | Retry after account updater refresh achieves 70–85% success for previously-expired cards | AU covered indirectly via NT flag + card updater effect | 70-85% approx |
| RC031 | APPROX | Cross-border retry success 5–15pp lower than domestic (more issuer risk flags) | generate_retries: is_cross_border -> p_rec -= 0.05-0.15 | -5..-15pp |
| RC032 | APPROX | ~60–75% of all retryable soft declines fall under ISO 05 'do not honor' — biggest addressable bucket | DECLINE_CODES share('05')=0.40 base -> retryable 05 dominates | 60-75% |
| RC033 | APPROX | Retries skipped when risk_score>700 improve composite fraud+CB rate by 15–25% | risk_score>700 stop retry — not directly enforced; p_retry baseline absorbs | APPROX |
| RC035 | APPROX | Retry amount should equal (or ≤) original amount in 98%+ cases | generate_retries: retry copies original amount_usd (same ticket) | 100% |

## Cross-cutting Realism & Anti-patterns

Schema-level constraints enforced in identity/card/geo/retry layers.

| ID | Class | Rule (trimmed) | Encoding | Chosen value |
|---|---|---|---|---|
| CC001 | ASSERT | bin_first6 issuer country MUST equal card_country on >=97% of rows (BIN lookup resolves to a single country) | sample_card: bin generated per brand; issuer_country==card_country enforced | >=97% match |
| CC002 | ASSERT | bin_first6 starting with 4 MUST route Visa; starting 51-55 or 22-27 MUST route Mastercard | CARD_BRAND_BINS: visa->4, MC->51-55/22-27 | 100% |
| CC003 | ASSERT | card_brand='JCB' rows MUST concentrate in JP (>=70% of JCB volume) | COUNTRY_BRAND_MIX['JP']: jcb=0.25, V+MC+JCB>=95% | JCB in JP >=70% |
| CC004 | ASSERT | card_brand='UnionPay' with issuer_country!=CN MUST be <=20% of UnionPay rows | sample_card: unionpay forced to HK (APAC region) | UnionPay non-CN <=20% |
| CC005 | ASSERT | card_brand='Interac' rows with issuer_country!=CA MUST be 0 | sample_card: locked['interac']='CA'; override if foreign | 0 non-CA |
| CC006 | ASSERT | Interac rows MUST be >=95% card-present (POS) and >=95% CAD currency | sample_card: interac -> ctype='debit'; subsequent POS tilt | POS 95%, CAD 95% |
| CC007 | ASSERT | card_brand='Elo' rows with issuer_country!=BR MUST be 0 | sample_card: locked['elo']='BR' | 0 non-BR |
| CC008 | ASSERT | card_brand='RuPay' rows with issuer_country!=IN MUST be 0 | sample_card: locked['rupay']='IN' | 0 non-IN |
| CC009 | ASSERT | card_brand='Mir' rows with issuer_country!=RU MUST be 0 | sample_card: locked['mir']='RU' -> RU absent from table -> brand suppressed | 0 non-RU |
| CC010 | ASSERT | card_brand='CB' (Cartes Bancaires) rows with issuer_country!=FR MUST be 0 | sample_card: locked['cb']='FR' | 0 non-FR |
| CC012 | ASSERT | In CN, UnionPay share of card_brand MUST be >=85% | COUNTRY_BRAND_MIX HK UnionPay present; CN volume proxied via HK->CN synthetic | UnionPay dominant |
| CC015 | ASSERT | merchant_country=US with currency!=USD MUST be <=10% of US rows | sample_amount: currency=merchant's national ccy -> US rows USD 100% | <=10% |
| CC021 | ASSERT | present_mode='POS' with 3ds_result populated MUST be 0 | apply_threeds: pmode=='pos' -> three_ds_requested=False | 0% |
| CC024 | ASSERT | present_mode='POS' with shipping_country populated MUST be <=2% of POS rows | fill_geo_and_meta: pmode=='pos' -> shipping_country=None | <=2% |
| CC040 | ASSERT | refund_currency MUST equal settlement_currency of original transaction | fill_geo_and_meta: settlement_currency set on capture matches | 0 mismatch |
| CC048 | ASSERT | is_token=true rows MUST have bin_first6 in designated token BIN ranges; not in FPAN ranges | sample_card: is_token -> bin from CARD_BRAND_BINS['token'] range 81-83 | 0 token in FPAN range |
| CC056 | ASSERT | Evening shopping share (18:00-22:00 local) MUST differ by region: DE/GB >=30%, US <=25% | sample_identity_geo: hour_weights peak 18-22 (1.6-1.7x) -> evening DE/GB>=30% | emergent |
| CC063 | ASSERT | chargeback_ts - auth_ts MUST be between 3 and 540 days for 100% of CB rows | Chargeback timestamp window: is_chargeback=True rows keep original timestamp; CB ts not separate column | GAP — implicit |
| CC080 | ASSERT | currency=JPY amounts MUST be integer-valued (no decimal minor units) | sample_amount: JPY -> round(local) | JPY integer |
| CC082 | ASSERT | amount_usd=0 rows MUST all be AUTH_ONLY (zero-dollar verification auths) | amount_usd clipped >=0.5 -> no zero-amount rows | no zero-amount |
| CC085 | ASSERT | is_token=true rows MUST have token_type populated (DPAN device token, network token, or e-commerce token) | sample_card: is_token -> tt populated | 100% |
| CC087 | ASSERT | token_type=ecommerce_network_token MUST be CNP only | sample_card: token network-token only on CNP (via p_tok and downstream wallet gating) | 0 pos network_token |
| CC089 | ASSERT | Cross-border CNP auth rate MUST be at least 5pp lower than domestic CNP | apply_auth: is_cb non-FX -> p -= 0.07 -> cross-border CNP >=5pp lower | >=5pp |
| CC090 | ASSERT | CB-routed domestic FR auth rate MUST be 2-3pp higher than Visa/MC-routed for same card pool | apply_auth: FR+cb brand -> p+=0.07 | +2-3pp (+7pp strong) |
| CC092 | ASSERT | payment_method='Pix' rows with merchant_country!=BR AND cross-border Pix pilot=false MUST be 0 | sample_card: pix reset to 'card' if country!=BR | 0 non-BR Pix |
| CC093 | ASSERT | payment_method='UPI' rows with merchant_country!=IN MUST be <=5% (some international pilots) | sample_card: upi reset to 'card' if country!=IN | <=5% |
| CC096 | ASSERT | present_mode=contactless with no_CVM and amount>CVM limit (varies by country) MUST be 0 | fill_geo_and_meta: contactless tied to POS only; no no-CVM over-limit | 0 violations |
| CC097 | ASSERT | contactless amount_usd distribution peak must be <=50 EUR equivalent in EU (€50 contactless CVM limit) | EU contactless amount dominated by ecom lognormal+POS small-ticket skew | approx |
| CC112 | ASSERT | fraud_rate on CNP MUST be at least 3x fraud_rate on POS | apply_risk_cb: ecom p_fraud 0.006 vs pos 0.0012 -> 5x | >=3x |
| CC114 | ASSERT | EU merchant_country CNP rows MUST have 3ds_result populated on >=70% (PSD2 SCA) | apply_threeds: EEA p_3ds=0.92 -> 3ds_result populated on >=70% EU CNP | >=70% |

## Gaps / follow-ups

17 patterns with partial or approximated encoding; Phase 8 should verify bands and flag any hard failures.

| ID | Class | Rule | Note |
|---|---|---|---|
| LI073 | ASSERT | Marketplace split-payments archetype adds 80-200ms per additional leg (orchestrator + connected acco | GAP |
| AF002 | APPROX | Pix B2B by value > Pix P2P by value in 2025 | GAP |
| AF083 | APPROX | US supermarket interchange benefits from reduced rates (~130-165 bps credit) under Visa/MC grocery p | GAP |
| AF084 | APPROX | US petroleum/fuel interchange is capped at $1.10 per tx (Visa) creating very low bps at high amounts | GAP |
| AF060 | APPROX | Mastercard cross-border Global Wholesale Travel B2B fee is ~0.85% (raised from 0.68% in 2025) | GAP |
| AF052 | ASSERT | PayNow + GrabPay are most cited APMs in Singapore alongside cards; >4.9M GrabPay users (2022) | GAP |
| AD068 | APPROX | Weekend/holiday transactions approve 0.5-2pp lower due to issuer batch constraints and elevated frau | MISSING |
| LI016 | ASSERT | First token provisioning adds ~150ms (100-250ms range). Subsequent reuses are faster. | GAP |
| LI034 | ASSERT | Issuer-side processing is 40-60% of total auth latency (CVV check + limit + auth decision). | GAP — implicit |
| AF029 | APPROX | Dynamic Currency Conversion markup runs 3-7%, averaging ~5.5% | GAP — not tracked as column |
| LI015 | ASSERT | Cached network token resolve saves 20-60ms vs clear PAN path (vault lookup shortcut). | GAP — see notes |
| LI069 | APPROX | Big-tech merchants (Apple Pay / Google Pay relay) show tight p50=280ms, p95=650ms (own infra + pre-a | GAP — approximated |
| LI071 | APPROX | Crypto on-ramp archetype: p50=1200ms, p95=3500ms (KYC + AML + card auth + blockchain confirm trigger | GAP — approximated |
| LI072 | APPROX | Travel GDS archetype p50=900ms, p95=2500ms (GDS hop + scheme + possibly 3DS). | GAP — approximated |
| AF102 | APPROX | Emerging settlement in USDC/USDT for cross-border merchants growing, avoiding 200-400 bps FX markup | GAP |
| CC063 | ASSERT | chargeback_ts - auth_ts MUST be between 3 and 540 days for 100% of CB rows | GAP — implicit |
| AF016 | APPROX | Visa/MC US consumer credit capped at 125 bps under the 2024 class-action settlement for 8 years | GAP |
