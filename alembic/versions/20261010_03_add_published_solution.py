"""Add the safe durable audience solution snapshot.

Revision ID: 20261010_03
Revises: 20261010_02
Create Date: 2026-10-10
"""

from alembic import op
import sqlalchemy as sa


revision = "20261010_03"
down_revision = "20261010_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "published_leaderboard",
        sa.Column("published_solution", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("published_leaderboard", "published_solution")
