from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid


class Base(DeclarativeBase):
    pass


def new_uuid() -> Any:
    return uuid4()


class User(Base):
    __tablename__ = "users"

    id: Mapped[Any] = mapped_column(Uuid, primary_key=True, default=uuid4)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16), index=True)
    team_id: Mapped[Any | None] = mapped_column(Uuid, ForeignKey("teams.id"), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    team: Mapped[Team | None] = relationship(back_populates="users")


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[Any] = mapped_column(Uuid, primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    score: Mapped[int] = mapped_column(Integer, default=0)
    swap_available: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    users: Mapped[list[User]] = relationship(back_populates="team")


class Question(Base):
    __tablename__ = "questions"

    id: Mapped[Any] = mapped_column(Uuid, primary_key=True, default=uuid4)
    index: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    topic: Mapped[str] = mapped_column(String(200))
    prompt: Mapped[str] = mapped_column(Text)
    qtype: Mapped[str] = mapped_column(String(32))
    options: Mapped[dict[str, Any] | list[Any] | None] = mapped_column(JSON, nullable=True)
    answer_key: Mapped[dict[str, Any]] = mapped_column(JSON)
    hint: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RoundState(Base):
    __tablename__ = "round_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    phase: Mapped[str] = mapped_column(String(16), default="idle")
    current_question_id: Mapped[Any | None] = mapped_column(Uuid, ForeignKey("questions.id"), nullable=True)
    topic_shown_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    live_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reveal_cards: Mapped[bool] = mapped_column(Boolean, default=False)
    version: Mapped[int] = mapped_column(Integer, default=0)

    current_question: Mapped[Question | None] = relationship()


class TeamQuestion(Base):
    __tablename__ = "team_questions"
    __table_args__ = (UniqueConstraint("team_id", "question_id", name="uq_team_question"),)

    id: Mapped[Any] = mapped_column(Uuid, primary_key=True, default=uuid4)
    team_id: Mapped[Any] = mapped_column(Uuid, ForeignKey("teams.id"), index=True)
    question_id: Mapped[Any] = mapped_column(Uuid, ForeignKey("questions.id"), index=True)
    bounty: Mapped[bool] = mapped_column(Boolean, default=False)
    freeze: Mapped[bool] = mapped_column(Boolean, default=False)
    peek: Mapped[bool] = mapped_column(Boolean, default=False)
    swap_used: Mapped[bool] = mapped_column(Boolean, default=False)
    freeze_elapsed_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    locked: Mapped[bool] = mapped_column(Boolean, default=False)
    locked_elapsed_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    answer: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    kingslayer_eligible: Mapped[bool] = mapped_column(Boolean, default=False)
    points_delta: Mapped[int] = mapped_column(Integer, default=0)
    graded: Mapped[bool] = mapped_column(Boolean, default=False)
    is_correct: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    admin_override: Mapped[bool] = mapped_column(Boolean, default=False)


class SwapEvent(Base):
    __tablename__ = "swap_events"

    id: Mapped[Any] = mapped_column(Uuid, primary_key=True, default=uuid4)
    question_id: Mapped[Any] = mapped_column(Uuid, ForeignKey("questions.id"), index=True)
    source_team_id: Mapped[Any] = mapped_column(Uuid, ForeignKey("teams.id"))
    target_team_id: Mapped[Any] = mapped_column(Uuid, ForeignKey("teams.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    applied: Mapped[bool] = mapped_column(Boolean, default=False)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[Any] = mapped_column(Uuid, primary_key=True, default=uuid4)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    actor: Mapped[str] = mapped_column(String(80))
    team_id: Mapped[Any | None] = mapped_column(Uuid, nullable=True)
    question_id: Mapped[Any | None] = mapped_column(Uuid, nullable=True)
    kind: Mapped[str] = mapped_column(String(40), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
