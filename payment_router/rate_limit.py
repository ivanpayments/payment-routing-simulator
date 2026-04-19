"""Redis sliding-window rate limiter.

Two independent guards, both checked on every authenticated request:

  per-key  (100 req/min)  — shared budget across all holders of one API key.
                            Prevents a single key from hammering the API.
  per-IP   (60 req/min)   — per client IP regardless of which key they use.
                            Prevents one abusive IP from exhausting the shared
                            key budget and locking out all other users.

Implementation: sorted set where score = Unix timestamp.
On each check: prune entries older than the window, record current ts, count.
"""

from __future__ import annotations

import time

import redis as _redis

_WINDOW_SECONDS = 60
_KEY_LIMIT = 100   # per API key
_IP_LIMIT  = 60    # per client IP


def _check(client: _redis.Redis, redis_key: str, limit: int) -> bool:
    """Return True if the counter for redis_key exceeds limit."""
    now = time.time()
    window_start = now - _WINDOW_SECONDS
    pipe = client.pipeline()
    pipe.zremrangebyscore(redis_key, 0, window_start)
    pipe.zadd(redis_key, {str(now): now})
    pipe.zcard(redis_key)
    pipe.expire(redis_key, _WINDOW_SECONDS + 1)
    _, _, count, _ = pipe.execute()
    return int(count) > limit


def is_rate_limited(
    client: _redis.Redis,
    api_key_id: str,
    client_ip: str | None = None,
) -> bool:
    """Return True if either the per-key or per-IP limit is exceeded."""
    if _check(client, f"rl:key:{api_key_id}", _KEY_LIMIT):
        return True
    if client_ip and _check(client, f"rl:ip:{client_ip}", _IP_LIMIT):
        return True
    return False
