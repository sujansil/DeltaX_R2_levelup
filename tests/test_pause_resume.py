from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.clock import FakeClock
from app.deps import Principal
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
    try_autostart,
    use_freeze,
    use_peek,
    use_swap,
)
from app.models import AuditEvent, Base, PublishedLeaderboard, Question, Team, TeamQuestion
from app.routers import admin as admin_routes
from app.routers import play as play_routes
from app.scoring import FREEZE_BONUS_MS


def admin_principal():
    return Principal(
        user_id=uuid4(), username="admin", role="admin", team_id=None,
        session_id=str(uuid4()),
    )


def team_principal(team):
    return Principal(
        user_id=uuid4(), username=team.name.lower(), role="team", team_id=team.id,
        session_id=str(uuid4()),
    )


@pytest.fixture
async def pause_ctx():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    clock = FakeClock()
    async with maker() as session:
        first = Team(id=uuid4(), name="Alpha", score=5000)
        second = Team(id=uuid4(), name="Beta", score=5000)
        question = Question(
            id=uuid4(), index=1, topic="Time", prompt="Pause?",
            qtype="mcq_single",
            options=[{"id": "yes", "text": "Yes"}],
            answer_key={"option_id": "yes"}, hint="Stop the clock",
        )
        session.add_all([first, second, question])
        await session.commit()
        await show_topic(session, question.id, "admin", clock)
        await start_question(session, "admin", clock)
        await session.commit()
        yield {
            "session": session,
            "maker": maker,
            "clock": clock,
            "first": first,
            "second": second,
            "question": question,
        }
    await engine.dispose()


async def rows_for(ctx):
    return (
        await ctx["session"].execute(
            select(TeamQuestion)
            .where(TeamQuestion.question_id == ctx["question"].id)
            .order_by(TeamQuestion.team_id)
        )
    ).scalars().all()


async def test_pause_and_resume_shift_exactly_and_write_exact_audits(pause_ctx):
    session = pause_ctx["session"]
    clock = pause_ctx["clock"]
    rnd = await get_round(session)
    original_start = rnd.live_started_at
    original_global = rnd.global_deadline_at
    original_deadlines = {row.team_id: row.deadline_at for row in await rows_for(pause_ctx)}

    clock.advance_sec(30)
    paused, changed = await pause_question(session, "admin", clock)
    await session.commit()
    assert changed is True
    assert paused.phase == "paused"
    assert paused.paused_at == clock.now()
    assert paused.live_started_at == original_start
    assert paused.global_deadline_at == original_global
    pause_version = paused.version

    clock.advance_sec(37.4)
    resumed, changed = await resume_question(session, "admin", clock)
    await session.commit()
    duration = timedelta(seconds=37.4)
    assert changed is True
    assert resumed.phase == "live"
    assert resumed.paused_at is None
    assert resumed.live_started_at == original_start + duration
    assert resumed.global_deadline_at == original_global + duration
    assert resumed.version == pause_version + 1
    for row in await rows_for(pause_ctx):
        assert row.deadline_at == original_deadlines[row.team_id] + duration

    events = (
        await session.execute(
            select(AuditEvent)
            .where(AuditEvent.kind.in_(["round_pause", "round_resume"]))
            .order_by(AuditEvent.at, AuditEvent.kind)
        )
    ).scalars().all()
    by_kind = {event.kind: event for event in events}
    assert by_kind["round_pause"].actor == "admin"
    assert by_kind["round_pause"].question_id == pause_ctx["question"].id
    assert by_kind["round_pause"].payload["phase_before"] == "live"
    resume_payload = by_kind["round_resume"].payload
    assert resume_payload["pause_duration_ms"] == 37_400
    assert len(resume_payload["team_deadlines"]) == 2


async def test_remaining_time_and_future_lock_exclude_pause(pause_ctx):
    session = pause_ctx["session"]
    clock = pause_ctx["clock"]
    clock.advance_sec(40)
    before_pause = (
        (await get_round(session)).global_deadline_at.timestamp() - clock.now().timestamp()
    )
    await pause_question(session, "admin", clock)
    clock.advance_sec(37.4)
    await resume_question(session, "admin", clock)
    after_resume = (
        (await get_round(session)).global_deadline_at.timestamp() - clock.now().timestamp()
    )
    assert after_resume == before_pause

    clock.advance_sec(10)
    row = await lock_answer(
        session, pause_ctx["first"].id, {"option_id": "yes"}, "alpha", clock
    )
    assert row.locked_elapsed_ms == 50_000
    await close_question(session, "admin", clock)
    await session.commit()
    await session.refresh(pause_ctx["first"])
    assert pause_ctx["first"].score == 7000


async def test_freeze_advantage_and_snapshot_survive_pause(pause_ctx):
    session = pause_ctx["session"]
    clock = pause_ctx["clock"]
    rnd = await get_round(session)
    normal_start = rnd.live_started_at
    clock.advance_sec(90)
    frozen = await use_freeze(session, pause_ctx["first"].id, "alpha", clock)
    frozen_elapsed = frozen.freeze_elapsed_ms
    clock.advance_sec(10)
    await pause_question(session, "admin", clock)
    clock.advance_sec(37.4)
    await resume_question(session, "admin", clock)

    rows = {row.team_id: row for row in await rows_for(pause_ctx)}
    frozen = rows[pause_ctx["first"].id]
    normal = rows[pause_ctx["second"].id]
    assert frozen.freeze_elapsed_ms == frozen_elapsed == 90_000
    assert frozen.deadline_at - normal.deadline_at == timedelta(milliseconds=FREEZE_BONUS_MS)
    assert (await get_round(session)).live_started_at == normal_start + timedelta(seconds=37.4)
    assert (await get_round(session)).global_deadline_at == frozen.deadline_at


async def test_duplicate_pause_and_resume_are_idempotent_without_drift(pause_ctx):
    session = pause_ctx["session"]
    clock = pause_ctx["clock"]
    clock.advance_sec(20)
    rnd, changed = await pause_question(session, "admin", clock)
    first_paused_at = rnd.paused_at
    first_version = rnd.version
    clock.advance_sec(5)
    same, changed_again = await pause_question(session, "admin", clock)
    assert changed is True
    assert changed_again is False
    same_paused_at = (
        same.paused_at.replace(tzinfo=timezone.utc)
        if same.paused_at.tzinfo is None
        else same.paused_at
    )
    assert same_paused_at == first_paused_at
    assert same.version == first_version

    clock.advance_sec(5)
    resumed, resumed_changed = await resume_question(session, "admin", clock)
    shifted_start = resumed.live_started_at
    resumed_version = resumed.version
    again, changed_again = await resume_question(session, "admin", clock)
    assert resumed_changed is True
    assert changed_again is False
    assert again.live_started_at == shifted_start
    assert again.version == resumed_version
    assert await session.scalar(
        select(func.count()).select_from(AuditEvent).where(
            AuditEvent.kind == "round_pause"
        )
    ) == 1
    assert await session.scalar(
        select(func.count()).select_from(AuditEvent).where(
            AuditEvent.kind == "round_resume"
        )
    ) == 1


async def test_multiple_pause_cycles_have_no_drift(pause_ctx):
    session = pause_ctx["session"]
    clock = pause_ctx["clock"]
    original_start = (await get_round(session)).live_started_at
    total = 0
    for pause_ms in (1234, 37_400, 789):
        clock.advance_sec(1)
        await pause_question(session, "admin", clock)
        clock.advance_ms(pause_ms)
        await resume_question(session, "admin", clock)
        total += pause_ms
    assert (await get_round(session)).live_started_at == original_start + timedelta(milliseconds=total)


async def test_pause_rejects_at_deadline(pause_ctx):
    clock = pause_ctx["clock"]
    clock.advance_sec(150)
    with pytest.raises(EngineError) as error:
        await pause_question(pause_ctx["session"], "admin", clock)
    assert error.value.code == "time_up"
    assert error.value.status == 409


async def test_paused_state_blocks_progression_and_gameplay_but_allows_state(pause_ctx, monkeypatch):
    session = pause_ctx["session"]
    clock = pause_ctx["clock"]
    first = pause_ctx["first"]
    second = pause_ctx["second"]
    clock.advance_sec(30)
    await pause_question(session, "admin", clock)
    await session.commit()
    clock.advance_sec(200)

    assert await try_autoclose(session, clock) is False
    assert await try_autostart(session, clock) is False
    for operation in (
        lambda: lock_answer(session, first.id, {"option_id": "yes"}, "alpha", clock),
        lambda: use_freeze(session, first.id, "alpha", clock),
        lambda: use_peek(session, first.id, "alpha", clock),
        lambda: use_swap(session, first.id, second.id, "alpha", clock),
    ):
        with pytest.raises(EngineError) as error:
            await operation()
        assert error.value.code == "bad_phase"

    with pytest.raises(EngineError) as close_error:
        await close_question(session, "admin", clock)
    assert close_error.value.code == "round_paused"
    assert close_error.value.status == 409

    async def no_controller(team_id):
        return None

    monkeypatch.setattr(play_routes, "get_controller", no_controller)
    payload = await play_routes.state(session, team_principal(first))
    assert payload["phase"] == "paused"
    assert payload["paused_at"] is not None
    assert payload["global_deadline_at"] is not None
    assert payload["time_left_sec"] == 120


async def test_paused_state_survives_new_session_and_export(pause_ctx, monkeypatch):
    session = pause_ctx["session"]
    clock = pause_ctx["clock"]
    clock.advance_sec(12)
    await pause_question(session, "admin", clock)
    await session.commit()

    async with pause_ctx["maker"]() as reloaded:
        durable = await get_round(reloaded)
        assert durable.phase == "paused"
        durable_paused_at = (
            durable.paused_at.replace(tzinfo=timezone.utc)
            if durable.paused_at.tzinfo is None
            else durable.paused_at
        )
        assert durable_paused_at == clock.now()

    async def no_controller(team_id):
        return None

    async def no_board():
        return None

    monkeypatch.setattr(admin_routes, "get_controller", no_controller)
    monkeypatch.setattr("app.redisutil.get_frozen_leaderboard", no_board)
    response = await admin_routes.export_event(admin_principal(), session)
    export = __import__("json").loads(response.body)
    round_row = export["postgres"]["round_states"][0]
    assert round_row["phase"] == "paused"
    exported_paused_at = datetime.fromisoformat(round_row["paused_at"])
    if exported_paused_at.tzinfo is None:
        exported_paused_at = exported_paused_at.replace(tzinfo=timezone.utc)
    assert exported_paused_at == clock.now()


async def test_pause_does_not_modify_answers_cards_scores_or_published_board(pause_ctx):
    session = pause_ctx["session"]
    row = (await rows_for(pause_ctx))[0]
    row.answer = {"option_id": "yes"}
    row.locked = True
    row.locked_elapsed_ms = 10_000
    snapshot = PublishedLeaderboard(
        id=1,
        published_at=pause_ctx["clock"].now(),
        published_by="admin",
        round_version=1,
        phase="live",
        question_id=pause_ctx["question"].id,
        question_index=1,
        rows=[{"id": str(pause_ctx["first"].id), "name": "Alpha", "score": 5000, "rank": 1}],
    )
    session.add(snapshot)
    await session.commit()
    original = {
        "answer": row.answer,
        "locked": row.locked,
        "locked_elapsed_ms": row.locked_elapsed_ms,
        "score": pause_ctx["first"].score,
        "published": list(snapshot.rows),
    }
    await pause_question(session, "admin", pause_ctx["clock"])
    pause_ctx["clock"].advance_sec(5)
    await resume_question(session, "admin", pause_ctx["clock"])
    assert row.answer == original["answer"]
    assert row.locked == original["locked"]
    assert row.locked_elapsed_ms == original["locked_elapsed_ms"]
    assert pause_ctx["first"].score == original["score"]
    assert snapshot.rows == original["published"]
