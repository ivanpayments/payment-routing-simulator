"""Webhook delivery — HMAC-SHA256 signing + Celery retry with exponential backoff.

Flow per state transition:
  1. state_machine.transition() fires dispatch_webhooks.delay(...)
  2. dispatch_webhooks queries all active webhook configs subscribed to the event
  3. For each matching config: queues deliver_single.apply_async(...)
  4. deliver_single POSTs signed payload; on failure retries with backoff

HMAC verification (for merchants):
    import hashlib, hmac
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    assert request.headers["X-Signature-256"] == f"sha256={expected}"
"""

from __future__ import annotations

import hashlib
import hmac as _hmac
import json
import logging
from datetime import datetime, timezone

import httpx

from payment_router.celery_app import celery_app

logger = logging.getLogger(__name__)

# Retry delays in seconds: attempt 1→2→3→4→5
_BACKOFF = [1, 2, 4, 8, 16]


def sign_payload(secret: str, body: bytes) -> str:
    """Return 'sha256=<hex>' HMAC-SHA256 signature over the raw payload bytes."""
    sig = _hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={sig}"


@celery_app.task(bind=True, max_retries=4, name="payment_router.webhooks.dispatch_webhooks")
def dispatch_webhooks(self, transaction_id: str, event_type: str, payload: dict) -> None:
    """Fan out: find all active webhook configs subscribed to event_type, queue one delivery task each."""
    from payment_router.db import WebhookConfig, engine
    from sqlalchemy.orm import Session

    with Session(engine) as db:
        configs = db.query(WebhookConfig).filter(WebhookConfig.active == True).all()
        for cfg in configs:
            subscribed = json.loads(cfg.events)
            if event_type in subscribed:
                deliver_single.apply_async(
                    args=[cfg.id, transaction_id, event_type, payload],
                    countdown=0,
                )


@celery_app.task(bind=True, max_retries=4, name="payment_router.webhooks.deliver_single")
def deliver_single(
    self,
    webhook_config_id: str,
    transaction_id: str,
    event_type: str,
    payload: dict,
) -> None:
    """Deliver one webhook with HMAC signature and exponential backoff on failure."""
    from payment_router.db import WebhookConfig, WebhookDelivery, engine
    from sqlalchemy.orm import Session

    attempt_number = self.request.retries + 1
    retry_countdown = _BACKOFF[min(self.request.retries, len(_BACKOFF) - 1)]

    with Session(engine) as db:
        cfg = db.get(WebhookConfig, webhook_config_id)
        if cfg is None:
            return  # config deleted — nothing to do

        body = json.dumps(payload, default=str).encode("utf-8")
        signature = sign_payload(cfg.secret, body)

        delivery = WebhookDelivery(
            transaction_id=transaction_id,
            webhook_config_id=webhook_config_id,
            url=cfg.url,
            event_type=event_type,
            attempt=attempt_number,
            success=False,
        )

        try:
            with httpx.Client(timeout=10) as client:
                resp = client.post(
                    cfg.url,
                    content=body,
                    headers={
                        "Content-Type": "application/json",
                        "X-Signature-256": signature,
                        "X-Event-Type": event_type,
                    },
                )
            delivery.status_code = resp.status_code
            delivery.response_body = resp.text[:500]
            delivery.success = 200 <= resp.status_code < 300
            db.add(delivery)
            db.commit()

            if not delivery.success:
                raise self.retry(
                    countdown=retry_countdown,
                    exc=RuntimeError(f"HTTP {resp.status_code} from {cfg.url}"),
                )

        except httpx.RequestError as exc:
            delivery.response_body = str(exc)[:500]
            db.add(delivery)
            db.commit()
            logger.warning("Webhook delivery failed (attempt %d): %s", attempt_number, exc)
            raise self.retry(countdown=retry_countdown, exc=exc)
