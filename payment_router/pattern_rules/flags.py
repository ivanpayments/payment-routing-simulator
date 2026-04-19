"""Routing-optimization flag approval lifts.

Pattern IDs: AD067 (smart_routed, MEA/LATAM), AD078 (routing_optimized, US debit),
AD080 (mcc_routing_optimized, FR).

Source of truth: generator lines ~1604-1630.
"""
from __future__ import annotations

from payment_router.pattern_rules import MEA_LATAM


def apply_smart_routed_lift(ctx, result) -> None:
    """AD067: smart_routed=True + country ∈ MEA∪LATAM ⇒ +0.04 lift (cap 0.95)."""
    if ctx.smart_routed and ctx.country in MEA_LATAM:
        result.approval_prob_adjust += 0.04
        result.mark("AD067")


def apply_routing_optimized_lift(ctx, result) -> None:
    """AD078: routing_optimized=True + country=US + card_type=debit ⇒ +0.025."""
    if ctx.routing_optimized and ctx.country == "US" and ctx.card_type == "debit":
        result.approval_prob_adjust += 0.025
        result.mark("AD078")


def apply_mcc_routing_optimized_lift(ctx, result) -> None:
    """AD080: mcc_routing_optimized=True + country=FR ⇒ +0.07 lift."""
    if ctx.mcc_routing_optimized and ctx.country == "FR":
        result.approval_prob_adjust += 0.07
        result.mark("AD080")
