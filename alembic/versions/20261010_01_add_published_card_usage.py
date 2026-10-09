"""Add durable published card usage rows.

Revision ID: 20261010_01
Revises: 20261009_02
Create Date: 2026-10-10
"""

from alembic import op
import sqlalchemy as sa


revision = "20261010_01"
down_revision = "20261009_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "published_leaderboard",
        sa.Column(
            "card_usage_rows",
            sa.JSON(),
            server_default=sa.text("'[]'"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("published_leaderboard", "card_usage_rows")
