"""Tests for input validation on /simulate and /compare.

Every /simulate and /compare request flows through Pydantic validators that
reject invalid country / card_brand / card_type / amount at the API boundary
with HTTP 422. Without these validators, unmodelled inputs (e.g. country=ZZ)
would silently fall back to `base_approval_rate * 0.95` and return HTTP 200
with a confident-looking but meaningless result.

Shares the TestClient / DB / auth setup with tests.test_state_machine so
that `app.dependency_overrides[get_db]` is consistent across modules.
"""
from __future__ import annotations

# Importing from test_state_machine bootstraps the test DB + api key override
# and the TestClient bound to the correct overrides.
from tests.test_state_machine import _AUTH, client  # noqa: F401


def _sim_body(**overrides):
    body = {
        "provider": "global-acquirer-a",
        "country": "US",
        "card_brand": "visa",
        "card_type": "credit",
        "amount": 100.0,
        "currency": "USD",
    }
    body.update(overrides)
    return body


def _cmp_body(**overrides):
    body = {
        "country": "US",
        "card_brand": "visa",
        "card_type": "credit",
        "amount": 100.0,
        "currency": "USD",
    }
    body.update(overrides)
    return body


# ---------------------------------------------------------------------------
# country
# ---------------------------------------------------------------------------

def test_simulate_rejects_country_not_in_allowlist():
    """ZZ is a reserved ISO 3166 code that no provider models — must return 422."""
    resp = client.post("/simulate", json=_sim_body(country="ZZ"), headers=_AUTH)
    assert resp.status_code == 422, resp.text
    detail = resp.json().get("detail", [])
    assert any("country" in str(d).lower() for d in detail), detail


def test_compare_rejects_country_not_in_allowlist():
    resp = client.post("/compare", json=_cmp_body(country="ZZ"), headers=_AUTH)
    assert resp.status_code == 422, resp.text


def test_simulate_rejects_country_wrong_format():
    resp = client.post("/simulate", json=_sim_body(country="USA"), headers=_AUTH)
    assert resp.status_code == 422


def test_simulate_accepts_country_in_allowlist():
    """US is in every provider's country block — must return 200."""
    resp = client.post("/simulate", json=_sim_body(country="US"), headers=_AUTH)
    assert resp.status_code == 200


def test_simulate_lowercases_country_is_normalized():
    """Lowercase input is normalised to uppercase, not rejected."""
    resp = client.post("/simulate", json=_sim_body(country="us"), headers=_AUTH)
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# card_brand
# ---------------------------------------------------------------------------

def test_simulate_rejects_garbage_card_brand():
    resp = client.post(
        "/simulate", json=_sim_body(card_brand="not_a_brand"), headers=_AUTH
    )
    assert resp.status_code == 422


def test_simulate_rejects_unknown_card_brand():
    """The CardBrand enum includes UNKNOWN for internal bookkeeping but
    the request path must reject it — callers must declare a real scheme."""
    resp = client.post(
        "/simulate", json=_sim_body(card_brand="unknown"), headers=_AUTH
    )
    assert resp.status_code == 422


def test_simulate_rejects_jcb_on_request():
    """JCB is in the enum but no provider models it in request-level lookups."""
    resp = client.post("/simulate", json=_sim_body(card_brand="jcb"), headers=_AUTH)
    assert resp.status_code == 422


def test_compare_rejects_garbage_card_brand():
    resp = client.post(
        "/compare", json=_cmp_body(card_brand="foobar"), headers=_AUTH
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# card_type
# ---------------------------------------------------------------------------

def test_simulate_rejects_garbage_card_type():
    resp = client.post(
        "/simulate", json=_sim_body(card_type="platinum"), headers=_AUTH
    )
    assert resp.status_code == 422


def test_simulate_rejects_unknown_card_type():
    resp = client.post(
        "/simulate", json=_sim_body(card_type="unknown"), headers=_AUTH
    )
    assert resp.status_code == 422


def test_compare_rejects_garbage_card_type():
    resp = client.post(
        "/compare", json=_cmp_body(card_type="foo"), headers=_AUTH
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# amount
# ---------------------------------------------------------------------------

def test_simulate_rejects_zero_amount():
    resp = client.post("/simulate", json=_sim_body(amount=0), headers=_AUTH)
    assert resp.status_code == 422


def test_simulate_rejects_negative_amount():
    resp = client.post("/simulate", json=_sim_body(amount=-10), headers=_AUTH)
    assert resp.status_code == 422


def test_simulate_rejects_oversized_amount():
    resp = client.post(
        "/simulate", json=_sim_body(amount=1e9), headers=_AUTH
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# /query (used by chatbot)
# ---------------------------------------------------------------------------

def test_query_rejects_invalid_country():
    resp = client.post(
        "/query",
        json={"country": "ZZ", "amount": 100.0, "currency": "USD"},
        headers=_AUTH,
    )
    assert resp.status_code == 422


def test_query_rejects_invalid_card_brand():
    resp = client.post(
        "/query",
        json={
            "country": "US",
            "amount": 100.0,
            "currency": "USD",
            "card_brand": "garbage",
        },
        headers=_AUTH,
    )
    assert resp.status_code == 422
