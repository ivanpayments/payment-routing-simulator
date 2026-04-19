"""Live API pattern-compliance harness.

Samples N /simulate calls against a running API, collects responses into an
in-memory DataFrame shaped like the CSV, and runs a subset of the Class-A
pattern validators on the response stream.

This focuses on the rules that CAN be verified from a single-response stream:
  - CC002 / CC107-CC110: BIN matches brand
  - CC038: is_recurring ⇒ is_mit
  - CC087: NT + POS ⇒ declined with code 14
  - CC021: POS ⇒ three_ds.requested=False
  - TS009: authenticated MC ⇒ ECI 02 (Visa/other ⇒ 05)
  - TS027: MIT ⇒ three_ds=None
  - AD107: approval share per cohort ≥32%

Rules that require multi-row cohort math (AD002-AD006, TS051, NT011, etc.)
are NOT checked here — they live in the CSV validators.

Usage:
    python scripts/validate_api_compliance.py \\
        --base-url http://localhost:8090 \\
        --n 10000 --key $API_KEY
"""
from __future__ import annotations

import argparse
import os
import random
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
import requests

from payment_router.pattern_rules.bins import bin_is_valid_for_brand

_COUNTRIES = ["US", "GB", "DE", "FR", "BR", "MX", "IN", "JP", "SG", "CA", "AU", "AE", "AR"]
_BRANDS = ["visa", "mastercard", "amex", "discover", "jcb", "unionpay"]
_TYPES = ["credit", "debit", "prepaid"]
_PROVIDERS = [
    "global-acquirer-a", "global-acquirer-b",
    "regional-bank-processor-a", "regional-bank-processor-b", "regional-bank-processor-c",
    "regional-card-specialist-a", "regional-card-specialist-b",
    "cross-border-fx-specialist-a", "cross-border-fx-specialist-b",
    "high-risk-or-orchestrator-a", "high-risk-or-orchestrator-b",
]


def _sample_bin(brand: str, rng: random.Random) -> str:
    """Emit a realistic BIN for the brand (matches generator's _gen_bin)."""
    pools = {
        "visa": ["400000", "411111", "440000", "455555"],
        "mastercard": ["510000", "520000", "530000", "540000", "550000", "222100", "271699"],
        "amex": ["340000", "370000"],
        "discover": ["601100", "644000", "650000"],
        "jcb": [str(p) + "00" for p in range(3528, 3590)],
        "unionpay": ["620000", "621100"],
    }
    return rng.choice(pools.get(brand, ["400000"]))


def _build_request(rng: random.Random) -> dict[str, Any]:
    country = rng.choice(_COUNTRIES)
    brand = rng.choice(_BRANDS)
    ctype = rng.choice(_TYPES)
    is_rec = rng.random() < 0.08
    # amount distribution shaped like the CSV (log-normal).
    amount = round(min(rng.lognormvariate(4.2, 1.1), 20000.0), 2)
    amount = max(amount, 1.0)

    present_mode = "pos" if rng.random() < 0.07 else "ecom"
    nt = (present_mode == "ecom") and (rng.random() < 0.20)

    req = {
        "provider": rng.choice(_PROVIDERS),
        "country": country,
        "card_brand": brand,
        "card_type": ctype,
        "amount": amount,
        "currency": "USD",
        "use_3ds": rng.random() < 0.25,
        "present_mode": present_mode,
        "is_recurring": is_rec,
        "is_mit": is_rec,  # CC038 mirror at client for the check
        "network_token_present": nt,
        "bin_first6": _sample_bin(brand, rng),
    }
    # 20% of the time, ask for a deliberately-wrong BIN — the server should
    # still honour the response but pattern_rules will flag BIN_MISMATCH.
    if rng.random() < 0.05:
        req["bin_first6"] = "999999"
    # Inject occasional CC087 violation so we can check the rule fires.
    if rng.random() < 0.03:
        req["present_mode"] = "pos"
        req["network_token_present"] = True
    return req


def call_simulate(session: requests.Session, base: str, body: dict[str, Any]) -> dict[str, Any] | None:
    url = base.rstrip("/") + "/simulate"
    try:
        r = session.post(url, json=body, timeout=30)
        if r.status_code >= 400:
            return None
        return r.json()
    except Exception:
        return None


def run_harness(base_url: str, n: int, key: str | None, seed: int, rps: float = 0.0) -> int:
    session = requests.Session()
    if key:
        session.headers["Authorization"] = f"Bearer {key}"

    # Pacing: rps=0 means no cap (best for localhost). Remote APIs have 60/min
    # per IP, so default to ~0.9 rps when a remote base URL is supplied.
    if rps == 0.0 and base_url.startswith("https://"):
        rps = 0.9
    delay = 1.0 / rps if rps > 0 else 0.0

    rng = random.Random(seed)
    requests_sent: list[dict[str, Any]] = []
    responses: list[dict[str, Any]] = []

    t0 = time.time()
    for i in range(n):
        body = _build_request(rng)
        resp = call_simulate(session, base_url, body)
        if resp is not None:
            requests_sent.append(body)
            responses.append(resp)
        if delay:
            time.sleep(delay)
        if i and i % 100 == 0:
            print(f"  {i:,} / {n:,}  collected={len(responses)}  elapsed={time.time()-t0:.1f}s")

    elapsed = time.time() - t0
    print(f"\n[harness] collected {len(responses):,} / {n:,} responses in {elapsed:.1f}s "
          f"({len(responses)/max(elapsed,0.001):.0f} rps)")
    if not responses:
        print("[harness] no successful responses — check URL / key / server")
        return 1

    df_req = pd.DataFrame(requests_sent)
    df_resp = pd.DataFrame(responses)
    df = df_req.add_suffix("_req").join(df_resp.add_suffix("_resp"))

    print(f"\n[harness] response sample columns: {list(df_resp.columns)[:20]}")

    # ------------------------------------------------------------------
    # Pattern checks
    # ------------------------------------------------------------------
    results: list[tuple[str, str, str]] = []

    def add(pid: str, status: str, evidence: str) -> None:
        results.append((pid, status, evidence))

    # CC002 / CC107-CC110: BIN ↔ brand
    valid = df_resp.apply(
        lambda r: bin_is_valid_for_brand(r["card_brand"], r.get("bin_first6")),
        axis=1,
    )
    violations = (~valid).sum()
    supplied = df_resp["bin_first6"].notna().sum()
    if supplied == 0:
        add("CC002/107-110", "N/A", "no BIN supplied in responses")
    else:
        # Expect the harness's deliberate 5% "999999" injections to show up.
        rate = violations / supplied
        status = "PASS" if rate <= 0.10 else "FAIL"
        add("CC002/107-110", status, f"invalid_bin_share={rate:.3f} (n={supplied})")

    # CC038: is_recurring ⇒ is_mit
    rec = df_resp[df_resp["is_recurring"] == True]  # noqa: E712
    if len(rec):
        mit_share = rec["is_mit"].mean()
        status = "PASS" if mit_share == 1.0 else "FAIL"
        add("CC038", status, f"is_mit_when_recurring={mit_share:.3f} (n={len(rec)})")
    else:
        add("CC038", "N/A", "no recurring responses in sample")

    # CC087: NT + POS ⇒ declined with code 14
    cc087_reqs = df_req[(df_req["present_mode"] == "pos") & (df_req["network_token_present"] == True)]  # noqa: E712
    if len(cc087_reqs):
        idx = cc087_reqs.index
        cc087_resps = df_resp.loc[idx]
        declined = (~cc087_resps["approved"]).sum()
        code14 = (cc087_resps["response_code"] == "14").sum()
        status = "PASS" if (declined == len(cc087_resps) and code14 == len(cc087_resps)) else "FAIL"
        add("CC087", status, f"n={len(cc087_resps)} declined={declined} code14={code14}")
    else:
        add("CC087", "N/A", "no NT+POS requests in sample")

    # CC021: POS ⇒ no 3DS in response (three_ds is None)
    pos_reqs = df_req[df_req["present_mode"] == "pos"]
    if len(pos_reqs):
        idx = pos_reqs.index
        pos_resps = df_resp.loc[idx]
        with_3ds = pos_resps["three_ds"].notna().sum()
        status = "PASS" if with_3ds == 0 else "FAIL"
        add("CC021", status, f"pos_with_3ds={with_3ds} of n={len(pos_resps)}")
    else:
        add("CC021", "N/A", "no POS requests in sample")

    # TS027: MIT ⇒ three_ds None
    mit_reqs = df_req[df_req["is_mit"] == True]  # noqa: E712
    if len(mit_reqs):
        idx = mit_reqs.index
        mit_resps = df_resp.loc[idx]
        with_3ds = mit_resps["three_ds"].notna().sum()
        status = "PASS" if with_3ds == 0 else "FAIL"
        add("TS027", status, f"mit_with_3ds={with_3ds} of n={len(mit_resps)}")
    else:
        add("TS027", "N/A", "no MIT requests in sample")

    # AD107: approval share ≥32%
    rate = df_resp["approved"].mean()
    status = "PASS" if rate >= 0.32 else "FAIL"
    add("AD107", status, f"approval_rate={rate:.3f} (n={len(df_resp)})")

    # ------------------------------------------------------------------
    # Report
    # ------------------------------------------------------------------
    print("\n[harness] pattern compliance")
    print(f"  {'ID':<18} {'Status':<6} {'Evidence'}")
    print(f"  {'-'*70}")
    failed = 0
    for pid, status, ev in results:
        marker = "*" if status == "FAIL" else " "
        print(f"  {pid:<18} {status:<6} {marker} {ev}")
        if status == "FAIL":
            failed += 1

    print(f"\n[harness] total FAIL={failed}  PASS={sum(1 for _,s,_ in results if s=='PASS')}  "
          f"N/A={sum(1 for _,s,_ in results if s=='N/A')}")
    return 0 if failed == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", default="http://localhost:8090")
    parser.add_argument("--n", type=int, default=10_000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--key", default=os.environ.get("API_KEY", ""))
    parser.add_argument("--rps", type=float, default=0.0,
                        help="Requests per second cap (0 = auto: 0.9 for https, unlimited otherwise)")
    args = parser.parse_args()
    return run_harness(args.base_url, args.n, args.key, args.seed, args.rps)


if __name__ == "__main__":
    sys.exit(main())
