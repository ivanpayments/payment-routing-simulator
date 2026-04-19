"""MIT / recurring / stored-credential invariants.

Pattern IDs: CC038 (recurring ⇒ MIT), CC036 (MIT ⇒ stored_credential_id).

Source of truth: Claude files/generate_routing_transactions.py lines 990-1022
(sample_present_and_recurring) — CC038 is strictly enforced there; the engine
never had it. This module re-expresses that invariant for runtime.
"""
from __future__ import annotations


def apply_recurring_implies_mit(ctx, result) -> None:
    """CC038 (strict): is_recurring=True ⇒ is_mit=True.

    The ctx field is frozen; we instead flag this in result.applied so the
    engine's post-chain code can reflect it in ProviderResponse.is_mit.
    """
    if ctx.is_recurring and not ctx.is_mit:
        # The context is a dataclass; ctx field can be mutated — but we keep
        # ctx immutable-by-convention and instead piggy-back on result.applied.
        # The engine reads ctx.is_recurring / ctx.is_mit; downstream rules that
        # need "is_mit-after-CC038" should use `_effective_is_mit(ctx, result)`.
        result.mark("CC038")


def _effective_is_mit(ctx, result) -> bool:
    """Post-CC038 effective MIT flag. Other rules should call this."""
    return ctx.is_mit or ctx.is_recurring


def apply_mit_populates_stored_credential(ctx, result) -> None:
    """CC036: is_mit=True ⇒ stored_credential_id populated.

    The response model does not carry stored_credential_id (that's a CSV-only
    column), so this rule is a no-op at runtime but is kept for traceability
    in RULE_CHAIN.
    """
    if _effective_is_mit(ctx, result):
        result.mark("CC036")
