"""Retry-cascade gates.

Pattern IDs: RC005 / RC008 (hard declines never retry), RC020 (MAC 01/02 halts),
RC026 (APM never retries), RC033 (risk_skip_flag ⇒ no retry), RC019 (MIT ⇒ no retry).

Source of truth: generator lines ~2251-2292 (generate_retries).

This module is consumed by payment_router.engine.simulate_with_retry, NOT by
apply_rule_chain (which is per-row-response). Keeping it in pattern_rules/
gives a single home for Class-A invariants.
"""
from __future__ import annotations

from payment_router.pattern_rules import HARD_DECLINE_CODES


def is_retryable(
    response_code: str,
    *,
    is_soft: bool,
    card_brand: str | None = None,
    mastercard_advice_code: str | None = None,
    risk_skip_flag: bool = False,
    is_mit: bool = False,
    payment_method_is_card: bool = True,
) -> bool:
    """Return True if the declined attempt is eligible for cascade.

    Called by simulate_with_retry before it picks the next provider.

    RC005 / RC008: response_code in HARD_DECLINE_CODES → no retry.
    RC020: MAC 01 or 02 → no retry (Mastercard "do not try again").
    RC026: payment_method != card → no retry.
    RC033: risk_skip_flag → no retry.
    RC019: is_mit → no retry (Visa SPS / stop-payment rule).
    """
    if not payment_method_is_card:
        return False
    if response_code in HARD_DECLINE_CODES:
        return False
    if not is_soft:
        return False
    if risk_skip_flag:
        return False
    if is_mit:
        return False
    if (mastercard_advice_code or "").strip() in ("01", "02"):
        return False
    return True


def gate_retries(ctx, result) -> None:
    """Placeholder hook for RULE_CHAIN; real enforcement happens in engine.

    Kept so that `payment_router.pattern_rules.apply_rule_chain` can log a
    cascade-gate decision when a downstream /route request is processed.
    """
    return
