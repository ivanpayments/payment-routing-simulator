"""Decline-code quality rules.

Pattern IDs: AD088 (network token ≤1% code 54), AD103 (hard-decline set fixed).

Source of truth: generator lines ~1672-1678.
"""
from __future__ import annotations

import random

# Soft-decline pool used when we need to re-draw away from a specific code.
_SOFT_POOL = ("05", "51", "57", "61", "65", "91", "96", "NW", "PR", "RC")


def redraw_expired_card_on_network_token(ctx, result) -> None:
    """AD088: network-token flows must emit code 54 (expired card) at <1% rate.

    Rationale: account-updater keeps NT BINs fresh, so "expired card" is
    extremely rare. If the engine sampled "54" on an NT row, redraw from the
    soft-pool 95% of the time.
    """
    if not ctx.network_token_present:
        return
    if result.approved:
        return
    if result.response_code != "54":
        return
    # 5% leak-through preserves distribution; 95% redraw forces soft.
    if random.random() < 0.95:
        new_code = random.choice(_SOFT_POOL)
        result.response_code = new_code
        # Best-effort message — engine updates the official label later.
        result.response_message = "Do not honor (AD088 redraw)"
        result.mark("AD088")
