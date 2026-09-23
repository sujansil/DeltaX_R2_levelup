from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.clock import FakeClock
from app.engine import (
    EngineError,
    activate_bounty,
    close_question,
    get_round,
    lock_answer,
    restart_round,
    show_topic,
    start_question,
    try_autostart,
    try_autoclose,
    use_freeze,
    use_swap,
)
from app.models import Base, Question, Team, TeamQuestion
from app.scoring import BOUNTY_COST


@pytest.fixture
async def ctx():
    eng = create_async_engine("sqlite+aiosqlite://")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(eng, expire_on_commit=False)
    clock = FakeClock()
    async with maker() as session:
        t1 = Team(id=uuid4(), name="Alpha", score=5000)
        t2 = Team(id=uuid4(), name="Beta", score=5000)
        q = Question(
            id=uuid4(),
            index=1,
            topic="Math",
            prompt="2+2?",
            qtype="numeric",
            options=None,
            answer_key={"value": "4", "tolerance": "0"},
            hint="even",
        )
        session.add_all([t1, t2, q])
        await session.commit()
        yield session, clock, t1, t2, q
    await eng.dispose()


async def test_bounty_only_during_topic_and_lock_once(ctx):
    session, clock, t1, t2, q = ctx
    await show_topic(session, q.id, "admin", clock)
    await activate_bounty(session, t1.id, "alpha", clock)
    await session.refresh(t1)
    assert t1.score == 5000 - BOUNTY_COST
    with pytest.raises(EngineError) as e:
        await activate_bounty(session, t1.id, "alpha", clock)
    assert e.value.code == "already_used"
    await start_question(session, "admin", clock)
    with pytest.raises(EngineError) as e:
        await activate_bounty(session, t2.id, "beta", clock)
    assert e.value.code == "bad_phase"
    await lock_answer(session, t1.id, {"value": "4"}, "alpha", clock)
    with pytest.raises(EngineError):
        await lock_answer(session, t1.id, {"value": "5"}, "alpha", clock)
    clock.advance_sec(10)
    await lock_answer(session, t2.id, {"value": "0"}, "beta", clock)
    await close_question(session, "admin", clock)
    await session.refresh(t1)
    await session.refresh(t2)
    # bounty triples correct at t=0 → 6000; wrong → -1500
    assert t1.score == 5000 - BOUNTY_COST + 6000
    assert t2.score == 5000 - 500


async def test_timeout_zero_freeze_extends_and_snapshots(ctx):
    session, clock, t1, t2, q = ctx
    t1.score = 5000
    await show_topic(session, q.id, "admin", clock)
    await start_question(session, "admin", clock)
    clock.advance_sec(90)
    await use_freeze(session, t1.id, "alpha", clock)
    clock.advance_sec(40)  # would be past 150 without freeze
    await lock_answer(session, t1.id, {"value": "4"}, "alpha", clock)
    await close_question(session, "admin", clock)
    await session.refresh(t1)
    await session.refresh(t2)
    assert t1.score == 5000 - 750 + 1400
    assert t2.score == 5000


async def test_cannot_lock_after_deadline(ctx):
    session, clock, t1, t2, q = ctx
    await show_topic(session, q.id, "admin", clock)
    await start_question(session, "admin", clock)
    clock.advance_sec(150)
    with pytest.raises(EngineError) as e:
        await lock_answer(session, t1.id, {"value": "4"}, "alpha", clock)
    assert e.value.code == "time_up"


async def test_restart_round_resets_state_and_scores(ctx):
    session, clock, t1, t2, q = ctx
    await show_topic(session, q.id, "admin", clock)
    await start_question(session, "admin", clock)
    await lock_answer(session, t1.id, {"value": "4"}, "alpha", clock)
    await close_question(session, "admin", clock)

    await restart_round(session, "admin", clock)

    rnd = await get_round(session)
    await session.refresh(t1)
    await session.refresh(t2)
    rows = (await session.execute(__import__("sqlalchemy").select(TeamQuestion))).scalars().all()
    assert rnd.phase == "idle"
    assert rnd.current_question_id is None
    assert t1.score == 0
    assert t2.score == 0
    assert rows == []


async def test_topic_and_live_auto_progress(ctx):
    session, clock, t1, t2, q = ctx
    await show_topic(session, q.id, "admin", clock)
    clock.advance_sec(45)
    assert await try_autostart(session, clock) is True
    rnd = await get_round(session)
    assert rnd.phase == "live"
    assert rnd.live_started_at is not None

    clock.advance_sec(150)
    assert await try_autoclose(session, clock) is True
    rnd = await get_round(session)
    assert rnd.phase == "closed"


async def test_swap_requires_lock_and_is_once(ctx):
    session, clock, t1, t2, q = ctx
    await show_topic(session, q.id, "admin", clock)
    await start_question(session, "admin", clock)
    with pytest.raises(EngineError) as e:
        await use_swap(session, t1.id, t2.id, "alpha", clock)
    assert e.value.code == "not_locked"
    await lock_answer(session, t1.id, {"value": "4"}, "alpha", clock)
    await use_swap(session, t1.id, t2.id, "alpha", clock)
    with pytest.raises(EngineError) as e:
        await use_swap(session, t1.id, t2.id, "alpha", clock)
    assert e.value.code == "already_used"
    await close_question(session, "admin", clock)
    await session.refresh(t1)
    await session.refresh(t2)
    # A had correct, swapped onto empty B → A gets 0, B gets A's correct at B's (unlocked→locked via swap) time 0
    assert t1.score == 5000
    assert t2.score == 5000 + 2000
