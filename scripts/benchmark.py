"""Routing strategy benchmark.

Compares two routing strategies on a synthetic transaction book and reports
the blended approval rate, latency, and per-market breakdown.

Strategy A (baseline): always route to global-acquirer
Strategy B (smart):    route to best provider per merchant country, with
                       soft-decline retry cascade to second-best

Usage:
    python scripts/benchmark.py
    python scripts/benchmark.py --runs 5000 --seed 42
"""

from __future__ import annotations

import argparse
import random
import sys
from dataclasses import dataclass, field
from pathlib import Path

# Make sure the package is importable when run from the project root
sys.path.insert(0, str(Path(__file__).parent.parent))

from payment_router.engine import simulate_transaction, simulate_with_retry
from payment_router.issuer_tiers import get_issuer_tier
from payment_router.models import CardBrand, CardType, SimulateRequest
from payment_router.provider_loader import clear_cache, list_providers

# ---------------------------------------------------------------------------
# Synthetic transaction book — realistic market + card mix
# ---------------------------------------------------------------------------

MARKET_WEIGHTS = {
    "US": 0.28,
    "GB": 0.10,
    "DE": 0.09,
    "BR": 0.09,
    "MX": 0.07,
    "IN": 0.07,
    "CA": 0.05,
    "AU": 0.05,
    "SG": 0.04,
    "FR": 0.04,
    "AE": 0.03,
    "JP": 0.03,
    "ZA": 0.02,
    "CO": 0.02,
    "AR": 0.02,
}

CARD_BRAND_WEIGHTS = {
    CardBrand.VISA: 0.52,
    CardBrand.MASTERCARD: 0.34,
    CardBrand.AMEX: 0.14,
}

CARD_TYPE_WEIGHTS = {
    CardType.CREDIT: 0.60,
    CardType.DEBIT: 0.32,
    CardType.PREPAID: 0.08,
}

# 20% of transactions are cross-border (issuer country != merchant country)
CROSS_BORDER_RATE = 0.20

# Issuer country pool for cross-border (weighted toward realistic corridors)
CROSS_BORDER_ISSUERS = {
    "US": 0.20, "IN": 0.12, "CN": 0.10, "BR": 0.09, "MX": 0.07,
    "GB": 0.07, "DE": 0.06, "NG": 0.05, "PK": 0.04, "PH": 0.04,
    "VN": 0.03, "EG": 0.03, "TH": 0.03, "AR": 0.03, "CO": 0.02,
    "ZA": 0.02,
}

# Routing map for Strategy B: merchant_country → [primary, fallback]
SMART_ROUTING: dict[str, list[str]] = {
    "US": ["global-acquirer-a", "global-acquirer-b"],
    "CA": ["global-acquirer-a", "global-acquirer-b"],
    "GB": ["regional-bank-processor-b", "global-acquirer-a"],
    "AU": ["regional-bank-processor-c", "global-acquirer-a"],
    "DE": ["regional-card-specialist-a", "global-acquirer-a"],
    "FR": ["regional-card-specialist-a", "global-acquirer-a"],
    "NL": ["regional-card-specialist-a", "global-acquirer-a"],
    "BR": ["regional-card-specialist-b", "regional-bank-processor-a"],
    "MX": ["regional-card-specialist-b", "regional-bank-processor-a"],
    "IN": ["regional-card-specialist-b", "regional-bank-processor-a"],
    "SG": ["cross-border-fx-specialist-a", "global-acquirer-a"],
    "JP": ["cross-border-fx-specialist-a", "global-acquirer-a"],
    "AE": ["regional-bank-processor-a", "global-acquirer-a"],
    "CO": ["regional-card-specialist-b", "regional-bank-processor-a"],
    "AR": ["regional-card-specialist-b", "global-acquirer-b"],
    "ZA": ["global-acquirer-a", "cross-border-fx-specialist-a"],
}
_DEFAULT_SMART = ["global-acquirer-a", "global-acquirer-b"]


@dataclass
class MarketStats:
    country: str
    runs: int = 0
    approved_a: int = 0
    approved_b: int = 0
    latency_a: list[float] = field(default_factory=list)
    latency_b: list[float] = field(default_factory=list)

    @property
    def rate_a(self) -> float:
        return self.approved_a / self.runs if self.runs else 0.0

    @property
    def rate_b(self) -> float:
        return self.approved_b / self.runs if self.runs else 0.0

    @property
    def delta(self) -> float:
        return self.rate_b - self.rate_a

    @property
    def p50_a(self) -> float:
        s = sorted(self.latency_a)
        return s[len(s) // 2] if s else 0.0

    @property
    def p50_b(self) -> float:
        s = sorted(self.latency_b)
        return s[len(s) // 2] if s else 0.0


def _sample_transaction(rng: random.Random) -> SimulateRequest:
    countries = list(MARKET_WEIGHTS.keys())
    weights = list(MARKET_WEIGHTS.values())
    merchant_country = rng.choices(countries, weights=weights, k=1)[0]

    card_brand = rng.choices(list(CARD_BRAND_WEIGHTS.keys()),
                              weights=list(CARD_BRAND_WEIGHTS.values()), k=1)[0]
    card_type = rng.choices(list(CARD_TYPE_WEIGHTS.keys()),
                             weights=list(CARD_TYPE_WEIGHTS.values()), k=1)[0]
    amount = round(rng.lognormvariate(5.0, 1.2), 2)  # log-normal amount distribution

    issuer_country = None
    if rng.random() < CROSS_BORDER_RATE:
        issuers = list(CROSS_BORDER_ISSUERS.keys())
        iweights = list(CROSS_BORDER_ISSUERS.values())
        issuer = rng.choices(issuers, weights=iweights, k=1)[0]
        if issuer != merchant_country:
            issuer_country = issuer

    return SimulateRequest(
        provider="global-acquirer-a",  # overridden per strategy
        country=merchant_country,
        issuer_country=issuer_country,
        card_brand=card_brand,
        card_type=card_type,
        amount=min(amount, 10_000),
        currency="USD",
    )


def run_benchmark(n: int, seed: int) -> None:
    clear_cache()
    rng = random.Random(seed)
    transactions = [_sample_transaction(rng) for _ in range(n)]

    market_stats: dict[str, MarketStats] = {
        c: MarketStats(c) for c in MARKET_WEIGHTS
    }

    total_a = total_b = 0
    approved_a = approved_b = 0
    latency_a_all: list[float] = []
    latency_b_all: list[float] = []

    for txn in transactions:
        c = txn.country

        # Strategy A: always global-acquirer
        req_a = txn.model_copy(update={"provider": "global-acquirer-a"})
        resp_a = simulate_transaction(req_a)

        # Strategy B: smart routing with retry
        providers_b = SMART_ROUTING.get(c, _DEFAULT_SMART)
        result_b = simulate_with_retry(txn, providers_b, max_attempts=2)
        resp_b = result_b.final_response

        total_a += 1
        total_b += 1
        if resp_a.approved:
            approved_a += 1
        if resp_b.approved:
            approved_b += 1

        latency_a_all.append(resp_a.latency_ms)
        latency_b_all.append(result_b.total_latency_ms)

        if c in market_stats:
            ms = market_stats[c]
            ms.runs += 1
            if resp_a.approved:
                ms.approved_a += 1
            if resp_b.approved:
                ms.approved_b += 1
            ms.latency_a.append(resp_a.latency_ms)
            ms.latency_b.append(result_b.total_latency_ms)

    # ---------------------------------------------------------------------------
    # Report
    # ---------------------------------------------------------------------------
    rate_a = approved_a / total_a
    rate_b = approved_b / total_b
    delta = rate_b - rate_a

    p50_a = sorted(latency_a_all)[len(latency_a_all) // 2]
    p50_b = sorted(latency_b_all)[len(latency_b_all) // 2]

    print(f"\n{'='*62}")
    print(f"  Routing Strategy Benchmark  —  {n:,} transactions  (seed={seed})")
    print(f"{'='*62}")
    print(f"\n  Strategy A  global-acquirer-a (always)")
    print(f"  Strategy B  smart routing + soft-decline retry\n")
    print(f"  {'Metric':<28} {'Strategy A':>12} {'Strategy B':>12} {'Delta':>8}")
    print(f"  {'-'*62}")
    print(f"  {'Blended approval rate':<28} {rate_a:>11.1%} {rate_b:>11.1%} {delta:>+7.1%}")
    print(f"  {'Latency p50 (ms)':<28} {p50_a:>11.0f} {p50_b:>11.0f} {p50_b-p50_a:>+7.0f}")
    print(f"  {'Transactions approved':<28} {approved_a:>11,} {approved_b:>11,} {approved_b-approved_a:>+7,}")

    print(f"\n  Per-market breakdown (markets with >={n//50} transactions):\n")
    print(f"  {'Country':<8} {'Runs':>6} {'Rate A':>8} {'Rate B':>8} {'Delta':>8} {'p50 A':>7} {'p50 B':>7}")
    print(f"  {'-'*58}")

    sorted_markets = sorted(
        [ms for ms in market_stats.values() if ms.runs >= n // 50],
        key=lambda x: x.delta,
        reverse=True,
    )
    for ms in sorted_markets:
        marker = " <--" if abs(ms.delta) >= 0.05 else ""
        print(
            f"  {ms.country:<8} {ms.runs:>6} {ms.rate_a:>7.1%} {ms.rate_b:>7.1%} "
            f"{ms.delta:>+7.1%} {ms.p50_a:>7.0f} {ms.p50_b:>7.0f}{marker}"
        )

    print(f"\n  Key finding: smart routing moves blended approval {delta:+.1%} "
          f"({delta*n:+.0f} additional approved transactions in this run)")

    if p50_b > p50_a:
        overhead = p50_b - p50_a
        print(f"  Latency tradeoff: +{overhead:.0f}ms p50 from retry overhead on soft declines")
    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Routing strategy benchmark")
    parser.add_argument("--runs", type=int, default=2000, help="Number of transactions (default: 2000)")
    parser.add_argument("--seed", type=int, default=42, help="Random seed (default: 42)")
    args = parser.parse_args()
    run_benchmark(args.runs, args.seed)
