"""BIN / card-brand invariants.

Pattern IDs: CC002 (Visa starts 4), CC107 (Amex 34/37), CC108 (Discover),
CC109 (JCB 3528-3589 — strict), CC110 (UnionPay 62), AD070 (BIN-mismatch penalty).

Source of truth: generator CARD_BRAND_BINS + _gen_bin (lines ~166-186, 703-711).
"""
from __future__ import annotations

# Brand -> list of acceptable BIN first-digit prefixes.
BRAND_BIN_PREFIXES: dict[str, tuple[str, ...]] = {
    "visa": ("4",),
    "mastercard": ("51", "52", "53", "54", "55", "22", "23", "24", "25", "26", "27"),
    "amex": ("34", "37"),
    "discover": ("6011", "644", "645", "646", "647", "648", "649", "65"),
    # CC109 strict: JCB must be in the 3528–3589 range.
    "jcb": tuple(str(p) for p in range(3528, 3590)),
    "unionpay": ("62",),
}


def bin_is_valid_for_brand(brand: str, bin6: str | None) -> bool:
    """True if bin6 matches at least one acceptable prefix for the brand.

    Unknown brands always return True (we cannot assert). Empty/missing bin
    returns True (nothing to validate).
    """
    if not bin6:
        return True
    b = str(bin6)
    prefixes = BRAND_BIN_PREFIXES.get(brand.lower())
    if prefixes is None:
        return True
    return any(b.startswith(p) for p in prefixes)


def validate_bin_for_brand(ctx, result) -> None:
    """CC002 / CC107 / CC108 / CC109 / CC110.

    If the supplied BIN does not match the brand we *do not* reject (the
    engine's response still represents a successful network hop); instead we
    attach a diagnostic marker. The compliance harness can then count the
    share of invalid BINs and fail fast in tests.
    """
    if ctx.bin_first6 is None:
        return
    if not bin_is_valid_for_brand(ctx.card_brand, ctx.bin_first6):
        result.mark("BIN_MISMATCH")


def apply_cross_border_penalty_if_not_tokenized(ctx, result) -> None:
    """AD070: cross-border + no network token ⇒ approval drag -10pp (non-GA).

    Global-acquirer already handled by engine's YAML profile (cross-border
    penalty baked in); we keep this as a tighter row-level guard for other
    archetypes by nudging approval_prob_adjust. Engine may or may not re-draw
    depending on its implementation; the adjust is additive so it is safe.
    """
    effective_issuer = (ctx.issuer_country or ctx.country).upper()
    is_cross_border = effective_issuer != ctx.country.upper()
    if is_cross_border and not ctx.network_token_present:
        # Approval-probability hint only — engine may elect to re-draw.
        # Conservative: small -2pp delta so tokenised cross-border has a
        # visible lift vs untokenised cross-border at runtime without
        # contradicting the YAML-driven cohort band.
        result.approval_prob_adjust -= 0.02
        result.mark("AD070")
