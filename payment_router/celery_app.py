"""Celery application for background task processing.

Broker and result backend: Redis (same instance used for idempotency/rate-limiting).

Tasks defined in:
    payment_router.webhooks  — webhook delivery with exponential backoff

To start a worker (from project root):
    .venv/Scripts/python.exe -m celery -A payment_router.celery_app worker --loglevel=info
"""

from __future__ import annotations

import os

from celery import Celery

_REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379")

celery_app = Celery(
    "payment_router",
    broker=_REDIS_URL,
    backend=_REDIS_URL,
    include=["payment_router.webhooks"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,            # don't ack until task completes (safer delivery)
    worker_prefetch_multiplier=1,   # one task at a time per worker process
    broker_transport_options={
        "max_retries": 0,           # fail immediately when broker is down (no retry loop)
    },
    broker_connection_retry=False,  # don't retry broker connection on task publish
)
