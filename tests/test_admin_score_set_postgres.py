"""PostgreSQL-only row-lock concurrency coverage for manual score edits.

Set TEST_POSTGRES_URL to an asyncpg URL, for example:
postgresql+asyncpg://levelup:levelup@localhost:5432/levelup
"""

import asyncio
import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.deps import Principal
from app.engine import activate_bounty, close_question, get_round
from app.models import AuditEvent, Base, Question, Team, TeamQuestion
from app.routers import admin as admin_routes
from app.routers.admin import ScoreIn
from app.scoring import BOUNTY_COST


POSTGRES_URL = os.getenv("TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(
    not POSTGRES_URL,
    reason="requires TEST_POSTGRES_URL pointing to PostgreSQL",
)


def admin_principal(username):
    return Principal(
        user_id=uuid4(),
        username=username,
        role="admin",
        team_id=None,
        session_id=str(uuid4()),
    )


class PausedCommitSession:
    def __init__(self, delegate, locked, release):
        self.delegate = delegate
        self.locked = locked
        self.release = release

    async def execute(self, statement, *args, **kwargs):
        return await self.delegate.execute(statement, *args, **kwargs)

    def add(self, instance):
        self.delegate.add(instance)

    async def commit(self):
        await self.delegate.flush()
        self.locked.set()
        await self.release.wait()
        await self.delegate.commit()


@pytest.fixture
async def pg_score_ctx(monkeypatch):
    schema = f"score_test_{uuid4().hex}"
    bootstrap = create_async_engine(POSTGRES_URL)
    async with bootstrap.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(
        POSTGRES_URL,
        connect_args={"server_settings": {"search_path": schema}},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async with maker() as session:
        team = Team(id=uuid4(), name="Alpha", score=3000)
        question = Question(
            id=uuid4(),
            index=1,
            topic="Concurrency",
            prompt="Serialized?",
            qtype="mcq_single",
            options=[{"id": "yes", "text": "Yes"}],
            answer_key={"option_id": "yes"},
        )
        session.add_all([team, question])
        await session.flush()
        rnd = await get_round(session)
        rnd.phase = "topic"
        rnd.current_question_id = question.id
        rnd.topic_shown_at = datetime.now(timezone.utc)
        session.add(TeamQuestion(team_id=team.id, question_id=question.id))
        await session.commit()

    async def no_fanout(session, extra_admin=None):
        return None

    monkeypatch.setattr(admin_routes, "_fanout", no_fanout)
    yield {"maker": maker, "team_id": team.id, "question_id": question.id}

    await engine.dispose()
    async with bootstrap.begin() as connection:
        await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    await bootstrap.dispose()


async def test_two_admin_edits_serialize_and_audit_locked_previous_score(pg_score_ctx):
    maker = pg_score_ctx["maker"]
    locked = asyncio.Event()
    release = asyncio.Event()

    async with maker() as first, maker() as second:
        first_task = asyncio.create_task(
            admin_routes.set_score(
                pg_score_ctx["team_id"],
                ScoreIn(score=200),
                session=PausedCommitSession(first, locked, release),
                admin=admin_principal("admin-a"),
            )
        )
        await asyncio.wait_for(locked.wait(), timeout=5)
        second_task = asyncio.create_task(
            admin_routes.set_score(
                pg_score_ctx["team_id"],
                ScoreIn(score=300),
                session=second,
                admin=admin_principal("admin-b"),
            )
        )
        await asyncio.sleep(0.1)
        assert not second_task.done()
        release.set()
        await asyncio.gather(first_task, second_task)

    async with maker() as verification:
        events = (
            await verification.execute(
                select(AuditEvent)
                .where(AuditEvent.kind == "score_set")
                .order_by(AuditEvent.at, AuditEvent.actor)
            )
        ).scalars().all()
        by_actor = {event.actor: event.payload for event in events}
        assert by_actor["admin-a"]["old_score"] == 3000
        assert by_actor["admin-a"]["new_score"] == 200
        assert by_actor["admin-b"]["old_score"] == 200
        assert by_actor["admin-b"]["new_score"] == 300
        assert (await verification.get(Team, pg_score_ctx["team_id"])).score == 300


async def test_admin_edit_then_concurrent_grading_preserves_grading_delta(pg_score_ctx):
    maker = pg_score_ctx["maker"]
    locked = asyncio.Event()
    release = asyncio.Event()

    async with maker() as setup:
        rnd = await get_round(setup)
        now = datetime.now(timezone.utc)
        rnd.phase = "live"
        rnd.live_started_at = now
        rnd.global_deadline_at = now + timedelta(seconds=150)
        row = (
            await setup.execute(
                select(TeamQuestion).where(TeamQuestion.team_id == pg_score_ctx["team_id"])
            )
        ).scalar_one()
        row.locked = True
        row.answer = {"option_id": "yes"}
        row.locked_elapsed_ms = 0
        row.deadline_at = rnd.global_deadline_at
        await setup.commit()

    async with maker() as admin_session, maker() as grading_session:
        admin_task = asyncio.create_task(
            admin_routes.set_score(
                pg_score_ctx["team_id"],
                ScoreIn(score=5000),
                session=PausedCommitSession(admin_session, locked, release),
                admin=admin_principal("admin"),
            )
        )
        await asyncio.wait_for(locked.wait(), timeout=5)

        async def grade():
            await close_question(grading_session, "grader")
            await grading_session.commit()

        grading_task = asyncio.create_task(grade())
        await asyncio.sleep(0.1)
        assert not grading_task.done()
        release.set()
        await asyncio.gather(admin_task, grading_task)

    async with maker() as verification:
        assert (await verification.get(Team, pg_score_ctx["team_id"])).score == 7000


async def test_admin_edit_then_concurrent_card_charge_preserves_cost(pg_score_ctx):
    maker = pg_score_ctx["maker"]
    locked = asyncio.Event()
    release = asyncio.Event()

    async with maker() as admin_session, maker() as card_session:
        admin_task = asyncio.create_task(
            admin_routes.set_score(
                pg_score_ctx["team_id"],
                ScoreIn(score=5000),
                session=PausedCommitSession(admin_session, locked, release),
                admin=admin_principal("admin"),
            )
        )
        await asyncio.wait_for(locked.wait(), timeout=5)

        async def charge_card():
            await activate_bounty(
                card_session, pg_score_ctx["team_id"], "alpha"
            )
            await card_session.commit()

        card_task = asyncio.create_task(charge_card())
        await asyncio.sleep(0.1)
        assert not card_task.done()
        release.set()
        await asyncio.gather(admin_task, card_task)

    async with maker() as verification:
        assert (await verification.get(Team, pg_score_ctx["team_id"])).score == 5000 - BOUNTY_COST
