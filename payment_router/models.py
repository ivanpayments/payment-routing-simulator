"""Pydantic models and dataclasses for payment-router."""

from __future__ import annotations

import re
from enum import Enum
from typing import Optional
from pydantic import AnyHttpUrl, BaseModel, Field, field_validator, model_validator

from payment_router.validators import (
    normalize_country,
    normalize_currency,
    normalize_optional_country,
)

_PROVIDER_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9\-]{0,63}$")

# API-surface allowlists. Used by the wrapper request models in
# api.py (ApiSimulateRequest, ApiCompareRequest, QueryRequest). The core
# CardBrand / CardType enums below include UNKNOWN/DISCOVER/JCB/UNIONPAY
# because the internal BIN-mismatch / pattern-rule layer needs to reason
# about them. We restrict only at the API surface so callers cannot
# request a brand/type no provider YAML actually models — that would fall
# back silently to `base_approval_rate * 0.95` in engine._approval_probability.
API_REQUEST_CARD_BRANDS = frozenset({"visa", "mastercard", "amex"})
API_REQUEST_CARD_TYPES = frozenset({"credit", "debit", "prepaid", "commercial"})

# API-surface MCC allowlist. Mirrors the 11-entry MCC dropdown in the
# Routing Simulator UI (routing-simulator/content.json::per_txn_simulator.mccs).
# Without this, a typo'd MCC ("abcd", "9999") silently falls through to the
# no-MCC code path and returns plausible-looking rankings — see audit v2 N1
# (2026-04-26). Engine-side _classify_mcc still treats unknown 4-digit MCCs
# as "mainstream" for back-compat with internal callers; the API surface is
# stricter so external callers cannot get silent no-op behaviour.
API_REQUEST_MCCS = frozenset({
    # Mainstream
    "5411",  # Grocery
    "5732",  # Electronics
    "5734",  # Software / SaaS
    "5812",  # Restaurants
    "4814",  # Telecom
    "7372",  # Online services
    # High-risk (also in engine._HIGH_RISK_MCCS)
    "5816",  # Digital goods / games
    "5944",  # Jewelry
    "5967",  # Direct marketing / adult
    "7273",  # Dating services
    "7995",  # Gambling
})

# Routing API training envelope for transaction amount. Above this, the
# pattern-rule lifts and amount_modifier_thresholds in the YAMLs are
# extrapolated rather than calibrated. Mirrors decline-recovery's $25K
# guard so cross-service buyers see consistent envelope behaviour.
# See audit v2 N2 (2026-04-26).
API_AMOUNT_ENVELOPE_USD = 25_000.0


def _validate_api_mcc(v):
    """Validator shared by ApiSimulateRequest / ApiCompareRequest / QueryRequest.

    Empty / None preserves the no-MCC path (engine treats as None).
    Reject anything that isn't in API_REQUEST_MCCS with a 422 listing the
    accepted set, matching the card_brand / card_type validation pattern.
    """
    if v is None:
        return None
    raw = str(v).strip()
    if raw == "":
        return None
    if raw not in API_REQUEST_MCCS:
        raise ValueError(
            f"mcc '{raw}' is not accepted by this endpoint. "
            f"Supported: {sorted(API_REQUEST_MCCS)}. "
            "These match the routing simulator UI's MCC dropdown; "
            "submit one of the listed codes or omit the field."
        )
    return raw


def _validate_api_amount(v):
    """Validator shared by ApiSimulateRequest / ApiCompareRequest / QueryRequest.

    Reject amounts above the routing API's training envelope ($25,000) with
    the same wording as decline-recovery's out_of_scope guard. Pydantic's
    `le=10_000_000` field constraint stays in place as a hard upper bound;
    this validator surfaces the soft envelope above which the model
    extrapolates rather than interpolates.
    """
    if v is None:
        return v
    if float(v) > API_AMOUNT_ENVELOPE_USD:
        raise ValueError(
            f"Amount ${float(v):,.0f} exceeds the model's training envelope "
            f"(${API_AMOUNT_ENVELOPE_USD:,.0f}). The routing simulator's "
            "approval rates and decline-code distributions are calibrated for "
            "card-present and card-not-present consumer transactions; for "
            "wire-sized payments the projections are extrapolated rather than "
            "calibrated. Send a smaller amount or treat the rankings as "
            "directional only."
        )
    return v


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

    _country_upper = field_validator("country")(classmethod(lambda cls, v: normalize_country(v)))
    _issuer_country_upper = field_validator("issuer_country")(classmethod(lambda cls, v: normalize_optional_country(v)))
    _currency_upper = field_validator("currency")(classmethod(lambda cls, v: normalize_currency(v)))


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
    # Optional MCC — when provided, the engine applies an archetype-fit
    # bucket (high-risk MCCs lift specialised orchestrators, mainstream
    # MCCs slightly demote them). Defaults to None for backwards compat.
    mcc: Optional[str] = Field(
        None,
        description="Merchant category code (4-digit ISO 18245). Optional. "
                    "When supplied, ranking applies a high-risk vs mainstream "
                    "MCC bucket adjustment to specialised archetypes.",
    )

    _country_upper = field_validator("country")(classmethod(lambda cls, v: normalize_country(v)))
    _issuer_country_upper = field_validator("issuer_country")(classmethod(lambda cls, v: normalize_optional_country(v)))
    _currency_upper = field_validator("currency")(classmethod(lambda cls, v: normalize_currency(v)))


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
    """Response from /recommend-route — used by Payment Data Chatbot.

    `defaults_applied` lists optional input fields (card_brand, card_type,
    currency, use_3ds, mcc, issuer_country) that the caller omitted and the
    server filled with platform defaults — same marker /compare and /query
    return so clients have one consistent shape across all three ranking
    endpoints (audit v4 M6, 2026-04-27).
    """
    rankings: list[CompareResult]
    recommended_provider: str
    reasoning: str
    defaults_applied: list[str] = Field(default_factory=list)
