"""API key generation and validation.

Keys are stored as SHA-256 hashes — plaintext is never persisted.
A hardcoded test keypair is seeded on startup if no keys exist.
"""

from __future__ import annotations

import hashlib
import secrets

from sqlalchemy.orm import Session


# Obviously-fake placeholders — only seeded in ENV=local.
# Prod mints real keys via scripts/create_api_key.py.
_TEST_PK = "pk_test_SEED_LOCAL_DEV_ONLY"
_TEST_SK = "sk_test_SEED_LOCAL_DEV_ONLY"


def generate_key_pair() -> tuple[str, str]:
    """Return (publishable_key, secret_key) as opaque token strings."""
    pk = "pk_test_" + secrets.token_urlsafe(24)
    sk = "sk_test_" + secrets.token_urlsafe(24)
    return pk, sk


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def seed_test_key(db: Session) -> None:
    """Insert the hardcoded test keypair if the api_keys table is empty."""
    from payment_router.db import ApiKey  # local import avoids circular deps at module load

    if db.query(ApiKey).count() == 0:
        record = ApiKey(
            name="test-default",
            publishable_key=_TEST_PK,
            secret_hash=hash_key(_TEST_SK),
        )
        db.add(record)
        db.commit()


def validate_secret_key(db: Session, provided_key: str):
    """Return the ApiKey row if the key is valid, else None."""
    from payment_router.db import ApiKey

    if not provided_key.startswith("sk_test_"):
        return None
    key_hash = hash_key(provided_key)
    return db.query(ApiKey).filter(ApiKey.secret_hash == key_hash).first()
