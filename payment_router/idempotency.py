"""Redis-backed idempotency cache for API endpoints.

Cache key: idem:{api_key_id}:{idempotency_key}
TTL: 24 hours

Scoping by api_key_id ensures two different API keys cannot collide on the
same idempotency key string.
"""

from __future__ import annotations

import json

import redis as _redis

_TTL_SECONDS = 86_400  # 24 h


def _redis_key(api_key_id: str, idem_key: str) -> str:
    return f"idem:{api_key_id}:{idem_key}"


def get_cached(client: _redis.Redis, api_key_id: str, idem_key: str) -> dict | None:
    """Return the cached response dict, or None on a cache miss."""
    raw = client.get(_redis_key(api_key_id, idem_key))
    return json.loads(raw) if raw else None


def store(client: _redis.Redis, api_key_id: str, idem_key: str, response_body: dict) -> None:
    """Cache a successful response dict for 24 h."""
    client.setex(
        _redis_key(api_key_id, idem_key),
        _TTL_SECONDS,
        json.dumps(response_body, default=str),
    )
