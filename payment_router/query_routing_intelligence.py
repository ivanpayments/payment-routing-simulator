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
    mcc: str | None = None,
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
    mcc:            Optional 4-digit MCC. High-risk MCCs (5944/5967/7273/7995/...)
                    boost specialised orchestrators; mainstream MCCs slightly
                    demote them.

    Returns
    -------
    dict with keys:
        recommended_provider    str   — top-ranked provider name
        fallback_provider       str   — second-best for retry cascade
        retry_order             list  — all providers in recommended routing order
        rankings                list  — [{provider, projected_approval_rate, latency_p50_ms, ...}]
        reasoning               str   — plain-English explanation of the recommendation
        key_insight             str   — one notable finding (cross-border penalty, 3DS etc.)
        scenario                dict  — echo of the input parameters

    Field naming aligned with /compare and /recommend response shapes —
    `projected_approval_rate` is now the canonical key everywhere a Monte
    Carlo projection is returned (audit v3 R3, 2026-04-26). Previously
    /query exposed the same metric under `approval_rate`, which forced
    client developers to remap one of the three endpoints by hand.
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
        mcc=mcc,
    )

    rankings_raw = compare_providers(req)

    # Cross-endpoint shape parity (audit v4 M2 + M3, 2026-04-27).
    #
    # Previously /query exposed `latency_p50_ms` as an int (rounded) and
    # the decline data as `top_decline_codes: [["05", 0.42], ...]` — a
    # different field name and a different shape than /compare and
    # /recommend, which return floats and `decline_code_distribution`
    # as a dict. Same metrics, two response shapes, two field names.
    #
    # Fix: emit floats everywhere and use `decline_code_distribution`
    # (dict) as the canonical key for the decline distribution. The
    # "00" filter (approval code must not show up in a "decline"
    # field) still applies. We round to keep the chatbot's payload
    # human-readable without forcing the type drift back in.
    rankings = [
        {
            "provider": r.provider,
            "projected_approval_rate": round(r.projected_approval_rate, 3),
            "latency_p50_ms": round(float(r.latency_p50_ms), 1),
            "latency_p95_ms": round(float(r.latency_p95_ms), 1),
            "decline_code_distribution": {
                code: round(share, 3)
                for code, share in r.decline_code_distribution.items()
                if code != "00"
            },
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
        tier_labels = {
            1: "Tier 1 (low friction)",
            2: "Tier 2 (moderate friction)",
            3: "Tier 3 (high friction)",
        }
        # Tier 1 = no approval drag; Tier 2/3 progressively reduce approvals.
        # Avoid the prior contradictory phrasing ("Tier 1 (low friction), which
        # reduces approval rates") — low friction does not reduce approvals.
        if tier == 1:
            impact = "has negligible cross-border friction on approval rates"
        elif tier == 2:
            impact = "applies moderate cross-border issuer friction (~6pp drag)"
        else:
            impact = "applies high cross-border issuer friction (~13pp drag)"
        cross_border_note = (
            f" Note: {issuer_country}-issued card on {country} merchant is cross-border — "
            f"issuer is {tier_labels[tier]}, which {impact}."
        )

    if second:
        gap = best["projected_approval_rate"] - second["projected_approval_rate"]
        gap_pp = gap * 100  # in percentage points
        # Spread-adaptive language (audit v2 N4, 2026-04-26): below 1pp the
        # spread is within Monte Carlo noise (n=500), so don't say "leads by"
        # — call it a tie and tell buyers to break by latency / fees.
        if gap_pp < 1.0:
            reasoning = (
                f"{best['provider']} ({best['projected_approval_rate']:.1%}) and "
                f"{second['provider']} ({second['projected_approval_rate']:.1%}) tie on "
                f"approval for {card_type} {card_brand} in {country} at "
                f"{amount} {currency} — spread is {gap_pp:.2f}pp, within "
                f"Monte Carlo noise (n=500). Pick by latency "
                f"(p50 {best['latency_p50_ms']:.0f}ms vs "
                f"{second['latency_p50_ms']:.0f}ms) or fees. "
                f"For a retry cascade on soft declines, "
                f"{second['provider']} is a sensible fallback."
                f"{cross_border_note}"
            )
        else:
            reasoning = (
                f"{best['provider']} is the best choice for {card_type} {card_brand} "
                f"transactions in {country} at {amount} {currency}, projecting "
                f"{best['projected_approval_rate']:.1%} approval at {best['latency_p50_ms']:.0f}ms p50. "
                f"It leads {second['provider']} by {gap:.1%} ({second['projected_approval_rate']:.1%}). "
                f"For a retry cascade on soft declines, route to {second['provider']} next."
                f"{cross_border_note}"
            )
    else:
        reasoning = (
            f"{best['provider']} is the only available provider, projecting "
            f"{best['projected_approval_rate']:.1%} approval.{cross_border_note}"
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
            "mcc": mcc,
        },
    }


def _derive_insight(
    rankings: list[dict],
    country: str,
    issuer_country: str | None,
    card_type: CardType,
    use_3ds: bool,
) -> str:
    """Generate one notable observation about this routing scenario.

    The headline insight compares the recommended provider against the
    realistic runner-up (the fallback) — NOT against the worst-of-N.
    Comparing top-1 to worst-of-N produces rhetorically misleading spreads
    (e.g. "37.8% lift") because the worst provider is often a structural
    misfit no merchant would route to. The runner-up is the realistic
    alternative a buyer would weigh.

    Wording adapts to spread magnitude (audit v3 R8, 2026-04-26):
      * spread < 0.5pp → "tied within Monte Carlo noise" — the previous
                         "edges by 0.0/0.2/0.4pp" template was meaningless
                         (n=500 standard error ≈ 1pp), so flag the tie
                         explicitly and tell buyers to pick on latency/fees.
      * 0.5pp ≤ spread < 2pp  → "edges by Xpp" — small but distinguishable
      * 2pp ≤ spread < 5pp    → "leads by Xpp" — meaningful gap
      * spread ≥ 5pp          → "clearly outperforms" — decisive
    """

    best = rankings[0]
    second = rankings[1] if len(rankings) > 1 else None

    # Headline: best vs realistic runner-up (the fallback returned to caller).
    # Only emit if there IS a runner-up; degenerate single-provider case
    # falls through to the secondary-insight branches below.
    if second is not None:
        gap = best["projected_approval_rate"] - second["projected_approval_rate"]
        gap_pp = gap * 100  # in percentage points
        # Scale anchor: incremental approvals per 1M txns at the given gap.
        # Round to nearest hundred so the number reads as an estimate, not a forecast.
        incremental_per_1m = int(round(gap * 1_000_000, -2))

        if gap_pp < 0.5:
            # Within Monte Carlo noise (n=500 SE ≈ 1pp) — call it a tie.
            return (
                f"{best['provider']} ({best['projected_approval_rate']:.1%}) and "
                f"{second['provider']} ({second['projected_approval_rate']:.1%}) "
                f"are tied within Monte Carlo noise (spread {gap_pp:.2f}pp, "
                f"n=500 standard error ≈ 1pp) — pick by latency/fees, "
                f"not approval."
            )
        elif gap_pp < 2.0:
            # Small but distinguishable spread — be honest. Don't oversell.
            return (
                f"{best['provider']} ({best['projected_approval_rate']:.1%}) edges the next-best "
                f"realistic option {second['provider']} ({second['projected_approval_rate']:.1%}) "
                f"by {gap_pp:.1f}pp — small absolute spread, "
                f"but on 1M txns that's ~{incremental_per_1m:,} incremental approvals."
            )
        elif gap_pp < 5.0:
            return (
                f"{best['provider']} ({best['projected_approval_rate']:.1%}) leads the next-best "
                f"realistic option {second['provider']} ({second['projected_approval_rate']:.1%}) "
                f"by {gap_pp:.1f}pp — a meaningful but not decisive gap; "
                f"on 1M txns that's ~{incremental_per_1m:,} incremental approvals."
            )
        else:
            return (
                f"{best['provider']} ({best['projected_approval_rate']:.1%}) clearly outperforms "
                f"the next-best realistic option {second['provider']} "
                f"({second['projected_approval_rate']:.1%}) by {gap_pp:.1f}pp — "
                f"on 1M txns that's ~{incremental_per_1m:,} incremental approvals; "
                f"the routing call is decisive."
            )

    # ---- Secondary insights (only reachable when there is no runner-up,
    # which in practice never happens with 11 providers — kept defensively) ----

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

    # Default fallback (single-provider case)
    return (
        f"{best['provider']} is the only available provider for this profile, "
        f"so there is no comparison to draw — projection: {best['projected_approval_rate']:.1%} approval."
    )
