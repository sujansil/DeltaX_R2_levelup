"""PostgreSQL-only concurrency tests for live-question pause/resume.

Set TEST_POSTGRES_URL to a disposable asyncpg database URL.
"""

import asyncio
import os
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.clock import FakeClock
from app.engine import (
    EngineError,
    close_question,
    get_round,
    lock_answer,
    pause_question,
    resume_question,
    show_topic,
    start_question,
    try_autoclose,
    use_freeze,
)
from app.models import AuditEvent, Base, Question, Team, TeamQuestion


POSTGRES_URL = os.getenv("TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(
    not POSTGRES_URL,
    reason="requires TEST_POSTGRES_URL pointing to disposable PostgreSQL",
)


@pytest.fixture
async def pg_pause_ctx():
    schema = f"pause_test_{uuid4().hex}"
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
    clock = FakeClock()

    async with maker() as session:
        first = Team(id=uuid4(), name="Alpha", score=5000)
        second = Team(id=uuid4(), name="Beta", score=5000)
        question = Question(
            id=uuid4(), index=1, topic="Concurrency", prompt="Serialized?",
            qtype="mcq_single",
            options=[{"id": "yes", "text": "Yes"}],
            answer_key={"option_id": "yes"},
        )
        session.add_all([first, second, question])
        await session.commit()
        await show_topic(session, question.id, "admin", clock)
        await start_question(session, "admin", clock)
        await session.commit()

    yield {
        "maker": maker,
        "clock": clock,
        "first_id": first.id,
        "second_id": second.id,
        "question_id": question.id,
    }

    await engine.dispose()
    async with bootstrap.begin() as connection:
        await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    await bootstrap.dispose()


async def hold_pause(session, clock, acquired, release):
    result = await pause_question(session, "admin", clock)
    acquired.set()
    await release.wait()
    await session.commit()
    return result


async def test_answer_vs_pause_is_serialized(pg_pause_ctx):
    maker = pg_pause_ctx["maker"]
    clock = pg_pause_ctx["clock"]
    clock.advance_sec(20)
    acquired, release = asyncio.Event(), asyncio.Event()
    async with maker() as pauser, maker() as player:
        pause_task = asyncio.create_task(hold_pause(pauser, clock, acquired, release))
        await asyncio.wait_for(acquired.wait(), 5)
        answer_task = asyncio.create_task(
            lock_answer(player, pg_pause_ctx["first_id"], {"option_id": "yes"}, "alpha", clock)
        )
        await asyncio.sleep(0.1)
        assert not answer_task.done()
        release.set()
        await pause_task
        with pytest.raises(EngineError, match="Can only lock"):
            await answer_task
        await player.rollback()
    async with maker() as verify:
        row = (
            await verify.execute(
                select(TeamQuestion).where(TeamQuestion.team_id == pg_pause_ctx["first_id"])
            )
        ).scalar_one()
        assert row.locked is False


async def test_freeze_vs_pause_is_serialized(pg_pause_ctx):
    maker = pg_pause_ctx["maker"]
    clock = pg_pause_ctx["clock"]
    clock.advance_sec(20)
    acquired, release = asyncio.Event(), asyncio.Event()
    async with maker() as pauser, maker() as player:
        pause_task = asyncio.create_task(hold_pause(pauser, clock, acquired, release))
        await asyncio.wait_for(acquired.wait(), 5)
        freeze_task = asyncio.create_task(
            use_freeze(player, pg_pause_ctx["first_id"], "alpha", clock)
        )
        await asyncio.sleep(0.1)
        assert not freeze_task.done()
        release.set()
        await pause_task
        with pytest.raises(EngineError, match="Freeze only"):
            await freeze_task
        await player.rollback()
    async with maker() as verify:
        row = (
            await verify.execute(
                select(TeamQuestion).where(TeamQuestion.team_id == pg_pause_ctx["first_id"])
            )
        ).scalar_one()
        assert row.freeze is False
        assert (await verify.get(Team, pg_pause_ctx["first_id"])).score == 5000


async def test_pause_vs_autoclose_near_deadline_is_serialized(pg_pause_ctx):
    maker = pg_pause_ctx["maker"]
    pause_clock = pg_pause_ctx["clock"]
    pause_clock.advance_ms(149_999)
    close_clock = FakeClock(pause_clock.now() + timedelta(milliseconds=1))
    acquired, release = asyncio.Event(), asyncio.Event()
    async with maker() as pauser, maker() as closer:
        pause_task = asyncio.create_task(hold_pause(pauser, pause_clock, acquired, release))
        await asyncio.wait_for(acquired.wait(), 5)
        close_task = asyncio.create_task(try_autoclose(closer, close_clock))
        await asyncio.sleep(0.1)
        assert not close_task.done()
        release.set()
        await pause_task
        with pytest.raises(EngineError) as error:
            await close_task
        assert error.value.code == "round_paused"
        await closer.rollback()
    async with maker() as verify:
        assert (await get_round(verify)).phase == "paused"
        assert await verify.scalar(
            select(func.count()).select_from(TeamQuestion).where(TeamQuestion.graded.is_(True))
        ) == 0


async def test_pause_vs_manual_close_is_serialized(pg_pause_ctx):
    maker = pg_pause_ctx["maker"]
    clock = pg_pause_ctx["clock"]
    clock.advance_sec(20)
    acquired, release = asyncio.Event(), asyncio.Event()
    async with maker() as pauser, maker() as closer:
        pause_task = asyncio.create_task(hold_pause(pauser, clock, acquired, release))
        await asyncio.wait_for(acquired.wait(), 5)
        close_task = asyncio.create_task(close_question(closer, "admin", clock))
        await asyncio.sleep(0.1)
        assert not close_task.done()
        release.set()
        await pause_task
        with pytest.raises(EngineError) as error:
            await close_task
        assert error.value.code == "round_paused"
        await closer.rollback()


async def test_two_simultaneous_pause_requests_are_idempotent(pg_pause_ctx):
    maker = pg_pause_ctx["maker"]
    clock = pg_pause_ctx["clock"]
    clock.advance_sec(20)
    acquired, release = asyncio.Event(), asyncio.Event()
    async with maker() as first, maker() as second:
        first_task = asyncio.create_task(hold_pause(first, clock, acquired, release))
        await asyncio.wait_for(acquired.wait(), 5)

        async def second_pause():
            result = await pause_question(second, "admin", clock)
            await second.commit()
            return result

        second_task = asyncio.create_task(second_pause())
        await asyncio.sleep(0.1)
        assert not second_task.done()
        release.set()
        (_, first_changed), (_, second_changed) = await asyncio.gather(first_task, second_task)
        assert first_changed is True
        assert second_changed is False
    async with maker() as verify:
        assert await verify.scalar(
            select(func.count()).select_from(AuditEvent).where(AuditEvent.kind == "round_pause")
        ) == 1


async def test_two_simultaneous_resume_requests_shift_once(pg_pause_ctx):
    maker = pg_pause_ctx["maker"]
    clock = pg_pause_ctx["clock"]
    clock.advance_sec(10)
    async with maker() as setup:
        await pause_question(setup, "admin", clock)
        await setup.commit()
        original_start = (await get_round(setup)).live_started_at
    clock.advance_sec(37.4)
    acquired, release = asyncio.Event(), asyncio.Event()

    async def first_resume(session):
        result = await resume_question(session, "admin", clock)
        acquired.set()
        await release.wait()
        await session.commit()
        return result

    async with maker() as first, maker() as second:
        first_task = asyncio.create_task(first_resume(first))
        await asyncio.wait_for(acquired.wait(), 5)

        async def second_resume():
            result = await resume_question(second, "admin", clock)
            await second.commit()
            return result

        second_task = asyncio.create_task(second_resume())
        await asyncio.sleep(0.1)
        assert not second_task.done()
        release.set()
        (_, first_changed), (_, second_changed) = await asyncio.gather(first_task, second_task)
        assert first_changed is True
        assert second_changed is False
    async with maker() as verify:
        rnd = await get_round(verify)
        assert rnd.live_started_at == original_start + timedelta(seconds=37.4)
        assert await verify.scalar(
            select(func.count()).select_from(AuditEvent).where(AuditEvent.kind == "round_resume")
        ) == 1


async def test_resume_vs_answer_observes_shifted_time_origin(pg_pause_ctx):
    maker = pg_pause_ctx["maker"]
    clock = pg_pause_ctx["clock"]
    clock.advance_sec(20)
    async with maker() as setup:
        await pause_question(setup, "admin", clock)
        await setup.commit()
    clock.advance_sec(37.4)
    acquired, release = asyncio.Event(), asyncio.Event()

    async def held_resume(session):
        result = await resume_question(session, "admin", clock)
        acquired.set()
        await release.wait()
        await session.commit()
        return result

    async with maker() as resumer, maker() as player:
        resume_task = asyncio.create_task(held_resume(resumer))
        await asyncio.wait_for(acquired.wait(), 5)
        answer_task = asyncio.create_task(
            lock_answer(player, pg_pause_ctx["first_id"], {"option_id": "yes"}, "alpha", clock)
        )
        await asyncio.sleep(0.1)
        assert not answer_task.done()
        release.set()
        await resume_task
        row = await answer_task
        assert row.locked_elapsed_ms == 20_000
        await player.commit()


async def test_resume_rollback_leaves_all_timestamps_unchanged(pg_pause_ctx):
    maker = pg_pause_ctx["maker"]
    clock = pg_pause_ctx["clock"]
    clock.advance_sec(20)
    async with maker() as setup:
        await pause_question(setup, "admin", clock)
        await setup.commit()
    clock.advance_sec(37.4)

    async with maker() as session:
        before_round = await get_round(session)
        expected = {
            "phase": before_round.phase,
            "paused_at": before_round.paused_at,
            "live_started_at": before_round.live_started_at,
            "global_deadline_at": before_round.global_deadline_at,
            "version": before_round.version,
            "deadlines": {
                row.team_id: row.deadline_at
                for row in (
                    await session.execute(select(TeamQuestion))
                ).scalars().all()
            },
        }
        await resume_question(session, "admin", clock)
        await session.flush()
        await session.rollback()

    async with maker() as verify:
        rnd = await get_round(verify)
        assert rnd.phase == expected["phase"]
        assert rnd.paused_at == expected["paused_at"]
        assert rnd.live_started_at == expected["live_started_at"]
        assert rnd.global_deadline_at == expected["global_deadline_at"]
        assert rnd.version == expected["version"]
        rows = (await verify.execute(select(TeamQuestion))).scalars().all()
        assert {row.team_id: row.deadline_at for row in rows} == expected["deadlines"]
        assert await verify.scalar(
            select(func.count()).select_from(AuditEvent).where(AuditEvent.kind == "round_resume")
        ) == 0
