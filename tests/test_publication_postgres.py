"""PostgreSQL-only publication snapshot concurrency tests.

Set TEST_POSTGRES_URL to a disposable asyncpg database URL.
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
from app.models import Base, PublishedLeaderboard, Question, Team, TeamQuestion
from app.routers import admin as admin_routes


POSTGRES_URL = os.getenv("TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(
    not POSTGRES_URL,
    reason="requires TEST_POSTGRES_URL pointing to disposable PostgreSQL",
)


def admin_principal() -> Principal:
    return Principal(
        user_id=uuid4(), username="publisher", role="admin", team_id=None,
        session_id=str(uuid4()),
    )


@pytest.fixture
async def pg_publication_ctx(monkeypatch):
    schema = f"publication_test_{uuid4().hex}"
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
        team = Team(id=uuid4(), name="Alpha", score=5000)
        question = Question(
            id=uuid4(), index=1, topic="Concurrency", prompt="Atomic?",
            qtype="mcq_single", options=[{"id": "yes", "text": "Yes"}],
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

    async def no_cache(_rows):
        return None

    async def no_broadcast(_channel, _payload):
        return None

    monkeypatch.setattr("app.redisutil.set_frozen_leaderboard", no_cache)
    monkeypatch.setattr(admin_routes.hub, "broadcast", no_broadcast)
    yield {
        "maker": maker, "team_id": team.id, "question_id": question.id,
    }

    await engine.dispose()
    async with bootstrap.begin() as connection:
        await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    await bootstrap.dispose()


async def test_card_commit_first_is_included_atomically(pg_publication_ctx):
    maker = pg_publication_ctx["maker"]
    card_ready, release_card = asyncio.Event(), asyncio.Event()

    async def held_card(session):
        await activate_bounty(session, pg_publication_ctx["team_id"], "alpha")
        card_ready.set()
        await release_card.wait()
        await session.commit()

    async with maker() as card_session, maker() as publish_session:
        card_task = asyncio.create_task(held_card(card_session))
        await asyncio.wait_for(card_ready.wait(), 5)
        publish_task = asyncio.create_task(
            admin_routes.api_publish_leaderboard(
                session=publish_session, admin=admin_principal()
            )
        )
        await asyncio.sleep(0.1)
        assert not publish_task.done()
        release_card.set()
        await asyncio.gather(card_task, publish_task)

    async with maker() as verify:
        snapshot = await verify.get(PublishedLeaderboard, 1)
        assert snapshot.rows[0]["score"] == 3000
        assert snapshot.card_usage_rows[0]["bounty_used"] is True


async def test_publish_lock_first_excludes_later_card_until_republish(pg_publication_ctx):
    maker = pg_publication_ctx["maker"]
    publish_locked, release_publish = asyncio.Event(), asyncio.Event()

    async def held_publish(session):
        await get_round(session, for_update=True)
        publish_locked.set()
        await release_publish.wait()
        return await admin_routes.api_publish_leaderboard(
            session=session, admin=admin_principal()
        )

    async def use_card(session):
        await activate_bounty(session, pg_publication_ctx["team_id"], "alpha")
        await session.commit()

    async with maker() as publish_session, maker() as card_session:
        publish_task = asyncio.create_task(held_publish(publish_session))
        await asyncio.wait_for(publish_locked.wait(), 5)
        card_task = asyncio.create_task(use_card(card_session))
        await asyncio.sleep(0.1)
        assert not card_task.done()
        release_publish.set()
        await asyncio.gather(publish_task, card_task)

    async with maker() as verify:
        snapshot = await verify.get(PublishedLeaderboard, 1)
        assert snapshot.rows[0]["score"] == 5000
        assert snapshot.card_usage_rows[0]["bounty_used"] is False
        assert (await verify.get(Team, pg_publication_ctx["team_id"])).score == 3000

    async with maker() as republish:
        await admin_routes.api_publish_leaderboard(
            session=republish, admin=admin_principal()
        )
    async with maker() as verify:
        snapshot = await verify.get(PublishedLeaderboard, 1)
        assert snapshot.rows[0]["score"] == 3000
        assert snapshot.card_usage_rows[0]["bounty_used"] is True


async def test_grading_commit_and_card_state_are_consistent_in_snapshot(
    pg_publication_ctx,
):
    maker = pg_publication_ctx["maker"]
    async with maker() as setup:
        await activate_bounty(setup, pg_publication_ctx["team_id"], "alpha")
        rnd = await get_round(setup)
        now = datetime.now(timezone.utc)
        rnd.phase = "live"
        rnd.live_started_at = now
        rnd.global_deadline_at = now + timedelta(seconds=150)
        row = (
            await setup.execute(
                select(TeamQuestion).where(
                    TeamQuestion.team_id == pg_publication_ctx["team_id"]
                )
            )
        ).scalar_one()
        row.locked = True
        row.answer = {"option_id": "yes"}
        row.locked_elapsed_ms = 0
        row.deadline_at = rnd.global_deadline_at
        await setup.commit()

    grading_ready, release_grading = asyncio.Event(), asyncio.Event()

    async def held_grading(session):
        await close_question(session, "grader")
        grading_ready.set()
        await release_grading.wait()
        await session.commit()

    async with maker() as grading_session, maker() as publish_session:
        grading_task = asyncio.create_task(held_grading(grading_session))
        await asyncio.wait_for(grading_ready.wait(), 5)
        publish_task = asyncio.create_task(
            admin_routes.api_publish_leaderboard(
                session=publish_session, admin=admin_principal()
            )
        )
        await asyncio.sleep(0.1)
        assert not publish_task.done()
        release_grading.set()
        await asyncio.gather(grading_task, publish_task)

    async with maker() as verify:
        snapshot = await verify.get(PublishedLeaderboard, 1)
        team = await verify.get(Team, pg_publication_ctx["team_id"])
        assert snapshot.rows[0]["score"] == team.score
        assert snapshot.card_usage_rows[0]["bounty_used"] is True
