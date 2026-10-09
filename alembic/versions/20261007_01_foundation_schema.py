"""Create the original Level Up application schema.

Revision ID: 20261007_01
Revises: None
Create Date: 2026-10-07

This foundation makes Alembic capable of initializing an empty database.  It
contains the tables that predated the first incremental migration; later
revisions continue to add auth sessions, published state, and pause state.
"""

from alembic import op
import sqlalchemy as sa


revision = "20261007_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "teams",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("swap_available", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_teams_name", "teams", ["name"], unique=True)

    op.create_table(
        "questions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("index", sa.Integer(), nullable=False),
        sa.Column("topic", sa.String(length=200), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("qtype", sa.String(length=32), nullable=False),
        sa.Column("media_url", sa.String(length=512), nullable=True),
        sa.Column("options", sa.JSON(), nullable=True),
        sa.Column("answer_key", sa.JSON(), nullable=False),
        sa.Column("hint", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_questions_index", "questions", ["index"], unique=True)

    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("team_id", sa.Uuid(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_users_role", "users", ["role"])
    op.create_index("ix_users_username", "users", ["username"], unique=True)

    op.create_table(
        "round_state",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("phase", sa.String(length=16), nullable=False),
        sa.Column("current_question_id", sa.Uuid(), nullable=True),
        sa.Column("topic_shown_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("live_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reveal_cards", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["current_question_id"], ["questions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "team_questions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column("question_id", sa.Uuid(), nullable=False),
        sa.Column("bounty", sa.Boolean(), nullable=False),
        sa.Column("freeze", sa.Boolean(), nullable=False),
        sa.Column("peek", sa.Boolean(), nullable=False),
        sa.Column("swap_used", sa.Boolean(), nullable=False),
        sa.Column("freeze_elapsed_ms", sa.Integer(), nullable=True),
        sa.Column("locked", sa.Boolean(), nullable=False),
        sa.Column("locked_elapsed_ms", sa.Integer(), nullable=True),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("answer", sa.JSON(), nullable=True),
        sa.Column("kingslayer_eligible", sa.Boolean(), nullable=False),
        sa.Column("points_delta", sa.Integer(), nullable=False),
        sa.Column("graded", sa.Boolean(), nullable=False),
        sa.Column("is_correct", sa.Boolean(), nullable=True),
        sa.Column("admin_override", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["question_id"], ["questions.id"]),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("team_id", "question_id", name="uq_team_question"),
    )
    op.create_index("ix_team_questions_question_id", "team_questions", ["question_id"])
    op.create_index("ix_team_questions_team_id", "team_questions", ["team_id"])

    op.create_table(
        "swap_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("question_id", sa.Uuid(), nullable=False),
        sa.Column("source_team_id", sa.Uuid(), nullable=False),
        sa.Column("target_team_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("applied", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["question_id"], ["questions.id"]),
        sa.ForeignKeyConstraint(["source_team_id"], ["teams.id"]),
        sa.ForeignKeyConstraint(["target_team_id"], ["teams.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_swap_events_question_id", "swap_events", ["question_id"])

    op.create_table(
        "audit_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("actor", sa.String(length=80), nullable=False),
        sa.Column("team_id", sa.Uuid(), nullable=True),
        sa.Column("question_id", sa.Uuid(), nullable=True),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_events_at", "audit_events", ["at"])
    op.create_index("ix_audit_events_kind", "audit_events", ["kind"])


def downgrade() -> None:
    op.drop_index("ix_audit_events_kind", table_name="audit_events")
    op.drop_index("ix_audit_events_at", table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_index("ix_swap_events_question_id", table_name="swap_events")
    op.drop_table("swap_events")
    op.drop_index("ix_team_questions_team_id", table_name="team_questions")
    op.drop_index("ix_team_questions_question_id", table_name="team_questions")
    op.drop_table("team_questions")
    op.drop_table("round_state")
    op.drop_index("ix_users_username", table_name="users")
    op.drop_index("ix_users_role", table_name="users")
    op.drop_table("users")
    op.drop_index("ix_questions_index", table_name="questions")
    op.drop_table("questions")
    op.drop_index("ix_teams_name", table_name="teams")
    op.drop_table("teams")
