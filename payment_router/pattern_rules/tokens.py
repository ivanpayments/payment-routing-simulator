"""Network-token / tokenisation invariants.

Pattern IDs: CC087 (NT at POS ⇒ reject — hard anti-pattern), NT001/NT006
(network token +2..+7pp approval lift), AD088 (NT flows must not hit code 54).

Source of truth: generator lines ~810-849 (NT generation), lines ~962-965
(CC087 hard gate), lines ~1524-1527 (NT lift), lines ~1671-1678 (AD088).
"""
from __future__ import annotations


def enforce_no_network_token_at_pos(ctx, result) -> None:
    """CC087 (hard anti-pattern): network_token_present + POS ⇒ reject.

    The generator silently coerces POS→ecom. At /simulate we cannot mutate the
    caller's present_mode silently; instead we reject with decline code 14
    ("Invalid card number") and flag rejected_by_rule so the chain stops.
    """
    if ctx.network_token_present and ctx.present_mode == "pos":
        result.approved = False
        result.response_code = "14"
        result.response_message = "Invalid card number (CC087: NT at POS)"
        result.merchant_advice_code = "03"
        result.rejected_by_rule = "CC087"
        result.mark("CC087")


def apply_network_token_lift(ctx, result) -> None:
    """NT001 / NT006: network token present ⇒ +4.5pp approval boost.

    The engine's YAML does not currently differentiate NT vs FPAN, so we add a
    runtime lift to surface the same Class-A invariant that the CSV encodes.
    """
    if ctx.network_token_present and ctx.present_mode != "pos":
        result.approval_prob_adjust += 0.045
        result.mark("NT001")
