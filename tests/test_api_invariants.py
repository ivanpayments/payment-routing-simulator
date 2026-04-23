"""API-level invariant tests closing the gaps exposed by the April 2026
adversarial routing review.

These tests operate directly on the engine (same code path as /simulate
and /compare) rather than live HTTP calls, so they run in the pre-deploy
gate without needing a running API. Each test corresponds to one of the
5 BLOCKERs in adversarial_routing_report.md; the file's top docstring in
Claude files/why_patterns_missed_blockers.md explains why the existing
150-pattern ASSERT + 502-pattern non-contradiction gates could not
reach these failures.
"""
from __future__ import annotations

from statistics import mean

import pytest

from payment_router.engine import (
    _approval_probability,
    compare_providers,
    simulate_transaction,
)
from payment_router.models import CardBrand, CardType, CompareRequest, SimulateRequest
from payment_router.provider_loader import clear_cache, list_providers
from payment_router.query_routing_intelligence import query_routing_intelligence
from payment_router.validators import normalize_currency


def setup_function():
    clear_cache()


def _sim_req(provider: str, **kwargs) -> SimulateRequest:
    defaults = dict(
        provider=provider,
        country="BR",
        card_brand=CardBrand.VISA,
        card_type=CardType.CREDIT,
        amount=300.0,
    )
    defaults.update(kwargs)
    return SimulateRequest(**defaults)


# ---------------------------------------------------------------------------
# B1 — /compare and /simulate must agree within +/-6pp at n=200
# ---------------------------------------------------------------------------

def test_compare_projection_matches_simulate_empirical():
    """/compare's 500-sample projection must be within +/-10pp of the
    empirical mean of 500 /simulate calls on the same body. 10pp bound
    covers stochastic noise at n=500 on a provider with 50-70pp base;
    the adversarial report flagged a 35-50pp divergence which this
    invariant catches with plenty of headroom.

    Previously /compare said 93% for high-risk-or-orchestrator-b on BR
    Visa while /simulate observed 45-57% in one run. This test runs
    both code paths on the same profile and asserts convergence.
    """
    req = CompareRequest(
        country="BR",
        card_brand=CardBrand.VISA,
        card_type=CardType.CREDIT,
        amount=300.0,
    )
    compare_rankings = {r.provider: r.projected_approval_rate for r in compare_providers(req)}

    for provider, projected in compare_rankings.items():
        sim = _sim_req(provider, country="BR", amount=300.0)
        n = 500
        observed = sum(1 for _ in range(n) if simulate_transaction(sim).approved) / n
        assert abs(projected - observed) <= 0.10, (
            f"{provider}: /compare projected {projected:.3f} but /simulate empirical {observed:.3f} "
            f"(delta {abs(projected - observed):.3f}) exceeds +/-10pp tolerance."
        )


# ---------------------------------------------------------------------------
# B2 — amount modifier must materially affect approval probability
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("provider", [
    "global-acquirer-a",
    "global-acquirer-b",
    "high-risk-or-orchestrator-a",
    "high-risk-or-orchestrator-b",
    "regional-bank-processor-a",
])
def test_amount_modifier_is_active(provider):
    """For every provider, the approval probability for $10 must exceed the
    probability for $5000 by at least 2pp. Catches the bug where
    amount_modifier_thresholds were defined in YAML but not firing.
    """
    small = _approval_probability(provider, _sim_req(provider, amount=10.0))
    large = _approval_probability(provider, _sim_req(provider, amount=5000.0))
    assert small - large >= 0.02, (
        f"{provider}: $10 prob {small:.3f} vs $5000 prob {large:.3f} — amount modifier "
        f"should induce >=2pp drop but observed {(small - large)*100:.1f}pp."
    )


# ---------------------------------------------------------------------------
# B3 — Amex in LATAM should not outperform Visa/Mastercard
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("country", ["BR", "MX"])
@pytest.mark.parametrize("provider", list_providers())
def test_amex_not_above_visa_in_latam(country, provider):
    """Amex penetration in BR and MX is structurally below V/MC; no provider
    should model Amex as materially higher-approving than Visa in those
    corridors. Tolerance: Amex may be up to 0.5pp above Visa (noise band).
    """
    from payment_router.provider_loader import load_provider
    profile = load_provider(provider)
    cp = profile.country(country)
    if cp is None:
        pytest.skip(f"{provider} does not model {country}")
    visa_prob = cp.base * cp.card_modifiers.visa
    amex_prob = cp.base * cp.card_modifiers.amex
    assert amex_prob <= visa_prob + 0.005, (
        f"{provider}/{country}: Amex modeled at {amex_prob:.3f} which is materially "
        f"above Visa {visa_prob:.3f} — contradicts LATAM Amex-penetration reality."
    )


# ---------------------------------------------------------------------------
# B4 — Archetype-country fit: regional-bank-processor-a (BR-tuned) must win
# ---------------------------------------------------------------------------

def test_br_domestic_regional_bank_beats_crossborder_fx():
    """For a BR domestic (BR→BR) Visa credit $300 transaction, the BR-tuned
    regional bank must beat every cross-border-fx specialist by >=5pp.
    """
    n = 300
    def _rate(provider):
        req = _sim_req(provider, country="BR", amount=300.0)
        return sum(1 for _ in range(n) if simulate_transaction(req).approved) / n

    br_bank = _rate("regional-bank-processor-a")
    for fx in ("cross-border-fx-specialist-a", "cross-border-fx-specialist-b"):
        fx_rate = _rate(fx)
        assert br_bank - fx_rate >= 0.05, (
            f"regional-bank-processor-a on BR domestic ({br_bank:.3f}) should beat "
            f"{fx} ({fx_rate:.3f}) by >=5pp — observed gap {(br_bank - fx_rate)*100:.1f}pp."
        )


def test_br_domestic_orchestrator_does_not_dominate():
    """high-risk-or-orchestrator-b must not be strictly the top provider for
    BR domestic Visa $300 — that ranking was the flagship adversarial
    counterexample (a routine V credit $300 should not land on a high-risk
    orchestrator).
    """
    req = CompareRequest(
        country="BR",
        card_brand=CardBrand.VISA,
        card_type=CardType.CREDIT,
        amount=300.0,
    )
    rankings = compare_providers(req)
    top = rankings[0].provider
    assert top != "high-risk-or-orchestrator-b", (
        f"BR domestic Visa $300 top provider was {top} — orchestrator should not "
        f"rank #1 for routine domestic traffic."
    )


# ---------------------------------------------------------------------------
# B5 — /query cross-border routing must surface the cross-border specialist
# ---------------------------------------------------------------------------

def test_query_crossborder_surfaces_fx_specialist():
    """US-issued Visa paying DE merchant in EUR: at least one cross-border-fx
    specialist must appear in the top 2 of the /query ranking.
    """
    result = query_routing_intelligence(
        country="DE",
        issuer_country="US",
        card_brand="visa",
        card_type="credit",
        amount=300.0,
        currency="EUR",
    )
    top2 = {r["provider"] for r in result["rankings"][:2]}
    fx_set = {"cross-border-fx-specialist-a", "cross-border-fx-specialist-b"}
    assert top2 & fx_set, (
        f"Cross-border US→DE EUR should route to cross-border-fx — top-2 was "
        f"{list(top2)}, no FX specialist present."
    )


def test_query_reasoning_is_not_self_contradictory():
    """The reasoning text must not contain the phrase 'low friction' paired
    with 'reduces approval' — that was a literal self-contradiction surfaced
    in the adversarial report.
    """
    result = query_routing_intelligence(
        country="DE",
        issuer_country="US",
        card_brand="visa",
        card_type="credit",
        amount=300.0,
        currency="EUR",
    )
    reasoning = result["reasoning"].lower()
    assert not ("low friction" in reasoning and "reduces approval" in reasoning), (
        f"Reasoning contains self-contradictory 'low friction ... reduces approval' phrase: "
        f"{result['reasoning']!r}"
    )


# ---------------------------------------------------------------------------
# HIGH-wins — response serializer completeness
# ---------------------------------------------------------------------------

def test_query_top_decline_codes_exclude_approval_code():
    """The `top_decline_codes` array must never contain "00" (the approval
    code). This is a response-serializer bug that planted doubt on every
    downstream stat in /query.
    """
    result = query_routing_intelligence(
        country="BR",
        card_brand="visa",
        card_type="credit",
        amount=300.0,
    )
    for row in result["rankings"]:
        assert "00" not in row.get("top_decline_codes", []), (
            f"{row['provider']}: top_decline_codes contains '00' (approval code): "
            f"{row['top_decline_codes']}"
        )


def test_compare_three_ds_challenge_rate_never_null():
    """/compare must populate three_ds_challenge_rate on every row — either
    from the empirical sample (use_3ds=True) or the YAML-declared rate."""
    req = CompareRequest(
        country="BR",
        card_brand=CardBrand.VISA,
        card_type=CardType.CREDIT,
        amount=300.0,
        use_3ds=False,
    )
    for r in compare_providers(req):
        assert r.three_ds_challenge_rate is not None, (
            f"{r.provider}: three_ds_challenge_rate is null even though YAML declares one."
        )


# ---------------------------------------------------------------------------
# Validator invariants
# ---------------------------------------------------------------------------

def test_currency_validator_rejects_garbage():
    """ISO 4217 allowlist must reject garbage currency codes like 'XYZ' — the
    adversarial test accepted these and returned a misleading decline 54."""
    with pytest.raises(ValueError, match="not a supported"):
        normalize_currency("XYZ")


def test_currency_validator_accepts_iso():
    assert normalize_currency("usd") == "USD"
    assert normalize_currency("EUR") == "EUR"
    assert normalize_currency("BRL") == "BRL"


def test_country_allowlist_rejects_zz():
    """Sanity check that ZZ country (never modeled) rejects — this is
    already enforced but keep the regression test."""
    from payment_router.validators import normalize_country
    with pytest.raises(ValueError):
        normalize_country("ZZ")
