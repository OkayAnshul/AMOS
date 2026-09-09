"""v0.8: run-level claiming for the async worker

Adds claim columns to runs, and **removes** the speculative task-level ones added
at V0.4.

That removal is the interesting half. V0.4 added `tasks.claimed_at` and a partial
`idx_tasks_claimable` index with the comment "present now because adding a column
later to a table with rows is a migration". The reasoning was sound; the guess was
wrong. Claiming happens at the RUN level, because a run is what a client submits
and polls, and a run's internal task concurrency is already handled inside one
worker.

The columns were never read by any code path. Keeping them would mean carrying
schema that documents an abandoned plan.
"""

from alembic import op
import sqlalchemy as sa

revision = "5892709841cc"
down_revision = "a0621f74b57c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("runs", sa.Column("claimed_by", sa.String(length=64), nullable=True))
    op.add_column(
        "runs",
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.execute(
        "CREATE INDEX idx_runs_claimable ON runs (status, created_at) "
        "WHERE status = 'QUEUED'"
    )

    op.execute("DROP INDEX IF EXISTS idx_tasks_claimable")
    op.drop_column("tasks", "claimed_at")


def downgrade() -> None:
    op.add_column("tasks", sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True))
    op.execute(
        "CREATE INDEX idx_tasks_claimable ON tasks (state, created_at) "
        "WHERE state = 'READY'"
    )
    op.execute("DROP INDEX IF EXISTS idx_runs_claimable")
    op.drop_column("runs", "attempt_count")
    op.drop_column("runs", "claimed_by")
    op.drop_column("runs", "claimed_at")
