"""Add durable current published leaderboard.

Revision ID: 20261009_01
Revises: 20261008_01
Create Date: 2026-10-09
"""

from alembic import op
import sqlalchemy as sa


revision = "20261009_01"
down_revision = "20261008_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "published_leaderboard",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_by", sa.String(length=80), nullable=True),
        sa.Column("round_version", sa.Integer(), nullable=False),
        sa.Column("phase", sa.String(length=16), nullable=False),
        sa.Column("question_id", sa.Uuid(), nullable=True),
        sa.Column("question_index", sa.Integer(), nullable=True),
        sa.Column("rows", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["question_id"], ["questions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("published_leaderboard")
