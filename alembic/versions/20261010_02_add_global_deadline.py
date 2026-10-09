"""Put round global deadline under Alembic ownership.

Revision ID: 20261010_02
Revises: 20261010_01
Create Date: 2026-10-10

Older installations received this column from a runtime compatibility helper,
so the existence check preserves their upgrade path.  Fresh databases receive
it here through Alembic.
"""

from alembic import op
import sqlalchemy as sa


revision = "20261010_02"
down_revision = "20261010_01"
branch_labels = None
depends_on = None


def _column_names() -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns("round_state")}


def upgrade() -> None:
    if "global_deadline_at" not in _column_names():
        op.add_column(
            "round_state",
            sa.Column("global_deadline_at", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    if "global_deadline_at" in _column_names():
        op.drop_column("round_state", "global_deadline_at")
