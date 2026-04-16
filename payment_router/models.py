"""Pydantic models and dataclasses for payment-router."""

from __future__ import annotations

from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field, field_validator


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
# Request / Response models
# ---------------------------------------------------------------------------

class SimulateRequest(BaseModel):
    provider: str = Field(..., description="Provider name")
    country: str = Field(..., description="ISO 3166-1 alpha-2 merchant country code")
    issuer_country: Optional[str] = Field(
        None,
        description="ISO 3166-1 alpha-2 card-issuing country. "
                    "If omitted, assumed domestic (same as merchant country).",
    )
    card_brand: CardBrand = Field(CardBrand.VISA, description="Card brand")
    card_type: CardType = Field(CardType.CREDIT, description="Card type (credit/debit/prepaid/commercial)")
    amount: float = Field(..., gt=0, description="Transaction amount in currency units")
    currency: str = Field("USD", description="ISO 4217 currency code")
    use_3ds: bool = Field(False, description="Whether to simulate 3DS flow")
    idempotency_key: Optional[str] = Field(None, description="Idempotency key for deduplication")
    callback_url: Optional[str] = Field(None, description="Webhook callback URL")

    @field_validator("country")
    @classmethod
    def country_upper(cls, v: str) -> str:
        return v.upper()

    @field_validator("issuer_country")
    @classmethod
    def issuer_country_upper(cls, v: Optional[str]) -> Optional[str]:
        return v.upper() if v else v

    @field_validator("currency")
    @classmethod
    def currency_upper(cls, v: str) -> str:
        return v.upper()


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


class CompareRequest(BaseModel):
    country: str
    issuer_country: Optional[str] = None
    card_brand: CardBrand = CardBrand.VISA
    card_type: CardType = CardType.CREDIT
    amount: float = Field(..., gt=0)
    currency: str = "USD"
    use_3ds: bool = False

    @field_validator("country")
    @classmethod
    def country_upper(cls, v: str) -> str:
        return v.upper()

    @field_validator("issuer_country")
    @classmethod
    def issuer_country_upper(cls, v: Optional[str]) -> Optional[str]:
        return v.upper() if v else v


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
