"""Shared Pydantic field validators for country / issuer_country / currency.

Kept in one place so SimulateRequest, CompareRequest, and QueryRequest
all enforce the same ISO 3166-1 alpha-2 / ISO 4217 contract.

The `SUPPORTED_COUNTRIES` allowlist is the union of `countries:` keys across
every provider YAML. If a caller asks for a country no provider models, we
would silently fall back to `base_approval_rate * 0.95` (see engine.py
`_approval_probability`). That turns garbage input into a confident-looking
wrong answer — so reject it at the API boundary with 422 instead.
"""
from __future__ import annotations

import re
from functools import lru_cache
from typing import Optional

COUNTRY_RE = re.compile(r"^[A-Z]{2}$")
CURRENCY_RE = re.compile(r"^[A-Z]{3}$")

# ISO 4217 major currencies the provider YAMLs reference, plus common settlement
# currencies. Anything outside this allowlist rejects with 422 rather than
# returning a misleading synthetic decline (e.g. code 54 "expired card" for
# a garbage currency like "XYZ").
SUPPORTED_CURRENCIES: frozenset[str] = frozenset({
    "USD", "EUR", "GBP", "CAD", "AUD", "NZD", "CHF", "SEK", "NOK", "DKK",
    "PLN", "CZK", "HUF", "RON", "BRL", "MXN", "COP", "CLP", "PEN", "ARS",
    "INR", "SGD", "HKD", "JPY", "AED", "SAR", "ZAR", "THB", "MYR", "IDR",
    "PHP", "VND", "KRW", "TWD", "CNY",
})


@lru_cache(maxsize=1)
def supported_countries() -> frozenset[str]:
    """Union of country codes across every provider YAML.

    Lazy-loaded and cached. Kept in a function (not module-level) so that
    tests can override the providers directory via `PAYMENT_ROUTER_PROVIDERS`
    before this is first called.
    """
    # Imported lazily to avoid a circular import at module load
    # (provider_loader imports pydantic models which import this file).
    from pathlib import Path
    import os

    import yaml

    override = os.environ.get("PAYMENT_ROUTER_PROVIDERS")
    providers_dir = (
        Path(override) if override else Path(__file__).parent / "providers"
    )
    countries: set[str] = set()
    if providers_dir.exists():
        for yaml_path in providers_dir.glob("*.yaml"):
            try:
                with yaml_path.open() as f:
                    data = yaml.safe_load(f) or {}
            except Exception:
                continue
            for code in (data.get("countries") or {}).keys():
                countries.add(str(code).upper())
    return frozenset(countries)


def normalize_country(value: str) -> str:
    v = value.upper().strip()
    if not COUNTRY_RE.match(v):
        raise ValueError(
            "country must be ISO 3166-1 alpha-2 (two uppercase letters, e.g. US)"
        )
    allowed = supported_countries()
    # allowed may be empty in unusual test environments — only enforce when populated.
    if allowed and v not in allowed:
        raise ValueError(
            f"country '{v}' is not modelled by any provider. "
            f"Supported: {', '.join(sorted(allowed))}."
        )
    return v


def normalize_optional_country(value: Optional[str]) -> Optional[str]:
    """Issuer country. Must be well-formed ISO alpha-2 AND in the provider
    allowlist (audit v4 M1, 2026-04-27).

    Rationale: the previous policy accepted any well-formed ISO alpha-2 on
    the theory that the engine would apply a default cross-border penalty.
    In practice that meant garbage inputs like ``ZZ`` or typo'd codes (``UA``
    when ``UK`` was intended) returned a confident 200 with provider
    rankings — the same silent-fallback failure mode that motivated the
    merchant-country allowlist. Aligning the two validators removes the
    asymmetry: every ISO/allowlist field is now strict at the API surface.

    The 31-country allowlist is the union of ``countries:`` keys across
    every provider YAML — same source of truth as ``normalize_country``.
    """
    if value is None:
        return value
    v = value.upper().strip()
    if not COUNTRY_RE.match(v):
        raise ValueError(
            "issuer_country must be ISO 3166-1 alpha-2 (two uppercase letters, e.g. US)"
        )
    allowed = supported_countries()
    if allowed and v not in allowed:
        raise ValueError(
            f"issuer_country '{v}' is not modelled by any provider. "
            f"Supported: {', '.join(sorted(allowed))}."
        )
    return v


def normalize_currency(value: str) -> str:
    v = value.upper().strip()
    if not CURRENCY_RE.match(v):
        raise ValueError("currency must be ISO 4217 (three uppercase letters, e.g. USD)")
    if v not in SUPPORTED_CURRENCIES:
        raise ValueError(
            f"currency '{v}' is not a supported ISO 4217 code. "
            f"Supported: {', '.join(sorted(SUPPORTED_CURRENCIES))}."
        )
    return v
