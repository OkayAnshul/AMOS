"""Who is asking (V1.4, ADR-013).

Authentication only. **Not** authorization: an authenticated user can do
everything the API offers, and there are no scopes, roles or read-only keys.
Saying which of the two this is matters, because "we have auth" is routinely
used to mean both.

API keys rather than JWT: AMOS is one process with one database (ADR-004), so
stateless verification buys nothing and costs key management. Keys are stored as
**SHA-256 hashes** — a database that leaks should not also hand over access — and
lookup is by hash, so the plaintext exists only inside the request presenting it.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from amos.database.models import User

#: Keys are compared by hash, so the length only affects guessing cost.
_KEY_BYTES = 24


@dataclass(frozen=True)
class Actor:
    """The authenticated user for one request.

    Frozen, and carried explicitly rather than read from a context variable.
    Ambient identity is how a query ends up scoped to whoever happened to be in
    the contextvar — and the whole point of V1.4 is that a query cannot be built
    without an owner.
    """

    id: uuid.UUID
    name: str


#: The identity used when there is no database.
#:
#: Without persistence there are no users, nothing is stored, and there is no
#: isolation to enforce — so requiring a key would mean the API could not run at
#: all without infrastructure, breaking a property this project has held since
#: V0.3 and that CI has a dedicated job for.
#:
#: The tradeoff is stated rather than hidden: **an AMOS running without a
#: database is unauthenticated**, it says so at startup, and
#: `docs/13-security.md` says so too.
LOCAL_ACTOR = Actor(id=uuid.UUID(int=0), name="local")


def generate_api_key() -> str:
    """A new key. Shown once; only its hash is stored."""
    return secrets.token_hex(_KEY_BYTES)


def hash_api_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def key_from_header(value: str | None) -> str | None:
    """Extract the key from an `Authorization: Bearer <key>` header.

    Returns None for anything malformed, so the caller has one failure path
    rather than branching on how the header was wrong — the distinction is not
    useful to a client and enumerating it is a small information leak.
    """
    if not value:
        return None
    scheme, _, token = value.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


async def authenticate(session: AsyncSession, key: str | None) -> Actor | None:
    """The user this key belongs to, or None.

    The lookup is by hash and therefore constant-time in the database; the
    `compare_digest` below guards the comparison *after* the row is found, which
    matters because a successful lookup followed by a naive `==` would leak
    through timing on the hash itself.
    """
    if not key:
        return None

    digest = hash_api_key(key)
    row = (
        await session.execute(select(User).where(User.api_key_hash == digest))
    ).scalar_one_or_none()
    if row is None:
        return None
    if not hmac.compare_digest(row.api_key_hash, digest):  # pragma: no cover - defensive
        return None
    return Actor(id=row.id, name=row.name)


async def create_user(session: AsyncSession, name: str) -> tuple[Actor, str]:
    """Create a user and return the actor plus the **plaintext key, once**.

    The key is never recoverable afterwards, which is the point of storing a
    hash — and the reason this returns it rather than expecting a later lookup.
    """
    key = generate_api_key()
    user = User(id=uuid.uuid4(), name=name, api_key_hash=hash_api_key(key))
    session.add(user)
    await session.flush()
    return Actor(id=user.id, name=user.name), key


async def actor_for_run(session: AsyncSession, run_id: uuid.UUID | None) -> Actor | None:
    """The owner of a run.

    Memory tools are built once at startup and serve every user, so they need an
    identity at *call* time. The alternative to this is a second ambient value —
    an actor contextvar alongside the run id — and ambient identity is how a
    query ends up scoped to whoever happened to be in the contextvar.

    Deriving it from the run keeps **one** ambient value, and makes the scope
    follow the data: a memory written during a run belongs to whoever owns that
    run, which is the correct answer on the synchronous path and in the worker
    without either of them having to arrange it.

    Costs one indexed lookup per memory-tool call.
    """
    if run_id is None:
        return None

    from amos.database.models import Run

    row = (
        await session.execute(
            select(User.id, User.name).join(Run, Run.user_id == User.id).where(Run.id == run_id)
        )
    ).first()
    return Actor(id=row.id, name=row.name) if row else None
