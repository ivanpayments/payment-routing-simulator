"""FastAPI REST API for payment-router."""

from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional

import redis

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session
from starlette.middleware.base import BaseHTTPMiddleware

from payment_router import __version__
from payment_router.api_keys import seed_test_key, validate_secret_key
from payment_router.db import Session as DBSession, create_tables, engine, get_db
from payment_router.engine import compare_providers, simulate_transaction, simulate_with_retry
from payment_router.idempotency import get_cached, store as idem_store
from payment_router.models import (
    API_REQUEST_CARD_BRANDS,
    API_REQUEST_CARD_TYPES,
    CardBrand,
    CardType,
    CompareRequest,
    CompareResult,
    ProviderResponse,
    RetryResult,
    RouteRecommendation,
    SimulateRequest,
    TransactionState,
)
from payment_router.provider_loader import list_providers, load_provider
from payment_router.query_routing_intelligence import query_routing_intelligence
from payment_router.rate_limit import is_rate_limited
from payment_router.validators import (
    normalize_country,
    normalize_currency,
    normalize_optional_country,
)
from payment_router.state_machine import (
    InvalidTransitionError,
    TransactionNotFoundError,
    get_transaction,
    get_transitions,
    transition,
)

_REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379")


# ---------------------------------------------------------------------------
# Lifespan — startup/shutdown
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(application: FastAPI):
    create_tables()
    if os.environ.get("ENV", "local") == "local":
        with DBSession(engine) as db:
            seed_test_key(db)
    try:
        application.state.redis = redis.from_url(_REDIS_URL, decode_responses=True)
        application.state.redis.ping()
        application.state.redis_available = True
    except Exception:
        application.state.redis_available = False
        application.state.redis = None
    yield
    if application.state.redis:
        application.state.redis.close()
    from payment_router.kafka_producer import close as kafka_close
    kafka_close()


# ---------------------------------------------------------------------------
# App + middleware
# ---------------------------------------------------------------------------

class _LimitBodySize(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > 1_048_576:
            return JSONResponse({"detail": "Request body too large (max 1 MB)"}, status_code=413)
        return await call_next(request)


app = FastAPI(
    title="payment-router",
    description=(
        "Payment provider routing simulator REST API. "
        "Responses are validated at runtime against 35 Class-A pattern-rule invariants "
        "(MIT gating, BIN ranges, 3DS-on-MIT caps, anti-patterns, cascade routing) — "
        "the same rules the dataset generator uses, so the live API matches the 106,739-row "
        "synthetic dataset's pattern compliance. Distributional and sequence patterns are "
        "encoded in YAML calibration and the Postgres state machine. "
        "See `scripts/validate_api_compliance.py` for a sampling-based compliance harness."
    ),
    version=__version__,
    lifespan=lifespan,
    root_path=os.environ.get("ROOT_PATH", ""),
    openapi_tags=[
        {"name": "simulation", "description": "Simulate transactions and compare providers. Class-A pattern rules applied per request."},
        {"name": "lifecycle", "description": "Payment lifecycle: capture, void, refund. Strict state-machine validation."},
        {"name": "transactions", "description": "Transaction history and state transitions."},
        {"name": "webhooks", "description": "Webhook registration. HMAC-SHA256 signing, exponential backoff."},
        {"name": "providers", "description": "Provider (archetype) profile management. 5 archetypes / 11 variants / 6 card brands."},
    ],
)
# Default values so app.state.redis is always present (lifespan overwrites if Redis is up).
app.state.redis = None
app.state.redis_available = False
app.add_middleware(_LimitBodySize)


# ---------------------------------------------------------------------------
# Auth + rate-limit dependency
# ---------------------------------------------------------------------------

bearer_scheme = HTTPBearer(
    bearerFormat="sk_test_*",
    description="Paste a secret API key (sk_test_...). Use sk_test_Nb2TroIRXM2anlnYxWI_OCy7jaQO_Osz for the shared public demo key.",
    auto_error=False,
)


def get_current_api_key(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
):
    """Validate Bearer sk_test_... token and enforce per-key rate limit."""
    if credentials is None or not credentials.credentials:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    api_key = validate_secret_key(db, credentials.credentials)
    if api_key is None:
        raise HTTPException(status_code=401, detail="Invalid API key")
    rc = request.app.state.redis
    client_ip = request.client.host if request.client else None
    if rc and is_rate_limited(rc, api_key.id, client_ip):
        raise HTTPException(status_code=429, detail="Rate limit exceeded")
    return api_key


# ---------------------------------------------------------------------------
# Idempotency helpers — used inside endpoints, not as middleware
# ---------------------------------------------------------------------------

def _idem_check(request: Request, api_key_id: str, idem_key: str | None) -> dict | None:
    if not idem_key:
        return None
    rc = request.app.state.redis
    if rc is None:
        return None
    return get_cached(rc, api_key_id, idem_key)


def _idem_store(request: Request, api_key_id: str, idem_key: str | None, body: dict) -> None:
    if not idem_key:
        return
    rc = request.app.state.redis
    if rc is None:
        return
    idem_store(rc, api_key_id, idem_key, body)


# ---------------------------------------------------------------------------
# Health + providers (no auth)
# ---------------------------------------------------------------------------

@app.get("/health")
def health() -> dict:
    return {"status": "ok", "version": __version__}


@app.get("/stats/rules", tags=["simulation"])
def rule_stats() -> dict:
    """Per-rule evaluated/applied counts since process start.

    Read-only observability. Each rule has two counters:
    `<rule_id>:evaluated` (chain visited the rule) and
    `<rule_id>:applied` (rule matched its condition and mutated the result).
    """
    from payment_router.pattern_rules import get_counters, rule_ids
    return {
        "rules": rule_ids(),
        "counters": get_counters(),
    }


@app.get("/providers")
def get_providers() -> list[str]:
    return list_providers()


@app.get("/providers/{name}")
def get_provider(name: str) -> dict:
    try:
        p = load_provider(name)
        return p.model_dump()
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ---------------------------------------------------------------------------
# API-surface request wrappers
#
# Narrow the core SimulateRequest / CompareRequest to the brand/type
# subsets the provider YAMLs actually model. Keeps JCB/Discover/UnionPay/
# UNKNOWN available for internal pattern-rule BIN checks while rejecting
# them at the HTTP boundary with a clear 422.
# ---------------------------------------------------------------------------

def _check_api_card_brand(v: CardBrand) -> CardBrand:
    if v.value not in API_REQUEST_CARD_BRANDS:
        raise ValueError(
            f"card_brand '{v.value}' is not accepted by this endpoint. "
            f"Supported: {sorted(API_REQUEST_CARD_BRANDS)}."
        )
    return v


def _check_api_card_type(v: CardType) -> CardType:
    if v.value not in API_REQUEST_CARD_TYPES:
        raise ValueError(
            f"card_type '{v.value}' is not accepted by this endpoint. "
            f"Supported: {sorted(API_REQUEST_CARD_TYPES)}."
        )
    return v


class ApiSimulateRequest(SimulateRequest):
    """Public /simulate body. Rejects internal-only brands / types."""

    _check_brand = field_validator("card_brand")(classmethod(lambda cls, v: _check_api_card_brand(v)))
    _check_type = field_validator("card_type")(classmethod(lambda cls, v: _check_api_card_type(v)))


class ApiCompareRequest(CompareRequest):
    """Public /compare body. Rejects internal-only brands / types."""

    _check_brand = field_validator("card_brand")(classmethod(lambda cls, v: _check_api_card_brand(v)))
    _check_type = field_validator("card_type")(classmethod(lambda cls, v: _check_api_card_type(v)))


# ---------------------------------------------------------------------------
# Simulate — single transaction
# ---------------------------------------------------------------------------

@app.post("/simulate", response_model=ProviderResponse, tags=["simulation"])
def simulate(
    req: ApiSimulateRequest,
    request: Request,
    db: Session = Depends(get_db),
    api_key=Depends(get_current_api_key),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
) -> ProviderResponse:
    """Simulate a single transaction against one provider.

    Requires `Authorization: Bearer sk_test_...`. Supports idempotency via
    the `Idempotency-Key` header — repeat calls with the same key return the
    cached response for 24 hours.

    Example body:
    ```json
    {
        "provider": "global-acquirer",
        "country": "BR",
        "issuer_country": "NG",
        "card_brand": "visa",
        "card_type": "credit",
        "amount": 299.99,
        "currency": "USD",
        "use_3ds": true
    }
    ```
    """
    cached = _idem_check(request, api_key.id, idempotency_key)
    if cached is not None:
        return JSONResponse(cached, headers={"X-Idempotency-Replay": "true"})
    try:
        result = simulate_transaction(req, db=db)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    _idem_store(request, api_key.id, idempotency_key, result.model_dump())
    return result


# ---------------------------------------------------------------------------
# Compare — rank all providers for a given transaction profile
# ---------------------------------------------------------------------------

@app.post("/compare", response_model=list[CompareResult], tags=["simulation"])
def compare(
    req: ApiCompareRequest,
    api_key=Depends(get_current_api_key),
) -> list[CompareResult]:
    """Compare all providers for a transaction profile (runs 500 simulations per provider).

    Returns providers ranked by projected approval rate descending.
    """
    return compare_providers(req)


# ---------------------------------------------------------------------------
# Route — single transaction with soft-decline retry cascade
# ---------------------------------------------------------------------------

class RouteRequest(ApiSimulateRequest):
    """ApiSimulateRequest extended with a priority-ordered provider list for retry."""
    providers: list[str] = Field(..., min_length=1, max_length=10)
    max_attempts: int = Field(3, ge=1, le=10)


@app.post("/route", response_model=RetryResult, tags=["simulation"])
def route(
    req: RouteRequest,
    api_key=Depends(get_current_api_key),
) -> RetryResult:
    """Route a transaction with soft-decline retry cascade across providers."""
    try:
        sim_req = SimulateRequest(
            provider=req.providers[0],
            country=req.country,
            issuer_country=req.issuer_country,
            card_brand=req.card_brand,
            card_type=req.card_type,
            amount=req.amount,
            currency=req.currency,
            use_3ds=req.use_3ds,
            idempotency_key=req.idempotency_key,
        )
        return simulate_with_retry(sim_req, req.providers, req.max_attempts)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ---------------------------------------------------------------------------
# Query — natural-language-ready routing intelligence (used by chatbot)
# ---------------------------------------------------------------------------

_QUERY_BRANDS = frozenset({"visa", "mastercard", "amex"})
_QUERY_TYPES = frozenset({"credit", "debit", "prepaid", "commercial"})


class QueryRequest(BaseModel):
    country: str
    amount: float = Field(..., gt=0, le=10_000_000)
    currency: str = "USD"
    card_brand: str = "visa"
    card_type: str = "credit"
    issuer_country: str | None = None
    use_3ds: bool = False

    _country_upper = field_validator("country")(classmethod(lambda cls, v: normalize_country(v)))
    _issuer_country_upper = field_validator("issuer_country")(classmethod(lambda cls, v: normalize_optional_country(v)))
    _currency_upper = field_validator("currency")(classmethod(lambda cls, v: normalize_currency(v)))

    @field_validator("card_brand")
    @classmethod
    def _validate_brand(cls, v: str) -> str:
        v2 = str(v).lower().strip()
        if v2 not in _QUERY_BRANDS:
            raise ValueError(
                f"card_brand '{v}' is not accepted. Supported: {sorted(_QUERY_BRANDS)}."
            )
        return v2

    @field_validator("card_type")
    @classmethod
    def _validate_type(cls, v: str) -> str:
        v2 = str(v).lower().strip()
        if v2 not in _QUERY_TYPES:
            raise ValueError(
                f"card_type '{v}' is not accepted. Supported: {sorted(_QUERY_TYPES)}."
            )
        return v2


@app.post("/query", tags=["simulation"])
def query(
    req: QueryRequest,
    api_key=Depends(get_current_api_key),
) -> dict:
    """Routing intelligence endpoint for the Payment Data Chatbot."""
    return query_routing_intelligence(
        country=req.country,
        amount=req.amount,
        currency=req.currency,
        card_brand=req.card_brand,
        card_type=req.card_type,
        issuer_country=req.issuer_country,
        use_3ds=req.use_3ds,
    )


@app.post("/recommend", response_model=RouteRecommendation, tags=["simulation"])
def recommend(
    req: ApiCompareRequest,
    api_key=Depends(get_current_api_key),
) -> RouteRecommendation:
    """Recommend the best provider with plain-English reasoning."""
    rankings = compare_providers(req)
    best = rankings[0]
    second = rankings[1] if len(rankings) > 1 else None
    issuer_note = f" (issuer country: {req.issuer_country})" if req.issuer_country else ""
    if second:
        gap = best.projected_approval_rate - second.projected_approval_rate
        reasoning = (
            f"{best.provider} projects {best.projected_approval_rate:.1%} approval "
            f"for {req.card_brand.value}/{req.card_type.value} in {req.country}{issuer_note} "
            f"at {req.amount} {req.currency} — "
            f"{gap:.1%} ahead of {second.provider} ({second.projected_approval_rate:.1%}). "
            f"p50 latency: {best.latency_p50_ms:.0f}ms."
        )
    else:
        reasoning = (
            f"{best.provider} is the only available provider "
            f"with {best.projected_approval_rate:.1%} projected approval."
        )
    return RouteRecommendation(rankings=rankings, recommended_provider=best.provider, reasoning=reasoning)


# ---------------------------------------------------------------------------
# Lifecycle — capture / void / refund
# ---------------------------------------------------------------------------

class TransactionResponse(BaseModel):
    transaction_id: str
    provider: str
    state: str
    amount: float
    currency: str
    country: str
    response_code: Optional[str]
    created_at: datetime
    updated_at: datetime


class StateTransitionResponse(BaseModel):
    from_state: str
    to_state: str
    triggered_by: str
    timestamp: datetime


def _txn_to_response(txn) -> TransactionResponse:
    return TransactionResponse(
        transaction_id=txn.id,
        provider=txn.provider,
        state=txn.state,
        amount=txn.amount,
        currency=txn.currency,
        country=txn.country,
        response_code=txn.response_code,
        created_at=txn.created_at,
        updated_at=txn.updated_at,
    )


@app.post("/capture/{transaction_id}", response_model=TransactionResponse, tags=["lifecycle"])
def capture(
    transaction_id: str,
    request: Request,
    db: Session = Depends(get_db),
    api_key=Depends(get_current_api_key),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
) -> TransactionResponse:
    """Capture an authorized payment (AUTHORIZED → CAPTURED). Returns 409 on invalid state."""
    cached = _idem_check(request, api_key.id, idempotency_key)
    if cached is not None:
        return JSONResponse(cached, headers={"X-Idempotency-Replay": "true"})
    try:
        txn = transition(db, transaction_id, TransactionState.CAPTURED, triggered_by="capture")
        result = _txn_to_response(txn)
        _idem_store(request, api_key.id, idempotency_key, result.model_dump())
        return result
    except TransactionNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except InvalidTransitionError as e:
        raise HTTPException(status_code=409, detail=str(e))


@app.post("/void/{transaction_id}", response_model=TransactionResponse, tags=["lifecycle"])
def void(
    transaction_id: str,
    request: Request,
    db: Session = Depends(get_db),
    api_key=Depends(get_current_api_key),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
) -> TransactionResponse:
    """Void an authorized payment (AUTHORIZED → VOIDED). Returns 409 on invalid state."""
    cached = _idem_check(request, api_key.id, idempotency_key)
    if cached is not None:
        return JSONResponse(cached, headers={"X-Idempotency-Replay": "true"})
    try:
        txn = transition(db, transaction_id, TransactionState.VOIDED, triggered_by="void")
        result = _txn_to_response(txn)
        _idem_store(request, api_key.id, idempotency_key, result.model_dump())
        return result
    except TransactionNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except InvalidTransitionError as e:
        raise HTTPException(status_code=409, detail=str(e))


@app.post("/refund/{transaction_id}", response_model=TransactionResponse, tags=["lifecycle"])
def refund(
    transaction_id: str,
    request: Request,
    db: Session = Depends(get_db),
    api_key=Depends(get_current_api_key),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
) -> TransactionResponse:
    """Refund a captured payment (CAPTURED → REFUNDED). Returns 409 on invalid state."""
    cached = _idem_check(request, api_key.id, idempotency_key)
    if cached is not None:
        return JSONResponse(cached, headers={"X-Idempotency-Replay": "true"})
    try:
        txn = transition(db, transaction_id, TransactionState.REFUNDED, triggered_by="refund")
        result = _txn_to_response(txn)
        _idem_store(request, api_key.id, idempotency_key, result.model_dump())
        return result
    except TransactionNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except InvalidTransitionError as e:
        raise HTTPException(status_code=409, detail=str(e))


# ---------------------------------------------------------------------------
# Webhooks — registration
# ---------------------------------------------------------------------------

_VALID_EVENTS = {
    "payment.authorized", "payment.declined",
    "payment.captured", "payment.voided", "payment.refunded",
}


class WebhookRegisterRequest(BaseModel):
    url: str = Field(..., description="HTTPS URL to POST events to")
    events: list[str] = Field(..., min_length=1, description="Event types to subscribe to")
    secret: str = Field(..., min_length=8, max_length=128, description="Signing secret (save it — not shown again)")

    @field_validator("events")
    @classmethod
    def validate_events(cls, v: list[str]) -> list[str]:
        invalid = set(v) - _VALID_EVENTS
        if invalid:
            raise ValueError(f"Unknown event types: {invalid}. Valid: {sorted(_VALID_EVENTS)}")
        return v


class WebhookRegisterResponse(BaseModel):
    webhook_id: str
    url: str
    events: list[str]
    created_at: datetime


@app.post("/webhooks/register", response_model=WebhookRegisterResponse, status_code=201, tags=["webhooks"])
def register_webhook(
    req: WebhookRegisterRequest,
    db: Session = Depends(get_db),
    api_key=Depends(get_current_api_key),
) -> WebhookRegisterResponse:
    """Register a webhook URL to receive signed payment events.

    The server will POST HMAC-SHA256 signed payloads to your URL on each
    matching event. Verify the signature:

        expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
        assert request.headers["X-Signature-256"] == f"sha256={expected}"

    Example body:
    ```json
    {
        "url": "https://your-server.com/webhooks/payments",
        "events": ["payment.authorized", "payment.declined"],
        "secret": "my_webhook_secret_32chars_minimum"
    }
    ```
    """
    from payment_router.db import WebhookConfig
    import json as _json

    cfg = WebhookConfig(
        url=req.url,
        events=_json.dumps(req.events),
        secret=req.secret,
        active=True,
    )
    db.add(cfg)
    db.commit()
    db.refresh(cfg)

    return WebhookRegisterResponse(
        webhook_id=cfg.id,
        url=cfg.url,
        events=req.events,
        created_at=cfg.created_at,
    )


# ---------------------------------------------------------------------------
# Transaction history (no auth — public read for demo)
# ---------------------------------------------------------------------------

@app.get("/transactions/{transaction_id}", response_model=TransactionResponse, tags=["transactions"])
def get_transaction_endpoint(transaction_id: str, db: Session = Depends(get_db)) -> TransactionResponse:
    """Fetch a transaction by ID with its current state."""
    try:
        txn = get_transaction(db, transaction_id)
        return _txn_to_response(txn)
    except TransactionNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get(
    "/transactions/{transaction_id}/transitions",
    response_model=list[StateTransitionResponse],
    tags=["transactions"],
)
def get_transaction_transitions(
    transaction_id: str, db: Session = Depends(get_db)
) -> list[StateTransitionResponse]:
    """Return the full state transition history for a transaction, oldest first."""
    try:
        transitions_list = get_transitions(db, transaction_id)
        return [
            StateTransitionResponse(
                from_state=t.from_state,
                to_state=t.to_state,
                triggered_by=t.triggered_by,
                timestamp=t.timestamp,
            )
            for t in transitions_list
        ]
    except TransactionNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/transactions", response_model=list[TransactionResponse], tags=["transactions"])
def list_transactions(
    provider: Optional[str] = Query(None, description="Filter by provider name"),
    state: Optional[str] = Query(None, description="Filter by state"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> list[TransactionResponse]:
    """List recent transactions with optional filters."""
    from payment_router.db import Transaction as TxnModel
    from sqlalchemy import select

    stmt = select(TxnModel).order_by(TxnModel.created_at.desc()).offset(offset).limit(limit)
    if provider:
        stmt = stmt.where(TxnModel.provider == provider)
    if state:
        stmt = stmt.where(TxnModel.state == state)

    txns = db.execute(stmt).scalars().all()
    return [_txn_to_response(t) for t in txns]
