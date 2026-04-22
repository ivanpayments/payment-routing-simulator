"""Shared Pydantic field validators for country / issuer_country / currency.

Kept in one place so SimulateRequest, CompareRequest, and QueryRequest
all enforce the same ISO 3166-1 alpha-2 / ISO 4217 contract.
"""
from __future__ import annotations

import re
from typing import Optional

COUNTRY_RE = re.compile(r"^[A-Z]{2}$")
CURRENCY_RE = re.compile(r"^[A-Z]{3}$")


def normalize_country(value: str) -> str:
    v = value.upper().strip()
    if not COUNTRY_RE.match(v):
        raise ValueError("country must be ISO 3166-1 alpha-2 (two uppercase letters, e.g. US)")
    return v


def normalize_optional_country(value: Optional[str]) -> Optional[str]:
    if value is None:
        return value
    v = value.upper().strip()
    if not COUNTRY_RE.match(v):
        raise ValueError("issuer_country must be ISO 3166-1 alpha-2 (two uppercase letters, e.g. US)")
    return v


def normalize_currency(value: str) -> str:
    v = value.upper().strip()
    if not CURRENCY_RE.match(v):
        raise ValueError("currency must be ISO 4217 (three uppercase letters, e.g. USD)")
    return v
