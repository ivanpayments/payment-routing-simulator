"""Tests for simulation engine."""

import pytest
from payment_router.engine import compare_providers, simulate_transaction, simulate_with_retry
from payment_router.models import CardBrand, CardType, CompareRequest, SimulateRequest
from payment_router.provider_loader import clear_cache


def setup_function():
    clear_cache()


def _make_req(**kwargs) -> SimulateRequest:
    defaults = dict(provider="global-acquirer-a", country="US", card_brand=CardBrand.VISA, amount=100.0)
    defaults.update(kwargs)
    return SimulateRequest(**defaults)


def test_simulate_returns_response():
    resp = simulate_transaction(_make_req())
    assert resp.transaction_id
    assert resp.provider == "global-acquirer-a"
    assert resp.amount == 100.0


def test_simulate_approved_or_declined():
    resp = simulate_transaction(_make_req())
    if resp.approved:
        assert resp.response_code == "00"
        assert resp.state.value == "authorized"
    else:
        assert resp.response_code != "00"
        assert resp.state.value == "declined"


def test_simulate_3ds_returns_result():
    resp = simulate_transaction(_make_req(use_3ds=True))
    assert resp.three_ds is not None
    assert resp.three_ds.version.value in ("1.0", "2.1", "2.2")


def test_simulate_3ds_liability_shift_field_present():
    resp = simulate_transaction(_make_req(use_3ds=True))
    assert resp.three_ds is not None
    assert isinstance(resp.three_ds.liability_shift, bool)


def test_simulate_no_3ds_by_default():
    resp = simulate_transaction(_make_req())
    assert resp.three_ds is None


def test_latency_positive():
    resp = simulate_transaction(_make_req())
    assert resp.latency_ms > 0


def test_approval_rate_in_range():
    """Run 200 simulations; approval rate should be within plausible range."""
    results = [simulate_transaction(_make_req()) for _ in range(200)]
    rate = sum(1 for r in results if r.approved) / len(results)
    assert 0.60 <= rate <= 0.99, f"Unexpected approval rate: {rate:.1%}"


# ---------------------------------------------------------------------------
# Card type
# ---------------------------------------------------------------------------

def test_prepaid_lower_approval_than_credit():
    """Prepaid modifier (0.87) should produce meaningfully lower approval than credit (1.0)."""
    n = 300
    credit = [simulate_transaction(_make_req(card_type=CardType.CREDIT)) for _ in range(n)]
    prepaid = [simulate_transaction(_make_req(card_type=CardType.PREPAID)) for _ in range(n)]
    credit_rate = sum(1 for r in credit if r.approved) / n
    prepaid_rate = sum(1 for r in prepaid if r.approved) / n
    assert prepaid_rate < credit_rate, (
        f"Expected prepaid ({prepaid_rate:.1%}) < credit ({credit_rate:.1%})"
    )


def test_card_type_in_response():
    resp = simulate_transaction(_make_req(card_type=CardType.DEBIT))
    assert resp.card_type == CardType.DEBIT


# ---------------------------------------------------------------------------
# Issuer country
# ---------------------------------------------------------------------------

def test_domestic_transaction_no_penalty():
    """Issuer country == merchant country → same approval as no issuer_country set."""
    n = 300
    no_issuer = [simulate_transaction(_make_req()) for _ in range(n)]
    domestic = [simulate_transaction(_make_req(issuer_country="US")) for _ in range(n)]
    rate_no = sum(1 for r in no_issuer if r.approved) / n
    rate_dom = sum(1 for r in domestic if r.approved) / n
    # Both should be statistically similar — allow 10pp variance (stochastic at n=300)
    assert abs(rate_no - rate_dom) < 0.10, (
        f"Domestic issuer should not change rate: {rate_no:.1%} vs {rate_dom:.1%}"
    )


def test_tier3_issuer_lower_approval():
    """Tier 3 issuer (NG) on US merchant should have lower approval than domestic."""
    n = 300
    domestic = [simulate_transaction(_make_req()) for _ in range(n)]
    cross_border = [simulate_transaction(_make_req(issuer_country="NG")) for _ in range(n)]
    rate_dom = sum(1 for r in domestic if r.approved) / n
    rate_xb = sum(1 for r in cross_border if r.approved) / n
    assert rate_xb < rate_dom, (
        f"Tier 3 issuer should lower approval: {rate_xb:.1%} vs domestic {rate_dom:.1%}"
    )


def test_issuer_country_in_response():
    resp = simulate_transaction(_make_req(issuer_country="BR"))
    assert resp.issuer_country == "BR"


# ---------------------------------------------------------------------------
# Retry
# ---------------------------------------------------------------------------

def test_retry_returns_retry_result():
    result = simulate_with_retry(
        _make_req(),
        providers=["global-acquirer-a", "regional-bank-processor-a"],
    )
    assert len(result.attempts) >= 1
    assert result.final_response is not None
    assert isinstance(result.succeeded, bool)
    assert result.total_latency_ms > 0


def test_retry_stops_on_approval():
    """If first provider approves, no second attempt should occur."""
    n = 50
    single_attempt_count = 0
    for _ in range(n):
        result = simulate_with_retry(
            _make_req(),
            providers=["global-acquirer-a", "regional-bank-processor-a"],
        )
        if result.succeeded and len(result.attempts) == 1:
            single_attempt_count += 1
    # Most successful transactions should stop at attempt 1
    assert single_attempt_count > 0


def test_retry_providers_tried_matches_attempts():
    result = simulate_with_retry(
        _make_req(),
        providers=["global-acquirer-a", "regional-bank-processor-a"],
    )
    assert result.providers_tried == [a.provider for a in result.attempts]


# ---------------------------------------------------------------------------
# Compare
# ---------------------------------------------------------------------------

def test_compare_providers_sorted():
    req = CompareRequest(country="US", card_brand=CardBrand.VISA, amount=100.0)
    results = compare_providers(req)
    assert len(results) >= 2
    rates = [r.projected_approval_rate for r in results]
    assert rates == sorted(rates, reverse=True)


def test_compare_with_issuer_country():
    """Compare should accept issuer_country and apply it consistently."""
    req = CompareRequest(country="US", issuer_country="NG", card_brand=CardBrand.VISA, amount=100.0)
    results = compare_providers(req)
    assert len(results) >= 2
    # All approval rates should be lower than without issuer penalty
    req_domestic = CompareRequest(country="US", card_brand=CardBrand.VISA, amount=100.0)
    results_domestic = compare_providers(req_domestic)
    avg_xb = sum(r.projected_approval_rate for r in results) / len(results)
    avg_dom = sum(r.projected_approval_rate for r in results_domestic) / len(results_domestic)
    assert avg_xb < avg_dom, "Cross-border (NG issuer) should have lower avg approval than domestic"
