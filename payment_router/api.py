"""FastAPI REST API for payment-router."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from payment_router import __version__
from payment_router.engine import compare_providers, simulate_transaction, simulate_with_retry
from payment_router.query_routing_intelligence import query_routing_intelligence
from payment_router.models import (
    CompareRequest,
    CompareResult,
    ProviderResponse,
    RetryResult,
    RouteRecommendation,
    SimulateRequest,
)
from payment_router.provider_loader import list_providers, load_provider

app = FastAPI(
    title="payment-router",
    description="Payment provider routing simulator REST API",
    version=__version__,
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

@app.post("/simulate", response_model=ProviderResponse)
def simulate(req: SimulateRequest) -> ProviderResponse:
    """Simulate a single transaction against one provider.

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
        return simulate_transaction(req)
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
