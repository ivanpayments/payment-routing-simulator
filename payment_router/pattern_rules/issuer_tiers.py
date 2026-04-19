"""Issuer-tier approval modifier.

Delegates to payment_router.issuer_tiers (already in the package); wraps it
so every Class-A rule lives under pattern_rules/ for discoverability.

Pattern IDs: AD070-adjacent (cross-border issuer drag).
"""
from __future__ import annotations

from payment_router.issuer_tiers import issuer_modifier


def apply_issuer_tier_modifier(ctx, result) -> None:
    """Multiplicative modifier for approval probability based on issuer tier.

    The engine already applies this in _approval_probability; keeping a
    pass-through rule here makes the invariant discoverable via rule_ids().
    """
    effective_issuer = ctx.issuer_country or ctx.country
    if effective_issuer != ctx.country:
        # Already applied upstream — record the marker only.
        result.mark("AD070_TIER")


__all__ = ["apply_issuer_tier_modifier", "issuer_modifier"]
