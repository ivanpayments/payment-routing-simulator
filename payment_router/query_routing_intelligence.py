"""query_routing_intelligence — callable tool for the Payment Data Chatbot.

The chatbot (Project 1) can call this function when a user asks routing
questions: "which provider should I use for Indian debit cards?",
"why are Brazil approvals low and what should I switch to?", etc.

Returns a structured dict the chatbot can format into a natural-language answer.
"""

from __future__ import annotations

from payment_router.engine import compare_providers
from payment_router.issuer_tiers import get_issuer_tier
from payment_router.models import CardBrand, CardType, CompareRequest


def query_routing_intelligence(
    country: str,
    amount: float,
    currency: str = "USD",
    card_brand: str = "visa",
    card_type: str = "credit",
    issuer_country: str | None = None,
    use_3ds: bool = False,
) -> dict:
    """Return routing recommendations for a given transaction profile.

    Parameters
    ----------
    country:        ISO 3166-1 alpha-2 merchant country code (e.g. "BR")
    amount:         Transaction amount in the given currency
    currency:       ISO 4217 currency code (default "USD")
    card_brand:     "visa", "mastercard", or "amex" (default "visa")
    card_type:      "credit", "debit", "prepaid", or "commercial" (default "credit")
    issuer_country: Card-issuing country, if known (omit = assume domestic)
    use_3ds:        Whether to include 3DS challenge rate in the analysis

    Returns
    -------
    dict with keys:
        recommended_provider    str   — top-ranked provider name
        fallback_provider       str   — second-best for retry cascade
        retry_order             list  — all providers in recommended routing order
        rankings                list  — [{provider, approval_rate, latency_p50_ms, ...}]
        reasoning               str   — plain-English explanation of the recommendation
        key_insight             str   — one notable finding (cross-border penalty, 3DS etc.)
        scenario                dict  — echo of the input parameters
    """
    # Normalise inputs
    country = country.upper()
    if issuer_country:
        issuer_country = issuer_country.upper()

    try:
        brand = CardBrand(card_brand.lower())
    except ValueError:
        brand = CardBrand.VISA

    try:
        ctype = CardType(card_type.lower())
    except ValueError:
        ctype = CardType.CREDIT

    req = CompareRequest(
        country=country,
        issuer_country=issuer_country,
        card_brand=brand,
        card_type=ctype,
        amount=amount,
        currency=currency,
        use_3ds=use_3ds,
    )

    rankings_raw = compare_providers(req)

    rankings = [
        {
            "provider": r.provider,
            "approval_rate": round(r.projected_approval_rate, 3),
            "latency_p50_ms": round(r.latency_p50_ms),
            "latency_p95_ms": round(r.latency_p95_ms),
            "top_decline_codes": list(r.decline_code_distribution.keys())[:3],
            **({"three_ds_challenge_rate": round(r.three_ds_challenge_rate, 3)}
               if r.three_ds_challenge_rate is not None else {}),
        }
        for r in rankings_raw
    ]

    best = rankings[0]
    second = rankings[1] if len(rankings) > 1 else None
    retry_order = [r["provider"] for r in rankings]

    # Build plain-English reasoning
    cross_border_note = ""
    if issuer_country and issuer_country != country:
        tier = get_issuer_tier(issuer_country)
        tier_labels = {1: "Tier 1 (low friction)", 2: "Tier 2 (moderate friction)", 3: "Tier 3 (high friction)"}
        cross_border_note = (
            f" Note: {issuer_country}-issued card on {country} merchant is cross-border — "
            f"issuer is {tier_labels[tier]}, which reduces approval rates across all providers."
        )

    if second:
        gap = best["approval_rate"] - second["approval_rate"]
        reasoning = (
            f"{best['provider']} is the best choice for {card_type} {card_brand} "
            f"transactions in {country} at {amount} {currency}, projecting "
            f"{best['approval_rate']:.1%} approval at {best['latency_p50_ms']}ms p50. "
            f"It leads {second['provider']} by {gap:.1%} ({second['approval_rate']:.1%}). "
            f"For a retry cascade on soft declines, route to {second['provider']} next."
            f"{cross_border_note}"
        )
    else:
        reasoning = (
            f"{best['provider']} is the only available provider, projecting "
            f"{best['approval_rate']:.1%} approval.{cross_border_note}"
        )

    # Build key insight
    key_insight = _derive_insight(rankings, country, issuer_country, ctype, use_3ds)

    return {
        "recommended_provider": best["provider"],
        "fallback_provider": second["provider"] if second else None,
        "retry_order": retry_order,
        "rankings": rankings,
        "reasoning": reasoning,
        "key_insight": key_insight,
        "scenario": {
            "country": country,
            "issuer_country": issuer_country,
            "card_brand": card_brand,
            "card_type": card_type,
            "amount": amount,
            "currency": currency,
            "use_3ds": use_3ds,
        },
    }


def _derive_insight(
    rankings: list[dict],
    country: str,
    issuer_country: str | None,
    card_type: CardType,
    use_3ds: bool,
) -> str:
    """Generate one notable observation about this routing scenario."""

    best = rankings[0]
    worst = rankings[-1]
    spread = best["approval_rate"] - worst["approval_rate"]

    # Large spread between best and worst — routing choice matters a lot
    if spread >= 0.20:
        return (
            f"Provider choice is high-impact here: {best['provider']} ({best['approval_rate']:.1%}) "
            f"outperforms {worst['provider']} ({worst['approval_rate']:.1%}) by {spread:.1%} — "
            f"routing to the wrong provider loses roughly 1 in {int(1/spread + 0.5)} transactions."
        )

    # Cross-border issuer penalty
    if issuer_country and issuer_country != country:
        tier = get_issuer_tier(issuer_country)
        if tier == 3:
            return (
                f"Cross-border flag: {issuer_country} is a Tier 3 issuer — expect ~13% approval "
                f"drag versus a domestic card. All rates shown already include this penalty."
            )
        elif tier == 2:
            return (
                f"Cross-border flag: {issuer_country} is a Tier 2 issuer — expect ~6% approval "
                f"drag versus a domestic card. All rates shown already include this penalty."
            )

    # Prepaid card — note the structural drag
    if card_type == CardType.PREPAID:
        return (
            f"Prepaid cards carry a structural approval penalty (0.75–0.88× depending on provider). "
            f"If approval rate is critical, consider routing prepaid to {best['provider']} "
            f"which has the highest prepaid tolerance in this market."
        )

    # High 3DS challenge rate
    if use_3ds:
        challenge_rates = [
            (r["provider"], r["three_ds_challenge_rate"])
            for r in rankings
            if "three_ds_challenge_rate" in r
        ]
        if challenge_rates:
            lowest = min(challenge_rates, key=lambda x: x[1])
            highest = max(challenge_rates, key=lambda x: x[1])
            if highest[1] - lowest[1] >= 0.15:
                return (
                    f"3DS challenge rates vary significantly: {lowest[0]} challenges "
                    f"{lowest[1]:.0%} of transactions vs {highest[0]} at {highest[1]:.0%}. "
                    f"Lower challenge rate reduces checkout abandonment."
                )

    # Latency spread
    latencies = [(r["provider"], r["latency_p50_ms"]) for r in rankings]
    fastest = min(latencies, key=lambda x: x[1])
    slowest = max(latencies, key=lambda x: x[1])
    if slowest[1] / fastest[1] >= 2.0:
        return (
            f"Latency spread is wide: {fastest[0]} at {fastest[1]}ms p50 vs "
            f"{slowest[0]} at {slowest[1]}ms p50. "
            f"For latency-sensitive flows, {fastest[0]} is the clear choice."
        )

    # Default
    return (
        f"Provider spread is narrow ({spread:.1%}). "
        f"In this market, approval rate differences are small — "
        f"latency and cost should drive the final routing decision."
    )
