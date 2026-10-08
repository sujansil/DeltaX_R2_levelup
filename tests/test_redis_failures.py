import logging
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.deps import Principal
from app.engine import (
    close_question,
    get_round,
    lock_answer,
    show_topic,
    start_question,
)
from app.models import AuditEvent, Base, Question, SwapEvent, Team, TeamQuestion
from app.routers import admin as admin_routes
from app.routers import play as play_routes
from app.routers.admin import OverrideIn, RestartIn, ScoreIn
from app.routers.play import AnswerIn, SwapIn


class RedisFailure(RuntimeError):
    pass


@pytest.fixture
async def route_ctx():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async with maker() as session:
        team = Team(id=uuid4(), name="Alpha", score=5000)
        target = Team(id=uuid4(), name="Beta", score=5000)
        question = Question(
            id=uuid4(),
            index=1,
            topic="Math",
            prompt="2+2?",
            qtype="mcq_single",
            options=[{"id": "a", "text": "3"}, {"id": "b", "text": "4"}],
            answer_key={"option_id": "b"},
            hint="even",
        )
        session.add_all([team, target, question])
        await session.commit()
        yield session, team, target, question

    await engine.dispose()


def team_principal(team):
    return Principal(
        user_id=team.id,
        username="alpha",
        role="team",
        team_id=team.id,
        session_id="controller-1",
    )


def admin_principal():
    return Principal(
        user_id=uuid4(),
        username="admin",
        role="admin",
        team_id=None,
        session_id="admin-1",
    )


async def fail_broadcast(channel, payload):
    raise RedisFailure("redis publish failed")


async def successful_broadcast(channel, payload):
    return None


async def no_frozen_leaderboard():
    return None


@pytest.mark.parametrize(
    "action,expected_score,retry_code",
    [
        pytest.param("bounty", 3000, "already_used", id="bounty"),
        pytest.param("freeze", 4250, "already_used", id="freeze"),
        pytest.param("peek", 4250, "already_used", id="peek"),
        pytest.param("lock", 5000, "already_locked", id="answer-lock"),
        pytest.param("swap", 5000, "already_used", id="swap"),
    ],
)
async def test_player_action_commits_before_broadcast_failure_and_retry_is_rejected(
    route_ctx, monkeypatch, caplog, action, expected_score, retry_code
):
    session, team, target, question = route_ctx
    team_id = team.id
    target_id = target.id
    question_id = question.id
    principal = team_principal(team)

    await show_topic(session, question_id, "admin")
    if action != "bounty":
        await start_question(session, "admin")
    if action == "swap":
        await lock_answer(session, team_id, {"option_id": "b"}, "alpha")
    await session.commit()

    async def allow_controller(principal):
        return None

    monkeypatch.setattr(play_routes, "_require_controller", allow_controller)
    monkeypatch.setattr(play_routes.hub, "broadcast", fail_broadcast)
    caplog.set_level(logging.ERROR, logger="app.ws")

    async def invoke():
        if action == "bounty":
            return await play_routes.bounty(session=session, principal=principal)
        if action == "freeze":
            return await play_routes.freeze(session=session, principal=principal)
        if action == "peek":
            return await play_routes.peek(session=session, principal=principal)
        if action == "lock":
            return await play_routes.lock(
                AnswerIn(answer={"option_id": "b"}),
                session=session,
                principal=principal,
            )
        return await play_routes.swap(
            SwapIn(target_team_id=target_id),
            session=session,
            principal=principal,
        )

    assert await invoke() == {"ok": True}
    assert any(
        f"play.{action}." in record.getMessage()
        for record in caplog.records
    )

    persisted_team = await session.get(Team, team_id)
    persisted_row = (
        await session.execute(
            select(TeamQuestion).where(
                TeamQuestion.team_id == team_id,
                TeamQuestion.question_id == question_id,
            )
        )
    ).scalar_one()
    assert persisted_team.score == expected_score
    if action == "bounty":
        assert persisted_row.bounty is True
    elif action == "freeze":
        assert persisted_row.freeze is True
    elif action == "peek":
        assert persisted_row.peek is True
    elif action == "lock":
        assert persisted_row.locked is True
    else:
        assert persisted_team.swap_available is False
        assert persisted_row.swap_used is True
        swap_count = await session.scalar(
            select(func.count()).select_from(SwapEvent).where(
                SwapEvent.source_team_id == team_id
            )
        )
        assert swap_count == 1

    with pytest.raises(HTTPException) as retry:
        await invoke()
    assert retry.value.status_code == 400
    assert retry.value.detail["code"] == retry_code

    team_after_retry = await session.get(Team, team_id)
    assert team_after_retry.score == expected_score
    if action == "swap":
        swap_count = await session.scalar(
            select(func.count()).select_from(SwapEvent).where(
                SwapEvent.source_team_id == team_id
            )
        )
        assert swap_count == 1


async def configure_failing_admin_fanout(monkeypatch):
    monkeypatch.setattr("app.redisutil.get_frozen_leaderboard", no_frozen_leaderboard)
    monkeypatch.setattr(admin_routes.hub, "broadcast", fail_broadcast)


async def test_admin_start_commits_before_fanout_failure_and_retry_conflicts(
    route_ctx, monkeypatch
):
    session, team, target, question = route_ctx
    await show_topic(session, question.id, "admin")
    await session.commit()
    await configure_failing_admin_fanout(monkeypatch)

    assert await admin_routes.api_start(
        session=session, admin=admin_principal()
    ) == {"ok": True}

    rnd = await get_round(session)
    assert rnd.phase == "live"
    assert rnd.live_started_at is not None
    rows = (
        await session.execute(
            select(TeamQuestion).where(TeamQuestion.question_id == question.id)
        )
    ).scalars().all()
    assert all(row.deadline_at is not None for row in rows)

    with pytest.raises(HTTPException) as retry:
        await admin_routes.api_start(session=session, admin=admin_principal())
    assert retry.value.detail["code"] == "bad_phase"


async def test_admin_postcommit_fanout_tolerates_frozen_leaderboard_read_failure(
    route_ctx, monkeypatch
):
    session, team, target, question = route_ctx
    await show_topic(session, question.id, "admin")
    await session.commit()

    async def fail_frozen_leaderboard_read():
        raise RedisFailure("redis read failed")

    monkeypatch.setattr(
        "app.redisutil.get_frozen_leaderboard",
        fail_frozen_leaderboard_read,
    )
    monkeypatch.setattr(admin_routes.hub, "broadcast", successful_broadcast)

    assert await admin_routes.api_start(
        session=session, admin=admin_principal()
    ) == {"ok": True}
    assert (await get_round(session)).phase == "live"


async def test_admin_close_commits_score_before_fanout_failure_and_retry_conflicts(
    route_ctx, monkeypatch
):
    session, team, target, question = route_ctx
    team_id = team.id
    await show_topic(session, question.id, "admin")
    await start_question(session, "admin")
    await lock_answer(session, team_id, {"option_id": "b"}, "alpha")
    await session.commit()
    await configure_failing_admin_fanout(monkeypatch)

    assert await admin_routes.api_close(
        session=session, admin=admin_principal()
    ) == {"ok": True}

    rnd = await get_round(session)
    persisted_team = await session.get(Team, team_id)
    persisted_row = (
        await session.execute(
            select(TeamQuestion).where(
                TeamQuestion.team_id == team_id,
                TeamQuestion.question_id == question.id,
            )
        )
    ).scalar_one()
    assert rnd.phase == "closed"
    assert persisted_team.score == 7000
    assert persisted_row.graded is True
    assert persisted_row.points_delta == 2000

    with pytest.raises(HTTPException) as retry:
        await admin_routes.api_close(session=session, admin=admin_principal())
    assert retry.value.detail["code"] == "bad_phase"
    await session.refresh(persisted_team)
    assert persisted_team.score == 7000


async def test_admin_show_topic_retry_repeats_transition_after_fanout_failure(
    route_ctx, monkeypatch
):
    session, team, target, question = route_ctx
    await configure_failing_admin_fanout(monkeypatch)

    assert await admin_routes.api_show_topic(
        question.id, session=session, admin=admin_principal()
    ) == {"ok": True}

    assert (await get_round(session)).phase == "topic"
    first_audit_count = await session.scalar(
        select(func.count()).select_from(AuditEvent).where(
            AuditEvent.kind == "show_topic"
        )
    )
    assert first_audit_count == 1

    response = await admin_routes.api_show_topic(
        question.id, session=session, admin=admin_principal()
    )
    assert response == {"ok": True}
    second_audit_count = await session.scalar(
        select(func.count()).select_from(AuditEvent).where(
            AuditEvent.kind == "show_topic"
        )
    )
    assert second_audit_count == 2


async def test_admin_score_retry_repeats_version_but_not_score_after_fanout_failure(
    route_ctx, monkeypatch
):
    session, team, target, question = route_ctx
    team_id = team.id
    await configure_failing_admin_fanout(monkeypatch)

    assert await admin_routes.set_score(
        team_id,
        ScoreIn(score=1234),
        session=session,
        admin=admin_principal(),
    ) == {"id": str(team_id), "score": 1234}

    persisted_team = await session.get(Team, team_id)
    assert persisted_team.score == 1234
    first_version = persisted_team.version

    response = await admin_routes.set_score(
        team_id,
        ScoreIn(score=1234),
        session=session,
        admin=admin_principal(),
    )
    assert response["score"] == 1234
    await session.refresh(persisted_team)
    assert persisted_team.score == 1234
    assert persisted_team.version == first_version + 1


async def test_admin_restart_retry_with_stale_version_is_rejected_after_postcommit_failure(
    route_ctx, monkeypatch
):
    session, team, target, question = route_ctx
    team_id = team.id
    await show_topic(session, question.id, "admin")
    await session.commit()
    rnd = await get_round(session)
    body = RestartIn(
        confirmation="RESTART ROUND",
        expected_round_version=rnd.version,
    )

    async def release_controller(team_id, session_id=None, *, force=False):
        return True

    async def reset_frozen_leaderboard(payload):
        return None

    monkeypatch.setattr(admin_routes, "release_controller", release_controller)
    monkeypatch.setattr("app.redisutil.clear_frozen_leaderboard", no_frozen_leaderboard)
    monkeypatch.setattr("app.redisutil.set_frozen_leaderboard", reset_frozen_leaderboard)
    await configure_failing_admin_fanout(monkeypatch)

    assert await admin_routes.api_restart(
        body=body, session=session, admin=admin_principal()
    ) == {"ok": True}

    rnd = await get_round(session)
    persisted_team = await session.get(Team, team_id)
    first_version = persisted_team.version
    assert rnd.phase == "idle"
    assert persisted_team.score == 0
    assert await session.scalar(select(func.count()).select_from(TeamQuestion)) == 0
    assert await session.scalar(
        select(func.count()).select_from(AuditEvent).where(
            AuditEvent.kind == "restart_round"
        )
    ) == 1

    with pytest.raises(HTTPException) as stale:
        await admin_routes.api_restart(
            body=body, session=session, admin=admin_principal()
        )
    assert stale.value.status_code == 409
    assert stale.value.detail["code"] == "stale_round_version"
    await session.refresh(persisted_team)
    assert persisted_team.score == 0
    assert persisted_team.version == first_version
    assert await session.scalar(
        select(func.count()).select_from(AuditEvent).where(
            AuditEvent.kind == "restart_round"
        )
    ) == 1


async def test_admin_override_retry_repeats_audit_but_not_score_after_fanout_failure(
    route_ctx, monkeypatch
):
    session, team, target, question = route_ctx
    team_id = team.id
    question_id = question.id
    await show_topic(session, question_id, "admin")
    await start_question(session, "admin")
    await lock_answer(session, team_id, {"option_id": "b"}, "alpha")
    await close_question(session, "admin")
    await session.commit()
    assert (await session.get(Team, team_id)).score == 7000

    await configure_failing_admin_fanout(monkeypatch)
    body = OverrideIn(team_id=team_id, question_id=question_id, correct=False)
    assert await admin_routes.api_override(
        body, session=session, admin=admin_principal()
    ) == {"ok": True}

    persisted_team = await session.get(Team, team_id)
    assert persisted_team.score == 4500
    first_version = persisted_team.version
    assert await session.scalar(
        select(func.count()).select_from(AuditEvent).where(
            AuditEvent.kind == "override"
        )
    ) == 1

    assert await admin_routes.api_override(
        body, session=session, admin=admin_principal()
    ) == {"ok": True}
    await session.refresh(persisted_team)
    assert persisted_team.score == 4500
    assert persisted_team.version == first_version + 1
    assert await session.scalar(
        select(func.count()).select_from(AuditEvent).where(
            AuditEvent.kind == "override"
        )
    ) == 2


async def test_publish_leaderboard_can_store_redis_state_then_fail_broadcast(
    route_ctx, monkeypatch
):
    session, team, target, question = route_ctx
    stored = []

    async def store_board(board):
        stored.append(board)

    monkeypatch.setattr("app.redisutil.set_frozen_leaderboard", store_board)
    monkeypatch.setattr(admin_routes.hub, "broadcast", fail_broadcast)

    with pytest.raises(RedisFailure):
        await admin_routes.api_publish_leaderboard(
            session=session, admin=admin_principal()
        )
    assert len(stored) == 1
    assert {row["name"] for row in stored[0]} == {"Alpha", "Beta"}

    monkeypatch.setattr(admin_routes.hub, "broadcast", successful_broadcast)
    response = await admin_routes.api_publish_leaderboard(
        session=session, admin=admin_principal()
    )
    assert response == {"ok": True, "published": True}
    assert len(stored) == 2


async def test_controller_claim_and_release_can_succeed_before_broadcast_failure(monkeypatch):
    principal = Principal(
        user_id=uuid4(),
        username="alpha",
        role="team",
        team_id=uuid4(),
        session_id="controller-1",
    )
    controller = {"session_id": None}

    async def claim_controller(team_id, session_id, ttl):
        if controller["session_id"] in (None, session_id):
            controller["session_id"] = session_id
            return True, session_id
        return False, controller["session_id"]

    async def release_controller(team_id, session_id):
        if controller["session_id"] in (None, session_id):
            controller["session_id"] = None
            return True
        return False

    monkeypatch.setattr(play_routes, "claim_controller", claim_controller)
    monkeypatch.setattr(play_routes, "release_controller", release_controller)
    monkeypatch.setattr(play_routes.hub, "broadcast", fail_broadcast)

    with pytest.raises(RedisFailure):
        await play_routes.claim(principal=principal)
    assert controller["session_id"] == principal.session_id

    monkeypatch.setattr(play_routes.hub, "broadcast", successful_broadcast)
    assert await play_routes.claim(principal=principal) == {
        "ok": True,
        "controller": True,
    }

    monkeypatch.setattr(play_routes.hub, "broadcast", fail_broadcast)
    with pytest.raises(RedisFailure):
        await play_routes.release(principal=principal)
    assert controller["session_id"] is None

    monkeypatch.setattr(play_routes.hub, "broadcast", successful_broadcast)
    assert await play_routes.release(principal=principal) == {"ok": True}


async def test_controller_heartbeat_redis_failure_prevents_success(monkeypatch):
    principal = Principal(
        user_id=uuid4(),
        username="alpha",
        role="team",
        team_id=uuid4(),
        session_id="controller-1",
    )

    async def fail_heartbeat(team_id, session_id, ttl):
        raise RedisFailure("redis expire failed")

    monkeypatch.setattr(play_routes, "heartbeat_controller", fail_heartbeat)
    with pytest.raises(RedisFailure):
        await play_routes.beat(principal=principal)
