from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.clock import FakeClock
from app.deps import Principal
from app.engine import (
    EngineError,
    activate_bounty,
    close_question,
    get_round,
    lock_answer,
    reorder_questions,
    restart_round,
    show_topic,
    start_question,
    try_autostart,
    try_autoclose,
    use_freeze,
    use_swap,
)
from app.models import Base, Question, Team, TeamQuestion
from app.routers.admin import api_round
from app.routers.play import _require_controller, release, state
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
    assert t1.score == 5000 - 750 + 1200
    assert t2.score == 5000


async def test_freeze_stacks_round_deadline_for_all_teams(ctx):
    session, clock, t1, t2, q = ctx
    await show_topic(session, q.id, "admin", clock)
    await start_question(session, "admin", clock)

    start = (await get_round(session)).live_started_at
    clock.advance_sec(90)
    await use_freeze(session, t1.id, "alpha", clock)
    rnd = await get_round(session)
    assert rnd.global_deadline_at == start + timedelta(seconds=180)

    clock.advance_sec(30)
    await use_freeze(session, t2.id, "beta", clock)
    rnd = await get_round(session)
    assert rnd.global_deadline_at == start + timedelta(seconds=180)

    clock.advance_sec(59)
    assert await try_autoclose(session, clock) is False
    clock.advance_sec(1)
    assert await try_autoclose(session, clock) is True


async def test_freeze_keeps_player_deadline_but_caps_global_deadline(ctx):
    session, clock, t1, t2, q = ctx
    await show_topic(session, q.id, "admin", clock)
    await start_question(session, "admin", clock)

    start = (await get_round(session)).live_started_at
    clock.advance_sec(120)
    await use_freeze(session, t1.id, "alpha", clock)
    t1_row = (await session.execute(__import__("sqlalchemy").select(TeamQuestion).where(TeamQuestion.team_id == t1.id, TeamQuestion.question_id == q.id))).scalar_one()
    assert t1_row.deadline_at == start + timedelta(seconds=180)

    rnd = await get_round(session)
    assert rnd.global_deadline_at == start + timedelta(seconds=180)

    clock.advance_sec(5)
    await use_freeze(session, t2.id, "beta", clock)
    t2_row = (await session.execute(__import__("sqlalchemy").select(TeamQuestion).where(TeamQuestion.team_id == t2.id, TeamQuestion.question_id == q.id))).scalar_one()
    assert t2_row.deadline_at == start + timedelta(seconds=180)

    rnd = await get_round(session)
    assert rnd.global_deadline_at == start + timedelta(seconds=180)


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


async def test_admin_round_route_handles_active_question(ctx):
    session, clock, t1, t2, q = ctx
    await show_topic(session, q.id, "admin", clock)
    await start_question(session, "admin", clock)

    payload = await api_round(
        session,
        Principal(user_id=t1.id, username="admin", role="admin", team_id=None, session_id="s-1"),
    )

    assert payload["phase"] == "live"
    assert payload["question"]["id"] == str(q.id)
    assert payload["question"]["index"] == 1


async def test_release_route_refuses_wrong_session_without_broadcast(monkeypatch):
    class FakeRedis:
        def __init__(self):
            self.store = {"ctrl:team-1": "sess-owner"}

        async def get(self, key):
            return self.store.get(key)

        async def delete(self, key):
            self.store.pop(key, None)

    fake_redis = FakeRedis()

    async def fake_get_redis():
        return fake_redis

    monkeypatch.setattr("app.redisutil.get_redis", fake_get_redis)

    broadcast_calls = []

    async def fake_broadcast(channel, payload):
        broadcast_calls.append((channel, payload))

    monkeypatch.setattr("app.routers.play.hub.broadcast", fake_broadcast)

    principal = Principal(
        user_id=uuid4(),
        username="alpha",
        role="team",
        team_id=uuid4(),
        session_id="sess-other",
    )

    with pytest.raises(Exception):
        await release(principal=principal)

    assert broadcast_calls == []


async def test_require_controller_renews_lease_for_active_session(monkeypatch):
    class FakeRedis:
        def __init__(self):
            self.store = {"ctrl:team-1": "sess-owner"}

        async def get(self, key):
            return self.store.get(key)

    fake_redis = FakeRedis()

    async def fake_get_redis():
        return fake_redis

    monkeypatch.setattr("app.redisutil.get_redis", fake_get_redis)

    calls = []

    async def fake_heartbeat(team_id, session_id, ttl):
        calls.append((team_id, session_id, ttl))
        return True

    monkeypatch.setattr("app.routers.play.heartbeat_controller", fake_heartbeat)

    principal = Principal(
        user_id=uuid4(),
        username="alpha",
        role="team",
        team_id=uuid4(),
        session_id="sess-owner",
    )

    await _require_controller(principal)

    assert calls == [(str(principal.team_id), principal.session_id, 120)]


async def test_reorder_questions_updates_canonical_index_order(ctx):
    session, clock, t1, t2, q = ctx
    q2 = Question(id=uuid4(), index=2, topic="History", prompt="Who won WWII?", qtype="text", answer_key={"value": "Allies"}, hint="coalition")
    q3 = Question(id=uuid4(), index=3, topic="Science", prompt="H2O?", qtype="text", answer_key={"value": "Water"}, hint="liquid")
    session.add_all([q2, q3])
    await session.commit()

    await reorder_questions(session, [q3.id, q.id, q2.id], actor="admin")
    await session.refresh(q)
    await session.refresh(q2)
    await session.refresh(q3)

    assert [q.index, q2.index, q3.index] == [2, 3, 1]


async def test_player_state_shows_live_timer_contract(ctx, monkeypatch):
    session, clock, t1, t2, q = ctx
    await show_topic(session, q.id, "admin", clock)
    await start_question(session, "admin", clock)

    row = (await session.execute(__import__("sqlalchemy").select(TeamQuestion).where(TeamQuestion.team_id == t1.id, TeamQuestion.question_id == q.id))).scalar_one()
    row.deadline_at = datetime.now(timezone.utc) + timedelta(seconds=120)
    await session.commit()

    async def fake_get_controller(team_id):
        return None

    monkeypatch.setattr("app.routers.play.get_controller", fake_get_controller)

    payload = await state(
        session,
        Principal(user_id=t1.id, username="alpha", role="team", team_id=t1.id, session_id="s-1"),
    )

    assert payload["phase"] == "live"
    assert payload["timer_total_sec"] == 150
    assert 0 < payload["time_left_sec"] <= 120
