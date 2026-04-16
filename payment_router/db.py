"""SQLAlchemy models and database session management.

Tables:
    transactions        — one row per simulated/processed transaction
    state_transitions   — full audit trail of every state change
    webhook_deliveries  — delivery log for every webhook attempt
    webhook_configs     — registered callback URLs (Session 5)

Usage (without Docker — SQLite for local dev):
    export DATABASE_URL=sqlite:///./payment_router.db
    alembic upgrade head

Usage (with Docker — PostgreSQL):
    export DATABASE_URL=postgresql://user:pass@localhost:5432/payment_router
    alembic upgrade head
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship
from sqlalchemy.pool import StaticPool

from payment_router.models import TransactionState

# ---------------------------------------------------------------------------
# Engine + session factory
# ---------------------------------------------------------------------------

_DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "sqlite:///./payment_router.db",  # default: local SQLite for zero-config dev
)

# SQLite needs special args for FastAPI (multiple threads)
_connect_args = {"check_same_thread": False} if _DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(
    _DATABASE_URL,
    connect_args=_connect_args,
    # StaticPool only for in-memory SQLite (tests); ignored otherwise
    poolclass=StaticPool if _DATABASE_URL == "sqlite:///:memory:" else None,
    echo=False,
)


def get_db():
    """FastAPI dependency — yields a DB session and closes it after the request."""
    with Session(engine) as session:
        yield session


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------

class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Helper: UUID primary key that works in both SQLite and PostgreSQL
# SQLite stores UUIDs as VARCHAR(36); PostgreSQL uses native UUID type.
# ---------------------------------------------------------------------------

def _uuid_column() -> Mapped[str]:
    """Mapped column: UUID stored as string (cross-DB compatible)."""
    return mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))


# ---------------------------------------------------------------------------
# Transaction
# ---------------------------------------------------------------------------

class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[str] = _uuid_column()
    provider: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    country: Mapped[str] = mapped_column(String(2), nullable=False, index=True)
    issuer_country: Mapped[str | None] = mapped_column(String(2), nullable=True)
    card_brand: Mapped[str] = mapped_column(String(16), nullable=False)
    card_type: Mapped[str] = mapped_column(String(16), nullable=False)
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    state: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=TransactionState.PENDING.value,
        index=True,
    )
    response_code: Mapped[str | None] = mapped_column(String(4), nullable=True)
    response_message: Mapped[str | None] = mapped_column(String(128), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True, unique=True, index=True)
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    use_3ds: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    transitions: Mapped[list[StateTransition]] = relationship(
        "StateTransition", back_populates="transaction", order_by="StateTransition.timestamp"
    )
    webhook_deliveries: Mapped[list[WebhookDelivery]] = relationship(
        "WebhookDelivery", back_populates="transaction"
    )


# ---------------------------------------------------------------------------
# StateTransition
# ---------------------------------------------------------------------------

class StateTransition(Base):
    __tablename__ = "state_transitions"

    id: Mapped[str] = _uuid_column()
    transaction_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("transactions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    from_state: Mapped[str] = mapped_column(String(16), nullable=False)
    to_state: Mapped[str] = mapped_column(String(16), nullable=False)
    triggered_by: Mapped[str] = mapped_column(String(64), nullable=False)  # e.g. "simulate", "capture"
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    transaction: Mapped[Transaction] = relationship("Transaction", back_populates="transitions")


# ---------------------------------------------------------------------------
# WebhookDelivery
# ---------------------------------------------------------------------------

class WebhookDelivery(Base):
    __tablename__ = "webhook_deliveries"

    id: Mapped[str] = _uuid_column()
    transaction_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("transactions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    url: Mapped[str] = mapped_column(String(512), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    success: Mapped[bool] = mapped_column(default=False)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    transaction: Mapped[Transaction] = relationship("Transaction", back_populates="webhook_deliveries")


# ---------------------------------------------------------------------------
# WebhookConfig — registered callback endpoints (used by Session 5)
# ---------------------------------------------------------------------------

class WebhookConfig(Base):
    __tablename__ = "webhook_configs"

    id: Mapped[str] = _uuid_column()
    url: Mapped[str] = mapped_column(String(512), nullable=False)
    events: Mapped[str] = mapped_column(Text, nullable=False)  # JSON list of event types
    secret_hash: Mapped[str] = mapped_column(String(64), nullable=False)  # SHA-256 of secret
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


# ---------------------------------------------------------------------------
# Create all tables (used directly when not using Alembic migrations)
# ---------------------------------------------------------------------------

def create_tables() -> None:
    """Create all tables. Call on startup when Alembic is not in use."""
    Base.metadata.create_all(bind=engine)
