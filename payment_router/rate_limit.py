"""Redis sliding-window rate limiter.

Two independent guards, both checked on every authenticated request:

  per-key  (100 req/min)  — shared budget across all holders of one API key.
                            Prevents a single key from hammering the API.
  per-IP   (60 req/min)   — per client IP regardless of which key they use.
                            Prevents one abusive IP from exhausting the shared
                            key budget and locking out all other users.

Implementation: sorted set where score = Unix timestamp.
On each check: prune entries older than the window, record current ts, count.

Audit v3 R6 (2026-04-26): the 429 response now carries the standard headers
`Retry-After`, `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset`.
The bucket that triggered the limit (per-key vs per-IP) is reported in the
`X-RateLimit-Scope` header so clients can disambiguate which budget they hit.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

import redis as _redis

_WINDOW_SECONDS = 60
_KEY_LIMIT = 100   # per API key
_IP_LIMIT  = 60    # per client IP


@dataclass
class RateLimitDecision:
    """Outcome of a single rate-limit check.

    `limited` — True when at least one bucket exceeded its limit.
    `scope`   — "key" or "ip" identifying which bucket triggered (None if not limited).
    `limit`   — limit applied to the triggering bucket (or per-key limit if not limited).
    `remaining` — requests still available in the current window for the reported bucket.
    `reset_at` — Unix timestamp (int seconds) when the window rolls over.
    `retry_after` — seconds the client should wait before retrying (>=1 when limited).
    """
    limited: bool
    scope: Optional[str]
    limit: int
    remaining: int
    reset_at: int
    retry_after: int


def _check(client: _redis.Redis, redis_key: str, limit: int) -> tuple[bool, int]:
    """Return (over_limit, current_count) for the sorted-set bucket."""
    now = time.time()
    window_start = now - _WINDOW_SECONDS
    pipe = client.pipeline()
    pipe.zremrangebyscore(redis_key, 0, window_start)
    pipe.zadd(redis_key, {str(now): now})
    pipe.zcard(redis_key)
    pipe.expire(redis_key, _WINDOW_SECONDS + 1)
    _, _, count, _ = pipe.execute()
    count_i = int(count)
    return count_i > limit, count_i


def check_rate_limit(
    client: _redis.Redis,
    api_key_id: str,
    client_ip: str | None = None,
) -> RateLimitDecision:
    """Return a RateLimitDecision describing the per-key + per-IP budgets.

    Both buckets are always evaluated (so the sliding window stays accurate
    for both); the first one that's over its limit wins for the response
    headers. When neither is over, `limited` is False and the reported
    bucket is per-key (the more restrictive in absolute terms).
    """
    now_i = int(time.time())
    reset_at = now_i + _WINDOW_SECONDS

    over_key, key_count = _check(client, f"rl:key:{api_key_id}", _KEY_LIMIT)
    over_ip = False
    ip_count = 0
    if client_ip:
        over_ip, ip_count = _check(client, f"rl:ip:{client_ip}", _IP_LIMIT)

    if over_key:
        return RateLimitDecision(
            limited=True,
            scope="key",
            limit=_KEY_LIMIT,
            remaining=0,
            reset_at=reset_at,
            retry_after=_WINDOW_SECONDS,
        )
    if over_ip:
        return RateLimitDecision(
            limited=True,
            scope="ip",
            limit=_IP_LIMIT,
            remaining=0,
            reset_at=reset_at,
            retry_after=_WINDOW_SECONDS,
        )

    # Not limited — surface the per-key budget so clients always see consistent
    # remaining/limit headers (the per-IP bucket is informational).
    return RateLimitDecision(
        limited=False,
        scope="key",
        limit=_KEY_LIMIT,
        remaining=max(0, _KEY_LIMIT - key_count),
        reset_at=reset_at,
        retry_after=0,
    )


def rate_limit_headers(decision: RateLimitDecision) -> dict[str, str]:
    """Standard rate-limit headers derived from a RateLimitDecision."""
    headers = {
        "X-RateLimit-Limit": str(decision.limit),
        "X-RateLimit-Remaining": str(decision.remaining),
        "X-RateLimit-Reset": str(decision.reset_at),
    }
    if decision.scope:
        headers["X-RateLimit-Scope"] = decision.scope
    if decision.limited:
        headers["Retry-After"] = str(decision.retry_after)
    return headers


# Backwards-compatible thin wrapper — preserved for any caller still on the
# boolean signature. Returns True when the request should be rejected.
def is_rate_limited(
    client: _redis.Redis,
    api_key_id: str,
    client_ip: str | None = None,
) -> bool:
    return check_rate_limit(client, api_key_id, client_ip).limited
