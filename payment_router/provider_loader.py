"""Load and validate provider YAML profiles."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

_PROVIDER_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9\-]{0,63}$")


# ---------------------------------------------------------------------------
# Sub-models
# ---------------------------------------------------------------------------

class CardModifiers(BaseModel):
    visa: float = Field(1.0, ge=0.5, le=1.5)
    mastercard: float = Field(1.0, ge=0.5, le=1.5)
    amex: float = Field(0.9, ge=0.5, le=1.5)


class CardTypeModifiers(BaseModel):
    """Approval rate multipliers by card funding type."""
    credit: float = Field(1.0, ge=0.5, le=1.5)
    debit: float = Field(0.97, ge=0.5, le=1.5)
    prepaid: float = Field(0.88, ge=0.5, le=1.5)
    commercial: float = Field(0.98, ge=0.5, le=1.5)


class LatencyProfile(BaseModel):
    p50_ms: float = Field(..., gt=0)
    p95_ms: float = Field(..., gt=0)
    p99_ms: float = Field(..., gt=0)

    @model_validator(mode="after")
    def percentiles_ordered(self) -> "LatencyProfile":
        if not (self.p50_ms <= self.p95_ms <= self.p99_ms):
            raise ValueError("Latency percentiles must satisfy p50 <= p95 <= p99")
        return self


class ThreeDSProfile(BaseModel):
    challenge_rate: float = Field(..., ge=0.0, le=1.0)
    frictionless_rate: float = Field(..., ge=0.0, le=1.0)
    version_2_2_rate: float = Field(0.7, ge=0.0, le=1.0)


class DeclineCodeWeight(BaseModel):
    code: str
    weight: float = Field(..., gt=0)


# ---------------------------------------------------------------------------
# Country profile — all fields optional except base approval rate.
# When latency / decline_codes / three_ds are absent, engine uses global defaults.
# ---------------------------------------------------------------------------

class CountryProfile(BaseModel):
    base: float = Field(..., ge=0.0, le=1.0)
    card_modifiers: CardModifiers = Field(default_factory=CardModifiers)
    latency: Optional[LatencyProfile] = None        # rail-specific override
    decline_codes: Optional[list[DeclineCodeWeight]] = None  # rail-specific override
    three_ds: Optional[ThreeDSProfile] = None       # rail-specific override


# ---------------------------------------------------------------------------
# Top-level provider profile
# ---------------------------------------------------------------------------

class ProviderProfile(BaseModel):
    name: str
    display_name: str
    base_approval_rate: float = Field(..., ge=0.0, le=1.0)  # fallback for unlisted countries

    # Global defaults — used when a country has no specific override
    latency: LatencyProfile
    three_ds: ThreeDSProfile
    decline_codes: list[DeclineCodeWeight]

    card_type_modifiers: CardTypeModifiers = Field(default_factory=CardTypeModifiers)
    supported_currencies: list[str]
    amount_modifier_thresholds: dict[str, float] = Field(default_factory=dict)

    # Per-country behaviour (approval rate + optional rail-specific overrides)
    countries: dict[str, CountryProfile] = Field(default_factory=dict)

    def country(self, code: str) -> CountryProfile | None:
        """Return the CountryProfile for a country code, or None if not listed."""
        return self.countries.get(code.upper())

    def effective_latency(self, country_code: str) -> LatencyProfile:
        """Country-specific latency if defined, else global default."""
        cp = self.country(country_code)
        return (cp.latency if cp and cp.latency else self.latency)

    def effective_decline_codes(self, country_code: str) -> list[DeclineCodeWeight]:
        """Country-specific decline codes if defined, else global default."""
        cp = self.country(country_code)
        return (cp.decline_codes if cp and cp.decline_codes else self.decline_codes)

    def effective_three_ds(self, country_code: str) -> ThreeDSProfile:
        """Country-specific 3DS profile if defined, else global default."""
        cp = self.country(country_code)
        return (cp.three_ds if cp and cp.three_ds else self.three_ds)


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

_PROVIDERS_DIR = Path(__file__).parent / "providers"
_cache: dict[str, ProviderProfile] = {}


def _providers_dir() -> Path:
    override = os.environ.get("PAYMENT_ROUTER_PROVIDERS")
    return Path(override) if override else _PROVIDERS_DIR


def load_provider(name: str) -> ProviderProfile:
    name = name.lower().strip()
    if not _PROVIDER_NAME_RE.match(name):
        raise FileNotFoundError(f"Provider not found. Use GET /providers to list available providers.")
    if name in _cache:
        return _cache[name]

    path = _providers_dir() / f"{name}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"Provider not found. Use GET /providers to list available providers.")

    with path.open() as f:
        raw = yaml.safe_load(f)

    profile = ProviderProfile.model_validate(raw)
    _cache[name] = profile
    return profile


def list_providers() -> list[str]:
    providers_dir = _providers_dir()
    if not providers_dir.exists():
        return []
    return sorted(p.stem for p in providers_dir.glob("*.yaml"))


def clear_cache() -> None:
    _cache.clear()
