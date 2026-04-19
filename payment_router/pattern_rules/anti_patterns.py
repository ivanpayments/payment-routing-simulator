"""Cross-cutting anti-patterns / floors.

Pattern IDs: AD107 (approval floor 32%), CC029 (wallet+POS ⇒ token),
CC022 (PIN ⇒ POS), CC024 (POS ⇒ no shipping).

Source of truth: generator lines ~1564-1565, ~2422-2438.
"""
from __future__ import annotations


def apply_approval_floor(ctx, result) -> None:
    """AD107: approval probability must be ≥0.32 on any cohort with >=1000 rows.

    At the per-request level this translates to "don't let stacked penalties
    push approved=False below a 32% chance".  Since engine already drew
    approved/declined, we cannot retroactively approve a declined row here;
    instead we nudge approval_prob_adjust so any downstream re-draw respects
    the floor.
    """
    # Compute current implied probability adjustment floor.
    # We cannot observe the base rate here, so we cap *negative* adjust at -0.5
    # (so the base+adjust clips to ~0.32 for a typical 0.82 base).
    if result.approval_prob_adjust < -0.5:
        result.approval_prob_adjust = -0.5
        result.mark("AD107_FLOOR")
