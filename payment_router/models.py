"""Pydantic models and dataclasses for payment-router."""

from __future__ import annotations

import re
from enum import Enum
from typing import Optional
from pydantic import AnyHttpUrl, BaseModel, Field, field_validator, model_validator

_PROVIDER_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9\-]{0,63}$")
_COUNTRY_RE = re.compile(r"^[A-Z]{2}$")
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class CardBrand(str, Enum):
    VISA = "visa"
    MASTERCARD = "mastercard"
    AMEX = "amex"
    DISCOVER = "discover"
    JCB = "jcb"
    UNIONPAY = "unionpay"
    UNKNOWN = "unknown"


class CardType(str, Enum):
    CREDIT = "credit"
    DEBIT = "debit"
    PREPAID = "prepaid"
    COMMERCIAL = "commercial"
    UNKNOWN = "unknown"


class TransactionState(str, Enum):
    PENDING = "pending"
    AUTHORIZED = "authorized"
    CAPTURED = "captured"
    VOIDED = "voided"
    DECLINED = "declined"
    REFUNDED = "refunded"


class ThreeDSVersion(str, Enum):
    V1 = "1.0"
    V2_1 = "2.1"
    V2_2 = "2.2"
    NONE = "none"


class PaResStatus(str, Enum):
    Y = "Y"   # Authenticated
    N = "N"   # Not authenticated
    A = "A"   # Attempted
    U = "U"   # Unable to authenticate
    R = "R"   # Rejected


# ---------------------------------------------------------------------------
# PCI DSS guardrail — reject cardholder data at the API surface
# ---------------------------------------------------------------------------

_CARDHOLDER_DATA_FIELDS = frozenset({
    "pan", "card_number", "primary_account_number", "cardnumber", "account_number",
    "cvv", "cvc", "cvv2", "cvc2", "cvn", "security_code",
    "track1", "track2", "track_data", "magstripe",
    "cardholder_name", "card_holder_name", "name_on_card",
    "pin", "pin_block",
})


class _RejectCardholderData(BaseModel):
    """Base model that rejects PAN/CVV/track/PIN fields in the request body.

    This router is out of PCI DSS scope because it accepts only BIN,
    card brand/type, country, and amount — never a full card number or
    sensitive authentication data. Enforce at the API surface so callers
    cannot accidentally push cardholder data into the simulator.
    """

    @model_validator(mode="before")
    @classmethod
    def _forbid_cardholder_data(cls, data):
        if isinstance(data, dict):
            bad = sorted(k for k in data.keys() if str(k).lower() in _CARDHOLDER_DATA_FIELDS)
            if bad:
                raise ValueError(
                    "This endpoint does not accept cardholder data. "
                    f"Disallowed field(s): {bad}. Send country, card brand, card type, "
                    "amount, and currency; BIN (bin_first6) may be sent. Full PAN, CVV, "
                    "track data, cardholder name, and PIN must never be sent — this is a "
                    "PCI DSS scope boundary."
                )
        return data


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class SimulateRequest(_RejectCardholderData):
    provider: str = Field(..., description="Provider name")
    country: str = Field(..., description="ISO 3166-1 alpha-2 merchant country code")
    issuer_country: Optional[str] = Field(
        None,
        description="ISO 3166-1 alpha-2 card-issuing country. "
                    "If omitted, assumed domestic (same as merchant country).",
    )
    card_brand: CardBrand = Field(CardBrand.VISA, description="Card brand")
    card_type: CardType = Field(CardType.CREDIT, description="Card type (credit/debit/prepaid/commercial)")
    amount: float = Field(..., gt=0, le=10_000_000, description="Transaction amount in currency units")
    currency: str = Field("USD", description="ISO 4217 currency code")
    use_3ds: bool = Field(False, description="Whether to simulate 3DS flow")
    idempotency_key: Optional[str] = Field(None, max_length=128, description="Idempotency key for deduplication")
    callback_url: Optional[AnyHttpUrl] = Field(None, description="Webhook callback URL")
    # ---- Optional Class-A context fields (2026-04-19 refactor) ----
    # These are consumed by payment_router.pattern_rules. Every field has a
    # backwards-compatible default so older clients keep working.
    present_mode: Optional[str] = Field(
        None,
        description="ecom | pos | moto. Defaults to ecom in rule engine when None.",
    )
    is_recurring: bool = Field(
        False,
        description="True when this is a subscription rebill (CC038: implies is_mit).",
    )
    is_mit: bool = Field(
        False,
        description="Merchant-initiated transaction (stored credential). CC038 force-true when is_recurring.",
    )
    network_token_present: bool = Field(
        False,
        description="True when the card data is a network token (Visa VTS / MC MDES).",
    )
    bin_first6: Optional[str] = Field(
        None,
        description="Card BIN first 6 digits. Validated against card_brand (CC002/CC107-110).",
    )
    mcc: Optional[str] = Field(
        None,
        description="Merchant category code, used by flag lifts and anti-patterns.",
    )
    routing_optimized: bool = Field(
        False,
        description="US debit least-cost routing flag (AD078).",
    )
    mcc_routing_optimized: bool = Field(
        False,
        description="FR MCC-optimized scheme routing (AD080).",
    )
    smart_routed: bool = Field(
        False,
        description="MEA/LATAM smart-routing lift (AD067).",
    )

    @field_validator("provider")
    @classmethod
    def provider_slug(cls, v: str) -> str:
        v = v.lower().strip()
        if not _PROVIDER_SLUG_RE.match(v):
            raise ValueError("provider must be a slug: lowercase letters, digits, hyphens, max 64 chars")
        return v

    @field_validator("country")
    @classmethod
    def country_upper(cls, v: str) -> str:
        v = v.upper().strip()
        if not _COUNTRY_RE.match(v):
            raise ValueError("country must be ISO 3166-1 alpha-2 (two uppercase letters, e.g. US)")
        return v

    @field_validator("issuer_country")
    @classmethod
    def issuer_country_upper(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.upper().strip()
        if not _COUNTRY_RE.match(v):
            raise ValueError("issuer_country must be ISO 3166-1 alpha-2 (two uppercase letters, e.g. US)")
        return v

    @field_validator("currency")
    @classmethod
    def currency_upper(cls, v: str) -> str:
        v = v.upper().strip()
        if not _CURRENCY_RE.match(v):
            raise ValueError("currency must be ISO 4217 (three uppercase letters, e.g. USD)")
        return v


class ThreeDSResult(BaseModel):
    version: ThreeDSVersion
    challenged: bool
    pares_status: PaResStatus
    eci: str           # Electronic Commerce Indicator
    liability_shift: bool  # True = issuer liable for fraud; False = merchant liable


class ProviderResponse(BaseModel):
    transaction_id: str
    provider: str
    state: TransactionState
    approved: bool
    response_code: str
    response_message: str
    merchant_advice_code: Optional[str] = None
    latency_ms: float
    amount: float
    currency: str
    country: str
    issuer_country: Optional[str] = None
    card_brand: CardBrand
    card_type: CardType = CardType.CREDIT
    three_ds: Optional[ThreeDSResult] = None
    idempotency_key: Optional[str] = None
    # ---- Class-A rule audit trail (2026-04-19 refactor) ----
    rules_applied: list[str] = Field(
        default_factory=list,
        description="Ordered list of Class-A pattern_rule IDs that modified this result.",
    )
    # Echoed back so compliance validators can inspect the request-level context
    present_mode: Optional[str] = None
    is_mit: bool = False
    is_recurring: bool = False
    network_token_present: bool = False
    bin_first6: Optional[str] = None


class CompareRequest(_RejectCardholderData):
    country: str
    issuer_country: Optional[str] = None
    card_brand: CardBrand = CardBrand.VISA
    card_type: CardType = CardType.CREDIT
    amount: float = Field(..., gt=0, le=10_000_000)
    currency: str = "USD"
    use_3ds: bool = False

    @field_validator("country")
    @classmethod
    def country_upper(cls, v: str) -> str:
        v = v.upper().strip()
        if not _COUNTRY_RE.match(v):
            raise ValueError("country must be ISO 3166-1 alpha-2 (two uppercase letters, e.g. US)")
        return v

    @field_validator("issuer_country")
    @classmethod
    def issuer_country_upper(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.upper().strip()
        if not _COUNTRY_RE.match(v):
            raise ValueError("issuer_country must be ISO 3166-1 alpha-2 (two uppercase letters, e.g. US)")
        return v

    @field_validator("currency")
    @classmethod
    def currency_upper(cls, v: str) -> str:
        v = v.upper().strip()
        if not _CURRENCY_RE.match(v):
            raise ValueError("currency must be ISO 4217 (three uppercase letters, e.g. USD)")
        return v


class CompareResult(BaseModel):
    provider: str
    projected_approval_rate: float
    latency_p50_ms: float
    latency_p95_ms: float
    decline_code_distribution: dict[str, float]
    three_ds_challenge_rate: Optional[float] = None


# ---------------------------------------------------------------------------
# Retry models
# ---------------------------------------------------------------------------

class RetryAttempt(BaseModel):
    attempt: int
    provider: str
    response_code: str
    approved: bool
    latency_ms: float
    was_soft_decline: bool


class RetryResult(BaseModel):
    attempts: list[RetryAttempt]
    final_response: ProviderResponse
    total_latency_ms: float
    succeeded: bool
    providers_tried: list[str]


# ---------------------------------------------------------------------------
# Routing / recommendation
# ---------------------------------------------------------------------------

class RouteRecommendation(BaseModel):
    """Response from /recommend-route — used by Payment Data Chatbot."""
    rankings: list[CompareResult]
    recommended_provider: str
    reasoning: str
