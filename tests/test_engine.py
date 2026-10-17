from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.clock import FakeClock
from app.config import get_settings
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
    tq_player_view,
    use_freeze,
    use_peek,
    use_swap,
)
from app.models import AuditEvent, Base, Question, SwapEvent, Team, TeamQuestion
from app.routers.admin import QuestionIn, api_round
from app.routers.play import _require_controller, release, state
from app.routers.public import audience
from app.scoring import BOUNTY_COST, PEEK_COST, QUESTION_MS, public_rules, validate_timing_constants


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
            qtype="mcq_single",
            options=[
                {"id": "a", "text": "3"},
                {"id": "b", "text": "4"},
            ],
            answer_key={"option_id": "b"},
            hint="even",
        )
        session.add_all([t1, t2, q])
        await session.commit()
        yield session, clock, t1, t2, q
    await eng.dispose()



def test_player_view_potential_points_stop_at_locked_elapsed():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    row = TeamQuestion(
        team_id=uuid4(),
        question_id=uuid4(),
        locked=True,
        locked_elapsed_ms=90_000,
        bounty=False,
        freeze=False,
    )

    view = tq_player_view(
        row,
        None,
        live_started_at=start,
        now=start + timedelta(seconds=120),
        include_hint=False,
    )

    assert view["locked_elapsed_ms"] == 90_000
    assert view["potential_points"] == 1400


def test_question_type_restricts_to_mcq():
    QuestionIn(index=1, topic="Math", prompt="1+1?", qtype="mcq_single", answer_key={"option_id": "b"})
    with pytest.raises(ValidationError):
        QuestionIn(index=1, topic="Math", prompt="1+1?", qtype="numeric", answer_key={"value": "2"})


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
    await lock_answer(session, t1.id, {"option_id": "b"}, "alpha", clock)
    with pytest.raises(EngineError):
        await lock_answer(session, t1.id, {"option_id": "a"}, "alpha", clock)
    clock.advance_sec(10)
    await lock_answer(session, t2.id, {"option_id": "a"}, "beta", clock)
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
    await lock_answer(session, t1.id, {"option_id": "b"}, "alpha", clock)
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


async def test_autoclose_rechecks_freeze_extended_deadline(ctx):
    session, clock, t1, _t2, q = ctx
    await show_topic(session, q.id, "admin", clock)
    await start_question(session, "admin", clock)

    clock.advance_sec(120)
    await use_freeze(session, t1.id, "alpha", clock)
    clock.advance_sec(30)

    assert await close_question(
        session,
        "system:autoclose",
        clock,
        require_deadline=True,
    ) is None
    assert (await get_round(session)).phase == "live"
    assert await session.scalar(
        select(func.count()).select_from(AuditEvent).where(AuditEvent.kind == "close_question")
    ) == 0

    clock.advance_sec(30)
    assert await try_autoclose(session, clock) is True
    assert (await get_round(session)).phase == "closed"
    assert await session.scalar(
        select(func.count()).select_from(AuditEvent).where(AuditEvent.kind == "close_question")
    ) == 1


async def test_manual_close_ignores_freeze_extended_deadline(ctx):
    session, clock, t1, _t2, q = ctx
    await show_topic(session, q.id, "admin", clock)
    await start_question(session, "admin", clock)
    clock.advance_sec(120)
    await use_freeze(session, t1.id, "alpha", clock)

    assert await close_question(session, "admin", clock) is not None
    assert (await get_round(session)).phase == "closed"


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
        await lock_answer(session, t1.id, {"option_id": "b"}, "alpha", clock)
    assert e.value.code == "time_up"


async def test_start_question_sets_team_deadlines_that_survive_state_reload(ctx):
    session, clock, t1, t2, q = ctx
    question_id = q.id
    await show_topic(session, q.id, "admin", clock)
    await start_question(session, "admin", clock)

    started_at = (await get_round(session)).live_started_at
    rows = (
        await session.execute(
            __import__("sqlalchemy").select(TeamQuestion).where(
                TeamQuestion.question_id == question_id
            )
        )
    ).scalars().all()
    assert {row.deadline_at for row in rows} == {
        started_at + timedelta(seconds=150)
    }

    await session.commit()
    session.expire_all()

    reloaded_round = await get_round(session)
    reloaded_rows = (
        await session.execute(
            __import__("sqlalchemy").select(TeamQuestion).where(
                TeamQuestion.question_id == question_id
            )
        )
    ).scalars().all()
    assert reloaded_round.phase == "live"
    assert reloaded_round.live_started_at == started_at
    assert {row.deadline_at for row in reloaded_rows} == {
        started_at + timedelta(seconds=150)
    }


async def test_valid_deadline_allows_149_999ms_but_rejects_exactly_150s(ctx):
    session, clock, t1, t2, q = ctx
    await show_topic(session, q.id, "admin", clock)
    await start_question(session, "admin", clock)

    clock.advance_ms(149_999)
    row = await lock_answer(session, t1.id, {"option_id": "b"}, "alpha", clock)
    assert row.locked_elapsed_ms == 149_999

    clock.advance_ms(1)
    with pytest.raises(EngineError) as exc:
        await lock_answer(session, t2.id, {"option_id": "b"}, "beta", clock)
    assert exc.value.code == "time_up"


async def test_missing_deadline_fallback_matches_normal_150s_deadline(ctx):
    session, clock, t1, t2, q = ctx
    await show_topic(session, q.id, "admin", clock)
    await start_question(session, "admin", clock)

    started_at = (await get_round(session)).live_started_at
    rows = (
        await session.execute(
            __import__("sqlalchemy").select(TeamQuestion).where(
                TeamQuestion.question_id == q.id
            )
        )
    ).scalars().all()
    normal_deadline = rows[0].deadline_at
    assert normal_deadline == started_at + timedelta(milliseconds=QUESTION_MS)

    for row in rows:
        row.deadline_at = None
    await session.flush()

    clock.advance_ms(QUESTION_MS - 1)
    peek_row = await use_peek(session, t1.id, "alpha", clock)
    assert peek_row.peek is True

    clock.advance_ms(1)
    with pytest.raises(EngineError) as exc:
        await use_peek(session, t2.id, "beta", clock)
    assert exc.value.code == "time_up"
    await session.refresh(t2)
    assert t2.score == 5000


async def test_restart_round_resets_state_and_scores(ctx):
    session, clock, t1, t2, q = ctx
    await show_topic(session, q.id, "admin", clock)
    await start_question(session, "admin", clock)
    await lock_answer(session, t1.id, {"option_id": "b"}, "alpha", clock)
    await close_question(session, "admin", clock)

    rnd = await get_round(session)
    await restart_round(session, "admin", rnd.version, clock)

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
    await lock_answer(session, t1.id, {"option_id": "b"}, "alpha", clock)
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


async def test_peek_charges_once_and_hint_survives_state_reload(ctx, monkeypatch):
    session, clock, t1, t2, q = ctx
    team_id = t1.id
    await show_topic(session, q.id, "admin", clock)
    await start_question(session, "admin", clock)

    row = await use_peek(session, t1.id, "alpha", clock)
    await session.commit()

    assert row.peek is True
    await session.refresh(t1)
    assert t1.score == 5000 - PEEK_COST

    async def fake_get_controller(team_id):
        return None

    async def fake_get_frozen_leaderboard():
        return []

    monkeypatch.setattr("app.routers.play.get_controller", fake_get_controller)
    monkeypatch.setattr("app.redisutil.get_frozen_leaderboard", fake_get_frozen_leaderboard)

    principal = Principal(
        user_id=t1.id,
        username="alpha",
        role="team",
        team_id=t1.id,
        session_id="s-1",
    )
    first_state = await state(session, principal)
    assert first_state["me"]["peek"] is True
    assert first_state["me"]["hint"] == "even"

    session.expire_all()
    reloaded_state = await state(session, principal)
    assert reloaded_state["me"]["peek"] is True
    assert reloaded_state["me"]["hint"] == "even"

    with pytest.raises(EngineError) as exc:
        await use_peek(session, t1.id, "alpha", clock)
    assert exc.value.code == "already_used"
    await session.rollback()

    reloaded_team = await session.get(Team, team_id)
    assert reloaded_team.score == 5000 - PEEK_COST


async def test_peek_locks_state_team_and_card_row_before_charging(ctx):
    session, clock, t1, t2, q = ctx
    await show_topic(session, q.id, "admin", clock)
    await start_question(session, "admin", clock)

    class RecordingSession:
        def __init__(self, delegate):
            self.delegate = delegate
            self.locked_tables = []

        async def execute(self, statement, *args, **kwargs):
            if statement._for_update_arg is not None:
                self.locked_tables.extend(table.name for table in statement.get_final_froms())
            return await self.delegate.execute(statement, *args, **kwargs)

        def add(self, instance):
            self.delegate.add(instance)

        async def flush(self):
            await self.delegate.flush()

    recording_session = RecordingSession(session)
    await use_peek(recording_session, t1.id, "alpha", clock)

    assert recording_session.locked_tables == ["round_state", "teams", "team_questions"]


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
    principal = Principal(
        user_id=uuid4(),
        username="alpha",
        role="team",
        team_id=uuid4(),
        session_id="sess-other",
    )

    class FakeRedis:
        def __init__(self):
            self.store = {f"ctrl:{principal.team_id}": "sess-owner"}

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

    with pytest.raises(HTTPException) as exc:
        await release(principal=principal)

    assert exc.value.status_code == 409
    assert fake_redis.store[f"ctrl:{principal.team_id}"] == "sess-owner"
    assert broadcast_calls == []


async def test_require_controller_renews_lease_for_active_session(monkeypatch):
    principal = Principal(
        user_id=uuid4(),
        username="alpha",
        role="team",
        team_id=uuid4(),
        session_id="sess-owner",
    )

    class FakeRedis:
        def __init__(self):
            self.store = {f"ctrl:{principal.team_id}": "sess-owner"}
            self.expirations = []

        async def get(self, key):
            return self.store.get(key)

        async def expire(self, key, ttl):
            self.expirations.append((key, ttl))

    fake_redis = FakeRedis()

    async def fake_get_redis():
        return fake_redis

    monkeypatch.setattr("app.redisutil.get_redis", fake_get_redis)

    await _require_controller(principal)

    assert fake_redis.expirations == [
        (f"ctrl:{principal.team_id}", get_settings().controller_ttl_sec)
    ]


async def test_reorder_questions_updates_canonical_index_order(ctx):
    session, clock, t1, t2, q = ctx
    q2 = Question(
        id=uuid4(), index=2, topic="History", prompt="Who won WWII?",
        qtype="mcq_single", options=[{"id": "a", "text": "Allies"}],
        answer_key={"option_id": "a"}, hint="coalition",
    )
    q3 = Question(
        id=uuid4(), index=3, topic="Science", prompt="H2O?",
        qtype="mcq_single", options=[{"id": "a", "text": "Water"}],
        answer_key={"option_id": "a"}, hint="liquid",
    )
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

    async def fake_get_frozen_leaderboard():
        return []

    monkeypatch.setattr("app.routers.play.get_controller", fake_get_controller)
    monkeypatch.setattr("app.redisutil.get_frozen_leaderboard", fake_get_frozen_leaderboard)

    payload = await state(
        session,
        Principal(user_id=t1.id, username="alpha", role="team", team_id=t1.id, session_id="s-1"),
    )

    assert payload["phase"] == "live"
    assert payload["timer_total_sec"] == 150
    assert 0 < payload["time_left_sec"] <= 120
    assert payload["rules"] == public_rules()
    assert payload["rules"]["question_duration_ms"] == QUESTION_MS
    assert all(payload["features"].values())


@pytest.mark.parametrize("feature", ["bounty", "freeze", "peek", "swap"])
async def test_disabled_card_rejects_before_mutation(ctx, monkeypatch, feature):
    session, clock, first, second, question = ctx
    await show_topic(session, question.id, "admin", clock)
    if feature != "bounty":
        await start_question(session, "admin", clock)
    if feature == "swap":
        await lock_answer(session, first.id, {"option_id": "b"}, "alpha", clock)
    await session.flush()
    row = (await session.execute(select(TeamQuestion).where(
        TeamQuestion.team_id == first.id, TeamQuestion.question_id == question.id
    ))).scalar_one()
    score_before = first.score
    deadline_before = row.deadline_at
    audit_before = await session.scalar(select(func.count()).select_from(AuditEvent))
    monkeypatch.setattr(get_settings(), f"enable_{feature}", False)

    action = {
        "bounty": lambda: activate_bounty(session, first.id, "alpha", clock),
        "freeze": lambda: use_freeze(session, first.id, "alpha", clock),
        "peek": lambda: use_peek(session, first.id, "alpha", clock),
        "swap": lambda: use_swap(session, first.id, second.id, "alpha", clock),
    }[feature]
    with pytest.raises(EngineError) as error:
        await action()
    assert error.value.code == "feature_disabled"
    assert error.value.status == 403
    assert first.score == score_before
    assert row.deadline_at == deadline_before
    assert not getattr(row, feature if feature != "swap" else "swap_used")
    assert await session.scalar(select(func.count()).select_from(AuditEvent)) == audit_before
    assert await session.scalar(select(func.count()).select_from(SwapEvent)) == 0


async def test_disabled_freeze_keeps_existing_extension(ctx, monkeypatch):
    session, clock, first, _second, question = ctx
    await show_topic(session, question.id, "admin", clock)
    await start_question(session, "admin", clock)
    clock.advance_sec(120)
    frozen = await use_freeze(session, first.id, "alpha", clock)
    extended_deadline = frozen.deadline_at
    monkeypatch.setattr(get_settings(), "enable_freeze", False)
    clock.advance_sec(40)
    await lock_answer(session, first.id, {"option_id": "b"}, "alpha", clock)
    assert frozen.deadline_at == extended_deadline
    assert frozen.locked is True


async def test_disabled_bounty_keeps_existing_scoring(ctx, monkeypatch):
    session, clock, first, _second, question = ctx
    await show_topic(session, question.id, "admin", clock)
    bounty_row = await activate_bounty(session, first.id, "alpha", clock)
    monkeypatch.setattr(get_settings(), "enable_bounty", False)
    await start_question(session, "admin", clock)
    await lock_answer(session, first.id, {"option_id": "b"}, "alpha", clock)
    await close_question(session, "admin", clock)
    assert bounty_row.points_delta == 6000


async def test_disabled_peek_keeps_existing_hint(ctx, monkeypatch):
    session, clock, first, _second, question = ctx
    await show_topic(session, question.id, "admin", clock)
    await start_question(session, "admin", clock)
    peeked = await use_peek(session, first.id, "alpha", clock)
    monkeypatch.setattr(get_settings(), "enable_peek", False)
    view = tq_player_view(peeked, question, live_started_at=(await get_round(session)).live_started_at,
                          now=clock.now(), include_hint=peeked.peek)
    assert view["hint"] == question.hint


async def test_disabled_swap_keeps_pending_event(ctx, monkeypatch):
    session, clock, first, second, question = ctx
    await show_topic(session, question.id, "admin", clock)
    await start_question(session, "admin", clock)
    await lock_answer(session, first.id, {"option_id": "b"}, "alpha", clock)
    swap = await use_swap(session, first.id, second.id, "alpha", clock)
    monkeypatch.setattr(get_settings(), "enable_swap", False)
    await close_question(session, "admin", clock)
    assert swap.applied is True
    target_row = (await session.execute(select(TeamQuestion).where(
        TeamQuestion.team_id == second.id, TeamQuestion.question_id == question.id
    ))).scalar_one()
    assert target_row.answer == {"option_id": "b"}


async def test_disabled_kingslayer_only_affects_new_snapshot(ctx, monkeypatch):
    session, clock, first, second, _question = ctx
    later = Question(id=uuid4(), index=6, topic="Later", prompt="2+2?", qtype="mcq_single",
                     options=[{"id": "b", "text": "4"}], answer_key={"option_id": "b"})
    session.add(later)
    first.score = 4000
    second.score = 5000
    await session.flush()
    await show_topic(session, later.id, "admin", clock)
    old_row = (await session.execute(select(TeamQuestion).where(
        TeamQuestion.team_id == first.id, TeamQuestion.question_id == later.id
    ))).scalar_one()
    assert old_row.kingslayer_eligible is True
    monkeypatch.setattr(get_settings(), "enable_kingslayer", False)
    await start_question(session, "admin", clock)
    await lock_answer(session, first.id, {"option_id": "b"}, "alpha", clock)
    await close_question(session, "admin", clock)
    assert old_row.points_delta == 2500
    another = Question(id=uuid4(), index=7, topic="Another", prompt="2+2?", qtype="mcq_single",
                       options=[{"id": "b", "text": "4"}], answer_key={"option_id": "b"})
    session.add(another)
    await session.flush()
    await show_topic(session, another.id, "admin", clock)
    new_rows = (await session.execute(select(TeamQuestion).where(TeamQuestion.question_id == another.id))).scalars().all()
    assert new_rows and not any(row.kingslayer_eligible for row in new_rows)


@pytest.mark.parametrize("field,value", [
    ("QUESTION_MS", 0),
    ("DECAY_START_MS", -1),
    ("DECAY_END_MS", 59_999),
    ("FREEZE_BONUS_MS", 0),
])
def test_timing_validation_rejects_invalid_values(monkeypatch, field, value):
    import app.scoring as scoring
    validate_timing_constants()
    monkeypatch.setattr(scoring, field, value)
    with pytest.raises(ValueError, match=field):
        validate_timing_constants()


def test_timing_validation_allows_decay_floor_after_question(monkeypatch):
    import app.scoring as scoring
    monkeypatch.setattr(scoring, "QUESTION_MS", 90_000)
    validate_timing_constants()


async def test_player_admin_audience_share_authoritative_deadline(ctx, monkeypatch):
    session, clock, first, _second, question = ctx
    await show_topic(session, question.id, "admin", clock)
    await start_question(session, "admin", clock)

    async def no_controller(_team_id):
        return None

    monkeypatch.setattr("app.routers.play.get_controller", no_controller)
    principal = Principal(user_id=first.id, username="alpha", role="team",
                          team_id=first.id, session_id="s-1")
    player = await state(session, principal)
    admin = await api_round(session, None)
    public = await audience(session)
    expected = int((clock.now() + timedelta(milliseconds=QUESTION_MS)).timestamp() * 1000)

    assert player["deadline_ms"] == expected
    assert admin["global_deadline_ms"] == expected
    assert int(datetime.fromisoformat(public["global_deadline_at"]).timestamp() * 1000) == expected
    assert player["rules"]["question_duration_ms"] == QUESTION_MS
