"""Payment state machine.

Valid transitions:
    pending    → authorized   (simulate: approved)
    pending    → declined     (simulate: declined)
    authorized → captured     (POST /capture)
    authorized → voided       (POST /void)
    captured   → refunded     (POST /refund)

All other transitions raise InvalidTransitionError → HTTP 409 Conflict.

Every transition is persisted as a StateTransition row for full audit history.
"""

from __future__ import annotations

import os
import socket
from datetime import datetime, timezone

from payment_router.models import TransactionState

_redis_checked: bool = False
_redis_ok: bool = False


def _redis_reachable() -> bool:
    """Fast one-time TCP probe for the Redis/Celery broker."""
    global _redis_checked, _redis_ok
    if _redis_checked:
        return _redis_ok
    _redis_checked = True
    url = os.environ.get("REDIS_URL", "redis://localhost:6379")
    try:
        hostport = url.replace("redis://", "").split("/")[0]
        host, _, port_str = hostport.partition(":")
        sock = socket.create_connection((host or "localhost", int(port_str or 6379)), timeout=0.3)
        sock.close()
        _redis_ok = True
    except OSError:
        _redis_ok = False
    return _redis_ok

# ---------------------------------------------------------------------------
# Valid transition map
# ---------------------------------------------------------------------------

VALID_TRANSITIONS: dict[TransactionState, set[TransactionState]] = {
    TransactionState.PENDING:     {TransactionState.AUTHORIZED, TransactionState.DECLINED},
    TransactionState.AUTHORIZED:  {TransactionState.CAPTURED, TransactionState.VOIDED},
    TransactionState.CAPTURED:    {TransactionState.REFUNDED},
    TransactionState.DECLINED:    set(),
    TransactionState.VOIDED:      set(),
    TransactionState.REFUNDED:    set(),
}

# Terminal states — no further transitions allowed
TERMINAL_STATES = {TransactionState.DECLINED, TransactionState.VOIDED, TransactionState.REFUNDED}


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class InvalidTransitionError(Exception):
    """Raised when a state transition is not permitted."""

    def __init__(self, from_state: TransactionState, to_state: TransactionState):
        self.from_state = from_state
        self.to_state = to_state
        super().__init__(
            f"Transition {from_state.value} → {to_state.value} is not allowed. "
            f"Valid next states from {from_state.value}: "
            f"{[s.value for s in VALID_TRANSITIONS.get(from_state, set())] or 'none (terminal state)'}"
        )


class TransactionNotFoundError(Exception):
    pass


# ---------------------------------------------------------------------------
# Core transition function
# ---------------------------------------------------------------------------

def transition(
    db,  # sqlalchemy Session — imported lazily to avoid circular import
    transaction_id: str,
    to_state: TransactionState,
    triggered_by: str,
) -> "Transaction":  # noqa: F821  (type resolved at runtime)
    """Validate and apply a state transition, persisting the audit record.

    Args:
        db:             SQLAlchemy Session
        transaction_id: UUID of the transaction to transition
        to_state:       Target state
        triggered_by:   Name of the operation triggering this transition (e.g. "capture")

    Returns:
        Updated Transaction ORM object

    Raises:
        TransactionNotFoundError: transaction_id not found
        InvalidTransitionError:   transition not permitted from current state
    """
    from payment_router.db import StateTransition, Transaction  # lazy import

    txn = db.get(Transaction, transaction_id)
    if txn is None:
        raise TransactionNotFoundError(f"Transaction {transaction_id} not found")

    from_state = TransactionState(txn.state)
    allowed = VALID_TRANSITIONS.get(from_state, set())

    if to_state not in allowed:
        raise InvalidTransitionError(from_state, to_state)

    # Apply transition
    txn.state = to_state.value

    # Persist audit record
    record = StateTransition(
        transaction_id=transaction_id,
        from_state=from_state.value,
        to_state=to_state.value,
        triggered_by=triggered_by,
    )
    db.add(record)
    db.commit()
    db.refresh(txn)

    # Publish event after commit — fire-and-forget, never blocks the response
    from payment_router.kafka_producer import STATE_TO_EVENT, publish as kafka_publish
    event_type = STATE_TO_EVENT.get(to_state.value)
    if event_type:
        kafka_publish(event_type, txn)
        # Queue webhook delivery via Celery — only if Redis broker is reachable
        if _redis_reachable():
            try:
                from payment_router.webhooks import dispatch_webhooks
                payload = {
                    "event_type": event_type,
                    "transaction_id": txn.id,
                    "provider": txn.provider,
                    "country": txn.country,
                    "card_brand": txn.card_brand,
                    "amount": txn.amount,
                    "currency": txn.currency,
                    "response_code": txn.response_code,
                    "state": txn.state,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                dispatch_webhooks.delay(txn.id, event_type, payload)
            except Exception as exc:
                import logging
                logging.getLogger(__name__).warning("Webhook dispatch skipped: %s", exc)

    return txn


# ---------------------------------------------------------------------------
# Read helpers
# ---------------------------------------------------------------------------

def get_transaction(db, transaction_id: str) -> "Transaction":  # noqa: F821
    from payment_router.db import Transaction
    txn = db.get(Transaction, transaction_id)
    if txn is None:
        raise TransactionNotFoundError(f"Transaction {transaction_id} not found")
    return txn


def get_transitions(db, transaction_id: str) -> list["StateTransition"]:  # noqa: F821
    from payment_router.db import StateTransition, Transaction
    txn = db.get(Transaction, transaction_id)
    if txn is None:
        raise TransactionNotFoundError(f"Transaction {transaction_id} not found")
    return txn.transitions
