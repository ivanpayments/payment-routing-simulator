"""FastAPI REST API for payment-router."""

from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional

import redis

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, field_validator
from typing import Any
from sqlalchemy.orm import Session
from starlette.middleware.base import BaseHTTPMiddleware

from payment_router import __version__
from payment_router.api_keys import seed_test_key, validate_secret_key
from payment_router.db import Session as DBSession, create_tables, engine, get_db
from payment_router.engine import compare_providers, simulate_transaction, simulate_with_retry
from payment_router.idempotency import get_cached, hash_body, store as idem_store
from payment_router.models import (
    API_AMOUNT_ENVELOPE_USD,
    API_REQUEST_CARD_BRANDS,
    API_REQUEST_CARD_TYPES,
    API_REQUEST_MCCS,
    CardBrand,
    CardType,
    CompareRequest,
    CompareResult,
    ProviderResponse,
    RetryResult,
    RouteRecommendation,
    SimulateRequest,
    TransactionState,
    _validate_api_amount,
    _validate_api_mcc,
)
from payment_router.provider_loader import list_providers, load_provider
from payment_router.query_routing_intelligence import query_routing_intelligence
from payment_router.rate_limit import check_rate_limit, rate_limit_headers
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


class _RateLimitHeaders(BaseHTTPMiddleware):
    """Echo `X-RateLimit-*` headers on successful responses (audit v3 R6).

    The 429 path already attaches headers via HTTPException. For 2xx/4xx
    responses we read the `RateLimitDecision` stashed by `get_current_api_key`
    on `request.state.rate_limit` and copy it onto the response. Endpoints
    that don't depend on auth (e.g. /health, /providers) skip this — no
    decision is computed for them.
    """

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        decision = getattr(request.state, "rate_limit", None)
        if decision is not None:
            for name, value in rate_limit_headers(decision).items():
                # Don't clobber the headers already attached to a 429 response.
                if name not in response.headers:
                    response.headers[name] = value
        return response


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
app.add_middleware(_RateLimitHeaders)


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
    """Validate Bearer sk_test_... token and enforce per-key rate limit.

    Audit v3 R6 (2026-04-26): when the limit fires we raise a 429 carrying the
    standard headers `Retry-After`, `X-RateLimit-Limit`, `X-RateLimit-Remaining`,
    `X-RateLimit-Reset` (plus `X-RateLimit-Scope=key|ip` to disambiguate which
    bucket triggered). On allowed requests we stash the decision on
    `request.state.rate_limit` so a response middleware echoes the same
    headers — clients can monitor their remaining budget without waiting
    for the limit to fire.
    """
    if credentials is None or not credentials.credentials:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    api_key = validate_secret_key(db, credentials.credentials)
    if api_key is None:
        raise HTTPException(status_code=401, detail="Invalid API key")
    rc = request.app.state.redis
    client_ip = request.client.host if request.client else None
    if rc:
        decision = check_rate_limit(rc, api_key.id, client_ip)
        request.state.rate_limit = decision
        if decision.limited:
            raise HTTPException(
                status_code=429,
                detail="Rate limit exceeded",
                headers=rate_limit_headers(decision),
            )
    return api_key


# ---------------------------------------------------------------------------
# Idempotency helpers — used inside endpoints, not as middleware
#
# The cache stores a `{body_hash, response_bytes}` envelope. We store the
# raw response *bytes* so a replay returns byte-identical output (Stripe
# contract). The body hash is compared on replay; a mismatch raises 422
# rather than silently returning the original cached body.
# ---------------------------------------------------------------------------

async def _idem_lookup_or_conflict(
    request: Request, api_key_id: str, idem_key: str | None
) -> Response | None:
    """If a cached response exists for this idempotency key, return it.

    Returns:
      * `Response` carrying the original bytes — caller should `return` it.
      * `None` if no cache entry (or Redis unavailable, or key not supplied).

    Raises 422 when the same key is replayed with a different body.
    """
    if not idem_key:
        return None
    rc = request.app.state.redis
    if rc is None:
        return None
    envelope = get_cached(rc, api_key_id, idem_key)
    if envelope is None:
        return None
    cached_hash = envelope.get("body_hash", "")
    body_bytes = await request.body()
    if cached_hash and hash_body(body_bytes) != cached_hash:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Idempotency-Key '{idem_key}' was previously used with a "
                f"different request body. Re-use of an idempotency key requires "
                f"an identical body; pick a new key for a different request."
            ),
        )
    return Response(
        content=envelope["response_bytes"],
        media_type="application/json",
        headers={"X-Idempotency-Replayed": "true"},
    )


def _idem_store_bytes(
    request: Request,
    api_key_id: str,
    idem_key: str | None,
    response_bytes: str,
    body_hash: str,
) -> None:
    if not idem_key:
        return
    rc = request.app.state.redis
    if rc is None:
        return
    idem_store(rc, api_key_id, idem_key, response_bytes, body_hash)


def _serialize(payload: Any) -> str:
    """Serialize a Pydantic model / dict to a JSON string FastAPI would return."""
    return json.dumps(jsonable_encoder(payload))


# ---------------------------------------------------------------------------
# Health + providers (no auth)
# ---------------------------------------------------------------------------

@app.get("/health")
def health() -> dict:
    """Liveness + service self-description.

    Mirrors the decline-recovery `/recovery/health` shape so a buyer doing
    diligence can see platform defaults (training-envelope cap, MC sample
    size) and rate-limit numbers without having to scrape /docs. See audit
    v2 N7 (2026-04-26).
    """
    return {
        "status": "ok",
        "version": __version__,
        "platform_defaults": {
            "amount_envelope_usd": API_AMOUNT_ENVELOPE_USD,
            "monte_carlo_samples": 500,
            "providers_compared": 11,
            "supported_card_brands": sorted(API_REQUEST_CARD_BRANDS),
            "supported_card_types": sorted(API_REQUEST_CARD_TYPES),
            "supported_mccs": sorted(API_REQUEST_MCCS),
        },
        "rate_limit": {
            "per_key_per_minute": 100,
            "per_ip_per_minute": 60,
        },
    }


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
#
# The pre-coercion validators below catch BOTH typos (e.g. "foobar") AND
# internal-only brands (discover/jcb/unionpay/unknown) with the SAME
# error message that lists only the 3 API-supported brands. Without the
# `mode="before"` indirection, Pydantic's enum coercion would run first
# and surface the underlying CardBrand enum's full 7-value list — that
# was the schema-vs-runtime mismatch reported on 2026-04-26.
# ---------------------------------------------------------------------------

def _check_api_card_brand(v: Any) -> Any:
    raw = v.value if isinstance(v, CardBrand) else str(v).lower().strip()
    if raw not in API_REQUEST_CARD_BRANDS:
        raise ValueError(
            f"card_brand '{raw}' is not accepted by this endpoint. "
            f"Supported: {sorted(API_REQUEST_CARD_BRANDS)}."
        )
    return raw


def _check_api_card_type(v: Any) -> Any:
    raw = v.value if isinstance(v, CardType) else str(v).lower().strip()
    if raw not in API_REQUEST_CARD_TYPES:
        raise ValueError(
            f"card_type '{raw}' is not accepted by this endpoint. "
            f"Supported: {sorted(API_REQUEST_CARD_TYPES)}."
        )
    return raw


class ApiSimulateRequest(SimulateRequest):
    """Public /simulate body. Rejects internal-only brands / types.

    The card_brand / card_type fields are redeclared so the OpenAPI schema
    advertises only the 3 brands / 4 types the provider YAMLs model. The
    underlying type stays CardBrand / CardType so the engine's enum-equality
    checks (e.g. `req.card_brand == CardBrand.VISA`) keep working.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "provider": "global-acquirer-a",
                "country": "BR",
                "issuer_country": "GB",
                "card_brand": "visa",
                "card_type": "credit",
                "amount": 300,
                "currency": "USD",
                "use_3ds": True,
            }
        }
    )

    card_brand: CardBrand = Field(
        CardBrand.VISA,
        description="Card brand. Only visa/mastercard/amex are routable; "
                    "discover/jcb/unionpay/unknown exist in the internal "
                    "enum for BIN-mismatch reasoning but no provider YAML "
                    "models them, so they are rejected at the API surface.",
        json_schema_extra={"enum": sorted(API_REQUEST_CARD_BRANDS)},
    )
    card_type: CardType = Field(
        CardType.CREDIT,
        description="Card type. Excludes internal-only 'unknown'.",
        json_schema_extra={"enum": sorted(API_REQUEST_CARD_TYPES)},
    )

    _check_brand = field_validator("card_brand", mode="before")(
        classmethod(lambda cls, v: _check_api_card_brand(v))
    )
    _check_type = field_validator("card_type", mode="before")(
        classmethod(lambda cls, v: _check_api_card_type(v))
    )
    _check_mcc = field_validator("mcc", mode="before")(
        classmethod(lambda cls, v: _validate_api_mcc(v))
    )
    _check_amount = field_validator("amount", mode="before")(
        classmethod(lambda cls, v: _validate_api_amount(v))
    )


class ApiCompareRequest(CompareRequest):
    """Public /compare body. Rejects internal-only brands / types.

    Same field-narrowing pattern as ApiSimulateRequest — see that docstring.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "country": "BR",
                "card_brand": "visa",
                "card_type": "credit",
                "amount": 300,
                "currency": "USD",
            }
        }
    )

    card_brand: CardBrand = Field(
        CardBrand.VISA,
        description="Card brand. Only visa/mastercard/amex are routable; "
                    "discover/jcb/unionpay/unknown exist in the internal "
                    "enum for BIN-mismatch reasoning but no provider YAML "
                    "models them, so they are rejected at the API surface.",
        json_schema_extra={"enum": sorted(API_REQUEST_CARD_BRANDS)},
    )
    card_type: CardType = Field(
        CardType.CREDIT,
        description="Card type. Excludes internal-only 'unknown'.",
        json_schema_extra={"enum": sorted(API_REQUEST_CARD_TYPES)},
    )

    _check_brand = field_validator("card_brand", mode="before")(
        classmethod(lambda cls, v: _check_api_card_brand(v))
    )
    _check_type = field_validator("card_type", mode="before")(
        classmethod(lambda cls, v: _check_api_card_type(v))
    )
    _check_mcc = field_validator("mcc", mode="before")(
        classmethod(lambda cls, v: _validate_api_mcc(v))
    )
    _check_amount = field_validator("amount", mode="before")(
        classmethod(lambda cls, v: _validate_api_amount(v))
    )


# ---------------------------------------------------------------------------
# Simulate — single transaction
# ---------------------------------------------------------------------------

@app.post("/simulate", response_model=ProviderResponse, tags=["simulation"])
async def simulate(
    req: ApiSimulateRequest,
    request: Request,
    db: Session = Depends(get_db),
    api_key=Depends(get_current_api_key),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
) -> Response:
    """Simulate a single transaction against one provider.

    Requires `Authorization: Bearer sk_test_...`. Supports idempotency via
    the `Idempotency-Key` header — repeat calls with the same key return the
    cached response for 24 hours.

    Example body:
    ```json
    {
        "provider": "global-acquirer",
        "country": "BR",
        "issuer_country": "GB",
        "card_brand": "visa",
        "card_type": "credit",
        "amount": 299.99,
        "currency": "USD",
        "use_3ds": true
    }
    ```
    """
    cached = await _idem_lookup_or_conflict(request, api_key.id, idempotency_key)
    if cached is not None:
        return cached
    try:
        result = simulate_transaction(req, db=db)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    response_bytes = _serialize(result)
    body_hash = hash_body(await request.body())
    _idem_store_bytes(request, api_key.id, idempotency_key, response_bytes, body_hash)
    return Response(content=response_bytes, media_type="application/json")


# ---------------------------------------------------------------------------
# Compare — rank all providers for a given transaction profile
# ---------------------------------------------------------------------------

# Optional fields the public ranking endpoints (/compare, /query, /recommend)
# silently fill with platform defaults when the caller omits them. Audit v3 R9
# (2026-04-26) introduced the `defaults_applied` marker on /compare for
# card_brand + card_type. Audit v4 M5 + M6 (2026-04-27) extended that to all
# six optional fields the routing profile depends on (a missing currency,
# use_3ds, mcc, or issuer_country quietly changes the ranking too) and made
# /query and /recommend emit the same marker so clients have one consistent
# signal across the three ranking endpoints.
_RANKING_OPTIONAL_FIELDS = (
    "card_brand",
    "card_type",
    "currency",
    "use_3ds",
    "mcc",
    "issuer_country",
)


async def _collect_defaults_applied(request: Request) -> list[str]:
    """Inspect the raw JSON body and return the optional fields the caller omitted.

    FastAPI caches the request body bytes on first read, so re-parsing here
    does not race with the body Pydantic already consumed. An empty or
    unparseable body falls back to assuming every optional was filled by
    default — the request still succeeds because Pydantic produced a valid
    model, but the marker lists every optional defensively.
    """
    supplied: set[str] = set()
    try:
        raw_bytes = await request.body()
        if raw_bytes:
            parsed = json.loads(raw_bytes)
            if isinstance(parsed, dict):
                supplied = set(parsed.keys())
    except (ValueError, json.JSONDecodeError):
        supplied = set()
    return [f for f in _RANKING_OPTIONAL_FIELDS if f not in supplied]


class CompareResponse(BaseModel):
    """Wrapped /compare response.

    The `providers` array preserves the previous payload shape. The
    `defaults_applied` array lists optional input fields that were not
    supplied by the caller and therefore fell back to platform defaults
    (audit v3 R9, 2026-04-26; expanded to all six optional fields per
    audit v4 M5, 2026-04-27).
    """
    providers: list[CompareResult]
    defaults_applied: list[str] = Field(default_factory=list)


@app.post("/compare", response_model=CompareResponse, tags=["simulation"])
async def compare(
    req: ApiCompareRequest,
    request: Request,
    api_key=Depends(get_current_api_key),
) -> CompareResponse:
    """Compare all providers for a transaction profile (runs 500 simulations per provider).

    Returns providers ranked by projected approval rate descending. The
    `defaults_applied` array lists any optional input fields (card_brand,
    card_type, currency, use_3ds, mcc, issuer_country) that were absent
    from the request and silently filled with platform defaults — clients
    should treat this as a hint that the routing call ran on a less-specific
    profile than they may have intended.
    """
    defaults_applied = await _collect_defaults_applied(request)
    providers = compare_providers(req)
    return CompareResponse(providers=providers, defaults_applied=defaults_applied)


# ---------------------------------------------------------------------------
# Route — single transaction with soft-decline retry cascade
# ---------------------------------------------------------------------------

class RouteRequest(ApiSimulateRequest):
    """ApiSimulateRequest extended with a priority-ordered provider list for retry."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "provider": "global-acquirer-a",
                "providers": ["global-acquirer-a", "regional-bank-processor-a"],
                "country": "BR",
                "card_brand": "visa",
                "card_type": "credit",
                "amount": 300,
                "currency": "USD",
                "max_attempts": 3,
            }
        }
    )

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
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "country": "BR",
                "amount": 300,
                "currency": "USD",
                "card_brand": "visa",
                "card_type": "credit",
                "issuer_country": "GB",
                "use_3ds": True,
            }
        }
    )

    country: str
    amount: float = Field(..., gt=0, le=10_000_000)
    currency: str = "USD"
    card_brand: str = "visa"
    card_type: str = "credit"
    issuer_country: str | None = None
    use_3ds: bool = False
    mcc: str | None = Field(
        None,
        description="Optional 4-digit ISO 18245 merchant category code. "
                    "When supplied, the ranker applies a high-risk vs "
                    "mainstream MCC bucket lift to specialised archetypes "
                    "(e.g. 5944/5967/7273/7995 boost the high-risk orchestrator).",
        max_length=4,
    )

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

    # MCC + amount validators harmonised with /compare and /simulate
    # (audit v2 N1 + N2, 2026-04-26).
    _check_mcc = field_validator("mcc", mode="before")(
        classmethod(lambda cls, v: _validate_api_mcc(v))
    )
    _check_amount = field_validator("amount", mode="before")(
        classmethod(lambda cls, v: _validate_api_amount(v))
    )


@app.post("/query", tags=["simulation"])
async def query(
    req: QueryRequest,
    request: Request,
    api_key=Depends(get_current_api_key),
) -> dict:
    """Routing intelligence endpoint for the Payment Data Chatbot.

    Returns the same shape as before plus a `defaults_applied` list (audit
    v4 M6, 2026-04-27) — same marker /compare has carried since v3 R9, now
    extended across all three ranking endpoints with the full six optional
    fields (card_brand, card_type, currency, use_3ds, mcc, issuer_country).
    """
    result = query_routing_intelligence(
        country=req.country,
        amount=req.amount,
        currency=req.currency,
        card_brand=req.card_brand,
        card_type=req.card_type,
        issuer_country=req.issuer_country,
        use_3ds=req.use_3ds,
        mcc=req.mcc,
    )
    result["defaults_applied"] = await _collect_defaults_applied(request)
    return result


@app.post("/recommend", response_model=RouteRecommendation, tags=["simulation"])
async def recommend(
    req: ApiCompareRequest,
    request: Request,
    api_key=Depends(get_current_api_key),
) -> RouteRecommendation:
    """Recommend the best provider with plain-English reasoning.

    Returns the same shape as before plus a `defaults_applied` list (audit
    v4 M6, 2026-04-27) — same marker /compare and /query carry, listing
    any optional input fields the caller omitted that the server filled
    with platform defaults.
    """
    defaults_applied = await _collect_defaults_applied(request)
    rankings = compare_providers(req)
    best = rankings[0]
    second = rankings[1] if len(rankings) > 1 else None
    issuer_note = f" (issuer country: {req.issuer_country})" if req.issuer_country else ""
    if second:
        gap = best.projected_approval_rate - second.projected_approval_rate
        gap_pp = gap * 100  # convert to percentage points for tier check
        # Spread-adaptive language (audit v2 N4, 2026-04-26).
        # Below 1pp the call is well within Monte Carlo noise (n=500), so
        # don't oversell with "leads by"; tell buyers it's a near-tie and
        # to break by latency / fee. This mirrors the tiered wording in
        # query_routing_intelligence._derive_insight.
        if gap_pp < 1.0:
            reasoning = (
                f"{best.provider} ({best.projected_approval_rate:.1%}) and "
                f"{second.provider} ({second.projected_approval_rate:.1%}) tie "
                f"on approval for {req.card_brand.value}/{req.card_type.value} "
                f"in {req.country}{issuer_note} at {req.amount} {req.currency} — "
                f"spread is {gap_pp:.2f}pp, within Monte Carlo noise (n=500). "
                f"Pick by latency (p50 {best.latency_p50_ms:.0f}ms vs "
                f"{second.latency_p50_ms:.0f}ms) or fees."
            )
        else:
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
    return RouteRecommendation(
        rankings=rankings,
        recommended_provider=best.provider,
        reasoning=reasoning,
        defaults_applied=defaults_applied,
    )


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
async def capture(
    transaction_id: str,
    request: Request,
    db: Session = Depends(get_db),
    api_key=Depends(get_current_api_key),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
) -> Response:
    """Capture an authorized payment (AUTHORIZED → CAPTURED). Returns 409 on invalid state."""
    cached = await _idem_lookup_or_conflict(request, api_key.id, idempotency_key)
    if cached is not None:
        return cached
    try:
        txn = transition(db, transaction_id, TransactionState.CAPTURED, triggered_by="capture")
        result = _txn_to_response(txn)
        response_bytes = _serialize(result)
        body_hash = hash_body(await request.body())
        _idem_store_bytes(request, api_key.id, idempotency_key, response_bytes, body_hash)
        return Response(content=response_bytes, media_type="application/json")
    except TransactionNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except InvalidTransitionError as e:
        raise HTTPException(status_code=409, detail=str(e))


@app.post("/void/{transaction_id}", response_model=TransactionResponse, tags=["lifecycle"])
async def void(
    transaction_id: str,
    request: Request,
    db: Session = Depends(get_db),
    api_key=Depends(get_current_api_key),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
) -> Response:
    """Void an authorized payment (AUTHORIZED → VOIDED). Returns 409 on invalid state."""
    cached = await _idem_lookup_or_conflict(request, api_key.id, idempotency_key)
    if cached is not None:
        return cached
    try:
        txn = transition(db, transaction_id, TransactionState.VOIDED, triggered_by="void")
        result = _txn_to_response(txn)
        response_bytes = _serialize(result)
        body_hash = hash_body(await request.body())
        _idem_store_bytes(request, api_key.id, idempotency_key, response_bytes, body_hash)
        return Response(content=response_bytes, media_type="application/json")
    except TransactionNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except InvalidTransitionError as e:
        raise HTTPException(status_code=409, detail=str(e))


@app.post("/refund/{transaction_id}", response_model=TransactionResponse, tags=["lifecycle"])
async def refund(
    transaction_id: str,
    request: Request,
    db: Session = Depends(get_db),
    api_key=Depends(get_current_api_key),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
) -> Response:
    """Refund a captured payment (CAPTURED → REFUNDED). Returns 409 on invalid state."""
    cached = await _idem_lookup_or_conflict(request, api_key.id, idempotency_key)
    if cached is not None:
        return cached
    try:
        txn = transition(db, transaction_id, TransactionState.REFUNDED, triggered_by="refund")
        result = _txn_to_response(txn)
        response_bytes = _serialize(result)
        body_hash = hash_body(await request.body())
        _idem_store_bytes(request, api_key.id, idempotency_key, response_bytes, body_hash)
        return Response(content=response_bytes, media_type="application/json")
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
