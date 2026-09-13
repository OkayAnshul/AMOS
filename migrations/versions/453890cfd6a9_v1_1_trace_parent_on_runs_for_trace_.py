"""v1.1: trace_parent on runs, for trace continuity into workers

A queued run was a *separate* trace: the submitting request had one, the worker
that executed it minutes later started another, and nothing joined them. Storing
the W3C traceparent at enqueue lets the worker continue the submitting trace
rather than beginning a new one.

Nullable, with no backfill: runs enqueued before this column existed genuinely
have no parent trace, and inventing one would be worse than the gap. The worker
falls back to a root span when it is NULL.

Revision ID: 453890cfd6a9
Revises: 5892709841cc
Create Date: 2026-09-13
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "453890cfd6a9"
down_revision = "5892709841cc"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 55 chars is the W3C traceparent format's fixed length
    # (version-trace_id-span_id-flags); 64 leaves room without being unbounded.
    op.add_column("runs", sa.Column("trace_parent", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("runs", "trace_parent")
