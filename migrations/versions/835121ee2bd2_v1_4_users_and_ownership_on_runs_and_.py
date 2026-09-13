"""v1.4: users, and ownership on runs, memories and documents

Multi-tenancy was an explicit non-goal until this milestone (ADR-013); the
requirements document changed with the decision rather than after it.

**The backfill is the interesting part.** `runs.user_id` and `memories.user_id`
are NOT NULL, and there are existing rows — so the column cannot simply be added.
The order is: create `users`, insert one owner, add the columns nullable, fill
them, then tighten to NOT NULL. Adding them NOT NULL in one step fails on any
database with data in it, which is every database this will ever run against
except a fresh one.

`documents.user_id` stays **nullable on purpose**. NULL means the system corpus:
readable by everyone, owned by nobody. The existing 28 documents are AMOS's own
documentation, so they are left NULL rather than assigned to the first user.

Revision ID: 835121ee2bd2
Revises: 453890cfd6a9
Create Date: 2026-09-13
"""

from __future__ import annotations

import hashlib
import os
import uuid

import sqlalchemy as sa
from alembic import op

revision = "835121ee2bd2"
down_revision = "453890cfd6a9"
branch_labels = None
depends_on = None

#: The account every pre-existing row is assigned to. Its key comes from the
#: environment when present so a deployment can set its own; otherwise a random
#: one is generated and printed, because a migration that silently creates a
#: credential nobody knows is a locked door with no key.
_LEGACY_USER = "default"


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("api_key_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("api_key_hash", name="users_api_key_hash_unique"),
        sa.UniqueConstraint("name", name="users_name_unique"),
    )

    key = os.getenv("AMOS_BOOTSTRAP_API_KEY") or uuid.uuid4().hex
    owner_id = uuid.uuid4()
    op.execute(
        sa.text("INSERT INTO users (id, name, api_key_hash) VALUES (:id, :name, :hash)").bindparams(
            id=owner_id,
            name=_LEGACY_USER,
            hash=hashlib.sha256(key.encode()).hexdigest(),
        )
    )
    if not os.getenv("AMOS_BOOTSTRAP_API_KEY"):
        print(f"\n  AMOS bootstrap API key for user '{_LEGACY_USER}': {key}")
        print("  Store it now — only its hash is kept.\n")

    # runs and memories: nullable, backfill, then tighten.
    for table in ("runs", "memories"):
        op.add_column(table, sa.Column("user_id", sa.UUID(as_uuid=True), nullable=True))
        op.execute(
            sa.text(f"UPDATE {table} SET user_id = :owner").bindparams(owner=owner_id)  # noqa: S608
        )
        op.alter_column(table, "user_id", nullable=False)
        op.create_foreign_key(
            op.f(f"fk_{table}_user_id_users"), table, "users", ["user_id"], ["id"],
            ondelete="CASCADE",
        )

    # documents: nullable and NOT backfilled. NULL is the shared system corpus.
    op.add_column("documents", sa.Column("user_id", sa.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        op.f("fk_documents_user_id_users"), "documents", "users", ["user_id"], ["id"],
        ondelete="CASCADE",
    )

    op.create_index("idx_runs_user", "runs", ["user_id", "created_at"])
    op.create_index("idx_memories_user", "memories", ["user_id"])
    op.create_index("idx_documents_user", "documents", ["user_id"])


def downgrade() -> None:
    op.drop_index("idx_documents_user", table_name="documents")
    op.drop_index("idx_memories_user", table_name="memories")
    op.drop_index("idx_runs_user", table_name="runs")

    op.drop_constraint(op.f("fk_documents_user_id_users"), "documents", type_="foreignkey")
    op.drop_column("documents", "user_id")
    for table in ("memories", "runs"):
        op.drop_constraint(op.f(f"fk_{table}_user_id_users"), table, type_="foreignkey")
        op.drop_column(table, "user_id")

    op.drop_table("users")
