"""Kafka event producer for payment state transitions.

Publishes to topic `payment.events` with transaction_id as the partition key.
Degrades silently if the broker is unavailable — the API never crashes due to Kafka.

Event types published:
    payment.authorized  — transaction approved
    payment.declined    — transaction declined (primary feed for Project 3 ML engine)
    payment.captured    — funds captured
    payment.voided      — authorization released
    payment.refunded    — funds returned to cardholder

Consumer note (Project 3): subscribe to `payment.events`, filter on
event_type == "payment.declined" to feed the retry/recovery model.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_BOOTSTRAP = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
_TOPIC = "payment.events"

_producer = None       # lazy singleton
_checked = False       # True once reachability has been tested


def _broker_reachable() -> bool:
    """Fast TCP probe — avoids kafka-python's slow connection retry when broker is down."""
    import socket
    try:
        host, _, port_str = _BOOTSTRAP.partition(":")
        sock = socket.create_connection((host, int(port_str or 9092)), timeout=0.5)
        sock.close()
        return True
    except OSError:
        return False


def _get_producer():
    global _producer, _checked
    if _checked:
        return _producer
    _checked = True
    if not _broker_reachable():
        logger.warning("Kafka broker not reachable at %s — events will be skipped", _BOOTSTRAP)
        return None
    try:
        from kafka import KafkaProducer
        _producer = KafkaProducer(
            bootstrap_servers=_BOOTSTRAP,
            value_serializer=lambda v: json.dumps(v, default=str).encode("utf-8"),
            key_serializer=lambda k: k.encode("utf-8") if k else None,
            acks="all",
            retries=3,
            request_timeout_ms=5_000,
            api_version=(2, 6, 0),  # skip auto-detection — avoids blocking connection
        )
        logger.info("Kafka producer connected to %s", _BOOTSTRAP)
    except Exception as exc:
        logger.warning("Kafka unavailable (%s) — events will not be published", exc)
        _producer = None
    return _producer


def publish(event_type: str, txn) -> None:
    """Publish a payment event. No-ops silently if Kafka is down.

    Args:
        event_type: e.g. "payment.authorized"
        txn:        Transaction ORM object (after db.commit, so state is final)
    """
    producer = _get_producer()
    if producer is None:
        return

    payload = {
        "event_type": event_type,
        "transaction_id": txn.id,
        "provider": txn.provider,
        "country": txn.country,
        "issuer_country": txn.issuer_country,
        "card_brand": txn.card_brand,
        "amount": txn.amount,
        "currency": txn.currency,
        "response_code": txn.response_code,
        "state": txn.state,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    try:
        producer.send(_TOPIC, key=txn.id, value=payload)
        # No synchronous flush — delivery happens in the background thread.
        # flush() is called on shutdown via close(). This keeps the API response
        # latency unaffected when Kafka is slow or temporarily unreachable.
    except Exception as exc:
        logger.warning("Kafka publish failed for %s: %s", txn.id, exc)


def close() -> None:
    """Flush and close the producer on shutdown."""
    global _producer, _checked
    if _producer is not None:
        try:
            _producer.flush(timeout=5)
            _producer.close()
        except Exception:
            pass
        _producer = None
    _checked = False


# Map TransactionState values to event type strings
STATE_TO_EVENT: dict[str, str] = {
    "authorized": "payment.authorized",
    "declined":   "payment.declined",
    "captured":   "payment.captured",
    "voided":     "payment.voided",
    "refunded":   "payment.refunded",
}
