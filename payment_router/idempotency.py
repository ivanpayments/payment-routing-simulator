"""Redis-backed idempotency cache for API endpoints.

Cache key: idem:{api_key_id}:{idempotency_key}
TTL: 24 hours

Scoping by api_key_id ensures two different API keys cannot collide on the
same idempotency key string.

Cache-entry shape — a JSON envelope with two fields:

    {
        "body_hash": "<sha256 of the request body bytes>",
        "response_bytes": "<the original response, JSON-serialized once>"
    }

Storing the original response *bytes* (not the parsed object) means the
replay returns byte-identical output to the original — required by
Stripe-style contract semantics. Re-serializing a parsed dict on replay
drifts on values such as `datetime`, where Python's `str()` and FastAPI's
JSON serializer disagree (`2026-04-27 07:46:35.749339+00:00` vs the ISO-8601
`2026-04-27T07:46:35.749339Z`). Caching the bytes side-steps the drift.

Storing the request body hash alongside the response lets the middleware
detect a "same key, different body" replay and return HTTP 422 instead
of silently serving the original cached response (Stripe / Square
behaviour).
"""

from __future__ import annotations

import hashlib
import json

import redis as _redis

_TTL_SECONDS = 86_400  # 24 h


def _redis_key(api_key_id: str, idem_key: str) -> str:
    return f"idem:{api_key_id}:{idem_key}"


def hash_body(body_bytes: bytes | None) -> str:
    """SHA-256 hex digest of the request body bytes (empty body → empty-bytes digest)."""
    return hashlib.sha256(body_bytes or b"").hexdigest()


def get_cached(client: _redis.Redis, api_key_id: str, idem_key: str) -> dict | None:
    """Return the cached envelope, or None on a cache miss.

    Envelope shape:
        {"body_hash": str, "response_bytes": str}

    Callers should compare the request body hash against `body_hash` and
    return the raw `response_bytes` verbatim (no re-serialization).
    """
    raw = client.get(_redis_key(api_key_id, idem_key))
    if not raw:
        return None
    try:
        envelope = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(envelope, dict) or "response_bytes" not in envelope:
        return None
    return envelope


def store(
    client: _redis.Redis,
    api_key_id: str,
    idem_key: str,
    response_bytes: str,
    body_hash: str = "",
) -> None:
    """Cache the raw response bytes and the request body hash for 24 h.

    `response_bytes` is the JSON string the server sent on the original
    request. Replays return this string verbatim so byte-identical replay
    is preserved.
    """
    envelope = {"body_hash": body_hash, "response_bytes": response_bytes}
    client.setex(
        _redis_key(api_key_id, idem_key),
        _TTL_SECONDS,
        json.dumps(envelope),
    )
