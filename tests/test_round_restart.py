import logging
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import get_settings
from app.db import get_session
from app.deps import Principal
from app.engine import get_round
from app.models import (
    AuditEvent,
    AuthSession,
    Base,
    Question,
    RoundState,
    SwapEvent,
    Team,
    TeamQuestion,
    User,
)
from app.routers import admin as admin_routes
from app.routers.admin import RestartIn
from app.security import create_token, hash_password


def admin_principal() -> Principal:
    return Principal(
        user_id=uuid4(), username="admin", role="admin", team_id=None,
        session_id=str(uuid4()),
    )


@pytest.fixture
async def restart_ctx(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    released_controllers = []
    frozen = {"value": [{"id": "stale", "name": "Old", "score": 9999, "rank": 1}]}

    async def release_controller(team_id, session_id=None, *, force=False):
        released_controllers.append((team_id, session_id, force))
        return True

    async def clear_frozen_leaderboard():
        frozen["value"] = None

    async def set_frozen_leaderboard(payload):
        frozen["value"] = payload

    async def no_fanout(session, extra_admin=None, public_leaderboard=None):
        return None

    monkeypatch.setattr(admin_routes, "release_controller", release_controller)
    monkeypatch.setattr(
        "app.redisutil.clear_frozen_leaderboard", clear_frozen_leaderboard
    )
    monkeypatch.setattr(
        "app.redisutil.set_frozen_leaderboard", set_frozen_leaderboard
    )
    monkeypatch.setattr(admin_routes, "_fanout", no_fanout)

    async with maker() as session:
        first_team = Team(id=uuid4(), name="Alpha", score=4321, swap_available=False)
        second_team = Team(id=uuid4(), name="Beta", score=6789, swap_available=False)
        user = User(
            id=uuid4(), username="alpha", password_hash=hash_password("pass123"),
            role="team", team=first_team,
        )
        question = Question(
            id=uuid4(), index=1, topic="Math", prompt="2 + 2?",
            qtype="mcq_single",
            options=[{"id": "a", "text": "3"}, {"id": "b", "text": "4"}],
            answer_key={"option_id": "b"}, hint="Even",
        )
        session.add_all([first_team, second_team, user, question])
        await session.flush()
        now = datetime.now(timezone.utc)
        auth_session = AuthSession(
            id=uuid4(), user_id=user.id, team_id=first_team.id, slot=1,
            created_at=now, last_seen_at=now,
            expires_at=now + timedelta(hours=1), device_label="Stage laptop",
        )
        session.add(auth_session)
        await session.commit()

        yield {
            "session": session,
            "teams": (first_team, second_team),
            "user": user,
            "question": question,
            "auth_session": auth_session,
            "released_controllers": released_controllers,
            "frozen": frozen,
        }

    await engine.dispose()


async def configure_phase(session, question, phase):
    rnd = await get_round(session)
    now = datetime.now(timezone.utc)
    rnd.phase = phase
    rnd.current_question_id = question.id if phase in {"topic", "live"} else None
    rnd.topic_shown_at = now if phase in {"topic", "live"} else None
    rnd.live_started_at = now if phase == "live" else None
    rnd.global_deadline_at = now + timedelta(seconds=150) if phase == "live" else None
    rnd.closed_at = None
    await session.commit()
    return rnd


def restart_body(rnd, confirmation="RESTART ROUND"):
    return RestartIn(
        confirmation=confirmation,
        expected_round_version=rnd.version,
    )


@pytest.mark.parametrize("phase", ["idle", "topic"])
async def test_confirmed_restart_succeeds_from_non_live_phase(restart_ctx, phase):
    session = restart_ctx["session"]
    rnd = await configure_phase(session, restart_ctx["question"], phase)
    old_version = rnd.version

    response = await admin_routes.api_restart(
        body=restart_body(rnd), session=session, admin=admin_principal()
    )

    assert response == {"ok": True}
    assert rnd.phase == "idle"
    assert rnd.version == old_version + 1
    assert rnd.current_question_id is None
    assert rnd.topic_shown_at is None
    assert rnd.live_started_at is None
    assert rnd.global_deadline_at is None
    assert rnd.closed_at is None
    assert rnd.reveal_cards is False


async def test_live_restart_is_rejected_without_any_mutation(restart_ctx):
    session = restart_ctx["session"]
    first_team = restart_ctx["teams"][0]
    rnd = await configure_phase(session, restart_ctx["question"], "live")
    row = TeamQuestion(
        team_id=first_team.id, question_id=restart_ctx["question"].id,
        locked=True, answer={"option_id": "b"}, deadline_at=rnd.global_deadline_at,
    )
    session.add(row)
    await session.commit()
    row_id = row.id
    team_id = first_team.id
    original_version = rnd.version
    original_score = first_team.score

    with pytest.raises(HTTPException) as rejected:
        await admin_routes.api_restart(
            body=restart_body(rnd), session=session, admin=admin_principal()
        )

    assert rejected.value.status_code == 409
    assert rejected.value.detail["code"] == "live_restart_forbidden"
    persisted_round = await session.get(RoundState, 1)
    persisted_team = await session.get(Team, team_id)
    assert persisted_round.phase == "live"
    assert persisted_round.version == original_version
    assert persisted_team.score == original_score
    assert await session.get(TeamQuestion, row_id) is not None
    assert await session.scalar(select(func.count()).select_from(AuditEvent)) == 0
    assert restart_ctx["released_controllers"] == []


async def test_wrong_confirmation_is_rejected_without_any_mutation(restart_ctx):
    session = restart_ctx["session"]
    rnd = await configure_phase(session, restart_ctx["question"], "topic")
    first_team = restart_ctx["teams"][0]
    original_version = rnd.version

    with pytest.raises(HTTPException) as rejected:
        await admin_routes.api_restart(
            body=restart_body(rnd, "restart round"),
            session=session,
            admin=admin_principal(),
        )

    assert rejected.value.status_code == 400
    assert rejected.value.detail["code"] == "invalid_confirmation"
    assert rnd.phase == "topic"
    assert rnd.version == original_version
    assert first_team.score == 4321
    assert await session.scalar(select(func.count()).select_from(AuditEvent)) == 0


async def test_stale_version_and_repeated_request_are_rejected(restart_ctx):
    session = restart_ctx["session"]
    rnd = await configure_phase(session, restart_ctx["question"], "topic")
    first_team = restart_ctx["teams"][0]
    row = TeamQuestion(
        team_id=first_team.id,
        question_id=restart_ctx["question"].id,
        peek=True,
    )
    session.add(row)
    await session.commit()
    row_id = row.id
    team_id = first_team.id
    stale_body = RestartIn(
        confirmation="RESTART ROUND",
        expected_round_version=rnd.version + 1,
    )

    with pytest.raises(HTTPException) as stale:
        await admin_routes.api_restart(
            body=stale_body, session=session, admin=admin_principal()
        )
    assert stale.value.status_code == 409
    assert stale.value.detail["code"] == "stale_round_version"
    rnd = await session.get(RoundState, 1)
    first_team = await session.get(Team, team_id)
    assert rnd.phase == "topic"
    assert first_team.score == 4321
    assert await session.get(TeamQuestion, row_id) is not None
    assert await session.scalar(select(func.count()).select_from(AuditEvent)) == 0

    valid_body = restart_body(rnd)
    assert await admin_routes.api_restart(
        body=valid_body, session=session, admin=admin_principal()
    ) == {"ok": True}
    with pytest.raises(HTTPException) as repeated:
        await admin_routes.api_restart(
            body=valid_body, session=session, admin=admin_principal()
        )
    assert repeated.value.status_code == 409
    assert repeated.value.detail["code"] == "stale_round_version"
    assert await session.scalar(
        select(func.count()).select_from(AuditEvent).where(
            AuditEvent.kind == "restart_round"
        )
    ) == 1


async def test_restart_audit_captures_metadata_cancels_swaps_and_preserves_sessions(
    restart_ctx,
):
    session = restart_ctx["session"]
    first_team, second_team = restart_ctx["teams"]
    question = restart_ctx["question"]
    auth_session = restart_ctx["auth_session"]
    rnd = await configure_phase(session, question, "topic")
    previous_version = rnd.version
    scores_before = {str(first_team.id): 4321, str(second_team.id): 6789}
    row = TeamQuestion(
        team_id=first_team.id, question_id=question.id, bounty=True,
        freeze=True, peek=True, swap_used=True, locked=True,
        answer={"option_id": "b"}, points_delta=6000, graded=True,
        is_correct=True,
    )
    pending_swap = SwapEvent(
        question_id=question.id, source_team_id=first_team.id,
        target_team_id=second_team.id, applied=False,
    )
    old_audit = AuditEvent(actor="admin", kind="before_restart", payload={"kept": True})
    session.add_all([row, pending_swap, old_audit])
    await session.commit()

    await admin_routes.api_restart(
        body=restart_body(rnd), session=session, admin=admin_principal()
    )

    assert await session.scalar(select(func.count()).select_from(TeamQuestion)) == 0
    await session.refresh(first_team)
    await session.refresh(second_team)
    assert first_team.score == get_settings().initial_team_score
    assert second_team.score == get_settings().initial_team_score
    assert first_team.swap_available is True
    assert second_team.swap_available is True

    preserved_session = await session.get(AuthSession, auth_session.id)
    assert preserved_session is not None
    assert preserved_session.revoked_at is None
    assert preserved_session.slot == 1

    restart_audit = (
        await session.execute(
            select(AuditEvent).where(AuditEvent.kind == "restart_round")
        )
    ).scalar_one()
    assert restart_audit.payload["previous_phase"] == "topic"
    assert restart_audit.payload["previous_round_version"] == previous_version
    assert restart_audit.payload["team_scores_before"] == scores_before
    assert restart_audit.payload["team_question_rows_deleted"] == 1
    assert restart_audit.payload["emergency_override"] is False
    assert restart_audit.payload["timestamp"]
    assert restart_audit.payload["cancelled_pending_swap_count"] == 1
    assert restart_audit.payload["cancelled_pending_swap_ids"] == [str(pending_swap.id)]

    preserved_swap = await session.get(SwapEvent, pending_swap.id)
    assert preserved_swap is not None
    assert preserved_swap.applied is True


async def test_restart_replaces_stale_frozen_board_and_fanout_uses_reset_scores(
    restart_ctx, monkeypatch
):
    session = restart_ctx["session"]
    rnd = await configure_phase(session, restart_ctx["question"], "idle")
    fanout_boards = []

    async def capture_fanout(session, extra_admin=None, public_leaderboard=None):
        fanout_boards.append(public_leaderboard)

    monkeypatch.setattr(admin_routes, "_fanout", capture_fanout)
    await admin_routes.api_restart(
        body=restart_body(rnd), session=session, admin=admin_principal()
    )

    reset_board = restart_ctx["frozen"]["value"]
    assert reset_board is not None
    assert {row["score"] for row in reset_board} == {get_settings().initial_team_score}
    assert all(row["id"] != "stale" for row in reset_board)
    assert fanout_boards == [reset_board]


async def test_controller_cleanup_failure_is_logged_and_does_not_undo_restart(
    restart_ctx, monkeypatch, caplog
):
    session = restart_ctx["session"]
    rnd = await configure_phase(session, restart_ctx["question"], "topic")

    async def fail_release(team_id, session_id=None, *, force=False):
        raise RuntimeError("controller redis unavailable")

    monkeypatch.setattr(admin_routes, "release_controller", fail_release)
    caplog.set_level(logging.ERROR, logger="app.ws")

    assert await admin_routes.api_restart(
        body=restart_body(rnd), session=session, admin=admin_principal()
    ) == {"ok": True}
    assert rnd.phase == "idle"
    assert all(
        team.score == get_settings().initial_team_score
        for team in restart_ctx["teams"]
    )
    assert "admin.restart.release_controller" in caplog.text


async def test_restart_endpoint_requires_admin_authentication(restart_ctx):
    session = restart_ctx["session"]
    rnd = await configure_phase(session, restart_ctx["question"], "topic")
    payload = restart_body(rnd).model_dump()
    app = FastAPI()
    app.include_router(admin_routes.router)

    async def same_session():
        yield session

    app.dependency_overrides[get_session] = same_session
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/api/v1/admin/round/restart", json=payload)
        team_token = create_token(
            sub=str(restart_ctx["user"].id), role="team",
            team_id=str(restart_ctx["teams"][0].id),
            session_id=str(restart_ctx["auth_session"].id),
        )
        team_response = await client.post(
            "/api/v1/admin/round/restart", json=payload,
            headers={"Authorization": f"Bearer {team_token}"},
        )

    assert response.status_code == 401
    assert team_response.status_code == 403
    assert rnd.phase == "topic"
    assert await session.scalar(
        select(func.count()).select_from(AuditEvent).where(
            AuditEvent.kind == "restart_round"
        )
    ) == 0
