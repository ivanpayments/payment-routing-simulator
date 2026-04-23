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
    """Issuer country. Must be well-formed ISO alpha-2 but NOT required to be in
    the provider allowlist — a merchant can legitimately accept a card issued in
    a country no provider has modelled. The engine handles unknown issuers via
    a default cross-border penalty.
    """
    if value is None:
        return value
    v = value.upper().strip()
    if not COUNTRY_RE.match(v):
        raise ValueError(
            "issuer_country must be ISO 3166-1 alpha-2 (two uppercase letters, e.g. US)"
        )
    return v


def normalize_currency(value: str) -> str:
    v = value.upper().strip()
    if not CURRENCY_RE.match(v):
        raise ValueError("currency must be ISO 4217 (three uppercase letters, e.g. USD)")
    return v
