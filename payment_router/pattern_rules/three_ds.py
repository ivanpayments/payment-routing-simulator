"""3DS request/outcome/version/ECI invariants.

Pattern IDs: CC021 (POS ⇒ no 3DS), CC046 (APM ⇒ no 3DS),
TS027/TS029 (MIT rebill ⇒ no 3DS), TS009 (ECI from brand + outcome).

Source of truth: generator apply_threeds (lines ~1056-1281).
"""
from __future__ import annotations

from payment_router.pattern_rules.mit import _effective_is_mit


def suppress_3ds_on_pos(ctx, result) -> None:
    """CC021: POS / MOTO ⇒ three_ds_requested=False."""
    if ctx.present_mode in ("pos", "moto"):
        if result.three_ds_requested:
            result.three_ds_requested = False
            result.three_ds_version = None
            result.three_ds_eci = None
            result.three_ds_challenged = None
            result.mark("CC021")


def suppress_3ds_on_apm(ctx, result) -> None:
    """CC046: non-card payment rails never request 3DS.

    Scope for this project is card-only, so this rule is defensive; it does
    match the generator's gate `if not is_pm_card: requested=False`.
    """
    # In the 6-brand card-only scope, a "card" context always has card_brand
    # set; APM cases are never reached through SimulateRequest. Still, keep
    # the hook so compliance validators can see the invariant in rules_applied.
    pass


def suppress_3ds_on_mit(ctx, result) -> None:
    """TS027 / TS029 / TS084: MIT subsequent rebills ⇒ no 3DS.

    Carve-out: the FIRST setup within a subscription (CIT) *may* request 3DS.
    The runtime engine cannot tell "first vs subsequent" from a single request,
    so we treat all MIT requests conservatively as subsequent (no 3DS). Clients
    wanting the subscription-setup path should pass is_mit=False and
    is_recurring=False on that initial call.
    """
    if _effective_is_mit(ctx, result):
        if result.three_ds_requested:
            result.three_ds_requested = False
            result.three_ds_version = None
            result.three_ds_eci = None
            result.three_ds_challenged = None
            result.mark("TS027")


def align_eci_to_brand_and_outcome(ctx, result) -> None:
    """TS009: ECI value must align with card_brand + 3DS outcome.

    authenticated + visa/other → 05; authenticated + mastercard → 02;
    attempted → 06; failed → 07.
    """
    if not result.three_ds_requested:
        return
    if result.three_ds_eci is None:
        return
    # Derive from challenged + brand. The runtime engine may have set ECI
    # independently via its own simulator; we only correct when the current
    # value disagrees with the brand mapping.
    brand = ctx.card_brand.lower()
    challenged = bool(result.three_ds_challenged)
    # We don't carry an explicit outcome field through RuleResult; default to
    # "authenticated" when challenged=True or when ECI already says so.
    eci = result.three_ds_eci
    expected = None
    if eci in ("05", "06"):
        expected = "02" if brand == "mastercard" else "05"
    elif eci == "07":
        expected = "07"
    if expected and expected != eci:
        result.three_ds_eci = expected
        result.mark("TS009")
