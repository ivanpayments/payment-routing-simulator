"""Tests for the payment state machine and lifecycle API endpoints."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from payment_router.api import app
from payment_router.db import Base, get_db
from payment_router.models import TransactionState
from payment_router.state_machine import (
    InvalidTransitionError,
    TransactionNotFoundError,
    get_transaction,
    get_transitions,
    transition,
)


# ---------------------------------------------------------------------------
# Test DB — in-memory SQLite, isolated per test session
# ---------------------------------------------------------------------------

TEST_DATABASE_URL = "sqlite:///:memory:"

test_engine = create_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)

Base.metadata.create_all(bind=test_engine)


def override_get_db():
    with Session(test_engine) as session:
        yield session


app.dependency_overrides[get_db] = override_get_db

# Seed the hardcoded test API key into the in-memory DB so auth works in tests.
from payment_router.db import ApiKey
from payment_router.api_keys import hash_key

_TEST_SK = "sk_test_SEED_LOCAL_DEV_ONLY"
_TEST_PK = "pk_test_SEED_LOCAL_DEV_ONLY"
_AUTH = {"Authorization": f"Bearer {_TEST_SK}"}

with Session(test_engine) as _db:
    _db.add(ApiKey(
        name="test-default",
        publishable_key=_TEST_PK,
        secret_hash=hash_key(_TEST_SK),
    ))
    _db.commit()

client = TestClient(app)


# ---------------------------------------------------------------------------
# State machine unit tests
# ---------------------------------------------------------------------------

def _make_transaction(db: Session, state: TransactionState = TransactionState.AUTHORIZED) -> str:
    """Insert a minimal transaction row directly and return its ID."""
    from payment_router.db import StateTransition, Transaction
    import uuid

    txn_id = str(uuid.uuid4())
    txn = Transaction(
        id=txn_id,
        provider="global-acquirer-a",
        country="US",
        card_brand="visa",
        card_type="credit",
        amount=100.0,
        currency="USD",
        state=state.value,
        response_code="00" if state == TransactionState.AUTHORIZED else "05",
    )
    db.add(txn)
    record = StateTransition(
        transaction_id=txn_id,
        from_state=TransactionState.PENDING.value,
        to_state=state.value,
        triggered_by="test",
    )
    db.add(record)
    db.commit()
    return txn_id


def test_valid_transition_authorized_to_captured():
    with Session(test_engine) as db:
        txn_id = _make_transaction(db, TransactionState.AUTHORIZED)
        txn = transition(db, txn_id, TransactionState.CAPTURED, "capture")
        assert txn.state == TransactionState.CAPTURED.value


def test_valid_transition_authorized_to_voided():
    with Session(test_engine) as db:
        txn_id = _make_transaction(db, TransactionState.AUTHORIZED)
        txn = transition(db, txn_id, TransactionState.VOIDED, "void")
        assert txn.state == TransactionState.VOIDED.value


def test_valid_transition_captured_to_refunded():
    with Session(test_engine) as db:
        txn_id = _make_transaction(db, TransactionState.CAPTURED)
        txn = transition(db, txn_id, TransactionState.REFUNDED, "refund")
        assert txn.state == TransactionState.REFUNDED.value


def test_invalid_transition_declined_to_captured():
    with Session(test_engine) as db:
        txn_id = _make_transaction(db, TransactionState.DECLINED)
        with pytest.raises(InvalidTransitionError):
            transition(db, txn_id, TransactionState.CAPTURED, "capture")


def test_invalid_transition_authorized_to_refunded():
    """Can't refund without capturing first."""
    with Session(test_engine) as db:
        txn_id = _make_transaction(db, TransactionState.AUTHORIZED)
        with pytest.raises(InvalidTransitionError):
            transition(db, txn_id, TransactionState.REFUNDED, "refund")


def test_terminal_state_voided_cannot_transition():
    with Session(test_engine) as db:
        txn_id = _make_transaction(db, TransactionState.VOIDED)
        with pytest.raises(InvalidTransitionError):
            transition(db, txn_id, TransactionState.CAPTURED, "capture")


def test_transition_records_audit_trail():
    with Session(test_engine) as db:
        txn_id = _make_transaction(db, TransactionState.AUTHORIZED)
        transition(db, txn_id, TransactionState.CAPTURED, "capture")
        history = get_transitions(db, txn_id)
        assert len(history) == 2  # pending→authorized + authorized→captured
        assert history[-1].from_state == "authorized"
        assert history[-1].to_state == "captured"
        assert history[-1].triggered_by == "capture"


def test_transaction_not_found():
    with Session(test_engine) as db:
        with pytest.raises(TransactionNotFoundError):
            transition(db, "nonexistent-id", TransactionState.CAPTURED, "capture")


# ---------------------------------------------------------------------------
# API lifecycle tests (via TestClient)
# ---------------------------------------------------------------------------

def _simulate_and_get_id(approved: bool = True) -> str | None:
    """Run /simulate until we get the desired outcome; return transaction_id."""
    for _ in range(20):
        resp = client.post("/simulate", json={
            "provider": "global-acquirer-a",
            "country": "US",
            "card_brand": "visa",
            "amount": 100.0,
        }, headers=_AUTH)
        assert resp.status_code == 200
        data = resp.json()
        if data["approved"] == approved:
            return data["transaction_id"]
    return None


def test_api_simulate_persists_transaction():
    resp = client.post("/simulate", json={
        "provider": "global-acquirer-a",
        "country": "US",
        "card_brand": "visa",
        "amount": 100.0,
    }, headers=_AUTH)
    assert resp.status_code == 200
    txn_id = resp.json()["transaction_id"]

    get_resp = client.get(f"/transactions/{txn_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["transaction_id"] == txn_id


def test_api_full_lifecycle_authorize_capture_refund():
    txn_id = _simulate_and_get_id(approved=True)
    assert txn_id is not None, "Could not get an approved transaction in 20 tries"

    cap = client.post(f"/capture/{txn_id}", headers=_AUTH)
    assert cap.status_code == 200
    assert cap.json()["state"] == "captured"

    ref = client.post(f"/refund/{txn_id}", headers=_AUTH)
    assert ref.status_code == 200
    assert ref.json()["state"] == "refunded"


def test_api_full_lifecycle_authorize_void():
    txn_id = _simulate_and_get_id(approved=True)
    assert txn_id is not None

    void = client.post(f"/void/{txn_id}", headers=_AUTH)
    assert void.status_code == 200
    assert void.json()["state"] == "voided"


def test_api_invalid_transition_returns_409():
    txn_id = _simulate_and_get_id(approved=True)
    assert txn_id is not None

    # Try to refund without capturing → 409
    resp = client.post(f"/refund/{txn_id}", headers=_AUTH)
    assert resp.status_code == 409


def test_api_declined_transaction_cannot_be_captured():
    txn_id = _simulate_and_get_id(approved=False)
    if txn_id is None:
        pytest.skip("Could not get a declined transaction in 20 tries")

    resp = client.post(f"/capture/{txn_id}", headers=_AUTH)
    assert resp.status_code == 409


def test_api_transaction_transitions_history():
    txn_id = _simulate_and_get_id(approved=True)
    assert txn_id is not None

    client.post(f"/capture/{txn_id}", headers=_AUTH)

    resp = client.get(f"/transactions/{txn_id}/transitions")
    assert resp.status_code == 200
    history = resp.json()
    assert len(history) == 2
    assert history[0]["from_state"] == "pending"
    assert history[0]["to_state"] == "authorized"
    assert history[1]["from_state"] == "authorized"
    assert history[1]["to_state"] == "captured"


def test_api_transaction_not_found_returns_404():
    resp = client.get("/transactions/nonexistent-id-12345")
    assert resp.status_code == 404


def test_api_list_transactions():
    # Ensure at least one transaction exists
    client.post("/simulate", json={"provider": "global-acquirer-a", "country": "US", "card_brand": "visa", "amount": 50.0})
    resp = client.get("/transactions?limit=10")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)
    assert len(resp.json()) >= 1


def test_api_list_transactions_filter_by_provider():
    resp = client.get("/transactions?provider=global-acquirer-a&limit=5")
    assert resp.status_code == 200
    for txn in resp.json():
        assert txn["provider"] == "global-acquirer-a"
