"""Add durable live-question pause state.

Revision ID: 20261009_02
Revises: 20261009_01
Create Date: 2026-10-09
"""

from alembic import op
import sqlalchemy as sa


revision = "20261009_02"
down_revision = "20261009_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "round_state",
        sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("round_state", "paused_at")
