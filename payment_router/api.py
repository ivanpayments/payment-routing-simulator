"""FastAPI REST API for payment-router."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from payment_router import __version__
from payment_router.db import create_tables, get_db
from payment_router.engine import compare_providers, simulate_transaction, simulate_with_retry
from payment_router.query_routing_intelligence import query_routing_intelligence
from payment_router.models import (
    CompareRequest,
    CompareResult,
    ProviderResponse,
    RetryResult,
    RouteRecommendation,
    SimulateRequest,
    TransactionState,
)
from payment_router.provider_loader import list_providers, load_provider
from payment_router.state_machine import (
    InvalidTransitionError,
    TransactionNotFoundError,
    get_transaction,
    get_transitions,
    transition,
)

@asynccontextmanager
async def lifespan(application: FastAPI):
    """Create DB tables on startup (SQLite for local dev; Alembic handles PostgreSQL)."""
    create_tables()
    yield


app = FastAPI(
    title="payment-router",
    description="Payment provider routing simulator REST API",
    version=__version__,
    lifespan=lifespan,
    openapi_tags=[
        {"name": "simulation", "description": "Simulate transactions and compare providers"},
        {"name": "lifecycle", "description": "Payment lifecycle: capture, void, refund"},
        {"name": "transactions", "description": "Transaction history and state"},
        {"name": "webhooks", "description": "Webhook registration (Session 5)"},
        {"name": "providers", "description": "Provider profile management"},
    ],
)


# ---------------------------------------------------------------------------
# Health + providers
# ---------------------------------------------------------------------------

@app.get("/health")
def health() -> dict:
    return {"status": "ok", "version": __version__}


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
# Simulate — single transaction
# ---------------------------------------------------------------------------

@app.post("/simulate", response_model=ProviderResponse, tags=["simulation"])
def simulate(req: SimulateRequest, db: Session = Depends(get_db)) -> ProviderResponse:
    """Simulate a single transaction against one provider.

    The transaction is persisted to the database and can be retrieved via
    `GET /transactions/{transaction_id}`. Use `POST /capture`, `/void`, or `/refund`
    to advance the payment lifecycle.

    Example body:
    ```json
    {
        "provider": "global-acquirer-a",
        "country": "US",
        "issuer_country": "NG",
        "card_brand": "visa",
        "card_type": "credit",
        "amount": 299.99,
        "currency": "USD",
        "use_3ds": true
    }
    ```
    """
    try:
        return simulate_transaction(req, db=db)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ---------------------------------------------------------------------------
# Compare — rank all providers for a given transaction profile
# ---------------------------------------------------------------------------

@app.post("/compare", response_model=list[CompareResult])
def compare(req: CompareRequest) -> list[CompareResult]:
    """Compare all providers for a transaction profile (runs 500 simulations per provider).

    Returns providers ranked by projected approval rate descending.

    Example body:
    ```json
    {
        "country": "BR",
        "issuer_country": "BR",
        "card_brand": "visa",
        "card_type": "credit",
        "amount": 300.0,
        "currency": "USD",
        "use_3ds": false
    }
    ```
    """
    return compare_providers(req)


# ---------------------------------------------------------------------------
# Route — single transaction with soft-decline retry cascade
# ---------------------------------------------------------------------------

class RouteRequest(SimulateRequest):
    """SimulateRequest extended with a priority-ordered provider list for retry."""
    providers: list[str]
    max_attempts: int = 3


@app.post("/route", response_model=RetryResult)
def route(req: RouteRequest) -> RetryResult:
    """Route a transaction with soft-decline retry cascade.

    Tries providers in the order given. On a soft decline (retryable code)
    cascades to the next provider. Stops on approval or hard decline.

    Example body:
    ```json
    {
        "providers": ["global-acquirer-a", "regional-bank", "apm-specialist"],
        "country": "BR",
        "issuer_country": "US",
        "card_brand": "visa",
        "card_type": "credit",
        "amount": 300.0,
        "currency": "USD",
        "use_3ds": false,
        "max_attempts": 3
    }
    ```
    """
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
# Recommend — suggest best provider + reasoning
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Query — natural-language-ready routing intelligence (used by chatbot)
# ---------------------------------------------------------------------------

class QueryRequest(BaseModel):
    country: str
    amount: float
    currency: str = "USD"
    card_brand: str = "visa"
    card_type: str = "credit"
    issuer_country: str | None = None
    use_3ds: bool = False


@app.post("/query")
def query(req: QueryRequest) -> dict:
    """Routing intelligence endpoint — designed to be called by the Payment Data Chatbot.

    Returns a structured answer with recommended provider, retry order,
    plain-English reasoning, and a key insight about this routing scenario.

    Example body:
    ```json
    {
        "country": "BR",
        "amount": 300.0,
        "card_brand": "visa",
        "card_type": "debit",
        "issuer_country": null,
        "use_3ds": false
    }
    ```
    """
    return query_routing_intelligence(
        country=req.country,
        amount=req.amount,
        currency=req.currency,
        card_brand=req.card_brand,
        card_type=req.card_type,
        issuer_country=req.issuer_country,
        use_3ds=req.use_3ds,
    )


@app.post("/recommend", response_model=RouteRecommendation)
def recommend(req: CompareRequest) -> RouteRecommendation:
    """Recommend the best provider for a transaction profile with reasoning.

    Runs compare internally, then returns the top provider with a plain-English
    explanation of why it ranks first.
    """
    rankings = compare_providers(req)
    best = rankings[0]
    second = rankings[1] if len(rankings) > 1 else None

    issuer_note = (
        f" (issuer country: {req.issuer_country})" if req.issuer_country else ""
    )

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

    return RouteRecommendation(
        rankings=rankings,
        recommended_provider=best.provider,
        reasoning=reasoning,
    )


# ---------------------------------------------------------------------------
# Lifecycle — capture / void / refund
# ---------------------------------------------------------------------------

class TransactionResponse(BaseModel):
    """Slim view of a persisted transaction returned by lifecycle endpoints."""
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
def capture(transaction_id: str, db: Session = Depends(get_db)) -> TransactionResponse:
    """Capture an authorized payment (AUTHORIZED → CAPTURED).

    Funds are transferred to the merchant. Only valid from AUTHORIZED state.
    Returns 409 if the transaction is not in AUTHORIZED state.
    """
    try:
        txn = transition(db, transaction_id, TransactionState.CAPTURED, triggered_by="capture")
        return _txn_to_response(txn)
    except TransactionNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except InvalidTransitionError as e:
        raise HTTPException(status_code=409, detail=str(e))


@app.post("/void/{transaction_id}", response_model=TransactionResponse, tags=["lifecycle"])
def void(transaction_id: str, db: Session = Depends(get_db)) -> TransactionResponse:
    """Void an authorized payment (AUTHORIZED → VOIDED).

    Releases the reserved funds without capturing. Only valid from AUTHORIZED state.
    Returns 409 if the transaction is not in AUTHORIZED state.
    """
    try:
        txn = transition(db, transaction_id, TransactionState.VOIDED, triggered_by="void")
        return _txn_to_response(txn)
    except TransactionNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except InvalidTransitionError as e:
        raise HTTPException(status_code=409, detail=str(e))


@app.post("/refund/{transaction_id}", response_model=TransactionResponse, tags=["lifecycle"])
def refund(transaction_id: str, db: Session = Depends(get_db)) -> TransactionResponse:
    """Refund a captured payment (CAPTURED → REFUNDED).

    Returns funds to the cardholder. Only valid from CAPTURED state.
    Returns 409 if the transaction is not in CAPTURED state.
    """
    try:
        txn = transition(db, transaction_id, TransactionState.REFUNDED, triggered_by="refund")
        return _txn_to_response(txn)
    except TransactionNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except InvalidTransitionError as e:
        raise HTTPException(status_code=409, detail=str(e))


# ---------------------------------------------------------------------------
# Transaction history
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
    state: Optional[str] = Query(None, description="Filter by state (authorized/captured/etc.)"),
    limit: int = Query(50, ge=1, le=500, description="Max results"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    db: Session = Depends(get_db),
) -> list[TransactionResponse]:
    """List recent transactions with optional provider/state filters."""
    from payment_router.db import Transaction as TxnModel
    from sqlalchemy import select

    stmt = select(TxnModel).order_by(TxnModel.created_at.desc()).offset(offset).limit(limit)
    if provider:
        stmt = stmt.where(TxnModel.provider == provider)
    if state:
        stmt = stmt.where(TxnModel.state == state)

    txns = db.execute(stmt).scalars().all()
    return [_txn_to_response(t) for t in txns]
