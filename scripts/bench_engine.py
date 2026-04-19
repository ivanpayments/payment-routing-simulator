"""Per-request pattern-rule overhead benchmark.

Runs N /simulate calls with APPLY_PATTERN_RULES=True vs False, reports mean,
median, p95, p99 per-request latency in microseconds. Target added overhead
from rule evaluation: <1 ms (1,000 us) at p95.

Usage:
    python scripts/bench_engine.py
    python scripts/bench_engine.py --runs 10000 --seed 42
"""
from __future__ import annotations

import argparse
import os
import random
import statistics
import sys
import time
from pathlib import Path

# Make sure the package is importable when run from the project root
sys.path.insert(0, str(Path(__file__).parent.parent))

from payment_router.models import CardBrand, CardType, SimulateRequest
from payment_router.provider_loader import clear_cache


_MARKETS = ["US", "GB", "DE", "BR", "IN", "JP", "FR", "MX", "CA", "AU"]
_BRANDS = [CardBrand.VISA, CardBrand.MASTERCARD, CardBrand.AMEX, CardBrand.JCB, CardBrand.UNIONPAY]
_TYPES = [CardType.CREDIT, CardType.DEBIT]


def _sample_request(rng: random.Random) -> SimulateRequest:
    country = rng.choice(_MARKETS)
    brand = rng.choice(_BRANDS)
    return SimulateRequest(
        provider="global-acquirer-a",
        country=country,
        card_brand=brand,
        card_type=rng.choice(_TYPES),
        amount=round(rng.lognormvariate(4.5, 1.0), 2),
        currency="USD",
        use_3ds=rng.random() < 0.3,
        is_recurring=rng.random() < 0.1,
        network_token_present=rng.random() < 0.2,
        present_mode=rng.choice(["ecom", "ecom", "ecom", "pos"]),
        smart_routed=country in ("BR", "MX", "AE") and rng.random() < 0.35,
        routing_optimized=country == "US" and rng.random() < 0.45,
        mcc_routing_optimized=country == "FR" and rng.random() < 0.40,
    )


def _run(n: int, seed: int, rules_on: bool) -> list[float]:
    """Run n simulate calls, return per-call latency in microseconds."""
    os.environ["APPLY_PATTERN_RULES"] = "1" if rules_on else "0"

    # Reload engine module so the flag is re-read.
    import importlib
    import payment_router.engine as _eng
    importlib.reload(_eng)

    clear_cache()
    rng = random.Random(seed)
    requests = [_sample_request(rng) for _ in range(n)]

    samples: list[float] = []
    for req in requests:
        t0 = time.perf_counter()
        _eng.simulate_transaction(req)
        t1 = time.perf_counter()
        samples.append((t1 - t0) * 1_000_000.0)  # microseconds
    return samples


def _pct(xs: list[float], p: float) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    idx = min(len(s) - 1, int(len(s) * p))
    return s[idx]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", type=int, default=10_000)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    print(f"\n[bench] {args.runs:,} calls each, seed={args.seed}")

    # WARMUP — cache provider profiles, JIT any per-process caches.
    _run(500, args.seed, rules_on=True)
    _run(500, args.seed, rules_on=False)

    off = _run(args.runs, args.seed, rules_on=False)
    on = _run(args.runs, args.seed, rules_on=True)

    def _fmt(xs: list[float], label: str) -> str:
        return (
            f"  {label:<10}"
            f" mean={statistics.mean(xs):>7.1f} us"
            f" p50={_pct(xs, 0.50):>7.1f} us"
            f" p95={_pct(xs, 0.95):>7.1f} us"
            f" p99={_pct(xs, 0.99):>7.1f} us"
        )

    print("\n[bench] results")
    print(_fmt(off, "rules_off"))
    print(_fmt(on, "rules_on"))

    delta_p50 = _pct(on, 0.50) - _pct(off, 0.50)
    delta_p95 = _pct(on, 0.95) - _pct(off, 0.95)
    delta_p99 = _pct(on, 0.99) - _pct(off, 0.99)
    delta_mean = statistics.mean(on) - statistics.mean(off)

    print(
        "\n[bench] delta (rules_on - rules_off):"
        f" mean={delta_mean:+.1f} us"
        f" p50={delta_p50:+.1f} us"
        f" p95={delta_p95:+.1f} us"
        f" p99={delta_p99:+.1f} us"
    )
    target_us = 1000.0
    verdict = "PASS" if delta_p95 < target_us else "FAIL"
    print(f"[bench] target <{target_us:.0f} us p95 overhead  —  {verdict}")
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
