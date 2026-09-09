"""v0.6: semantic memory table, episodic columns on runs

Hand-written for the vector columns, which SQLAlchemy core cannot express.

Note what is NOT here: an `episodes` table. An episode IS a run, so episodic
memory adds two columns to `runs` rather than duplicating goal, status, tokens
and timings into a parallel table. Episodic memory is an index on an existing
store, not a new store.
"""

from alembic import op
import sqlalchemy as sa

revision = "a0621f74b57c"
down_revision = "5a881f4bdb98"
branch_labels = None
depends_on = None

DIMENSIONS = 1536


def upgrade() -> None:
    op.create_table(
        "memories",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("subject", sa.String(length=200), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("source_run_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("superseded_by", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
                  nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_memories")),
        sa.ForeignKeyConstraint(["source_run_id"], ["runs.id"],
                                name=op.f("fk_memories_source_run_id_runs"),
                                ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["superseded_by"], ["memories.id"],
                                name=op.f("fk_memories_superseded_by_memories"),
                                ondelete="SET NULL"),
    )
    op.execute(f"ALTER TABLE memories ADD COLUMN embedding vector({DIMENSIONS})")

    # Partial index: only current facts are ever read on the hot path.
    op.execute(
        "CREATE INDEX idx_memories_current ON memories (subject) "
        "WHERE superseded_by IS NULL"
    )
    op.create_index("idx_memories_source_run", "memories", ["source_run_id"])
    op.execute(
        "CREATE INDEX idx_memories_embedding ON memories "
        "USING hnsw (embedding vector_cosine_ops)"
    )

    # Episodic memory on the existing runs table.
    op.add_column("runs", sa.Column("lesson", sa.Text(), nullable=True))
    op.execute(f"ALTER TABLE runs ADD COLUMN goal_embedding vector({DIMENSIONS})")
    op.execute(
        "CREATE INDEX idx_runs_goal_embedding ON runs "
        "USING hnsw (goal_embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_runs_goal_embedding")
    op.drop_column("runs", "goal_embedding")
    op.drop_column("runs", "lesson")
    op.execute("DROP INDEX IF EXISTS idx_memories_embedding")
    op.drop_index("idx_memories_source_run", table_name="memories")
    op.execute("DROP INDEX IF EXISTS idx_memories_current")
    op.drop_table("memories")
