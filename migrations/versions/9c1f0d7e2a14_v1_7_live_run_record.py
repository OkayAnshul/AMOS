"""v1.7: the live run record — attribution, one order, events, per-run options (ADR-015/016)

Until now model and tool calls were batch-inserted when a run finished, with no
task, agent or purpose, and one shared `created_at`. This adds:

- `run_record_seq`, one sequence used as the DEFAULT for `seq` on `llm_calls`,
  `tool_calls` and `run_events` — a total order across all three. Adding a column
  whose default is `nextval()` makes Postgres fill existing rows, each with its
  own value, so old runs get an order too (arbitrary within a run, as before).
- attribution columns on both call tables: `purpose`, `agent`, `task_ref`,
  `task_attempt`, `run_attempt`.
- `run_events`, append-only, for what has no table: plan created, task started /
  retrying / finished, routing decisions, critic verdicts, delegation.
- `runs.options`, the per-run feature switches (ADR-016).

Revision ID: 9c1f0d7e2a14
Revises: 835121ee2bd2
Create Date: 2026-11-20
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "9c1f0d7e2a14"
down_revision = "835121ee2bd2"
branch_labels = None
depends_on = None

_SEQ = "run_record_seq"
_CALL_TABLES = ("llm_calls", "tool_calls")


def upgrade() -> None:
    op.execute(f"CREATE SEQUENCE {_SEQ}")

    for table in _CALL_TABLES:
        op.add_column(
            table,
            sa.Column(
                "seq", sa.BigInteger(), server_default=sa.text(f"nextval('{_SEQ}')"), nullable=False
            ),
        )
        op.add_column(table, sa.Column("purpose", sa.String(32), nullable=True))
        op.add_column(table, sa.Column("agent", sa.String(64), nullable=True))
        op.add_column(table, sa.Column("task_ref", sa.String(32), nullable=True))
        op.add_column(
            table,
            sa.Column("task_attempt", sa.Integer(), server_default="0", nullable=False),
        )
        op.add_column(
            table,
            sa.Column("run_attempt", sa.Integer(), server_default="0", nullable=False),
        )
        op.create_index(f"idx_{table}_run_seq", table, ["run_id", "seq"])

    op.create_table(
        "run_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "seq", sa.BigInteger(), server_default=sa.text(f"nextval('{_SEQ}')"), nullable=False
        ),
        sa.Column("kind", sa.String(48), nullable=False),
        sa.Column("task_ref", sa.String(32), nullable=True),
        sa.Column("agent", sa.String(64), nullable=True),
        sa.Column("task_attempt", sa.Integer(), server_default="0", nullable=False),
        sa.Column("run_attempt", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
    )
    op.create_index("idx_run_events_run_seq", "run_events", ["run_id", "seq"])

    op.add_column("runs", sa.Column("options", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("runs", "options")
    op.drop_index("idx_run_events_run_seq", table_name="run_events")
    op.drop_table("run_events")
    for table in _CALL_TABLES:
        op.drop_index(f"idx_{table}_run_seq", table_name=table)
        for column in ("run_attempt", "task_attempt", "task_ref", "agent", "purpose", "seq"):
            op.drop_column(table, column)
    op.execute(f"DROP SEQUENCE {_SEQ}")
