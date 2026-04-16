"""Issuer country risk tiers.

When the card-issuing country differs from the merchant country, the issuer's
bank applies its own risk logic. Tier 1 issuers (US, GB, DE, etc.) have low
friction. Tier 3 issuers (NG, PK, BD, etc.) decline cross-border transactions
at significantly higher rates due to FX exposure, fraud scoring, and issuer
policy restrictions.

These modifiers are applied on top of the merchant-country approval rate.
A domestic transaction (issuer == merchant country) gets no penalty.
"""

from __future__ import annotations

# Tier 1 — developed market issuers, well-known banks, low cross-border friction
TIER_1: frozenset[str] = frozenset({
    "US", "CA", "GB", "AU", "DE", "FR", "NL", "SE", "NO", "DK",
    "IE", "NZ", "SG", "JP", "HK", "AT", "BE", "CH", "FI", "LU",
    "IT", "ES", "PT", "PL", "CZ", "IL",
})

# Tier 2 — emerging markets with established payment infrastructure
TIER_2: frozenset[str] = frozenset({
    "BR", "MX", "IN", "ZA", "AR", "CL", "CO", "MY", "TH", "PH",
    "ID", "HU", "RO", "TR", "AE", "SA", "CN", "KR", "TW", "VN",
    "PE", "EC", "UY", "CR", "PA", "EG", "MA", "QA", "KW", "BH",
})

# Tier 3 — everything else (higher issuer-side friction, FX risk, fraud scoring)
# Examples: NG, PK, BD, KE, TZ, GH, ET, UA, RU, MM, KH, LA

_MODIFIERS: dict[int, float] = {
    1: 1.00,   # no penalty — domestic or mature cross-border issuer
    2: 0.94,   # ~6% approval drag from issuer friction
    3: 0.87,   # ~13% drag — issuer blocks many cross-border CNP transactions
}


def get_issuer_tier(country_code: str) -> int:
    code = country_code.upper()
    if code in TIER_1:
        return 1
    if code in TIER_2:
        return 2
    return 3


def issuer_modifier(country_code: str) -> float:
    """Return the approval rate multiplier for a given issuing country.

    Only applies when issuer_country != merchant_country (cross-border).
    Domestic transactions should not call this function.
    """
    return _MODIFIERS[get_issuer_tier(country_code)]
