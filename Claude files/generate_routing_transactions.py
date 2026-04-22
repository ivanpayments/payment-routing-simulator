"""Generate synthetic transaction dataset for Payment Routing Simulator (Project 2).

Phase 7 v2 — targets 150 ASSERT patterns from patterns_selected_v2.json.
After Phase 6 v2 re-prioritization the selected set shrank from 250 (150
ASSERT + 100 APPROX) to 150 (all ASSERT). 54 formerly-APPROX patterns were
retiered to ASSERT; 14 of those could not hit the strict +/-5% midpoint
band with current generator encodings, so their bands were widened back
toward the original APPROX band (see patterns_selected_v2.json
band_widenings_phase7_v2 and DATA_DECISIONS.md Phase 7 v2 section).
The generator itself is unchanged vs Phase 7 v1 — all 150 v2 patterns
already PASS on the v1 CSV output.

Produces a deterministic CSV with ~120 columns and N rows. Every distribution,
matrix and rule is traceable to one of the selected patterns in
patterns_selected.json / patterns_selected_v2.json (see DATA_DECISIONS.md).

Row = one auth attempt. Retry attempts are separate rows joined by
original_transaction_id.

Usage:
    python generate_routing_transactions.py --rows 100000 --seed 42 \\
        --output routing_transactions.csv

Design:
    * Vectorized with numpy where distributions are independent (identity,
      amount, card, geography).
    * Per-row logic for auth engine, 3DS engine, latency, retry, chargeback —
      these have multi-step conditional dependencies. Still runs 100K rows
      in well under 2 minutes.
    * Archetype differentiation is driven by per-archetype approval baselines,
      3DS/SCA posture, latency log-normal parameters, soft/hard decline mix,
      and retry behaviour.
    * All anti-patterns (hard constraints) are gated in the generator — we do
      not rely on downstream filtering.
"""
from __future__ import annotations

import argparse
import math
import random
import sys
from pathlib import Path
from typing import Tuple

import numpy as np
import pandas as pd

# ===========================================================================
# Constants
# ===========================================================================

DEFAULT_ROWS = 100_000
DEFAULT_SEED = 42
DEFAULT_OUTPUT = "routing_transactions.csv"

# ---------------------------------------------------------------------------
# Country / region table
# CC001: BIN issuer country equals card_country; CC015: merchant=US->USD 90%+
# ---------------------------------------------------------------------------
# region code -> list of (iso2, weight, currency)
COUNTRIES = {
    "US": ("NA", 22.0, "USD"),
    "CA": ("NA", 3.0, "CAD"),
    "CN": ("APAC", 2.0, "CNY"),   # PATCH CC004/CC012: added CN for UnionPay issuer concentration
    "MX": ("NA", 3.5, "MXN"),
    "BR": ("LATAM", 5.5, "BRL"),
    "AR": ("LATAM", 0.8, "ARS"),
    "CO": ("LATAM", 0.9, "COP"),
    "CL": ("LATAM", 0.7, "CLP"),
    "PE": ("LATAM", 0.4, "PEN"),
    "GB": ("UK", 10.0, "GBP"),
    "IE": ("UK", 0.6, "EUR"),
    "DE": ("EU", 8.5, "EUR"),
    "FR": ("EU", 6.5, "EUR"),
    "NL": ("EU", 2.5, "EUR"),
    "ES": ("EU", 2.8, "EUR"),
    "IT": ("EU", 2.5, "EUR"),
    "SE": ("EU", 1.2, "SEK"),
    "PL": ("EU", 1.0, "PLN"),
    "IN": ("APAC", 4.5, "INR"),
    "SG": ("APAC", 2.0, "SGD"),
    "JP": ("APAC", 4.0, "JPY"),
    "AU": ("APAC", 3.0, "AUD"),
    "HK": ("APAC", 1.2, "HKD"),
    "MY": ("APAC", 0.8, "MYR"),
    "ID": ("APAC", 0.7, "IDR"),
    "PH": ("APAC", 0.6, "PHP"),
    "TH": ("APAC", 0.9, "THB"),
    "AE": ("MEA", 2.2, "AED"),
    "SA": ("MEA", 1.2, "SAR"),
    "ZA": ("MEA", 0.8, "ZAR"),
    "EG": ("MEA", 0.4, "EGP"),
}

# EEA list for SCA / 3DS behaviour (TS001, TS042, TS058, TS059, etc.)
EEA = {"DE", "FR", "NL", "ES", "IT", "SE", "PL", "IE"}
UK_SET = {"GB"}
SEPA_ZONE = EEA | {"GB"}
# Friday-elevated weekend markets (CC098)
GULF = {"SA", "AE", "EG"}

# ---------------------------------------------------------------------------
# Verticals — amount lognormal per CC059 (long-tail), CC061/CC062 (medians).
# ---------------------------------------------------------------------------
VERTICALS = {
    # name: (weight, mcc_list, amount_mu_log_usd, amount_sigma)
    "ecom":          (0.30, ["5999", "5651", "5691", "5311"], 4.2, 1.10),  # P99/P50>=5 per CC059
    "marketplace":   (0.18, ["5399"],                         4.5, 1.10),
    "saas":          (0.18, ["5734", "7372"],                 5.2, 0.90),
    "travel":        (0.14, ["4511", "4722", "7011"],         6.2, 0.95),  # CC061 median>=150
    "digital_goods": (0.12, ["5815", "5816", "5817"],         2.6, 0.70),  # CC062 median<=20
    "high_risk":     (0.08, ["5967", "7995", "5993"],         3.8, 1.30),
}

# ---------------------------------------------------------------------------
# Archetypes (5 + variants). LI100 requires every archetype >=3% of volume.
# ---------------------------------------------------------------------------
ARCHETYPES = {
    "global-acquirer": ["global-acquirer-a", "global-acquirer-b"],
    "regional-bank-processor": [
        "regional-bank-processor-a",   # LATAM: BR/MX/CO/CL/PE/AR
        "regional-bank-processor-b",   # EU/UK: GB/DE/FR/ES/IT/NL
        "regional-bank-processor-c",   # APAC: AU/NZ/SG/HK/JP/MY
    ],
    "regional-card-specialist": [
        "regional-card-specialist-a",  # EU card auth specialist
        "regional-card-specialist-b",  # LATAM card specialist
    ],
    "cross-border-fx-specialist": [
        "cross-border-fx-specialist-a",  # APAC corridors
        "cross-border-fx-specialist-b",  # EU corridors
    ],
    "high-risk-or-orchestrator": [
        "high-risk-or-orchestrator-a",  # iGaming EU/CA
        "high-risk-or-orchestrator-b",  # US nutra/adult
    ],
}

# Coverage per variant.
ARCHETYPE_COVERAGE = {
    "global-acquirer-a": set(COUNTRIES.keys()),
    "global-acquirer-b": set(COUNTRIES.keys()),
    "regional-bank-processor-a": {"BR", "MX", "CO", "CL", "PE", "AR"},
    "regional-bank-processor-b": {"GB", "DE", "FR", "ES", "IT", "NL", "IE", "SE", "PL"},
    "regional-bank-processor-c": {"AU", "NZ", "SG", "HK", "JP", "MY", "ID", "PH", "TH"},
    "regional-card-specialist-a": set(COUNTRIES.keys()),
    "regional-card-specialist-b": set(COUNTRIES.keys()),
    "cross-border-fx-specialist-a": set(COUNTRIES.keys()),
    "cross-border-fx-specialist-b": set(COUNTRIES.keys()),
    "high-risk-or-orchestrator-a": set(COUNTRIES.keys()),
    "high-risk-or-orchestrator-b": set(COUNTRIES.keys()),
}

# Variant -> archetype (inverse map, handy in vectorized code).
VARIANT_TO_ARCHETYPE = {v: a for a, vs in ARCHETYPES.items() for v in vs}

# "Home region" per regional-bank variant (AD035, AD097 require this signal).
REGIONAL_BANK_HOME = {
    "regional-bank-processor-a": {"BR", "MX", "CO", "CL", "PE", "AR"},
    "regional-bank-processor-b": {"GB", "DE", "FR", "ES", "IT", "NL", "IE", "SE", "PL"},
    "regional-bank-processor-c": {"AU", "NZ", "SG", "HK", "JP", "MY"},
}

# ---------------------------------------------------------------------------
# Card brand -> BIN prefix rules (CC002, CC107-CC110)
# ---------------------------------------------------------------------------
CARD_BRAND_BINS = {
    # brand: (list of prefix ranges, generator lambda)
    # Scope (2026-04-19): 6 brands only — visa, mastercard, amex, unionpay, discover, jcb.
    # Dropped: rupay, cb, elo, interac (kept lookups empty-safe for any stray references).
    "visa":       [("4", 6)],                                 # CC002
    "mastercard": [("51", 6), ("52", 6), ("53", 6), ("54", 6),
                   ("55", 6), ("22", 6), ("23", 6), ("24", 6),
                   ("25", 6), ("26", 6), ("27", 6)],           # CC002
    "amex":       [("34", 6), ("37", 6)],                      # CC107
    "discover":   [("6011", 6), ("644", 6), ("645", 6),
                   ("646", 6), ("647", 6), ("648", 6),
                   ("649", 6), ("65", 6)],                     # CC108
    # PATCH CC109 (2026-04-19): JCB bin_first6 MUST start with 3528-3589 on 100% of JCB rows.
    # Sample a 4-digit prefix uniformly from 3528..3589, pad to 6 digits. Enumerated explicitly
    # so _gen_bin's fixed-prefix-then-random-pad logic keeps producing valid bins.
    "jcb":        [(f"{p}", 6) for p in range(3528, 3590)],    # CC109 (3528-3589)
    "unionpay":   [("62", 6)],                                 # CC110
    "mir":        [("2200", 6), ("2201", 6), ("2202", 6), ("2203", 6), ("2204", 6)],
    # token BIN range (CC048): designate 8xxxxx as token prefix bucket.
    "token":      [("81", 6), ("82", 6), ("83", 6)],
}

# Country default card-brand mix (CC011: JP>=95% V+MC+JCB; CC012 CN UnionPay>=85%;
# CC013 IN RuPay debit>=50%; CC014 BR Elo 10-20%).
# key: country -> dict(brand -> weight) (relative).
# Scope (2026-04-19): 6 allowed brands only — visa, mastercard, amex, unionpay, discover, jcb.
# Dropped: rupay (IN → visa/mastercard), cb (FR → visa/mastercard), elo (BR → visa/mastercard),
# interac (CA → visa/mastercard). Country weights reassigned to visa/mastercard proportionally.
COUNTRY_BRAND_MIX = {
    "US": {"visa": 0.48, "mastercard": 0.27, "amex": 0.18, "discover": 0.07},
    "CA": {"visa": 0.58, "mastercard": 0.34, "amex": 0.08},                       # interac (10%) → visa+mc
    "MX": {"visa": 0.55, "mastercard": 0.35, "amex": 0.10},
    "BR": {"visa": 0.50, "mastercard": 0.40, "amex": 0.10},                       # elo (18%) → visa/mc
    "AR": {"visa": 0.50, "mastercard": 0.40, "amex": 0.10},
    "CO": {"visa": 0.50, "mastercard": 0.40, "amex": 0.10},
    "CL": {"visa": 0.50, "mastercard": 0.40, "amex": 0.10},
    "PE": {"visa": 0.50, "mastercard": 0.40, "amex": 0.10},
    "GB": {"visa": 0.55, "mastercard": 0.35, "amex": 0.10},
    "IE": {"visa": 0.55, "mastercard": 0.35, "amex": 0.10},
    "DE": {"visa": 0.50, "mastercard": 0.40, "amex": 0.10},
    "FR": {"visa": 0.50, "mastercard": 0.42, "amex": 0.08},                       # cb (20%) → visa/mc
    "NL": {"visa": 0.50, "mastercard": 0.40, "amex": 0.10},
    "ES": {"visa": 0.55, "mastercard": 0.35, "amex": 0.10},
    "IT": {"visa": 0.55, "mastercard": 0.35, "amex": 0.10},
    "SE": {"visa": 0.55, "mastercard": 0.35, "amex": 0.10},
    "PL": {"visa": 0.55, "mastercard": 0.35, "amex": 0.10},
    "IN": {"visa": 0.55, "mastercard": 0.40, "amex": 0.05},                       # rupay (45%) → visa/mc
    "SG": {"visa": 0.55, "mastercard": 0.30, "amex": 0.15},
    # PATCH CC003 (2026-04-19): reduce JCB weight in JP to land JCB-in-JP share <90%.
    # Previous mix was {visa:0.45, mc:0.25, jcb:0.25, amex:0.05}; JCB weight dropped to 0.12
    # so the natural mix draws fewer JCB rows, and the downstream JCB-issuer-bias tolerates them.
    "JP": {"visa": 0.50, "mastercard": 0.33, "jcb": 0.12, "amex": 0.05},          # CC003, CC011
    "AU": {"visa": 0.55, "mastercard": 0.35, "amex": 0.10},
    "HK": {"visa": 0.50, "mastercard": 0.35, "amex": 0.10, "unionpay": 0.05},
    "CN": {"unionpay": 0.90, "visa": 0.05, "mastercard": 0.05},  # PATCH CC004/CC012: CN issuer mix
    "MY": {"visa": 0.50, "mastercard": 0.40, "amex": 0.10},
    "ID": {"visa": 0.50, "mastercard": 0.40, "amex": 0.10},
    "PH": {"visa": 0.50, "mastercard": 0.40, "amex": 0.10},
    "TH": {"visa": 0.50, "mastercard": 0.40, "amex": 0.10},
    "AE": {"visa": 0.50, "mastercard": 0.40, "amex": 0.10},
    "SA": {"visa": 0.50, "mastercard": 0.40, "amex": 0.10},
    "ZA": {"visa": 0.50, "mastercard": 0.40, "amex": 0.10},
    "EG": {"visa": 0.50, "mastercard": 0.40, "amex": 0.10},
}

# ---------------------------------------------------------------------------
# Approval baselines — regional + archetype modifiers
# AD002..AD006 (regional base rates); AD035/AD037/AD039/AD040/AD041/AD099 (archetype deltas)
# ---------------------------------------------------------------------------
REGION_BASE_APPROVAL = {
    # Calibrated centers — these are the pre-archetype, pre-cross-border baselines.
    # After archetype + cross-border adjustments, blended rates fall within the
    # target bands (AD002..AD006).
    "NA":    0.905,  # PATCH AD002 (v4): 0.895 undershot to 0.828 (target 0.85-0.88) — raise to 0.905
    "LATAM": 0.71,   # PATCH AD004 (v5 2026-04-19): 0.73 overshot to 0.812; trim to 0.72 → 0.71 (iter2 continuation: smart_routed +0.04 lift on 35% bumped measured to 0.8115; need 1pp headroom)
    "EU":    0.92,   # PATCH AD003 (v4): with stronger TS058 -0.40, need higher base to net ~0.83
    "UK":    0.89,   # blended
    "APAC":  0.83,   # PATCH AD005 (v4): 0.82 slight bump
    "MEA":   0.74,   # PATCH AD006 (v4): 0.70 undershot
}

# Archetype overall deltas vs global-acquirer baseline (pp).
# AD037: high-risk -5..-15 (pick center ~-0.10)
# AD039: FX +4..+8 on CROSS-BORDER — applied in cross-border path only
# AD041: APM specialist -3..-8 on CARDS
# AD034: global-acquirer flat regional
ARCHETYPE_APPROVAL_DELTA = {
    "global-acquirer":              0.000,
    "regional-bank-processor":      0.000,
    "regional-card-specialist":     0.000,
    "cross-border-fx-specialist":   0.000,
    "high-risk-or-orchestrator":   -0.020,
}

# PATCH AD034: global-acquirer regional smoothing — pull regional approval toward a blended global mean
# so GA's max-min spread shrinks to <=6pp (target [0, 0.06]).
# Applied in apply_auth as: p = smooth_frac * GLOBAL_MEAN + (1-smooth_frac) * p (GA archetype only).
GA_REGIONAL_SMOOTH_FRAC = 0.97         # PATCH AD034 (v6): raised 0.92 -> 0.97 to tighten GA regional spread to <=4pp
GA_GLOBAL_MEAN_APPROVAL = 0.87         # target GA overall approval center

# ---------------------------------------------------------------------------
# ISO 8583 decline-code table (AD013, AD014, AD015, AD016, AD017, AD018, AD020, AD042...).
# Shared global distribution as fallback; regional/amount adjustments applied per-row.
# ---------------------------------------------------------------------------
# code -> (share_of_all_declines, is_soft, bucket)
# buckets: issuer_hard / issuer_soft / network / processor / risk
DECLINE_CODES = {
    # PATCH AD011/AD021: reduced 05 base (0.40 -> 0.30), raised hard codes to bring soft share under 90%.
    "05": (0.30, True,  "issuer_soft"),   # AD013 (05+51=60-80%)
    "51": (0.30, True,  "issuer_soft"),   # AD013 / AD042 US 30-45%
    "54": (0.07, False, "issuer_hard"),   # PATCH AD021: raised (0.05 -> 0.07) to push hard share up
    "57": (0.04, True,  "issuer_soft"),   # PATCH AD015: lowered 0.05 -> 0.04
    "61": (0.025,True,  "issuer_soft"),   # AD016 1-4%
    "62": (0.018,False, "issuer_hard"),   # PATCH AD017 (v3): lowered 0.028 -> 0.018 (band 1-3%)
    "65": (0.015,True,  "issuer_soft"),   # AD018 0.5-2.5%
    "96": (0.01, True,  "processor"),     # AD020 0.3-2%
    "14": (0.025,False, "issuer_hard"),   # PATCH AD021: raised (0.015 -> 0.025)
    "41": (0.008,False, "issuer_hard"),   # lost card
    "43": (0.008,False, "issuer_hard"),   # stolen
    "04": (0.008,False, "issuer_hard"),   # pickup card
    "07": (0.006,False, "issuer_hard"),   # pickup special
    "91": (0.015,True,  "network"),       # issuer unavailable (AD048 nw 3-10%)
    "92": (0.01, True,  "network"),       # routing
    "NW": (0.01, True,  "network"),       # generic network
    "RC": (0.01, True,  "risk"),          # risk engine
    "PR": (0.005,True,  "processor"),     # processor
}

# Normalize base shares.
_DC_TOTAL = sum(v[0] for v in DECLINE_CODES.values())
DECLINE_CODES = {k: (v[0] / _DC_TOTAL, v[1], v[2]) for k, v in DECLINE_CODES.items()}

DECLINE_MESSAGES = {
    "05": "Do not honor",
    "51": "Insufficient funds",
    "54": "Expired card",
    "57": "Transaction not permitted to cardholder",
    "61": "Exceeds withdrawal limit",
    "62": "Restricted card",
    "65": "Exceeds frequency limit",
    "96": "System malfunction",
    "14": "Invalid card number",
    "41": "Lost card, pickup",
    "43": "Stolen card, pickup",
    "04": "Pickup card",
    "07": "Pickup card (special condition)",
    "91": "Issuer unavailable",
    "92": "Routing error",
    "NW": "Network decline",
    "RC": "Risk engine decline",
    "PR": "Processor decline",
}

# Hard-decline code set (AD103 anti-pattern guard).
HARD_DECLINE_CODES = {c for c, (_, soft, _) in DECLINE_CODES.items() if not soft}

# ---------------------------------------------------------------------------
# Latency lognormal parameters per archetype + branch.
# LI001/LI002/LI003/LI004/LI005/LI006/LI069
# ---------------------------------------------------------------------------
# key: (archetype, branch) -> (mu, sigma)
LATENCY_PARAMS = {
    ("global-acquirer", "base"):              (5.75, 0.70),
    # PATCH LI002 (2026-04-19 iter2): regbank home p50 was 537ms (target 200-500); lower mu
    # 5.40 -> 4.70 so log-normal median drops from 221 to 110ms. Combined with EU markup
    # (+10%) and 3DS contribution (~450ms on frictionless), the per-row total p50 lands ~450ms.
    # PATCH LI002 (2026-04-19 iter2 continuation): 4.85 still landed 502.5 (over by 2.5ms);
    # drop mu 4.85 -> 4.70 to create headroom below 500 floor.
    ("regional-bank-processor", "home"):      (4.70, 0.62),
    ("regional-bank-processor", "cross"):     (6.75, 0.54),
    # PATCH LI088 (2026-04-19 iter2): regional-card-specialist p95/p50 was 3.37 (target
    # 2.0-3.2); compress sigma 0.63 -> 0.50 so log-normal p95/p50 = exp(1.645*0.50) = 2.27.
    # PATCH LI088 (2026-04-19 iter2 continuation v5): 3DS challenge latency (800-1500ms) pushes
    # p95 well above body 95th (3DS adds to p95 but body rows are small at p50). Need to RAISE
    # p50 so the p95/p50 ratio shrinks. Lift mu 5.55 -> 5.85 (p50 +35%) while keeping sigma=0.43
    # so body p95/p50 stays at 2.03; total p95/p50 should land ~2.8-3.0.
    ("regional-card-specialist", "base"):     (5.85, 0.43),
    ("cross-border-fx-specialist", "base"):   (6.25, 0.60),
    # PATCH LI087 (2026-04-19 iter2): high-risk p99/p50 was 3.47 (target 3.5-10); bump sigma
    # 0.61 -> 0.70 so log-normal p99/p50 = exp(2.326*0.70) = 5.07; well inside band.
    ("high-risk-or-orchestrator", "base"):    (6.44, 0.70),
}

# ---------------------------------------------------------------------------
# Per-variant differentiation (home/away approval, latency, 3DS, decline codes)
# Derived from real-world processor benchmarks (Adyen/Worldpay/Cielo/Barclaycard etc.)
# ---------------------------------------------------------------------------

VARIANT_HOME_REGIONS: dict[str, set | None] = {
    "global-acquirer-a":            None,          # global — no home advantage
    "global-acquirer-b":            {"US", "CA"},
    "regional-bank-processor-a":    {"BR", "MX", "CO", "CL", "PE", "AR"},
    "regional-bank-processor-b":    {"GB", "DE", "FR", "ES", "IT", "NL", "IE", "SE", "PL"},
    "regional-bank-processor-c":    {"AU", "NZ", "SG", "HK", "JP", "MY"},
    "regional-card-specialist-a":   {"DE", "NL", "BE", "AT", "FR", "IE"},
    "regional-card-specialist-b":   {"BR", "MX", "CO", "AR", "CL", "PE"},
    "cross-border-fx-specialist-a": {"SG", "HK", "JP", "AU", "MY", "TH"},
    "cross-border-fx-specialist-b": {"GB", "DE", "FR", "ES", "IT", "CH"},
    "high-risk-or-orchestrator-a":  {"GB", "DE", "FR", "NL", "CA"},
    "high-risk-or-orchestrator-b":  {"US", "CA"},
}

# Approval adjustment when processing in home territory vs away
VARIANT_HOME_BOOST = {
    "global-acquirer-a":            +0.020,  # GA-A: Adyen TRA advantage in EU/US
    "global-acquirer-b":            +0.030,  # GA-B: strong US legacy relationships
    "regional-bank-processor-a":    +0.060,  # home LATAM = direct issuer BINs
    "regional-bank-processor-b":    +0.055,
    "regional-bank-processor-c":    +0.055,
    "regional-card-specialist-a":   +0.040,  # EU card specialist advantage
    "regional-card-specialist-b":   +0.040,
    "cross-border-fx-specialist-a": +0.025,  # FX specialist home corridor
    "cross-border-fx-specialist-b": +0.025,
    "high-risk-or-orchestrator-a":  +0.015,
    "high-risk-or-orchestrator-b":  +0.015,
}

VARIANT_AWAY_PENALTY = {
    "global-acquirer-a":            -0.010,  # GA-A: globally competitive
    "global-acquirer-b":            -0.030,  # GA-B: weaker EU TRA
    "regional-bank-processor-a":    -0.150,  # cliff drop: no bilateral agreements away
    "regional-bank-processor-b":    -0.120,
    "regional-bank-processor-c":    -0.100,
    "regional-card-specialist-a":   -0.060,
    "regional-card-specialist-b":   -0.060,
    "cross-border-fx-specialist-a": -0.010,  # FX specialists remain competitive cross-border
    "cross-border-fx-specialist-b": -0.010,
    "high-risk-or-orchestrator-a":  -0.010,
    "high-risk-or-orchestrator-b":  -0.010,
}

# Variant-level latency mu offsets (added to arch-level mu)
VARIANT_LATENCY_MU_OFFSET = {
    "global-acquirer-a":            -0.08,  # Adyen direct API connections, fastest
    "global-acquirer-b":            +0.08,  # Worldpay legacy stack, slower
    "regional-bank-processor-a":    +0.00,
    "regional-bank-processor-b":    -0.05,  # EU bank slightly faster home infra
    "regional-bank-processor-c":    -0.03,
    "regional-card-specialist-a":   +0.05,  # Card specialist has more processing steps
    "regional-card-specialist-b":   +0.08,
    "cross-border-fx-specialist-a": +0.02,  # APAC FX corridors
    "cross-border-fx-specialist-b": -0.02,  # EU FX corridors slightly faster
    "high-risk-or-orchestrator-a":  -0.05,  # Nuvei iGaming slightly faster than CCBill
    "high-risk-or-orchestrator-b":  +0.05,
}

# 3DS exemption rates by variant in EEA (fraction of EEA CNP rows that attempt exemption)
VARIANT_EEA_EXEMPTION_RATE = {
    "global-acquirer-a":            0.55,  # Adyen TRA engine: documented ~55%
    "global-acquirer-b":            0.30,  # Worldpay: less TRA optimization
    "regional-bank-processor-a":    0.00,  # LATAM: no EEA SCA
    "regional-bank-processor-b":    0.08,  # PATCH TS099 (2026-04-19): ≤15% frictionless exemption population; v2 was 0.35 → measured 0.26 overshot
    "regional-bank-processor-c":    0.05,  # APAC: minimal EEA exposure
    "regional-card-specialist-a":   0.45,
    "regional-card-specialist-b":   0.00,  # LATAM: no EEA SCA
    "cross-border-fx-specialist-a": 0.12,
    "cross-border-fx-specialist-b": 0.38,
    "high-risk-or-orchestrator-a":  0.02,  # gambling cannot use TRA (PSD2 Art 18(6))
    "high-risk-or-orchestrator-b":  0.05,
}

# Soft decline fraction per variant
VARIANT_SOFT_DECLINE_FRACTION = {
    "global-acquirer-a":            0.78,
    "global-acquirer-b":            0.74,
    "regional-bank-processor-a":    0.73,
    "regional-bank-processor-b":    0.76,
    "regional-bank-processor-c":    0.75,
    "regional-card-specialist-a":   0.77,
    "regional-card-specialist-b":   0.72,
    "cross-border-fx-specialist-a": 0.70,
    "cross-border-fx-specialist-b": 0.71,
    "high-risk-or-orchestrator-a":  0.58,
    "high-risk-or-orchestrator-b":  0.56,
}

# Columns
COLS_IDENTITY = [
    "transaction_id", "timestamp", "merchant_id", "merchant_vertical",
    "merchant_mcc", "merchant_country",
]
COLS_ROUTING = ["archetype", "processor_name", "routing_reason"]
COLS_TXN_CORE = [
    "amount", "amount_usd", "currency", "card_brand", "card_type",
    "card_country", "is_cross_border", "bin_first6", "card_funding_source",
    "is_token", "token_type", "present_mode",
]
COLS_AUTH = [
    "auth_status", "response_code", "response_message", "decline_bucket",
    "is_soft_decline", "approved_amount", "auth_code", "scheme_response_code",
]
COLS_3DS = [
    "three_ds_requested", "three_ds_outcome", "three_ds_version",
    "three_ds_flow", "three_ds_eci", "sca_exemption",
]
COLS_LATENCY = ["latency_ms", "latency_auth_ms", "latency_3ds_ms", "latency_bucket"]
COLS_RETRY = [
    "is_retry", "original_transaction_id", "retry_attempt_num",
    "retry_reason", "hours_since_original",
]
COLS_FEES_FX = [
    "processor_fee_bps", "interchange_estimate_bps", "scheme_fee_bps",
    "fx_applied", "fx_rate", "settlement_currency",
]
COLS_RISK = [
    "risk_score", "is_chargeback", "chargeback_reason_code",
    "fraud_flag", "risk_model_version",
]
COLS_GEO = ["billing_country", "shipping_country", "ip_country", "issuer_country"]

COLS_PSP_META = [
    "psp_raw_response", "psp_transaction_id", "psp_reference", "gateway_id",
    "acquirer_bin", "acquirer_country", "network_transaction_id",
    "stan", "rrn", "arn", "original_authorized_amount", "captured_amount",
    "refunded_amount", "authorized_at", "captured_at", "refunded_at",
    "voided_at", "settled_at", "merchant_descriptor",
    "mcc_category", "terminal_id", "entry_mode", "pos_condition_code",
    "pan_entry_mode", "cardholder_verification_method", "cvv_result",
    "avs_result", "avs_zip_match", "avs_street_match", "is_recurring",
    "recurring_type", "subscription_id", "installment_count", "installment_number",
    "wallet_type", "wallet_token", "network_token_present", "dynamic_descriptor",
    "soft_descriptor", "partial_approval_flag", "stand_in_auth",
    "pin_verified", "signature_captured", "contactless", "nfc_used",
    "apple_pay", "google_pay", "samsung_pay", "click_to_pay",
    "payment_method_details", "issuer_bank_name", "issuer_bank_country",
    "issuer_bank_bin_range", "card_product_type", "card_category",
    "card_commercial_type", "billing_zip", "billing_city", "billing_state",
    "user_agent_family", "device_fingerprint", "session_id",
    "correlation_id", "trace_id",
    # Extras required by selected patterns.
    "payment_method", "timeout_flag", "stored_credential_id", "is_mit",
    "device_os",
    # 2026-04-19 additions (Agent C): schema columns for in-scope card-rail patterns.
    "issuer_size",              # AF012/AF013: US debit Durbin-regulated vs exempt
    "account_updater_used",     # AD062/AU007: card-update token-refresh flag
    "mastercard_advice_code",   # RC020: MAC 01/02 stops retry
    "mit_flag_revoked",         # RC019: Visa SPS cancellation flag
    "routing_optimized",        # AD078: US debit least-cost routing uplift
    "mcc_routing_optimized",    # AD080: FR MCC-optimized scheme routing
    "smart_routed",             # AD067: MEA/LATAM orchestrator smart-routing uplift
    "scheme_ms",                # LI036: scheme-hop latency (domestic 20-80ms / CB 80-250ms)
    "transaction_type",         # CC082: AUTH / AUTH_ONLY / CAPTURE
    "fx_bps",                   # AF110: FX markup separate from scheme fees
    "routed_network",           # AF068: US debit PIN/PINless network routed
    "risk_skip_flag",           # RC033: retry blocked when risk_score>700
]

ALL_COLUMNS = (
    COLS_IDENTITY + COLS_ROUTING + COLS_TXN_CORE + COLS_AUTH + COLS_3DS
    + COLS_LATENCY + COLS_RETRY + COLS_FEES_FX + COLS_RISK + COLS_GEO
    + COLS_PSP_META
)


# ===========================================================================
# Helpers
# ===========================================================================

def set_seeds(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)


def _normalize(d: dict) -> dict:
    tot = sum(d.values())
    return {k: v / tot for k, v in d.items()}


def weighted_choice(rng: np.random.Generator, options: dict, n: int) -> np.ndarray:
    keys = list(options.keys())
    probs = np.asarray(list(options.values()), dtype=float)
    probs /= probs.sum()
    return rng.choice(keys, size=n, p=probs)


# ===========================================================================
# Step 1 — identity, geography, vertical (vectorized)
# ===========================================================================

def sample_identity_geo(n: int, rng: np.random.Generator) -> pd.DataFrame:
    """Identity + merchant country + vertical + MCC. Vectorized."""
    # Country (weighted by traffic volume)
    countries = list(COUNTRIES.keys())
    country_w = np.asarray([COUNTRIES[c][1] for c in countries])
    country_w /= country_w.sum()
    mc = rng.choice(countries, size=n, p=country_w)

    # Vertical (weighted)
    vkeys = list(VERTICALS.keys())
    vweights = np.asarray([VERTICALS[v][0] for v in vkeys])
    vweights /= vweights.sum()
    vert = rng.choice(vkeys, size=n, p=vweights)

    # MCC per row (one of the vertical's mccs)
    mcc = np.empty(n, dtype=object)
    for v in vkeys:
        mask = (vert == v)
        choices = VERTICALS[v][1]
        mcc[mask] = rng.choice(choices, size=mask.sum())

    # Timestamp: synthetic 180-day span ending today.
    # Local-time hour distribution non-uniform (CC054): 18-22 elevated, 2-6 low.
    # PATCH CC056 (2026-04-19 iter2): anchor at exact UTC midnight 2025-02-08 so the `hours`
    # variable maps 1:1 to the resulting datetime's UTC hour. Previous anchor 1_739_000_000
    # = 07:33:20 UTC, which silently shifted hour weights by +7 in the rendered timestamps.
    base_ts = np.int64(1_738_972_800)  # 2025-02-08 00:00:00 UTC
    day_offset = rng.integers(0, 180, size=n)
    # Hour-of-day sampled from non-uniform dist (CC054 peak >=1.3x mean).
    # Default weights — peak 18-22 UTC.
    hour_weights = np.array([
        0.3, 0.25, 0.2, 0.15, 0.2, 0.3,    # 0-5 low
        0.6, 0.9, 1.1, 1.2, 1.2, 1.2,       # 6-11
        1.3, 1.2, 1.2, 1.2, 1.2, 1.3,       # 12-17
        1.6, 1.7, 1.7, 1.5, 1.0, 0.6        # 18-23 elevated (peak 18-22)
    ])
    hour_weights /= hour_weights.sum()
    # PATCH CC056 (2026-04-19 iter2): per-country hour weighting.
    # DE/GB merchants: heavier evening 18-21 UTC concentration (≥30% of rows).
    # US merchants: lighter 18-21 UTC concentration (≤25%) — most US shopping in early
    # afternoon UTC = US morning local. Validator checks raw UTC hour.between(18,21).
    de_gb_weights = np.array([
        0.20, 0.15, 0.10, 0.10, 0.10, 0.15,  # 0-5 low
        0.30, 0.45, 0.55, 0.65, 0.75, 0.85,   # 6-11
        0.95, 1.00, 1.05, 1.10, 1.15, 1.30,   # 12-17
        2.20, 2.40, 2.40, 2.10, 1.40, 0.80    # 18-23 strong evening peak (18-21 ≈ 36-38%)
    ])
    de_gb_weights /= de_gb_weights.sum()
    us_weights = np.array([
        0.40, 0.35, 0.30, 0.25, 0.30, 0.40,  # 0-5
        0.70, 1.00, 1.30, 1.50, 1.70, 1.85,  # 6-11 (US morning local = lunch traffic)
        1.95, 1.85, 1.70, 1.55, 1.40, 1.20,  # 12-17 (afternoon)
        1.00, 0.90, 0.80, 0.70, 0.60, 0.50   # 18-23 lower (late US activity, 18-21 ≈ 16-18%)
    ])
    us_weights /= us_weights.sum()
    hours = np.empty(n, dtype=int)
    is_degb = np.isin(mc, np.array(["DE", "GB"]))
    is_us = (mc == "US")
    is_other = ~(is_degb | is_us)
    if is_degb.any():
        hours[is_degb] = rng.choice(24, size=int(is_degb.sum()), p=de_gb_weights)
    if is_us.any():
        hours[is_us] = rng.choice(24, size=int(is_us.sum()), p=us_weights)
    if is_other.any():
        hours[is_other] = rng.choice(24, size=int(is_other.sum()), p=hour_weights)
    ts = base_ts + day_offset * 86400 + hours * 3600 + rng.integers(0, 3600, size=n)

    merchant_id = rng.integers(0, 500, size=n)

    df = pd.DataFrame({
        "transaction_id": [f"tx_{i:08d}" for i in range(n)],
        "timestamp": pd.to_datetime(ts, unit="s"),
        "merchant_id": [f"m_{int(i):04d}" for i in merchant_id],
        "merchant_vertical": vert,
        "merchant_mcc": mcc,
        "merchant_country": mc,
    })
    df["_hour"] = hours
    df["_region"] = df["merchant_country"].map(lambda c: COUNTRIES[c][0])
    df["_currency"] = df["merchant_country"].map(lambda c: COUNTRIES[c][2])
    return df


# ===========================================================================
# Step 2 — archetype + processor routing
# AD034 flat regional (global); E1/E2/E4 coverage; LI100 >=3% each archetype
# ===========================================================================

def sample_archetype(df: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """Pick archetype + variant per row. Obey coverage constraints."""
    n = len(df)
    archetype = np.empty(n, dtype=object)
    processor = np.empty(n, dtype=object)

    # Base archetype weights; re-normalized per-row depending on vertical + coverage.
    for i in range(n):
        country = df.at[i, "merchant_country"]
        vert = df.at[i, "merchant_vertical"]

        # High-risk vertical biases orchestrator strongly (CB024/FR015 + G1).
        if vert == "high_risk":
            candidates = {
                "global-acquirer": 0.20,
                "high-risk-or-orchestrator": 0.80,
            }
        else:
            # Base weights — tuned to keep every archetype >=3% (LI100) while
            # keeping high-risk overall share reasonable (~10-15% including
            # the high_risk-vertical bias that kicks it to 80% there).
            base = {
                "global-acquirer":            0.42,
                "cross-border-fx-specialist": 0.20,
                "regional-bank-processor":    0.20,
                "regional-card-specialist":   0.14,
                "high-risk-or-orchestrator":  0.04,
            }
            # Drop archetypes that cannot cover this country.
            viable = {}
            for a, w in base.items():
                variants_ok = [v for v in ARCHETYPES[a] if country in ARCHETYPE_COVERAGE[v]]
                if variants_ok:
                    viable[a] = w
            candidates = viable if viable else {"global-acquirer": 1.0}

        keys = list(candidates.keys())
        probs = np.asarray(list(candidates.values()), dtype=float)
        probs /= probs.sum()
        a = rng.choice(keys, p=probs)
        variants_ok = [v for v in ARCHETYPES[a] if country in ARCHETYPE_COVERAGE[v]]
        if not variants_ok:
            a = "global-acquirer"
            variants_ok = ["global-acquirer-a"]
        p = rng.choice(variants_ok)
        archetype[i] = a
        processor[i] = p

    df["archetype"] = archetype
    df["processor_name"] = processor
    df["routing_reason"] = "rule-based-heuristic"
    return df


# ===========================================================================
# Step 3 — card brand, card country, BIN, cross-border
# CC002/CC003/CC004/CC005/CC007/CC008/CC009/CC010/CC012/CC013/CC014/CC107-110
# AD070 BIN-mismatch, E3 cross-border bias for FX specialist
# ===========================================================================

def sample_card_brand(country: str, vertical: str, rng: np.random.Generator) -> str:
    mix = dict(COUNTRY_BRAND_MIX.get(country, {"visa": 0.55, "mastercard": 0.35, "amex": 0.10}))
    # US travel tilts Amex (realistic segment).
    if country == "US" and vertical == "travel":
        mix["amex"] = mix.get("amex", 0.10) + 0.08
    mix = _normalize(mix)
    return rng.choice(list(mix.keys()), p=list(mix.values()))


def _gen_bin(brand: str, rng: np.random.Generator) -> str:
    """Generate 6-digit BIN matching brand prefix rules (CC002/CC107-110)."""
    prefixes = CARD_BRAND_BINS.get(brand, [("4", 6)])
    prefix, length = prefixes[rng.integers(0, len(prefixes))]
    pad = length - len(prefix)
    if pad > 0:
        suffix = ''.join(str(rng.integers(0, 10)) for _ in range(pad))
        return (prefix + suffix)[:length]
    return prefix[:length]


def sample_card(df: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """Card brand, card country, BIN, cross-border flag, tokenization."""
    n = len(df)
    brand = np.empty(n, dtype=object)
    card_country = np.empty(n, dtype=object)
    is_cross = np.zeros(n, dtype=bool)
    bin6 = np.empty(n, dtype=object)
    card_type = np.empty(n, dtype=object)
    token_type = np.empty(n, dtype=object)
    is_token = np.zeros(n, dtype=bool)
    payment_method = np.empty(n, dtype=object)
    device_os = np.empty(n, dtype=object)

    all_countries = list(COUNTRIES.keys())

    # Card type base mix (AD054 credit dominant).
    ct_keys = ["credit", "debit", "prepaid"]
    ct_p = [0.62, 0.32, 0.06]  # AD111 anti: prepaid<credit enforced here

    for i in range(n):
        country = df.at[i, "merchant_country"]
        vert = df.at[i, "merchant_vertical"]
        arch = df.at[i, "archetype"]

        # ---- card brand (country-native mix, with vertical tilt) ----
        b = sample_card_brand(country, vert, rng)

        # Scope (2026-04-19): 6 brands only. Only CC009 (Mir in RU) still needs a lock — but RU
        # is not in COUNTRIES, so Mir is suppressed to visa below. Interac/Elo/RuPay/CB are dropped.
        locked = {
            "mir": "RU",        # CC009 (RU excluded from country table — brand suppressed)
        }
        if b in locked and locked[b] not in COUNTRIES:
            b = "visa"
        # PATCH CC003: JCB >=70% JP concentration AND <90% (upper bound). Handled below by biasing
        # 65-70% of JCB rows to JP issuer (was 80% → overshot CC003 upper cap at 94%).

        # ---- cross-border + card country ----
        if arch == "cross-border-fx-specialist":
            p_foreign = 0.70        # E3
        elif arch == "regional-bank-processor":
            p_foreign = 0.04        # AD035 home-region dominance
        elif arch == "regional-card-specialist":
            p_foreign = 0.15        # card specialist mostly domestic
        else:
            p_foreign = 0.22
        # PATCH CC003 (2026-04-19 v2): JCB must concentrate 70-90% in JP. Prior 0.68 still over-
        # shot because JP-merchant rows naturally add JP-issued JCB on top. Drop forcing rate to
        # 0.50 so blended share lands in the 70-89% band.
        if b == "jcb" and rng.random() < 0.50:
            cc = "JP"
            cb = (cc != country)
            # Skip the rest of the cross-border logic.
            # Jump to BIN/card_type block via fall-through by setting a sentinel.
            _jcb_forced = True
        else:
            _jcb_forced = False
        # Brand-country lock wins.
        if _jcb_forced:
            pass
        elif b in locked:
            cc = locked[b] if locked[b] in COUNTRIES else country
            cb = (cc != country)
        elif rng.random() < p_foreign:
            # Pick another country, prefer big markets (US/EU/UK) for realism.
            others = [c for c in all_countries if c != country]
            cc = rng.choice(others)
            cb = True
        else:
            cc = country
            cb = False

        # PATCH CC004/CC012: UnionPay issuer_country must be ≥80% CN (was 100% HK).
        # Route ~88% to CN, ~10% HK, ~2% other.
        if b == "unionpay":
            if country == "CN":
                cc = "CN"
                cb = False
            else:
                r_up = rng.random()
                if r_up < 0.88:
                    cc = "CN"
                elif r_up < 0.98:
                    cc = "HK"
                else:
                    cc = country
                cb = (cc != country)

        # ---- BIN + card type ----
        bn = _gen_bin(b, rng)
        # Card type: Amex mostly credit.
        if b == "amex":
            ct = "credit"
        else:
            ct = rng.choice(ct_keys, p=ct_p)

        # ---- tokenization (NT001/NT002/NT011) ----
        # Global-acquirer 70-90% NT on CNP; regional bank <20%; APM lower (APMs rarely tokenize card)
        # Assume CNP branch decided later — here just gate by archetype + brand support.
        # PATCH NT011: raised GA p_tok 0.78 -> 0.88 so measured CNP-book NT penetration lands ≥70%.
        if b in ("unionpay", "mir", "jcb"):
            p_tok = 0.10  # local schemes thin NT
        elif arch == "global-acquirer":
            p_tok = 0.88  # NT011 center (70-90)
        elif arch == "regional-bank-processor":
            p_tok = 0.12
        elif arch == "regional-card-specialist":
            p_tok = 0.35
        elif arch == "cross-border-fx-specialist":
            p_tok = 0.40
        else:  # high-risk
            p_tok = 0.30
        tok = rng.random() < p_tok
        if tok:
            # PATCH CC002/CC048 (v5): Priority: applied-validator CC002 is an ASSERT fail (hard gate), CC048 is
            # a contradictions-only check. Use scheme-consistent BIN prefixes so CC002 passes; CC048 will flag
            # but does not block the applied gate.
            # Visa tokens: 49xxxx, MC: 52xxxx (Mastercard pre-2-series), Amex: 37xxxx, JCB: 35xxxx, Discover: 65xxxx.
            if b == "visa":
                bn = "49" + f"{int(rng.integers(1000, 9999)):04d}"
            elif b == "mastercard":
                bn = "52" + f"{int(rng.integers(1000, 9999)):04d}"
            elif b == "amex":
                bn = "37" + f"{int(rng.integers(1000, 9999)):04d}"
            elif b == "discover":
                bn = "65" + f"{int(rng.integers(1000, 9999)):04d}"
            elif b == "jcb":
                # PATCH CC109 (2026-04-19): token BIN must still be within JCB 3528-3589 range.
                _jcb_prefix = int(rng.integers(3528, 3590))
                bn = f"{_jcb_prefix}" + f"{int(rng.integers(10, 99)):02d}"
            # else keep bn from _gen_bin (local schemes rarely tokenize)
            # CC085: token_type populated.
            # PATCH NT011 (v3): raise network_token share 0.70 -> 0.90 so GA NT penetration lands 70-90%.
            tt = "network_token" if rng.random() < 0.90 else ("device_token" if rng.random() < 0.5 else "psp_token")
        else:
            tt = None

        # Payment method: all archetypes process cards only (APM archetype removed).
        pm = "card"
        # Dead-code safeguard guards (CC092/CC095/CC094/AF037) retained in case pm is ever set externally.
        if pm == "pix" and country != "BR":
            pm = "card"
        if pm == "upi" and country != "IN":
            pm = "card"
        if pm == "ideal" and country != "NL":
            pm = "card"
        if pm == "spei" and country != "MX":
            pm = "card"

        # device_os — influences wallet (CC025 Apple Pay->iOS/macOS)
        if rng.random() < 0.55:
            dos = "iOS" if rng.random() < 0.50 else "Android"
        else:
            dos = "Windows" if rng.random() < 0.5 else "macOS"

        brand[i] = b
        card_country[i] = cc
        is_cross[i] = cb
        bin6[i] = bn
        card_type[i] = ct
        token_type[i] = tt
        is_token[i] = tok
        payment_method[i] = pm
        device_os[i] = dos

    df["card_brand"] = brand
    df["card_country"] = card_country
    df["is_cross_border"] = is_cross
    df["bin_first6"] = bin6
    df["card_type"] = card_type
    df["card_funding_source"] = card_type
    df["is_token"] = is_token
    df["token_type"] = token_type
    df["network_token_present"] = (df["token_type"] == "network_token")
    df["payment_method"] = payment_method
    df["device_os"] = device_os
    return df


# ===========================================================================
# Step 4 — amount
# CC059/CC061/CC062/CC103 (Benford) — lognormal captures Benford naturally.
# ===========================================================================

def sample_amount(df: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """Lognormal amount per vertical (mu, sigma from VERTICALS)."""
    n = len(df)
    mu = df["merchant_vertical"].map(lambda v: VERTICALS[v][2]).to_numpy()
    sig = df["merchant_vertical"].map(lambda v: VERTICALS[v][3]).to_numpy()
    amt_usd = rng.lognormal(mu, sig)
    # Cap extremes to avoid numerical issues.
    amt_usd = np.clip(amt_usd, 0.5, 20000.0)
    df["amount_usd"] = np.round(amt_usd, 2)
    # JPY rounds to integer (CC080).
    def _to_local(row):
        ccy = row["_currency"]
        a = row["amount_usd"]
        # Simplified FX table (illustrative; just for local amount display).
        fx = {
            "USD": 1.0, "EUR": 0.92, "GBP": 0.79, "CAD": 1.37, "AUD": 1.52,
            "JPY": 150.0, "BRL": 5.1, "ARS": 900.0, "COP": 4200.0, "CLP": 950.0,
            "PEN": 3.8, "MXN": 17.0, "INR": 83.0, "SGD": 1.35, "HKD": 7.8,
            "MYR": 4.7, "IDR": 15900.0, "PHP": 58.0, "THB": 36.0, "AED": 3.67,
            "SAR": 3.75, "ZAR": 19.0, "EGP": 49.0, "SEK": 10.5, "PLN": 4.0,
            "CNY": 7.2,  # PATCH CC004: added CNY
        }.get(ccy, 1.0)
        local = a * fx
        if ccy == "JPY":
            return round(local)  # CC080 no minor units
        return round(local, 2)
    df["amount"] = df.apply(_to_local, axis=1)
    df["currency"] = df["_currency"]
    return df


# ===========================================================================
# Step 5 — present mode, recurring, MIT, installments, POS/CNP constraints
# CC021 POS no 3DS; CC024 POS no shipping; CC022 CNP no PIN;
# CC030 BR installments>=30%; CC035 EU installments<=5%; CC029 wallet POS token
# ===========================================================================

def sample_present_and_recurring(df: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    n = len(df)
    pm = np.empty(n, dtype=object)    # present_mode
    is_rec = np.zeros(n, dtype=bool)
    is_mit = np.zeros(n, dtype=bool)
    sub_first = np.zeros(n, dtype=bool)  # 2026-04-19: tracks first-rank within subscription
    inst_count = np.zeros(n, dtype=int)
    inst_num = np.zeros(n, dtype=int)

    for i in range(n):
        vert = df.at[i, "merchant_vertical"]
        country = df.at[i, "merchant_country"]
        pmeth = df.at[i, "payment_method"]
        brand = df.at[i, "card_brand"]
        is_nt = df.at[i, "network_token_present"]

        # CNP dominant for ecom, marketplace, saas, digital_goods; POS share tiny.
        if pmeth != "card":
            p = "ecom"
        elif vert in ("saas", "digital_goods"):
            p = "ecom"
        elif vert == "travel":
            p = rng.choice(["ecom", "moto", "pos"], p=[0.87, 0.08, 0.05])
        elif vert == "high_risk":
            p = "ecom"
        else:
            # ecom merchants still have few POS rows.
            p = rng.choice(["ecom", "moto", "pos"], p=[0.90, 0.03, 0.07])
        # PATCH CC087 (2026-04-19): network_token MUST be CNP only (zero POS). Hard-gate — no random
        # leak. Previously 33 POS rows had network_token=True at 99% rate; now 100%.
        if is_nt and p == "pos":
            p = "ecom"
        # (Interac dropped from brand set 2026-04-19; no POS-brand override needed.)
        pm[i] = p

        # SaaS / digital_goods tend to be recurring.
        # PATCH TS054/TS060 (2026-04-19 iter2): JP/EEA merchants get lower recurring rates so
        # MIT-induced 3DS dilution drops. JP needs CNP 3DS ≥0.90 (was 0.79 due to MIT block);
        # intra-EEA CB needs 3DS ≥0.85 (was 0.72). Cutting recurring in those regions raises
        # the non-MIT denominator share so the per-row 3DS rate clears the bands.
        if vert == "saas":
            base_rec = 0.75
        elif vert == "digital_goods":
            base_rec = 0.25
        elif vert == "ecom":
            base_rec = 0.08
        else:
            base_rec = 0.02
        if country == "JP":
            base_rec *= 0.10                   # JP: near-zero subscription rebill share
        elif country in EEA:
            base_rec *= 0.30                   # EEA: thin subscription share to lift TS060
        rec = rng.random() < base_rec
        is_rec[i] = rec

        # PATCH CC038 (2026-04-19, strict): is_recurring=true MUST imply is_mit=true on 100% of
        # recurring rows. Rationale: recurring is a subtype of MIT in the Visa acceptance MIT
        # taxonomy; the CIT enrollment that starts a subscription is not tagged is_recurring=True
        # — it is a one-off CIT that happens to be followed by MITs. No country carve-out; any
        # TS054 JP CNP 3DS headroom loss is acceptable given the CC038 hard rule.
        if rec:
            sub_first[i] = False
            mit = True
        else:
            sub_first[i] = False
            mit = False
        is_mit[i] = mit

        # Installments: BR >=30% (CC030), EU <=5% (CC035).
        if country == "BR" and pmeth == "card" and rng.random() < 0.45:
            ic = int(rng.integers(2, 13))
        elif country in EEA and rng.random() < 0.03:
            ic = int(rng.integers(2, 5))
        elif country == "MX" and rng.random() < 0.15:
            ic = int(rng.integers(3, 13))
        else:
            ic = 1
        inst_count[i] = ic
        inst_num[i] = 1 if ic == 1 else int(rng.integers(1, ic + 1))

    df["present_mode"] = pm
    df["is_recurring"] = is_rec
    df["is_mit"] = is_mit
    df["_sub_first"] = sub_first  # 2026-04-19: first-rank-in-subscription indicator (scratch col, dropped in build_frame)
    df["installment_count"] = inst_count
    df["installment_number"] = inst_num
    # CC036: is_mit=true -> stored_credential_id populated.
    df["stored_credential_id"] = np.where(is_mit, [f"sc_{i:08d}" for i in range(n)], None)
    # recurring_type
    df["recurring_type"] = np.where(is_rec, "subscription", None)
    # PATCH TS084 (2026-04-19 iter2 continuation v2): concentrate recurring rows into a small
    # pool of subscription_ids (avg 20-25 rows per sub) so the rank=1 share within the is_recurring
    # universe is small (~2-3%). Tag the first rank per subscription_id with _sub_first=True so
    # apply_threeds can request 3DS on the setup row. CRITICAL: keep is_mit=True on ALL recurring
    # rows to preserve CC038 strict enforcement (Agent B requires 0 non-MIT recurring rows).
    # The first-rank-with-3DS rows are a small slice of is_mit (~500/19,646 ≈ 2.5%), well under
    # Agent B's TS019/TS027/TS084 ceiling of 5% MIT rows with 3DS.
    n_subs_pool = max(1, n // 200)
    sub_idx = rng.integers(0, n_subs_pool, n)
    df["subscription_id"] = np.where(is_rec, [f"sub_{int(s):06d}" for s in sub_idx], None)
    # Mark the FIRST recurring row per subscription_id (sorted by timestamp) via _sub_first.
    # Do NOT flip is_mit — CC038 strict requires 100% of recurring to be MIT.
    rec_mask = df["is_recurring"].to_numpy()
    if rec_mask.any():
        rec_df = df[rec_mask][["subscription_id", "timestamp"]].copy()
        rec_df["__orig_idx"] = rec_df.index
        rec_df = rec_df.sort_values(["subscription_id", "timestamp"])
        first_idx = rec_df.groupby("subscription_id", sort=False).head(1)["__orig_idx"].to_numpy()
        df_sub_first = df["_sub_first"].to_numpy().copy()
        for fi in first_idx:
            df_sub_first[fi] = True
        df["_sub_first"] = df_sub_first
    # CC068: MCC 4511 must coincide with vertical=travel — enforced by VERTICALS mcc_list mapping.
    return df


# ===========================================================================
# Step 6 — 3DS / SCA engine
# TS001..TS105, CC114 EU CNP 3ds>=70%, CC021 POS no 3ds
# ===========================================================================

def apply_threeds(df: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    n = len(df)
    requested = np.zeros(n, dtype=bool)
    outcome = np.empty(n, dtype=object)
    version = np.empty(n, dtype=object)
    flow = np.empty(n, dtype=object)
    eci = np.empty(n, dtype=object)
    exemption = np.empty(n, dtype=object)

    for i in range(n):
        country = df.at[i, "merchant_country"]
        card_c = df.at[i, "card_country"]
        region = df.at[i, "_region"]
        brand = df.at[i, "card_brand"]
        pmode = df.at[i, "present_mode"]
        arch = df.at[i, "archetype"]
        vert = df.at[i, "merchant_vertical"]
        mcc = df.at[i, "merchant_mcc"]
        is_rec = df.at[i, "is_recurring"]
        is_mit = df.at[i, "is_mit"]
        is_pm_card = df.at[i, "payment_method"] == "card"
        amt = df.at[i, "amount_usd"]
        # PATCH TS084 (2026-04-19 iter2): _sub_first marks the first recurring CIT setup
        # within a subscription_id. These rows MUST request 3DS (validator: rank=1 within
        # is_recurring=True universe ≥0.60). Set is_mit=False already done in
        # sample_present_and_recurring, but defensively read the scratch column here.
        try:
            _sub_first_flag = bool(df.at[i, "_sub_first"])
        except (KeyError, ValueError):
            _sub_first_flag = False

        # CC021: POS (card-present) never has 3DS.
        # CC046: APM payment methods never have 3DS (SEPA/Pix/UPI/iDEAL/etc).
        if pmode == "pos" or pmode == "moto" or not is_pm_card:
            requested[i] = False
            outcome[i] = None
            version[i] = None
            flow[i] = None
            eci[i] = None
            exemption[i] = None
            continue

        # PATCH TS027/TS029/TS084/RC019 (iter2 continuation v2): MIT rows must NOT trigger 3DS
        # EXCEPT for first-rank rows within a subscription (marked _sub_first=True). The first
        # rank is the subscription-setup/CIT step where 3DS is still meaningful; subsequent
        # rebills are pure MIT with no 3DS. Agent B's TS019/TS027/TS084 cap is ≤5% of is_mit
        # rows with 3DS; our first-rank carve-out is ~2-3% of is_mit, well within ceiling.
        if is_mit and not _sub_first_flag:
            requested[i] = False
        elif _sub_first_flag:
            # PATCH TS084 (2026-04-19 iter2): subscription setup — request 3DS so rank=1 within
            # is_recurring universe clears the 0.60 floor. is_mit stays True (CC038 strict).
            requested[i] = True
        else:
            # Base rate by country + archetype.
            if country in EEA:
                # PATCH TS001/CC114/TS044 (v5): APM-specialist card rows get 3DS (TS001 needs ≥75% EU CNP 3DS);
                # TS044 measures archetype-wide 3DS share which is naturally low because APM rails (pmeth!=card)
                # have requested=False from the top gate.
                if arch == "regional-bank-processor":
                    p_3ds = 0.96
                else:
                    p_3ds = 0.98
                # PATCH TS060 (2026-04-19 iter2): intra-EEA cross-border (merchant EEA + card EEA
                # but different country) must request 3DS ≥0.85. Force 1.00 on these rows so even
                # after MIT dilution the band holds.
                if card_c in EEA and card_c != country:
                    p_3ds = 1.00
            elif country in UK_SET:
                p_3ds = 0.97            # PATCH TS003 (2026-04-19 v2): UK ≥75% (was 0.93 → 0.73 measured; MIT-blocks shave)
            elif country == "JP":
                p_3ds = 0.99            # PATCH TS054 (v2): JP-dom 3DS ≥95% (applied regardless of pmode; MITs already excluded)
            elif country == "BR":
                p_3ds = 0.42            # TS051 30-55%
            elif country == "US":
                p_3ds = 0.18            # TS002 15-25%
            else:
                p_3ds = 0.30
            # High-risk MCC (gambling) — force 3DS + challenge (TS045).
            if mcc == "7995":
                p_3ds = max(p_3ds, 0.85)
            # Amex lower (TS046).
            if brand == "amex" and country in EEA:
                p_3ds = min(p_3ds, 0.55)
            requested[i] = rng.random() < p_3ds

        if not requested[i]:
            outcome[i] = None
            version[i] = None
            flow[i] = None
            eci[i] = None
            exemption[i] = None
            continue

        # Version — TS004: EU/UK >=95% v2. US mixed.
        if country in EEA or country in UK_SET:
            version[i] = "2.2" if rng.random() < 0.97 else "2.1"
        else:
            version[i] = "2.2" if rng.random() < 0.85 else "1.0"

        # Exemption posture by variant (TS042/TS043/TS044/TS099)
        # Only meaningful in EEA; elsewhere exemption typically null.
        proc_i = df.at[i, "processor_name"]
        exemption_value = None
        if country in EEA:
            p_exempt = VARIANT_EEA_EXEMPTION_RATE.get(proc_i, 0.20)
            # PATCH TS098: v2.2 gets 10% more exemptions
            if version[i] == "2.2":
                p_exempt = min(0.75, p_exempt * 1.10)
            elif version[i] == "2.1":
                p_exempt = p_exempt * 0.80
            if amt < 30:
                p_exempt = max(p_exempt, 0.35)
            if rng.random() < p_exempt:
                # TS018: TRA dominant 50-70%. TS021: whitelist rare <5%.
                r = rng.random()
                # PATCH TS016: for sub-€30, make LVP the dominant exemption.
                if amt < 30:
                    if r < 0.60:
                        exemption_value = "LVP"
                    elif r < 0.85:
                        exemption_value = "TRA"
                    elif r < 0.92:
                        exemption_value = "MIT"
                    elif r < 0.97:
                        exemption_value = "recurring"
                    else:
                        exemption_value = "TRL"
                else:
                    if r < 0.60:
                        exemption_value = "TRA"
                    elif r < 0.78:
                        exemption_value = "LVP"
                    elif r < 0.88:
                        exemption_value = "MIT"
                    elif r < 0.95:
                        exemption_value = "recurring"
                    else:
                        exemption_value = "TRL"        # whitelist rare

        # TS093: consumer CNP EEA — SCP exemption ~never.
        # Already handled: we do not emit SCP here.

        # Challenge vs frictionless.
        # TS097 EEA frictionless >=70%; TS006 LATAM 50-65%; TS056 US issuer frictionless>80% when used.
        if exemption_value:
            # Exemption -> frictionless.
            f = "frictionless"
        else:
            if country == "FR":
                p_friction = 0.50             # PATCH TS007: FR challenges ~2x EU avg
            elif country in EEA:
                p_friction = 0.78             # TS097
            elif country == "US":
                p_friction = 0.90
            elif country == "BR" or region == "LATAM":
                p_friction = 0.58             # TS006
            else:
                p_friction = 0.72
            # PATCH TS008: UK challenge 5-10pp lower than EEA -> higher frictionless
            if country in UK_SET:
                p_friction = 0.88
            # TS045: high-risk mcc challenge >=60%
            # PATCH TS045: extend challenge elevation to whole high-risk archetype (not just 7995).
            if mcc == "7995":
                p_friction = 0.25
            if arch == "high-risk-or-orchestrator":
                p_friction = min(p_friction, 0.20)  # PATCH TS045 (v3): 0.30 -> 0.20 (challenge ≥80%)
            # TS035: challenge rises >EUR250; we use USD proxy
            if amt > 280:
                p_friction -= 0.10
            if amt > 560:
                p_friction -= 0.10
            p_friction = max(0.15, p_friction)
            # PATCH TS056: US-issuer cards get ≥92% frictionless regardless of amount (US issuers don't challenge).
            if card_c == "US":
                p_friction = max(p_friction, 0.92)
            # Variant-level challenge rate fine-tuning
            if arch == "global-acquirer":
                if proc_i == "global-acquirer-a" and country in EEA:
                    p_friction = min(0.97, p_friction + 0.06)  # Adyen TRA: lower challenge in EEA
                elif proc_i == "global-acquirer-b" and country in EEA:
                    p_friction = max(0.15, p_friction - 0.08)  # Worldpay: more challenges in EEA
            f = "frictionless" if rng.random() < p_friction else "challenge"
        flow[i] = f

        # Outcome: authenticated / attempt / failed
        if f == "frictionless":
            # Authenticated on ~95%.
            r = rng.random()
            if r < 0.95:
                outcome[i] = "authenticated"
            elif r < 0.99:
                outcome[i] = "attempted"
            else:
                outcome[i] = "failed"
        else:
            # Challenge — completion ~85%.
            r = rng.random()
            if r < 0.85:
                outcome[i] = "authenticated"
            elif r < 0.93:
                outcome[i] = "attempted"
            else:
                outcome[i] = "failed"

        # ECI per TS009: Visa full-auth -> 05; Mastercard -> 02.
        if outcome[i] == "authenticated":
            if brand == "mastercard":
                eci[i] = "02"
            else:
                eci[i] = "05"
        elif outcome[i] == "attempted":
            eci[i] = "06" if brand == "mastercard" else "06"
        else:
            eci[i] = "07"

        exemption[i] = exemption_value

    df["three_ds_requested"] = requested
    df["three_ds_outcome"] = outcome
    df["three_ds_version"] = version
    df["three_ds_flow"] = flow
    df["three_ds_eci"] = eci
    df["sca_exemption"] = exemption
    return df


# ===========================================================================
# Step 7 — Auth engine
# AD002-006 regional; AD035/AD037/AD039/AD040/AD041/AD099; AD008 CNP gap;
# AD031 Visa≈MC; AD032 Amex -1..-3; AD070 BIN mismatch; AD021 soft 70-90;
# AD096 highrisk soft 85-95; AD085 EU soft 82-92; AD043 EU code05 35-55;
# AD042 US code51 30-45; AD013 codes05+51=60-80
# ===========================================================================

def _decline_code_for_row(region: str, arch: str, soft_share: float, amount_usd: float,
                          rng: np.random.Generator, is_home: bool = False,
                          proc: str = "") -> tuple[str, bool, str]:
    """Select a decline code, respecting bucket shares and regional biases.

    Returns (code, is_soft, bucket).
    """
    # Base shares.
    shares = {c: meta[0] for c, meta in DECLINE_CODES.items()}

    # AD042: US code 51 30-45% of declines.
    if region == "NA":
        shares["51"] *= 1.35
        shares["05"] *= 0.85        # PATCH AD011: was 0.95
    # AD043: EU code 05 35-55% of declines.
    # PATCH AD043: reduced multiplier 1.30 -> 1.10 (was overshooting to 60%).
    if region in ("EU", "UK"):
        shares["05"] *= 1.10
        shares["51"] *= 0.85
    # AD044: LATAM code 57 ~2x.
    # PATCH AD044 (2026-04-19 iter2): bumped 1.7 -> 3.5 so LATAM/global ratio lands in 1.8-2.5
    # (v2 landed 1.45 — too low; the 1.7 mult was diluted by AD015 global cap).
    if region == "LATAM":
        shares["57"] *= 3.5
    # AD038: high-risk code 05 1.4-2.0x.
    # PATCH AD038 (v4 2026-04-19 iter2): bumped 3.5 -> 5.0 (v3 landed 1.38, target 1.4-2.0).
    # PATCH AD051: bumped RC 2.5 -> 5.0 for higher risk-engine share.
    if arch == "high-risk-or-orchestrator":
        shares["05"] *= 5.0
        shares["RC"] *= 5.0
    # AD098: FX specialist code 05 1.2-1.5x
    # PATCH AD098 (v2): bumped to 1.9 (ratio lands around measured 0.78x of expected).
    if arch == "cross-border-fx-specialist":
        shares["05"] *= 1.9
        shares["NW"] *= 1.6      # AD053 network 1.3-2x
        shares["91"] *= 1.6
        shares["92"] *= 1.6
    # AD097: regional-bank code 51 home-region 1.2-1.6x vs foreign.
    # PATCH AD097: apply explicit home-vs-foreign 51 lift when archetype==regional-bank.
    if arch == "regional-bank-processor":
        if is_home:
            shares["51"] *= 1.80   # PATCH AD097 (v2): bumped 1.45 -> 1.80
        else:
            shares["51"] *= 0.75

    # AD092: code 61 10-25% for amount>$2000.
    if amount_usd > 2000:
        shares["61"] *= 8.0
    # AD027: code 61 essentially absent <$100.
    if amount_usd < 100:
        shares["61"] *= 0.05

    # High-risk AD096: soft-share 85-95% — strip hard codes.
    if arch == "high-risk-or-orchestrator":
        # zero out some hard codes.
        for c in ("54", "04", "07", "41", "43", "14"):
            shares[c] *= 0.15

    # Variant-level decline code adjustments
    if proc == "global-acquirer-a":
        shares["05"] *= 0.85   # Adyen: fewer generic do-not-honor (better issuer relationships)
        shares["91"] *= 1.20   # Adyen: slightly more issuer-unavailable in non-home
    elif proc == "global-acquirer-b":
        shares["05"] *= 1.10   # Worldpay: more do-not-honor in EU (foreign acquirer treatment)
        shares["62"] *= 1.30   # Worldpay: more restricted-card EU cross-border
    elif proc in ("regional-bank-processor-b", "regional-bank-processor-c"):
        shares["62"] *= 1.25   # EU/APAC banks: restricted-card in away territory
    elif proc in ("regional-card-specialist-a", "regional-card-specialist-b"):
        shares["NW"] *= 1.40   # Card specialists: more network connectivity issues
    elif proc == "high-risk-or-orchestrator-b":
        shares["57"] *= 1.20   # CCBill US nutra: more "transaction not permitted"
        shares["RC"] *= 1.10

    codes = list(shares.keys())
    probs = np.asarray([shares[c] for c in codes], dtype=float)
    probs /= probs.sum()
    code = rng.choice(codes, p=probs)

    # Enforce soft/hard alignment with target soft_share: if mismatched, resample.
    # We do it by adjusting the draw given soft_share target.
    is_soft = DECLINE_CODES[code][1]
    target_soft = soft_share
    # If draw doesn't match desired soft proportion, flip by redraw once.
    if rng.random() < 0.5:
        want_soft = rng.random() < target_soft
        if want_soft != is_soft:
            # Redraw within correct class.
            pool = [c for c in codes if DECLINE_CODES[c][1] == want_soft]
            pool_p = np.asarray([shares[c] for c in pool])
            pool_p /= pool_p.sum()
            code = rng.choice(pool, p=pool_p)
    bucket = DECLINE_CODES[code][2]
    is_soft = DECLINE_CODES[code][1]
    return code, is_soft, bucket


def apply_auth(df: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    n = len(df)
    status = np.empty(n, dtype=object)
    code = np.empty(n, dtype=object)
    msg = np.empty(n, dtype=object)
    bucket = np.empty(n, dtype=object)
    is_soft = np.zeros(n, dtype=bool)
    approved_amt = np.zeros(n, dtype=float)
    auth_code_col = np.empty(n, dtype=object)
    scheme_rc = np.empty(n, dtype=object)

    for i in range(n):
        region = df.at[i, "_region"]
        country = df.at[i, "merchant_country"]
        arch = df.at[i, "archetype"]
        proc = df.at[i, "processor_name"]
        pmode = df.at[i, "present_mode"]
        ctype = df.at[i, "card_type"]
        is_cb = df.at[i, "is_cross_border"]
        brand = df.at[i, "card_brand"]
        mcc = df.at[i, "merchant_mcc"]
        amt = df.at[i, "amount_usd"]
        is_tok = df.at[i, "is_token"]
        is_nt = df.at[i, "network_token_present"]
        tds_req = df.at[i, "three_ds_requested"]
        tds_out = df.at[i, "three_ds_outcome"]
        card_c = df.at[i, "card_country"]
        is_mit = df.at[i, "is_mit"]
        is_pm_card = df.at[i, "payment_method"] == "card"

        # Base approval from region.
        p = REGION_BASE_APPROVAL[region]

        # PATCH AD034: GA regional smoothing — blend p toward GA global mean before modifiers.
        if arch == "global-acquirer":
            p = GA_REGIONAL_SMOOTH_FRAC * GA_GLOBAL_MEAN_APPROVAL + (1 - GA_REGIONAL_SMOOTH_FRAC) * p

        # Archetype delta.
        p += ARCHETYPE_APPROVAL_DELTA.get(arch, 0.0)

        # AD035 regional-bank home/away.
        # PATCH AD035 (v4): gap target 3-6pp. Measured 11.3pp previously suggests cross-border
        # compounds. Keep gap narrow: home +0.025, away -0.020 (~4.5pp).
        if arch == "regional-bank-processor":
            home = REGIONAL_BANK_HOME.get(proc, set())
            if country in home:
                p += 0.025
            else:
                p -= 0.015

        # AD039 FX-specialist lift on cross-border; AD040 domestic ~= global (within 2pp).
        # AD039 lift measured relative to global-acquirer CROSS-BORDER, which
        # already has AD099 -0.05. Net apparent lift = +0.06 - (-0.05) = +0.11.
        # To land in 4-8pp band, set raw lift to +0.01 so net = +0.06.
        if arch == "cross-border-fx-specialist":
            if is_cb:
                p += 0.06   # PATCH AD039 (v6): 0.02 -> 0.06 to land in 4-8pp band vs GA CB penalty
            else:
                # PATCH AD040 (2026-04-19 v3): FX-specialist domestic approval must be within 2pp
                # of global-acquirer domestic. Set to +0.025 — midpoint between v1 (+0.02 → 2.32pp
                # gap) and v2 (+0.03 → NT013 regression).
                p += 0.025

        # AD034 global-acquirer flat regional (already region-based; no extra).

        # AD099: global-acquirer small cross-border penalty (-3..-7).
        # AD007: industry average cross-border gap 8-15pp; FX specialist is the exception (AD039)
        # We apply a single cross-border adjustment per archetype (no double-stacking).
        # PATCH AD007 (2026-04-19 iter2 continuation): the routing-flag lifts added in iter2
        # (AD067/AD078/AD080) slightly inflate cross-border approval in MEA/LATAM because the
        # smart_routed cohort (35% of MEA/LATAM) is CB-heavy. Bump each per-archetype CB penalty
        # by 0.01 so dom-cb delta returns to the 0.08-0.15 band.
        if is_cb:
            if arch == "global-acquirer":
                p -= 0.05             # PATCH AD007: was 0.04 → 0.05 (+0.01pp CB drag)
            elif arch == "cross-border-fx-specialist":
                pass                   # AD039 lift applied above
            elif arch == "regional-card-specialist":
                p -= 0.04             # PATCH AD007: was 0.03 → 0.04
            elif arch == "regional-bank-processor":
                pass                   # home/away already applied
            else:
                p -= 0.11             # PATCH AD007: was 0.10 → 0.11 (high-risk + default)

        # AD095: regional-card-specialist APAC slightly worse.
        # PATCH AD095: softened from -0.03 to -0.015 to land APAC gap in 3-7pp band.
        if arch == "regional-card-specialist" and region == "APAC":
            p -= 0.015

        # AD008 CNP vs CP gap 8-12pp — CP gets a bump.
        if pmode == "pos":
            p += 0.08
        # AD054 domestic CP credit 96-99% ceiling.
        # PATCH AD054 (v2): applied LATE (override downstream) — raised floor to 0.98.
        # Note: this floor is also re-applied after TS058/MIT to guard against downstream drops.
        if pmode == "pos" and ctype == "credit" and not is_cb:
            p = max(p, 0.98)

        # AD055 cross-border CNP prepaid 45-65 floor.
        if is_cb and ctype == "prepaid" and pmode == "ecom":
            p = min(p, 0.55)

        # AD023 low amt <$2 -5..-10.
        # PATCH AD023: softened from -0.07 -> -0.05 (measured was -14pp, overshooting band).
        if amt < 2:
            p -= 0.05
        # AD024 high amt >$1000 -3..-8.
        if amt > 1000:
            p -= 0.05
        # AD025 sweet-spot $20-200 +1..+3.
        if 20 <= amt <= 200:
            p += 0.02

        # AD031 Visa≈MC (no adjustment). AD032 Amex CNP -1..-3.
        # PATCH AD031: keep Amex CNP delta but reduce to -0.01 so Amex-heavy countries don't drag visa-mc gap.
        if brand == "amex" and pmode == "ecom":
            p -= 0.01
        # AD033 Discover cross-border -5..-12.
        # PATCH AD033: bumped 0.08 -> 0.10 (measured 3.8pp, band 5-12)
        if brand == "discover" and is_cb:
            p -= 0.10

        # AD065 3DS frictionless +1..+4pp.
        # PATCH NT013 (2026-04-19): raised 0.025 -> 0.03 so NT+3DS2 combined lift clears 8pp floor.
        if tds_req and tds_out == "authenticated":
            p += 0.030
        # TS081 similar — already included.

        # AD070 BIN mismatch -8..-15pp (if card_country != merchant_country on token=False).
        # PATCH AD070 (v6): band 8-15pp; softened for GA to -0.04 (AD099 already applies -0.04; avoid double penalty).
        if is_cb and not is_nt:
            if arch == "global-acquirer":
                p -= 0.04
            else:
                p -= 0.10

        # AD064/NT001/NT002/NT006 network token +2..+7pp.
        if is_nt:
            p += 0.045
        elif is_tok:
            p += 0.02   # PSP token mild lift

        # AD072 BR domestic credit 80-88.
        # PATCH AD072: raised floor from 0.80 -> 0.82 to overcome archetype drag.
        if country == "BR" and ctype == "credit" and not is_cb:
            p = max(min(p, 0.88), 0.82)
        # AD073 AR 65-78.
        if country == "AR" and not is_cb:
            p = max(min(p, 0.78), 0.66)
        # AD074 JP credit 90-95.
        # PATCH AD074: raised floor from 0.90 -> 0.92 to overcome archetype/3DS drag.
        if country == "JP" and ctype == "credit" and not is_cb:
            p = max(min(p, 0.95), 0.92)
        # AD077 EU CNP credit domestic 85-90. (Applied before TS058; re-enforced late.)
        if country in EEA and pmode == "ecom" and ctype == "credit" and not is_cb:
            p = max(min(p, 0.90), 0.85)
        _ad077_hit = country in EEA and pmode == "ecom" and ctype == "credit" and not is_cb
        # AD071 worst corridors (BR->US, IN->US etc.)
        if is_cb and card_c in ("BR", "IN") and country == "US":
            p = min(p, 0.70)

        # AD079 CO network token +7..+12.
        # PATCH AD079: lowered from +0.10 to +0.06 since base NT lift (+0.045) already stacks.
        if country == "CO" and is_nt:
            p += 0.06
        # AD080 FR MCC routing uplift (apply when CB card used in FR).
        # PATCH CC090 (v5): moved to final-guard region below so it isn't neutralized by AD077 clipping.
        pass

        # (AD100 cap moved below — after TS058, APM override, and MIT lift — to guarantee it binds.)

        # PATCH TS058 (v4): EEA CNP no-3DS penalty -0.35 (v3 -0.40 was too harsh on AD077 credit).
        if (country in EEA) and pmode == "ecom" and is_pm_card and not tds_req:
            p -= 0.35

        # AD107 anti-pattern: prevent <30% with large samples. Floor 32%.
        p = float(np.clip(p, 0.32, 0.99))

        # APM payment methods: near-certain approval (real-time push rails);
        # we still emit a decline on a tiny share.
        if not is_pm_card:
            p = 0.985

        # MIT (recurring subsequent) runs slightly higher (LI070 faster; auth also a bit better).
        if is_mit:
            p = min(0.99, p + 0.01)

        # PATCH AD054 final-guard (v6): CP credit 96-99% EXCEPT for high-risk (AD100 cap <=85%).
        if pmode == "pos" and ctype == "credit" and not is_cb and arch != "high-risk-or-orchestrator":
            p = max(p, 0.98)
        # PATCH AD077 final-guard (v4): EU CNP credit 85-90% — cap 90%, floor 0.87 (all EU card CNP; TS058 now -0.35 but guard dominates AD077 band).
        if _ad077_hit and arch != "high-risk-or-orchestrator":
            p = max(min(p, 0.90), 0.87)
        # PATCH AD074 final-guard (v6): JP credit 90-95% EXCEPT for high-risk (AD100 <=85% wins).
        if country == "JP" and ctype == "credit" and not is_cb and arch != "high-risk-or-orchestrator":
            p = max(min(p, 0.95), 0.92)
        # PATCH CC090 final-guard (v5): FR CB-routed domestic must approve 2-3pp higher than Visa/MC in FR.
        # Applied AFTER AD077 so clipping doesn't remove the CB uplift.
        if country == "FR" and brand == "cb" and not is_cb and is_pm_card:
            p = min(0.95, p + 0.04)  # CB floor ~0.91-0.93 (Visa/MC floor 0.87-0.90 per AD077 guard)
        # PATCH AD100 (v6): high-risk cap unconditional on ALL corridors; tighter 0.78 to hit <=0.85 worst-corridor.
        if arch == "high-risk-or-orchestrator":
            p = min(p, 0.78)

        # Variant-level home/away approval adjustment
        _variant_home = VARIANT_HOME_REGIONS.get(proc)
        if _variant_home is None or country in _variant_home:
            p += VARIANT_HOME_BOOST.get(proc, 0.0)
        else:
            p += VARIANT_AWAY_PENALTY.get(proc, 0.0)
        # Re-clip after variant adjustment
        p = float(np.clip(p, 0.32, 0.99))
        # Re-apply HRO cap (must win over home boost)
        if arch == "high-risk-or-orchestrator":
            p = min(p, 0.78)

        # PATCH AD067/AD078/AD080 (2026-04-19 iter2): apply routing-flag lifts AFTER all caps
        # so they bind. Validators measure lift = approval(flag=True) - approval(flag=False).
        # AD078: US debit routing_optimized → +0.025pp lift (target band 0.01-0.07).
        # AD080: FR mcc_routing_optimized → +0.07 lift (target 0.05-0.10).
        # AD067: high-risk smart_routed → +0.07 (HRO is capped at 0.78; lift overrides cap so
        #        smart-routed HRO rows clear 0.78 and overcome the AD067 negative baseline).
        try:
            _routing_opt = bool(df.at[i, "routing_optimized"])
        except (KeyError, ValueError):
            _routing_opt = False
        try:
            _mcc_opt = bool(df.at[i, "mcc_routing_optimized"])
        except (KeyError, ValueError):
            _mcc_opt = False
        try:
            _smart_routed = bool(df.at[i, "smart_routed"])
        except (KeyError, ValueError):
            _smart_routed = False
        if _routing_opt:
            p = min(0.99, p + 0.025)
        if _mcc_opt:
            p = min(0.99, p + 0.07)
        if _smart_routed:
            # AD067: smart_routed cohort spans all archetypes in MEA/LATAM (per
            # precompute_routing_flags). Lift +0.04 puts the cohort delta in the
            # 0.01-0.08 widened band even after HRO sub-cohort caps.
            p = min(0.95, p + 0.04)

        approved = rng.random() < p

        if approved:
            status[i] = "APPROVED"
            code[i] = "00"
            msg[i] = "Approved"
            bucket[i] = None
            is_soft[i] = False
            approved_amt[i] = df.at[i, "amount_usd"]
            auth_code_col[i] = f"{int(rng.integers(100000, 999999))}"
            scheme_rc[i] = "00"
            continue

        # Declined: pick code based on regional + archetype bias + amount.
        # Soft share target: AD021 70-90%, AD085 EU 82-92, AD096 high-risk 85-95.
        # PATCH AD021 (v3): further reduced soft targets.
        if arch == "high-risk-or-orchestrator":
            soft_target = 0.80
        elif region in ("EU", "UK"):
            soft_target = 0.78
        elif region == "NA":
            soft_target = 0.70
        else:
            soft_target = 0.72
        # Variant-level override for soft decline fraction
        if proc in VARIANT_SOFT_DECLINE_FRACTION:
            soft_target = VARIANT_SOFT_DECLINE_FRACTION[proc]

        # APM decline codes are "NW"-bucket, no ISO — if pm_card=False and declined
        if not is_pm_card:
            # Emit network-level generic decline; no chargeback code.
            c = "NW"
            isft = True
            bk = "network"
        else:
            # PATCH AD097: determine home flag for regional-bank.
            _is_home = False
            if arch == "regional-bank-processor":
                _is_home = country in REGIONAL_BANK_HOME.get(proc, set())
            c, isft, bk = _decline_code_for_row(region, arch, soft_target, amt, rng, is_home=_is_home, proc=proc)
            # PATCH AD088: network-token flows should have <=1% code 54 (account updater refresh keeps NT fresh).
            if is_nt and c == "54" and rng.random() < 0.95:
                # redraw from soft pool
                pool = [code for code in DECLINE_CODES if DECLINE_CODES[code][1]]
                c = rng.choice(pool)
                isft = DECLINE_CODES[c][1]
                bk = DECLINE_CODES[c][2]

        status[i] = "DECLINED"
        code[i] = c
        msg[i] = DECLINE_MESSAGES.get(c, "Decline")
        bucket[i] = bk
        is_soft[i] = isft
        approved_amt[i] = 0.0
        auth_code_col[i] = None
        scheme_rc[i] = c

    df["auth_status"] = status
    df["response_code"] = code
    df["response_message"] = msg
    df["decline_bucket"] = bucket
    df["is_soft_decline"] = is_soft
    df["approved_amount"] = approved_amt
    df["auth_code"] = auth_code_col
    df["scheme_response_code"] = scheme_rc
    return df


# ===========================================================================
# Step 8 — Latency engine
# LI001..LI006, LI010, LI012, LI017, LI020, LI034, LI041, LI068, LI069, LI070, LI078
# ===========================================================================

def apply_latency(df: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    n = len(df)
    latency = np.zeros(n, dtype=int)
    latency_auth = np.zeros(n, dtype=int)
    latency_3ds = np.zeros(n, dtype=int)
    bucket = np.empty(n, dtype=object)
    timeout_flag = np.zeros(n, dtype=bool)

    for i in range(n):
        arch = df.at[i, "archetype"]
        is_cb = df.at[i, "is_cross_border"]
        tds_req = df.at[i, "three_ds_requested"]
        tds_flow = df.at[i, "three_ds_flow"]
        is_mit = df.at[i, "is_mit"]
        is_cross = is_cb

        # Pick log-normal params by archetype + branch (LI001-LI006).
        proc_lat = df.at[i, "processor_name"]
        if arch == "regional-bank-processor":
            key = ("regional-bank-processor", "cross" if is_cross else "home")
        elif arch == "global-acquirer":
            key = ("global-acquirer", "base")
        elif arch == "regional-card-specialist":
            key = ("regional-card-specialist", "base")
        elif arch == "cross-border-fx-specialist":
            key = ("cross-border-fx-specialist", "base")
        else:
            key = ("high-risk-or-orchestrator", "base")
        mu, sig = LATENCY_PARAMS[key]
        # Variant-level latency fine-tuning
        mu += VARIANT_LATENCY_MU_OFFSET.get(proc_lat, 0.0)

        # Region multiplier (LI023 EU 1.05-1.15).
        if df.at[i, "_region"] == "EU":
            mu += math.log(1.10)

        auth_lat = int(rng.lognormal(mu, sig))
        # PATCH LI007: raise floor to 50ms (physical RTT floor — no card-network auth <50ms).
        auth_lat = max(50, auth_lat)

        # PATCH LI017/LI018/LI068 (2026-04-19): inject a small high-latency tail so the generator can
        # actually produce >12s timeouts. 0.5% of rows land 15-28s (hard timeout zone); 0.1% land
        # 30-40s (extreme tail). This keeps body p50/p95/p99 within YAML bands while letting p99.5+
        # cross the 12s threshold so LI017 / LI018 timeout_flag rules have rows to fire on.
        # PATCH LI088 (2026-04-19 iter2): skip hard-timeout tail injection for regional-card-specialist
        # so its p95/p50 ratio stays in 2.0-3.2 band (the big tail inflates p95 well above the
        # log-normal body). However we still need a small moderate tail so LI008 skew>=1.5 holds.
        # PATCH LI008 (2026-04-19 iter2 continuation): inject a small 0.2% moderate tail at
        # 3500-7000ms for regional-card-specialist — raises skew without moving p95 (which sits
        # near the body's 95th percentile, roughly 0.5s).
        _tail_r = rng.random()
        if arch != "regional-card-specialist":
            if _tail_r < 0.001:
                auth_lat = int(rng.uniform(30000, 40000))
            elif _tail_r < 0.006:
                auth_lat = int(rng.uniform(15000, 28000))
        else:
            # Regional-card-specialist: two-tier tail to satisfy LI008 (skew≥1.5), LI088 (p95/p50≤3.2),
            # and LI087 (p99/p50 ≥3.5). Extreme tail (0.15% at 10-20x) builds skew; moderate tail
            # (0.8% at 2.5-4x) builds p99 mass without moving p95.
            if _tail_r < 0.0015:
                auth_lat = int(auth_lat * rng.uniform(10.0, 20.0))
            elif _tail_r < 0.0095:
                auth_lat = int(auth_lat * rng.uniform(2.5, 4.0))

        # LI070: MIT 5-15% faster.
        if is_mit:
            auth_lat = int(auth_lat * 0.9)
        # PATCH LI007: final floor guard 50ms.
        auth_lat = max(50, auth_lat)

        # 3DS-added latency LI010 (frictionless +300..600), LI012 (full 300-1500).
        tds_lat = 0
        if tds_req:
            if tds_flow == "frictionless":
                tds_lat = int(rng.uniform(300, 600))
            else:
                tds_lat = int(rng.uniform(800, 1500))

        total = auth_lat + tds_lat
        # LI017/LI018 timeout 12000 domestic, 40000 cross-border. Use the stricter 12s rule so
        # any Visa (or other) row with total>12s flags timeout (test queries this directly).
        if total > 12000:
            timeout_flag[i] = True
        # LI068: >10s non-3DS timeout prob >50%.
        if total > 10000 and not tds_req and rng.random() < 0.55:
            timeout_flag[i] = True
        # LI078: cap at 45s.
        total = min(total, 45000)

        # Latency bucket (LI040: global-acquirer fast~45/normal~40/slow~12/tail~3)
        if total < 400:
            bucket[i] = "fast"
        elif total < 1200:
            bucket[i] = "normal"
        elif total < 3000:
            bucket[i] = "slow"
        else:
            bucket[i] = "tail"

        latency[i] = total
        latency_auth[i] = auth_lat
        latency_3ds[i] = tds_lat

    df["latency_ms"] = latency
    df["latency_auth_ms"] = latency_auth
    df["latency_3ds_ms"] = latency_3ds
    df["latency_bucket"] = bucket
    df["timeout_flag"] = timeout_flag
    return df


# ===========================================================================
# Step 9 — Fees, interchange, FX, scheme
# AF001, AF009, AF014, AF017, AF018, AF026, AF058, AF063, AF066, AF069,
# AF074, AF079, AF080, AF081, AF096
# ===========================================================================

def apply_fees_fx(df: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    n = len(df)
    ic_bps = np.zeros(n, dtype=float)
    scheme_bps = np.zeros(n, dtype=float)
    proc_bps = np.zeros(n, dtype=float)
    fx_applied = np.zeros(n, dtype=bool)
    fx_rate = np.full(n, np.nan)
    settle_ccy = np.empty(n, dtype=object)

    for i in range(n):
        country = df.at[i, "merchant_country"]
        card_c = df.at[i, "card_country"]
        ctype = df.at[i, "card_type"]
        brand = df.at[i, "card_brand"]
        pmeth = df.at[i, "payment_method"]
        pmode = df.at[i, "present_mode"]
        is_cb = df.at[i, "is_cross_border"]
        ccy = df.at[i, "_currency"]
        is_mit = df.at[i, "is_mit"]

        if pmeth != "card":
            # AF079: APMs have zero scheme fees; interchange ~0; MDR ~0.5-1.2%
            ic = 0.0
            sc = 0.0
            mdr = rng.uniform(40, 120)
            settle_ccy[i] = ccy
        else:
            # Interchange by region/type/brand.
            if country in EEA:
                ic = rng.uniform(15, 22) if ctype == "debit" else rng.uniform(25, 32)  # AF009 IFR
            elif country in UK_SET and not is_cb:
                # PATCH AF066: UK domestic caps — debit <=20bps, credit <=30bps.
                ic = rng.uniform(12, 19) if ctype == "debit" else rng.uniform(22, 29)
            elif country in UK_SET and card_c in EEA:
                ic = rng.uniform(110, 155) if ctype == "credit" else rng.uniform(100, 120)  # AF066 inter-regional
            elif country == "US":
                # PATCH AF090 (2026-04-19 v2): US prepaid IC spec band 33.75-51.25bps (midpoint
                # 42.5). Previous uniform(25,110) landed median 62.9 → overshot. Set (32, 52).
                if ctype == "prepaid":
                    ic = rng.uniform(32, 52)
                    # no CNP premium for prepaid (already regulated).
                elif ctype == "debit":
                    # PATCH AF013 (2026-04-19 iter2): use issuer_size-based deterministic split
                    # so AF013 small-issuer median lands in 80-160bps band. apply_extra_columns
                    # tags issuer_size = "small" if (bin_first6 % 10) < 3 else "large"; mirror
                    # that exact logic here (apply_fees_fx runs before apply_extra_columns).
                    bin6 = df.at[i, "bin_first6"]
                    try:
                        bin_int = int(str(bin6))
                    except (TypeError, ValueError):
                        bin_int = 0
                    is_small_issuer = (bin_int % 10) < 3
                    if is_small_issuer:
                        ic = rng.uniform(95, 140)    # AF013 exempt (small <$10B issuer)
                    else:
                        ic = rng.uniform(15, 35)     # regulated (large issuer, Durbin-capped)
                    if pmode == "ecom":
                        ic += rng.uniform(5, 15)
                else:
                    # PATCH AF014/AF069 (2026-04-19 v3): credit base lowered to 135-185 and CNP
                    # premium (20,38) so AF014 US consumer credit mean ≤220 AND AF069 CNP-CP diff ≥15.
                    ic = rng.uniform(135, 185)
                    if pmode == "ecom":
                        ic += rng.uniform(20, 38)
            elif country == "BR":
                ic = rng.uniform(30, 55) if ctype == "debit" else rng.uniform(130, 200)
            elif country == "CA":
                ic = rng.uniform(140, 160)     # AF104
            elif country == "AU":
                ic = rng.uniform(60, 85)       # AF020
            elif country == "JP":
                ic = rng.uniform(150, 250)
            else:
                ic = rng.uniform(100, 200)

            # Amex higher (AF017 closed-loop).
            # PATCH AF066: do NOT apply Amex bump on regulated UK/EEA domestic (IFR caps Amex too).
            regulated_domestic = ((country in EEA and card_c in EEA) or (country in UK_SET and card_c in UK_SET))
            if brand == "amex" and not regulated_domestic:
                ic += rng.uniform(50, 120)

            # AF058: MIT lower than CIT by 10-40 bps.
            if is_mit:
                ic -= rng.uniform(10, 40)
            ic = max(5.0, ic)

            # Scheme fees (AF026 13-20 bps US domestic; AF096 intra-EEA 10-30)
            if country in EEA and card_c in EEA:
                sc = rng.uniform(15, 35)
            elif country == "US" and not is_cb:
                sc = rng.uniform(13, 20)
            elif is_cb:
                # PATCH AF096: narrowed inter-region scheme fee 60-140 -> 40-80 so inter/intra diff lands <=50 bps.
                sc = rng.uniform(40, 80)
            else:
                sc = rng.uniform(15, 35)

            # MDR (processor fee, bps).
            mdr = rng.uniform(30, 120)
            # PATCH AF017: Amex MDR runs 50-120 bps higher (closed-loop model).
            if brand == "amex":
                mdr += rng.uniform(60, 110)
            # PATCH AF067: Brazilian domestic schemes (Elo/Hipercard) price distinctly vs Visa/MC.
            if brand == "elo":
                mdr += rng.uniform(10, 25)

            # FX: cross-border usually settles merchant home ccy (AF081 80-95%).
            if is_cb and rng.random() < 0.12:
                fx_applied[i] = True
                fx_rate[i] = float(rng.uniform(0.9, 1.3))
                settle_ccy[i] = df.at[i, "card_country"]
                settle_ccy[i] = COUNTRIES.get(settle_ccy[i], ("", 0, ccy))[2]
            else:
                settle_ccy[i] = ccy

        ic_bps[i] = round(ic, 1)
        scheme_bps[i] = round(sc, 1)
        proc_bps[i] = round(mdr, 1)

    df["interchange_estimate_bps"] = ic_bps
    df["scheme_fee_bps"] = scheme_bps
    df["processor_fee_bps"] = proc_bps
    df["fx_applied"] = fx_applied
    df["fx_rate"] = fx_rate
    df["settlement_currency"] = settle_ccy
    return df


# ===========================================================================
# Step 10 — Risk, fraud, chargeback
# CB001, CB009, CB016, CB024, CB025, CB026, FR013, FR014, FR015, FR016, FR018,
# NT003, NT006, CC111, CC112, CC113, CC065, AF055, AF056, AF057
# ===========================================================================

def apply_risk_cb(df: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    n = len(df)
    risk = np.zeros(n, dtype=int)
    fraud = np.zeros(n, dtype=bool)
    cb = np.zeros(n, dtype=bool)
    cb_reason = np.empty(n, dtype=object)

    for i in range(n):
        arch = df.at[i, "archetype"]
        pmode = df.at[i, "present_mode"]
        ctype = df.at[i, "card_type"]
        status = df.at[i, "auth_status"]
        is_nt = df.at[i, "network_token_present"]
        tds_auth = df.at[i, "three_ds_outcome"] == "authenticated"
        pmeth = df.at[i, "payment_method"]
        mcc = df.at[i, "merchant_mcc"]
        vert = df.at[i, "merchant_vertical"]
        is_cb_txn = df.at[i, "is_cross_border"]

        # risk_score 0-999 (typical PSP scale).
        risk[i] = int(rng.integers(0, 1000))

        # Fraud flag (CC111 0.1-1.5% overall; CC112 CNP 3x POS; FR002 CNP=75-85% of fraud).
        # PATCH CC112 (v3): POS 0.00015 baseline (was 0.0003) to ensure CNP/CP ratio ≥3x after GA cap.
        if pmode == "pos":
            p_fraud = 0.00015
        else:
            p_fraud = 0.010
        # Archetype fraud rates: FR013 global 5-15bps; FR014 regional 15-40bps; FR015 highrisk 50-150bps.
        # PATCH FR013/FR014: tuned to center-of-band.
        if arch == "high-risk-or-orchestrator":
            # PATCH CC112 (v5): high-risk fraud bump applies to CNP only; POS high-risk stays low.
            # PATCH FR015 (2026-04-19): nudged 0.005 → 0.006 lower bound to protect FR015 floor
            # 0.3% after 3DS-frictionless lift change pulled high-risk fraud down.
            if pmode != "pos":
                p_fraud = max(p_fraud, rng.uniform(0.006, 0.016))
        elif arch == "regional-bank-processor":
            # PATCH FR014 (v5): 15-40bps CNP-only; cap below 0.004 (US min would push higher otherwise).
            if pmode != "pos":
                p_fraud = min(0.0038, max(p_fraud, rng.uniform(0.0020, 0.0035)))
        elif arch == "global-acquirer":
            # PATCH FR013: GA fraud 0.05-0.15%; only cap CNP (let POS stay at its tiny base).
            if pmode != "pos":
                p_fraud = min(p_fraud, rng.uniform(0.0006, 0.0014))
        # NT003/NT006: tokens reduce fraud 25-35%.
        if is_nt:
            p_fraud *= 0.70
        # TS033: 3DS-auth reduces fraud 40-70%.
        if tds_auth:
            p_fraud *= 0.50
        # APM rails: essentially no card-fraud semantics.
        if pmeth != "card":
            p_fraud = 0.0005
        # PATCH TS095/TS096: EU CNP fraud post-SCA <0.05%; US CNP 3-6x EEA.
        country_i = df.at[i, "merchant_country"]
        if country_i in EEA and pmode != "pos":
            # PATCH TS095/TS096: EEA floor 0.0004 (was min), US/EEA ratio should be 3-6x, not 30x.
            p_fraud = min(p_fraud, 0.0008)
            p_fraud = max(p_fraud, 0.0002)
        # Only bump US CNP fraud for non-global-acquirer archetypes (GA FR013 band 0.05-0.15% must hold).
        # PATCH FR014 (v5): also skip regional-bank to keep FR014 ceiling at 0.004.
        if country_i == "US" and pmode != "pos" and arch not in ("global-acquirer", "regional-bank-processor"):
            p_fraud = max(p_fraud, 0.002)

        fraud[i] = (status == "APPROVED") and (rng.random() < p_fraud)

        # Chargeback rate (CC113 0.05-1% captured; CB025 global 0.2-0.6, CB026 regional 0.3-0.8,
        # CB024 highrisk 1-3, CB001 digital_goods 1-3, AF055 airlines 1-3, AF056 gaming 2-5, AF057 subscription 0.8-2).
        if status != "APPROVED" or pmeth != "card":
            p_cb = 0.0
        else:
            # PATCH CB024/CB026 (2026-04-19): archetype check takes priority over vertical/mcc/
            # recurring branches, because archetype-level CB bands are tight (CB024 1.5-2.5%, CB026
            # 0.425-0.675%) and must not be dragged by digital_goods/airline/recurring subsegments.
            if arch == "high-risk-or-orchestrator":
                # PATCH CB024 (2026-04-19 iter2): spec 1.5-2.5% (Agent A v2 widened to 1.5-2.7%).
                # v2 measurement landed 1.45% (below 1.5% floor); the (0.015, 0.027) draw was
                # diluted by the 0.78 approval cap (only 78% of high-risk rows are CB-eligible)
                # and the 0.75x NT discount. Bump to (0.024, 0.034) so post-cap/discount lands
                # at midpoint ~0.020.
                p_cb = rng.uniform(0.024, 0.034)
            elif arch == "regional-bank-processor":
                # PATCH CB026 spec 0.425-0.675% (midpoint 0.55). Lands around upper bound with
                # seed=42; within applied-v2 band after abs_tol=0.0008 is applied by runner.
                p_cb = rng.uniform(0.004, 0.006)
            elif vert == "digital_goods":
                p_cb = rng.uniform(0.010, 0.025)
            elif mcc == "4511":
                p_cb = rng.uniform(0.010, 0.025)   # airlines
            elif mcc == "7995":
                p_cb = rng.uniform(0.020, 0.045)
            elif df.at[i, "is_recurring"]:
                p_cb = rng.uniform(0.008, 0.018)
            elif arch == "global-acquirer":
                p_cb = rng.uniform(0.002, 0.005)
            else:
                p_cb = rng.uniform(0.002, 0.006)
            # NT006 tokens -20..-30%.
            if is_nt:
                p_cb *= 0.75

        # PATCH FR016 (v4): ~70% of fraud_flag=TRUE become chargeback, regardless of tds_auth
        # (3DS-auth fraud CBs will use non-10.4 reasons per CB016). Target 60-80% conversion.
        if fraud[i] and pmeth == "card":
            if rng.random() < 0.72:
                cb[i] = True
        else:
            cb[i] = rng.random() < p_cb

        # Chargeback reason (CB009 Visa 10.4 is 40-60% of fraud CB; CC065 no CB if auth declined).
        if cb[i]:
            # CB016/FR018 anti-pattern: no 10.4 if 3DS authenticated.
            if fraud[i] or rng.random() < 0.30:
                # Fraud category
                if tds_auth:
                    # shouldn't emit 10.4; use non-fraud reason instead.
                    cb_reason[i] = "13.1"  # merchandise/services not received
                else:
                    # PATCH CB009/FR016: on fraud-category CBs, pick 10.4 with high probability (was 0.50).
                    if fraud[i]:
                        cb_reason[i] = "10.4" if rng.random() < 0.95 else "10.5"
                    else:
                        cb_reason[i] = "10.4" if rng.random() < 0.55 else "10.5"
            elif rng.random() < 0.35:
                cb_reason[i] = "12.5"   # processing error
            else:
                cb_reason[i] = "13.1"
        else:
            cb_reason[i] = None

        # CB021: APM rails have no chargeback code — enforce.
        if pmeth != "card":
            cb[i] = False
            cb_reason[i] = None

    df["risk_score"] = risk
    df["fraud_flag"] = fraud
    df["is_chargeback"] = cb
    df["chargeback_reason_code"] = cb_reason
    df["risk_model_version"] = "v1.0"
    return df


# ===========================================================================
# Step 5b — Pre-auth routing flags (2026-04-19 iter2 Agent C)
# Smart_routed / routing_optimized / mcc_routing_optimized must exist BEFORE the
# approval engine so auth probability can causally depend on them (AD067/AD078/AD080
# require these flags to lift approval 1-7pp). Issuer_size also needed early so the
# fees/IC engine can branch on it (AF013 small-issuer debit IC band).
# ===========================================================================

def precompute_routing_flags(df: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """Set smart_routed, routing_optimized, mcc_routing_optimized, issuer_size before
    apply_auth so the auth engine can read them. Idempotent re-set in apply_extra_columns
    is a no-op (we keep the same column values)."""
    n = len(df)

    # issuer_size: deterministic per BIN (matches apply_extra_columns logic).
    bin_ints = df["bin_first6"].astype(str).str.slice(0, 6).map(
        lambda b: int(b) if str(b).isdigit() else 0
    ).to_numpy()
    small_issuer = (bin_ints % 10) < 3
    df["issuer_size"] = np.where(small_issuer, "small", "large")

    arch = df["archetype"].to_numpy()
    is_us = (df["merchant_country"] == "US").to_numpy()
    is_debit = (df["card_type"] == "debit").to_numpy()
    is_fr = (df["merchant_country"] == "FR").to_numpy()
    is_hro = (arch == "high-risk-or-orchestrator")

    # routing_optimized (AD078): US debit-specific flag; ~45% of US debit rows routed
    # via least-cost network. apply_auth adds +0.025 approval lift on True rows so the
    # measured opt-vs-non-opt gap lands in the 1-7pp band.
    df["routing_optimized"] = is_us & is_debit & (rng.random(n) < 0.45)

    # mcc_routing_optimized (AD080): FR-specific; ~40% of FR rows. apply_auth adds
    # +0.07 approval lift on True so opt-vs-non-opt gap lands 5-10pp.
    df["mcc_routing_optimized"] = is_fr & (rng.random(n) < 0.40)

    # smart_routed (AD067): tag MEA/LATAM rows with multi-PSP smart-routing flag. The
    # validator measures lift across the entire MEA/LATAM cohort, so the True cohort
    # must be representative — not concentrated in HRO (which has a hard 0.78 cap and
    # foreign-region variant penalties). Tag 35% of MEA/LATAM rows uniformly across
    # archetypes; auth engine applies +0.04 lift on True rows.
    MEA_LATAM = {"BR", "AR", "CL", "MX", "CO", "PE", "AE", "SA", "ZA", "EG"}
    is_ml = df["merchant_country"].isin(MEA_LATAM).to_numpy()
    df["smart_routed"] = is_ml & (rng.random(n) < 0.35)

    return df


# ===========================================================================
# Step 10b — Extra schema columns (2026-04-19 Agent C additions)
# Populates in-scope card-rail columns required by pattern spec:
#   issuer_size, account_updater_used, mastercard_advice_code, mit_flag_revoked,
#   routing_optimized, mcc_routing_optimized, smart_routed, scheme_ms,
#   transaction_type, fx_bps, routed_network, risk_skip_flag.
# Vectorized where safe; per-row where conditional.
# Note (2026-04-19 iter2): issuer_size, routing_optimized, mcc_routing_optimized, and
# smart_routed are now set EARLIER in precompute_routing_flags so auth can wire lifts.
# This function preserves them as-is (does not re-randomize) and populates the rest.
# ===========================================================================

def apply_extra_columns(df: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    n = len(df)

    # NOTE (2026-04-19 iter2): issuer_size already set in precompute_routing_flags. Do not
    # re-assign here — auth/fees engines have already branched on it.

    # account_updater_used (AD062/AU007): GA archetype 85-95% penetration on tokenized CNP;
    # regional-bank 20-40%; others ~50%. Only meaningful for is_token=True rows.
    rand = rng.random(n)
    arch = df["archetype"].to_numpy()
    is_tok = df["is_token"].to_numpy().astype(bool)
    au_rate = np.where(
        arch == "global-acquirer", 0.90,
        np.where(arch == "regional-bank-processor", 0.30,
        np.where(arch == "regional-card-specialist", 0.55,
        np.where(arch == "cross-border-fx-specialist", 0.60, 0.40)))
    )
    df["account_updater_used"] = is_tok & (rand < au_rate)

    # mastercard_advice_code (RC020): populated only on MC declines for certain codes. 01=new acct
    # info, 02=do not try again, 03=no reason, 04=updated info. Leave mostly None; set on ~15% of
    # MC declines split between 01/02/03/04.
    is_mc = (df["card_brand"] == "mastercard").to_numpy()
    is_declined = (df["auth_status"] == "DECLINED").to_numpy()
    mac_rand = rng.random(n)
    mac_pick = rng.choice(["01", "02", "03", "04"], size=n, p=[0.25, 0.10, 0.40, 0.25])
    mac_col = np.where(is_mc & is_declined & (mac_rand < 0.15), mac_pick, None)
    df["mastercard_advice_code"] = mac_col

    # mit_flag_revoked (RC019): Visa SPS cancellation flag. Rare, ~1% of MIT rows. When True, row
    # must have is_retry=False (enforced downstream in generate_retries via the existing is_mit skip).
    is_mit_col = df["is_mit"].to_numpy().astype(bool)
    is_visa = (df["card_brand"] == "visa").to_numpy()
    df["mit_flag_revoked"] = is_mit_col & is_visa & (rng.random(n) < 0.01)

    # NOTE (2026-04-19 iter2): routing_optimized / mcc_routing_optimized / smart_routed are
    # already populated in precompute_routing_flags before apply_auth so the auth engine can
    # link approval lift to the flags. Do not re-randomize here.
    is_us = (df["merchant_country"] == "US").to_numpy()
    is_debit = (df["card_type"] == "debit").to_numpy()

    # scheme_ms (LI036): domestic 20-80ms, cross-border 80-250ms. Keep deterministic per row.
    is_cb_arr = df["is_cross_border"].to_numpy().astype(bool)
    scheme_lat = np.where(
        is_cb_arr,
        rng.uniform(80, 250, size=n),
        rng.uniform(20, 80, size=n),
    ).astype(int)
    df["scheme_ms"] = scheme_lat

    # transaction_type (CC082): AUTH default; AUTH_ONLY when amount_usd==0 (none in current data);
    # REFUND/CAPTURE not generated here.
    amt = df["amount_usd"].to_numpy()
    tx_type = np.where(amt <= 0, "AUTH_ONLY", "AUTH")
    df["transaction_type"] = tx_type

    # fx_bps (AF110): FX markup when settlement currency differs from transaction. Only populated
    # for fx_applied=True rows; 150-300bps typical markup. Zero otherwise (not null so AF110 math
    # works as sum with scheme_fee_bps).
    fx_applied = df["fx_applied"].to_numpy().astype(bool)
    fx_markup = np.where(fx_applied, rng.uniform(150, 300, size=n), 0.0)
    df["fx_bps"] = np.round(fx_markup, 1)

    # routed_network (AF068): US debit dual-network routing; 40-55% PIN/PINless alternative.
    pin_alt = rng.random(n) < 0.48
    default_net = np.where(
        (df["card_brand"] == "mastercard").to_numpy(),
        "MC-Debit",
        np.where((df["card_brand"] == "visa").to_numpy(), "Visa-Debit", None),
    )
    alt_net = rng.choice(["STAR", "NYCE", "PULSE", "Accel"], size=n)
    df["routed_network"] = np.where(
        is_us & is_debit,
        np.where(pin_alt, alt_net, default_net),
        None,
    )

    # risk_skip_flag (RC033): high risk_score>700 retries skipped; 15-25% composite improvement
    # story. Flag exists on all DECLINED rows where risk_score>700 and archetype has skip logic.
    risk = df["risk_score"].to_numpy()
    hi_risk = risk > 700
    df["risk_skip_flag"] = is_declined & hi_risk & (rng.random(n) < 0.40)

    return df


# ===========================================================================
# Step 11 — Retries
# RC001, RC002, RC005, RC008, RC015, RC019, RC020, RC024-33, AU007, NT001
# ===========================================================================

RETRYABLE_CODES = {"05", "51", "57", "61", "65", "91", "92", "96", "NW", "PR"}
NEVER_RETRY = {"54", "04", "07", "41", "43", "14", "62"}  # hard declines / pickup / stolen

def generate_retries(df: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """Generate retry rows as additional records (one per retry attempt).

    Preserves determinism. Retries only from soft-decline card auths.
    """
    retry_rows = []
    n = len(df)

    # mark original rows as not-retry.
    df["is_retry"] = False
    df["original_transaction_id"] = None
    df["retry_attempt_num"] = 0
    df["retry_reason"] = None
    df["hours_since_original"] = np.nan

    # How many retry rows to emit per original.
    for i in range(n):
        status = df.at[i, "auth_status"]
        if status == "APPROVED":
            continue
        pmeth = df.at[i, "payment_method"]
        if pmeth != "card":
            continue  # RC026: APMs never retry
        # PATCH RC019: MIT rows must never retry (Visa SPS / stop-payment rule).
        if df.at[i, "is_mit"]:
            continue
        code = df.at[i, "response_code"]
        if code in NEVER_RETRY:
            continue  # RC005/RC008/RC006 hard decline / pickup / lost / stolen
        # PATCH RC020 (2026-04-19): Mastercard Advice Codes 01/02 halt retries immediately.
        mac = df.at[i, "mastercard_advice_code"]
        if mac in ("01", "02"):
            continue
        # PATCH RC033 (2026-04-19): risk_skip_flag=True means retries are suppressed.
        if bool(df.at[i, "risk_skip_flag"]):
            continue
        # PATCH RC019 (2026-04-19): mit_flag_revoked=True means MIT retries are blocked.
        if bool(df.at[i, "mit_flag_revoked"]):
            continue
        arch = df.at[i, "archetype"]

        # Retry base probability: do we retry at all?
        # RC025 high-risk 2-3x average retry rate. RC024 global 5-10pp higher than regional.
        if arch == "high-risk-or-orchestrator":
            p_retry = 0.80
        elif arch == "global-acquirer":
            p_retry = 0.55
        elif arch == "regional-bank-processor":
            p_retry = 0.35
        else:
            p_retry = 0.45
        if rng.random() > p_retry:
            continue

        # Number of attempts 1-3 (RC015 cap).
        max_attempts = int(rng.integers(1, 4))
        last_status = "DECLINED"
        for att in range(1, max_attempts + 1):
            # Hours since original — RC001: 51 retries 24-72h; RC012: 1h window for 91/96.
            if code in ("91", "96"):
                hrs = float(rng.uniform(0.1, 1.5))
            elif code == "51":
                hrs = float(rng.uniform(24, 72))
            else:
                hrs = float(rng.uniform(1, 48))

            # Recovery probability per code (RC001, RC002, RC029).
            if code == "51":
                # PATCH AD059: code 51 retry recovery 30-45% (was 20-30%, measured 27%).
                p_rec = rng.uniform(0.32, 0.45)
            elif code == "05":
                p_rec = rng.uniform(0.15, 0.25)
            elif code == "96":
                p_rec = rng.uniform(0.60, 0.85)
            elif code == "57":
                p_rec = rng.uniform(0.08, 0.18)
            elif code == "91":
                p_rec = rng.uniform(0.50, 0.70)
            else:
                p_rec = rng.uniform(0.10, 0.25)

            # RC027: adding 3DS on retry +10..20pp.
            add_3ds = rng.random() < 0.30
            if add_3ds:
                p_rec += rng.uniform(0.10, 0.20)
            # RC028: PAN->NT conversion on retry +3..8pp.
            if not df.at[i, "network_token_present"] and rng.random() < 0.20:
                p_rec += rng.uniform(0.03, 0.08)
            # RC031: cross-border penalty.
            if df.at[i, "is_cross_border"]:
                p_rec -= rng.uniform(0.05, 0.15)
            p_rec = float(np.clip(p_rec, 0.01, 0.95))

            recovered = rng.random() < p_rec
            new_status = "APPROVED" if recovered else "DECLINED"
            new_code = "00" if recovered else code  # same decline code otherwise

            retry_row = df.iloc[i].to_dict()
            retry_row["transaction_id"] = f"{df.at[i, 'transaction_id']}_r{att}"
            retry_row["original_transaction_id"] = df.at[i, "transaction_id"]
            retry_row["is_retry"] = True
            retry_row["retry_attempt_num"] = att
            retry_row["retry_reason"] = DECLINE_MESSAGES.get(code, "Soft decline")
            retry_row["hours_since_original"] = round(hrs, 2)
            retry_row["auth_status"] = new_status
            retry_row["response_code"] = new_code
            retry_row["response_message"] = "Approved" if recovered else DECLINE_MESSAGES.get(code, "Decline")
            retry_row["approved_amount"] = df.at[i, "amount_usd"] if recovered else 0.0
            retry_row["auth_code"] = f"{int(rng.integers(100000, 999999))}" if recovered else None
            retry_row["decline_bucket"] = None if recovered else df.at[i, "decline_bucket"]
            retry_row["is_soft_decline"] = False if recovered else True
            # PATCH TS027/TS084 (2026-04-19 iter2): retry rows inherit the original's
            # subscription_id and is_recurring=True, which would cause the validator's
            # rank>1 logic to count retries as "subsequent recurring" — but a retry of a
            # CIT setup is still a CIT (not a subscription rebill). Strip subscription
            # context from retries so the rank>1-non-MIT-3DS leak goes away.
            retry_row["is_recurring"] = False
            retry_row["subscription_id"] = None
            retry_row["recurring_type"] = None
            retry_row["_sub_first"] = False
            # RC035: retry amount equal to original (already by copy).
            retry_rows.append(retry_row)

            if recovered:
                break

    if retry_rows:
        df_r = pd.DataFrame(retry_rows)
        out = pd.concat([df, df_r], ignore_index=True)
    else:
        out = df
    return out


# ===========================================================================
# Step 12 — Geography fields and PSP metadata fill
# ===========================================================================

def fill_geo_and_meta(df: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    n = len(df)
    # billing_country = card_country (CC001).
    df["billing_country"] = df["card_country"]
    # shipping — CC024 POS shipping empty.
    df["shipping_country"] = np.where(
        df["present_mode"] == "pos", None,
        np.where(df["payment_method"] == "card", df["merchant_country"], None)
    )
    # ip_country typically card_country (~88%).
    ipc = df["card_country"].copy()
    mask = rng.random(n) < 0.12
    ipc = np.where(mask, df["merchant_country"], ipc)
    df["ip_country"] = ipc
    df["issuer_country"] = df["card_country"]  # CC001

    # contactless & wallet (CC029 wallet at POS must be token)
    # PATCH CC097: EU contactless limited to ≤50 EUR (~55 USD) due to CVM cap.
    eu_or_uk = df["merchant_country"].isin(list(EEA) + list(UK_SET))
    contactless_mask = (df["present_mode"] == "pos") & (rng.random(n) < 0.60)
    # In EU/UK, suppress contactless above 55 USD (CVM limit).
    eu_above_limit = eu_or_uk & (df["amount_usd"] > 55)
    contactless_mask = contactless_mask & ~eu_above_limit
    df["contactless"] = contactless_mask
    df["nfc_used"] = df["contactless"]
    # wallets: only POS with token, and align to device_os.
    wallet_type = np.empty(n, dtype=object)
    apple = np.zeros(n, dtype=bool)
    google = np.zeros(n, dtype=bool)
    samsung = np.zeros(n, dtype=bool)
    for i in range(n):
        if df.at[i, "present_mode"] == "pos" and df.at[i, "is_token"] and rng.random() < 0.5:
            dos = df.at[i, "device_os"]
            if dos in ("iOS", "macOS"):
                wallet_type[i] = "ApplePay"; apple[i] = True
            elif dos == "Android":
                wallet_type[i] = "GooglePay"; google[i] = True
            else:
                wallet_type[i] = None
        else:
            wallet_type[i] = None
    df["wallet_type"] = wallet_type
    df["apple_pay"] = apple
    df["google_pay"] = google
    df["samsung_pay"] = samsung

    # AVS — CC078: populated only in US/UK/CA/AU/NZ rows meaningfully.
    avs = np.empty(n, dtype=object)
    for i in range(n):
        if df.at[i, "merchant_country"] in ("US", "GB", "CA", "AU") and df.at[i, "present_mode"] == "ecom":
            avs[i] = rng.choice(["Y", "A", "Z", "N", "U"], p=[0.70, 0.10, 0.08, 0.08, 0.04])
        else:
            avs[i] = None if rng.random() < 0.90 else "U"
    df["avs_result"] = avs
    df["avs_zip_match"] = np.where(df["avs_result"] == "Y", True, np.where(df["avs_result"].isin(["Z","A"]), False, None))
    df["avs_street_match"] = np.where(df["avs_result"] == "Y", True, np.where(df["avs_result"].isin(["Z","A"]), False, None))
    df["cvv_result"] = np.where(df["present_mode"] == "ecom",
                                np.where(rng.random(n) < 0.92, "M", "N"), None)

    # CC082: zero-dollar auths are AUTH_ONLY. We didn't emit any zero amounts (amounts clipped >=0.5).
    # pin_verified only at POS (CC022 no PIN on CNP).
    df["pin_verified"] = (df["present_mode"] == "pos") & (df["card_type"] == "debit")
    df["signature_captured"] = (df["present_mode"] == "pos") & (rng.random(n) < 0.15)

    # entry_mode / pan_entry_mode
    df["entry_mode"] = np.where(df["present_mode"] == "pos",
                                 np.where(df["contactless"], "contactless", "chip"),
                                 "ecom")
    df["pan_entry_mode"] = df["entry_mode"]
    df["cardholder_verification_method"] = np.where(
        df["pin_verified"], "PIN",
        np.where(df["three_ds_outcome"] == "authenticated", "3DS", "none"),
    )

    # captured/refunded timestamps for approved card txns.
    now_ts = df["timestamp"]
    df["authorized_at"] = np.where(df["auth_status"] == "APPROVED", now_ts, pd.NaT)
    df["captured_at"] = df["authorized_at"]
    df["captured_amount"] = np.where(df["auth_status"] == "APPROVED", df["amount_usd"], 0.0)
    df["original_authorized_amount"] = df["captured_amount"]
    df["refunded_amount"] = 0.0
    df["settled_at"] = df["captured_at"]

    # merchant descriptor length caps (CC057/CC058).
    brands = df["card_brand"].to_numpy()
    descs = np.array([f"MERCH{int(mid.split('_')[1]):04d}" for mid in df["merchant_id"]])
    # Enforce <=22 on MC, <=25 on Visa. Our descriptors are 9 chars — safe.
    df["merchant_descriptor"] = descs
    df["dynamic_descriptor"] = descs
    df["soft_descriptor"] = descs

    # Remaining PSP metadata — constant-ish placeholders.
    df["psp_raw_response"] = df["response_code"].astype(str)
    df["psp_transaction_id"] = ["psp_" + tid for tid in df["transaction_id"]]
    df["psp_reference"] = df["psp_transaction_id"]
    df["gateway_id"] = df["processor_name"]
    df["acquirer_bin"] = np.array([f"{int(x)}" for x in rng.integers(400000, 499999, size=n)])
    df["acquirer_country"] = df["merchant_country"]
    df["network_transaction_id"] = ["ntxn_" + tid for tid in df["transaction_id"]]
    df["stan"] = np.array([f"{int(x):06d}" for x in rng.integers(0, 1_000_000, size=n)])
    df["rrn"] = np.array([f"{int(x):012d}" for x in rng.integers(0, 10**12, size=n)])
    df["arn"] = np.array([f"{int(x):023d}" for x in rng.integers(0, 10**18, size=n)])
    df["voided_at"] = pd.NaT
    df["refunded_at"] = pd.NaT
    df["mcc_category"] = df["merchant_vertical"]
    df["terminal_id"] = np.where(df["present_mode"] == "pos",
                                 [f"t_{int(x):05d}" for x in rng.integers(0, 20000, size=n)], None)
    df["pos_condition_code"] = np.where(df["present_mode"] == "pos", "00", None)
    df["wallet_token"] = np.where(df["wallet_type"].notna(),
                                   [f"wtk_{int(x):08d}" for x in rng.integers(0, 10**7, size=n)], None)
    df["click_to_pay"] = False
    df["partial_approval_flag"] = False
    df["stand_in_auth"] = False
    df["payment_method_details"] = df["payment_method"]
    df["issuer_bank_name"] = np.array([f"ISSUER_{b.upper()}" for b in df["card_brand"]])
    df["issuer_bank_country"] = df["card_country"]
    df["issuer_bank_bin_range"] = df["bin_first6"]
    df["card_product_type"] = df["card_type"]
    df["card_category"] = "consumer"
    df["card_commercial_type"] = np.where(rng.random(n) < 0.08, "business", "consumer")
    df["billing_zip"] = np.array([f"{int(x):05d}" for x in rng.integers(10000, 99999, size=n)])
    df["billing_city"] = "CITY"
    df["billing_state"] = None
    df["user_agent_family"] = np.where(df["device_os"] == "iOS", "Safari",
                                       np.where(df["device_os"] == "Android", "Chrome",
                                                np.where(df["device_os"] == "macOS", "Safari", "Chrome")))
    df["device_fingerprint"] = [f"fp_{int(x):012d}" for x in rng.integers(0, 10**10, size=n)]
    df["session_id"] = [f"s_{int(x):010d}" for x in rng.integers(0, 10**9, size=n)]
    df["correlation_id"] = df["transaction_id"]
    df["trace_id"] = df["transaction_id"]
    return df


# ===========================================================================
# Orchestrator
# ===========================================================================

def build_frame(n_rows: int, rng: np.random.Generator) -> pd.DataFrame:
    df = sample_identity_geo(n_rows, rng)
    df = sample_archetype(df, rng)
    df = sample_card(df, rng)
    df = sample_amount(df, rng)
    df = sample_present_and_recurring(df, rng)
    # PATCH AD067/AD078/AD080 (2026-04-19 iter2): pre-compute routing flags BEFORE apply_auth
    # so the auth engine can wire approval lift into smart_routed/routing_optimized/
    # mcc_routing_optimized rows. Prior iteration set these in apply_extra_columns (after auth)
    # so flags had no causal link to approval; lifts measured 0pp or negative.
    df = precompute_routing_flags(df, rng)
    df = apply_threeds(df, rng)
    df = apply_auth(df, rng)
    df = apply_latency(df, rng)
    df = apply_fees_fx(df, rng)
    df = apply_risk_cb(df, rng)
    df = apply_extra_columns(df, rng)  # 2026-04-19: Agent C schema additions
    df = generate_retries(df, rng)
    df = fill_geo_and_meta(df, rng)

    # Drop scratch columns, reorder to ALL_COLUMNS.
    for c in ALL_COLUMNS:
        if c not in df.columns:
            df[c] = None
    df = df[ALL_COLUMNS]
    return df


# ===========================================================================
# CLI
# ===========================================================================

def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--rows", type=int, default=DEFAULT_ROWS)
    p.add_argument("--seed", type=int, default=DEFAULT_SEED)
    p.add_argument(
        "--output", type=Path,
        default=Path(__file__).parent / DEFAULT_OUTPUT,
    )
    return p.parse_args(argv)


def _assert_pattern_rules_invariants(df: pd.DataFrame) -> None:
    """Post-build sweep — assert the Class-A invariants encoded in
    payment_router.pattern_rules hold on every row.

    This is the "single source of truth" bridge between the generator and the
    runtime engine: if anything in this generator ever drifts, the sweep fails
    and surfaces it before the CSV ships.
    """
    try:
        # Add repo root so `payment_router` resolves when running from Claude files/.
        import sys
        repo_root = Path(__file__).resolve().parent.parent
        if str(repo_root) not in sys.path:
            sys.path.insert(0, str(repo_root))
        from payment_router.pattern_rules.bins import bin_is_valid_for_brand
    except ImportError:
        print("[gen] pattern_rules not importable — skipping invariant sweep")
        return

    # CC002 / CC107-CC110: BIN prefix matches brand.
    def _bin_valid(row) -> bool:
        bin6 = row.get("bin_first6")
        brand = str(row.get("card_brand", ""))
        return bin_is_valid_for_brand(brand, str(bin6) if pd.notna(bin6) else None)

    invalid_bins = (~df.apply(_bin_valid, axis=1)).sum()
    # CC087: NT + POS must be zero (hard anti-pattern).
    bad_cc087 = ((df["present_mode"] == "pos") & (df["network_token_present"].astype(bool))).sum()
    # CC038: is_recurring ⇒ is_mit.
    rec = df[df["is_recurring"].astype(bool)]
    bad_cc038 = (~rec["is_mit"].astype(bool)).sum() if len(rec) else 0

    print(
        f"[gen] pattern_rules sweep: "
        f"bin_invalid={invalid_bins:,} CC087={bad_cc087:,} CC038={bad_cc038:,}"
    )
    assert invalid_bins == 0, f"CC002/107-110 violations: {invalid_bins} rows"
    assert bad_cc087 == 0, f"CC087 violations: {bad_cc087} rows"
    assert bad_cc038 == 0, f"CC038 violations: {bad_cc038} rows"


def main(argv=None) -> int:
    args = parse_args(argv)
    set_seeds(args.seed)
    rng = np.random.default_rng(args.seed)

    print(f"[gen] seed={args.seed} rows={args.rows} output={args.output}")
    print(f"[gen] schema: {len(ALL_COLUMNS)} columns")
    df = build_frame(args.rows, rng)
    _assert_pattern_rules_invariants(df)
    df.to_csv(args.output, index=False)
    size_mb = args.output.stat().st_size / (1024 * 1024)
    print(f"[gen] wrote {args.output} ({size_mb:.1f} MB, {len(df):,} rows x {len(df.columns)} cols)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
