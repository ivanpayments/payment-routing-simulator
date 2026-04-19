"""Mint a random API keypair and insert the hash into the DB.

Run inside the container:
    docker exec -it payment-router-api-1 python -m scripts.create_api_key "my-key-name"

Prints the plaintext secret ONCE. Save it — it is not recoverable.
"""

from __future__ import annotations

import sys

from payment_router.api_keys import generate_key_pair, hash_key
from payment_router.db import ApiKey, Session as DBSession, create_tables, engine


def main() -> int:
    name = sys.argv[1] if len(sys.argv) > 1 else "unnamed"
    create_tables()
    pk, sk = generate_key_pair()
    with DBSession(engine) as db:
        db.add(ApiKey(name=name, publishable_key=pk, secret_hash=hash_key(sk)))
        db.commit()
    print(f"name:             {name}")
    print(f"publishable_key:  {pk}")
    print(f"secret_key:       {sk}")
    print()
    print("Save the secret_key now — it is hashed on the server and cannot be recovered.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
