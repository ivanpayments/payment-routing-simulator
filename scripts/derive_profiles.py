"""derive_profiles.py — regenerate all provider YAML profiles from routing_transactions.csv.

Reads the raw CSV and computes per-provider profiles with per-country overrides.
Outputs one YAML file per processor_name to payment_router/providers/.

What it derives:
  - base_approval_rate         overall approval rate across all transactions
  - latency p50/p95/p99        from latency_auth_ms
  - three_ds challenge_rate    from three_ds_flow where three_ds_requested=True
  - card_type_modifiers        approval rate by card_type relative to credit baseline
  - decline_codes              top decline codes with weights (declined txns only)
  - per-country overrides      base, card_modifiers, three_ds, latency
    - included when country has >= MIN_COUNTRY_TXN transactions
    - latency override written when country p50 differs from global p50 by > LATENCY_DELTA_PCT

Usage:
    python scripts/derive_profiles.py \\
        --csv "Claude files/routing_transactions.csv" \\
        --output payment_router/providers/
    python scripts/derive_profiles.py \\
        --csv "Claude files/routing_transactions.csv" \\
        --output payment_router/providers/ \\
        --min-txn 50 --latency-delta 0.20
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------

# Minimum transactions for a country override to be written
MIN_COUNTRY_TXN = 30
# Minimum transactions for a card-brand modifier to be written per country
MIN_BRAND_TXN = 10
# Minimum 3DS transactions to write a country 3DS override
MIN_3DS_TXN = 15
# Write a country latency override when p50 differs from global p50 by this fraction
LATENCY_DELTA_PCT = 0.15
# Minimum declined transactions to include a decline code
MIN_DECLINE_CODE_TXN = 3
# Number of top decline codes to keep per provider
TOP_DECLINE_CODES = 8

# Card brands we model (others like rupay, jcb, unionpay → mapped to closest or excluded)
TRACKED_BRANDS = {"visa", "mastercard", "amex"}

# Map CSV processor_name → archetype variant name (our 10 YAML files)
# regional-bank variants: a=LATAM, b=EU, c=APAC
# regional-card-specialist: a=EU, b=LATAM
# cross-border-fx: a=APAC corridor, b=EU corridor
PROCESSOR_TO_ARCHETYPE: dict[str, str] = {
    "global-acquirer-a":      "global-acquirer-a",
    "global-acquirer-b":      "global-acquirer-b",
    "fx-cross-border":        "cross-border-fx-specialist-a",
    "orchestrator-high-risk": "high-risk-or-orchestrator-a",
    "regional-bank-ae":       "regional-bank-processor-a",
    "regional-bank-br":       "regional-bank-processor-a",
    "regional-bank-in":       "regional-bank-processor-a",
    "regional-bank-mx":       "regional-bank-processor-a",
    "apm-specialist-in":      "regional-card-specialist-b",
    "apm-specialist-latam":   "regional-card-specialist-b",
    "apm-specialist-sepa":    "regional-card-specialist-a",
}

# Supported currencies per provider archetype (hand-coded — CSV doesn't vary by archetype)
SUPPORTED_CURRENCIES: dict[str, list[str]] = {
    "global-acquirer-a":            ["USD","EUR","GBP","BRL","MXN","INR","AUD","CAD","SGD","JPY","AED"],
    "global-acquirer-b":            ["USD","EUR","GBP","BRL","MXN","INR","AUD","CAD","SGD","JPY","AED"],
    "regional-bank-processor-a":    ["USD","BRL","MXN","COP","ARS","CLP","PEN","AED"],
    "regional-bank-processor-b":    ["USD","EUR","GBP","CHF","SEK","NOK","DKK","PLN"],
    "regional-bank-processor-c":    ["USD","AUD","NZD","SGD","HKD","JPY","MYR"],
    "regional-card-specialist-a":   ["USD","EUR","GBP","CHF","SEK","NOK","DKK","PLN","CZK"],
    "regional-card-specialist-b":   ["USD","BRL","MXN","COP","ARS","CLP","PEN"],
    "cross-border-fx-specialist-a": ["USD","SGD","HKD","JPY","AUD","MYR","THB","IDR","PHP","NZD"],
    "cross-border-fx-specialist-b": ["USD","EUR","GBP","CHF","SEK","NOK","DKK","PLN","HUF","CZK"],
    "high-risk-or-orchestrator-a":  ["USD","EUR","GBP","CAD","AUD"],
    "high-risk-or-orchestrator-b":  ["USD","EUR","GBP","CAD","AUD"],
}

AMOUNT_THRESHOLDS: dict[str, dict[str, float]] = {
    "global-acquirer-a":            {"100": 1.00, "500": 0.99, "1000": 0.97, "5000": 0.95},
    "global-acquirer-b":            {"100": 1.00, "500": 0.99, "1000": 0.97, "5000": 0.94},
    "regional-bank-processor-a":    {"100": 1.00, "500": 0.98, "1000": 0.95, "5000": 0.90},
    "regional-bank-processor-b":    {"100": 1.00, "500": 0.99, "1000": 0.96, "5000": 0.92},
    "regional-bank-processor-c":    {"100": 1.00, "500": 0.98, "1000": 0.96, "5000": 0.91},
    "regional-card-specialist-a":   {"100": 1.00, "500": 1.00, "1000": 0.98, "5000": 0.96},
    "regional-card-specialist-b":   {"100": 1.00, "500": 0.99, "1000": 0.97, "5000": 0.94},
    "cross-border-fx-specialist-a": {"100": 1.00, "500": 0.98, "1000": 0.95, "5000": 0.91},
    "cross-border-fx-specialist-b": {"100": 1.00, "500": 0.98, "1000": 0.96, "5000": 0.92},
    "high-risk-or-orchestrator-a":  {"100": 1.00, "500": 0.97, "1000": 0.94, "5000": 0.89},
    "high-risk-or-orchestrator-b":  {"100": 1.00, "500": 0.97, "1000": 0.93, "5000": 0.86},
}

DISPLAY_NAMES = {
    "global-acquirer-a":            "Global Acquirer A",
    "global-acquirer-b":            "Global Acquirer B",
    "regional-bank-processor-a":    "Regional Bank Processor A",
    "regional-bank-processor-b":    "Regional Bank Processor B",
    "regional-bank-processor-c":    "Regional Bank Processor C",
    "regional-card-specialist-a":   "Regional Card Specialist A",
    "regional-card-specialist-b":   "Regional Card Specialist B",
    "cross-border-fx-specialist-a": "FX Cross-Border Specialist A",
    "cross-border-fx-specialist-b": "FX Cross-Border Specialist B",
    "high-risk-or-orchestrator-a":  "High-Risk Orchestrator A",
    "high-risk-or-orchestrator-b":  "High-Risk Orchestrator B",
}

# v2.2 rate is hand-tuned (CSV has version column but not always reliable)
VERSION_2_2_RATE = 0.78


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _percentile(series: "pd.Series[float]", pct: int) -> float:
    return float(np.percentile(series.dropna(), pct))


def _approval_rate(df: "pd.DataFrame") -> float:
    if len(df) == 0:
        return 0.0
    return float((df["auth_status"] == "APPROVED").sum() / len(df))


def _card_type_modifiers(df: "pd.DataFrame") -> dict:
    """Compute card_type modifiers relative to credit baseline."""
    credit_rate = _approval_rate(df[df["card_type"] == "credit"])
    if credit_rate == 0:
        return {"credit": 1.0, "debit": 0.97, "prepaid": 0.88, "commercial": 0.99}

    modifiers: dict[str, float] = {}
    for ctype in ("credit", "debit", "prepaid", "commercial"):
        sub = df[df["card_type"] == ctype]
        if len(sub) >= MIN_BRAND_TXN:
            raw = _approval_rate(sub) / credit_rate
            # Clamp to [0.50, 1.50] — schema constraint
            modifiers[ctype] = round(max(0.50, min(1.50, raw)), 2)
        else:
            # Fallback to sensible defaults
            defaults = {"credit": 1.00, "debit": 0.97, "prepaid": 0.88, "commercial": 0.99}
            modifiers[ctype] = defaults[ctype]
    return modifiers


def _global_three_ds(df: "pd.DataFrame") -> dict | None:
    """Global 3DS profile from rows where three_ds_requested=True."""
    three_ds_df = df[df["three_ds_requested"] == True]
    if len(three_ds_df) < MIN_3DS_TXN:
        return None
    challenge = (three_ds_df["three_ds_flow"] == "challenge").sum()
    challenge_rate = round(float(challenge / len(three_ds_df)), 2)
    frictionless_rate = round(1.0 - challenge_rate, 2)
    return {
        "challenge_rate": challenge_rate,
        "frictionless_rate": frictionless_rate,
        "version_2_2_rate": VERSION_2_2_RATE,
    }


def _country_three_ds(country_df: "pd.DataFrame", global_three_ds: dict | None) -> dict | None:
    """Country-level 3DS override — only write if data exists and differs meaningfully."""
    three_ds_df = country_df[country_df["three_ds_requested"] == True]
    if len(three_ds_df) < MIN_3DS_TXN:
        return None
    challenge = (three_ds_df["three_ds_flow"] == "challenge").sum()
    challenge_rate = round(float(challenge / len(three_ds_df)), 2)
    frictionless_rate = round(1.0 - challenge_rate, 2)
    profile = {
        "challenge_rate": challenge_rate,
        "frictionless_rate": frictionless_rate,
        "version_2_2_rate": VERSION_2_2_RATE,
    }
    # Only write if it differs from global by >= 0.03
    if global_three_ds is not None:
        diff = abs(challenge_rate - global_three_ds["challenge_rate"])
        if diff < 0.03:
            return None
    return profile


def _decline_codes(df: "pd.DataFrame", n: int = TOP_DECLINE_CODES) -> list[dict]:
    """Top N decline codes with normalised weights summing to 100."""
    declined = df[df["auth_status"] == "DECLINED"]
    if len(declined) == 0:
        return []
    counts = declined["response_code"].value_counts()
    counts = counts[counts >= MIN_DECLINE_CODE_TXN]
    counts = counts.head(n)
    total = counts.sum()
    result = []
    for code, cnt in counts.items():
        result.append({
            "code": str(code),
            "weight": round(float(cnt / total * 100), 1),
        })
    return result


def _card_brand_modifiers(country_df: "pd.DataFrame") -> dict | None:
    """Card brand modifiers relative to visa baseline. Only include brands with enough data."""
    visa_rate = _approval_rate(country_df[country_df["card_brand"] == "visa"])
    if visa_rate == 0 or len(country_df[country_df["card_brand"] == "visa"]) < MIN_BRAND_TXN:
        return None

    modifiers: dict[str, float] = {}
    for brand in ("visa", "mastercard", "amex"):
        sub = country_df[country_df["card_brand"] == brand]
        if len(sub) >= MIN_BRAND_TXN:
            raw = _approval_rate(sub) / visa_rate
            modifiers[brand] = round(max(0.50, min(1.50, raw)), 2)

    if len(modifiers) <= 1:
        return None
    return modifiers


def _latency_profile(df: "pd.DataFrame") -> dict:
    lat = df["latency_auth_ms"].dropna()
    if len(lat) < 5:
        lat = df["latency_ms"].dropna()
    return {
        "p50_ms": int(_percentile(lat, 50)),
        "p95_ms": int(_percentile(lat, 95)),
        "p99_ms": int(_percentile(lat, 99)),
    }


def _country_latency_override(country_df: "pd.DataFrame", global_p50: int) -> dict | None:
    """Write a country latency override when p50 differs from global by > threshold."""
    lat = country_df["latency_auth_ms"].dropna()
    if len(lat) < 20:
        return None
    country_p50 = int(_percentile(lat, 50))
    if abs(country_p50 - global_p50) / global_p50 < LATENCY_DELTA_PCT:
        return None
    return {
        "p50_ms": country_p50,
        "p95_ms": int(_percentile(lat, 95)),
        "p99_ms": int(_percentile(lat, 99)),
    }


# ---------------------------------------------------------------------------
# Build YAML dict for one provider
# ---------------------------------------------------------------------------

def build_provider_profile(name: str, df: "pd.DataFrame") -> dict:
    """Compute the full profile dict for one processor_name."""
    global_rate = _approval_rate(df)
    global_lat = _latency_profile(df)
    global_p50 = global_lat["p50_ms"]
    global_3ds = _global_three_ds(df)
    ctm = _card_type_modifiers(df)
    codes = _decline_codes(df)

    profile: dict = {
        "name": name,
        "display_name": DISPLAY_NAMES.get(name, name.replace("-", " ").title()),
        "base_approval_rate": round(global_rate, 2),
        "latency": global_lat,
    }

    if global_3ds:
        profile["three_ds"] = global_3ds

    profile["card_type_modifiers"] = ctm

    if codes:
        profile["decline_codes"] = codes

    currencies = SUPPORTED_CURRENCIES.get(name)
    if currencies:
        profile["supported_currencies"] = currencies

    amount_thresholds = AMOUNT_THRESHOLDS.get(name)
    if amount_thresholds:
        profile["amount_modifier_thresholds"] = amount_thresholds

    # -----------------------------------------------------------------------
    # Per-country overrides
    # -----------------------------------------------------------------------
    countries: dict[str, dict] = {}

    country_groups = df.groupby("merchant_country")
    for country_code, c_df in sorted(country_groups, key=lambda x: x[0]):
        if len(c_df) < MIN_COUNTRY_TXN:
            continue

        c_base = _approval_rate(c_df)
        c_mods = _card_brand_modifiers(c_df)
        c_3ds = _country_three_ds(c_df, global_3ds)
        c_lat = _country_latency_override(c_df, global_p50)

        entry: dict = {"base": round(c_base, 2)}

        if c_mods:
            entry["card_modifiers"] = c_mods

        if c_lat:
            entry["latency"] = c_lat

        if c_3ds:
            entry["three_ds"] = c_3ds

        countries[country_code] = entry

    if countries:
        profile["countries"] = countries

    return profile


# ---------------------------------------------------------------------------
# YAML serialisation
# ---------------------------------------------------------------------------

class _FloatDumper(yaml.Dumper):
    """Custom dumper: floats with 2dp, no aliases."""
    pass


def _float_representer(dumper: yaml.Dumper, value: float) -> yaml.Node:
    # Represent as plain scalar, 2 decimal places
    return dumper.represent_scalar("tag:yaml.org,2002:float", f"{value:.2f}")


_FloatDumper.add_representer(float, _float_representer)


def _write_yaml(profile: dict, output_dir: Path) -> Path:
    name = profile["name"]
    out_path = output_dir / f"{name}.yaml"
    with open(out_path, "w", encoding="utf-8") as f:
        yaml.dump(
            profile,
            f,
            Dumper=_FloatDumper,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
        )
    return out_path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Derive provider profiles from transaction CSV")
    parser.add_argument("--csv", required=True, help="Path to routing_transactions.csv")
    parser.add_argument(
        "--output",
        default="payment_router/providers/",
        help="Output directory for YAML files (default: payment_router/providers/)",
    )
    parser.add_argument(
        "--min-txn", type=int, default=MIN_COUNTRY_TXN,
        help=f"Minimum transactions per country for override (default: {MIN_COUNTRY_TXN})",
    )
    parser.add_argument(
        "--latency-delta", type=float, default=LATENCY_DELTA_PCT,
        help=f"Latency p50 delta fraction to trigger country override (default: {LATENCY_DELTA_PCT})",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print summary without writing files",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    csv_path = Path(args.csv)
    output_dir = Path(args.output)

    global MIN_COUNTRY_TXN, LATENCY_DELTA_PCT
    MIN_COUNTRY_TXN = args.min_txn
    LATENCY_DELTA_PCT = args.latency_delta

    if not csv_path.exists():
        print(f"ERROR: CSV not found: {csv_path}", file=sys.stderr)
        sys.exit(1)

    if not args.dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Reading {csv_path} ...")
    df = pd.read_csv(csv_path, low_memory=False)
    print(f"  {len(df):,} rows, {df['processor_name'].nunique()} providers")

    # Normalise
    df["card_brand"] = df["card_brand"].str.lower().str.strip()
    df["card_type"] = df["card_type"].str.lower().str.strip()
    df["auth_status"] = df["auth_status"].str.upper().str.strip()
    df["three_ds_requested"] = df["three_ds_requested"].astype(str).str.upper() == "TRUE"
    df["three_ds_flow"] = df["three_ds_flow"].fillna("").str.lower().str.strip()

    # Map to archetypes and pool sub-archetypes
    df["archetype_name"] = df["processor_name"].map(PROCESSOR_TO_ARCHETYPE)
    unknown = df[df["archetype_name"].isna()]["processor_name"].unique()
    if len(unknown):
        print(f"  WARNING: unmapped processor_names (will be skipped): {list(unknown)}", file=sys.stderr)
    df = df[df["archetype_name"].notna()]

    archetypes = sorted(df["archetype_name"].unique())
    raw_names = sorted(df["processor_name"].dropna().unique())
    print(f"  Raw processor_names: {', '.join(raw_names)}")
    print(f"  Consolidated to {len(archetypes)} archetypes: {', '.join(archetypes)}\n")

    for provider_name in archetypes:
        p_df = df[df["archetype_name"] == provider_name].copy()
        print(f"  {provider_name:<28} {len(p_df):>7,} txns  ", end="")

        profile = build_provider_profile(provider_name, p_df)
        n_countries = len(profile.get("countries", {}))
        print(
            f"approval={profile['base_approval_rate']:.1%}  "
            f"latency_p50={profile['latency']['p50_ms']}ms  "
            f"countries={n_countries}"
        )

        if not args.dry_run:
            out_path = _write_yaml(profile, output_dir)
            print(f"    written: {out_path}")

    if args.dry_run:
        print("\n  [dry-run] No files written.")
    else:
        print(f"\n  Done — {len(archetypes)} YAML files written to {output_dir}")


if __name__ == "__main__":
    main()
