import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import get_session
from app.models import (
    AuditEvent,
    AuthSession,
    Base,
    PublishedLeaderboard,
    Question,
    RoundState,
    SwapEvent,
    Team,
    TeamQuestion,
    User,
)
from app.routers import admin as admin_routes
from app.security import create_token, hash_password


@pytest.fixture
async def export_ctx(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'export.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    now = datetime.now(timezone.utc)
    async with maker() as session:
        team = Team(id=uuid4(), name="Alpha", score=4321, swap_available=False)
        target = Team(id=uuid4(), name="Beta", score=2345)
        admin_user = User(
            id=uuid4(),
            username="admin-export",
            password_hash=hash_password("admin-password-never-export"),
            role="admin",
        )
        team_user = User(
            id=uuid4(),
            username="alpha-export",
            password_hash=hash_password("team-password-never-export"),
            role="team",
            team=team,
        )
        question = Question(
            id=uuid4(),
            index=8,
            topic="Recovery",
            prompt="Can this event be reconstructed?",
            qtype="mcq_single",
            options=[{"id": "yes", "text": "Yes"}],
            answer_key={"option_id": "yes"},
            hint="Inspect PostgreSQL",
            media_url="/static/uploads/recovery.png",
        )
        session.add_all([team, target, admin_user, team_user, question])
        await session.flush()

        admin_sid = uuid4()
        team_sid = uuid4()
        session.add_all(
            [
                RoundState(
                    id=1,
                    phase="live",
                    current_question_id=question.id,
                    topic_shown_at=now - timedelta(seconds=50),
                    live_started_at=now - timedelta(seconds=5),
                    global_deadline_at=now + timedelta(seconds=145),
                    reveal_cards=True,
                    version=7,
                ),
                PublishedLeaderboard(
                    id=1,
                    published_at=now,
                    published_by="admin-export",
                    round_version=7,
                    phase="live",
                    question_id=question.id,
                    question_index=8,
                    rows=[
                        {
                            "id": str(team.id),
                            "name": "Alpha",
                            "score": 4000,
                            "rank": 1,
                        }
                    ],
                    card_usage_rows=[
                        {
                            "team_id": str(team.id),
                            "team_name": "Alpha",
                            "bounty_used": True,
                            "freeze_used": True,
                            "peek_used": True,
                            "swap_used": True,
                        }
                    ],
                    published_solution={
                        "question_index": 8,
                        "type": "mcq_single",
                        "correct_option_ids": ["yes"],
                        "hint": "Inspect PostgreSQL",
                    },
                ),
                TeamQuestion(
                    id=uuid4(),
                    team_id=team.id,
                    question_id=question.id,
                    bounty=True,
                    freeze=True,
                    peek=True,
                    swap_used=True,
                    freeze_elapsed_ms=3000,
                    locked=True,
                    locked_elapsed_ms=4000,
                    deadline_at=now + timedelta(seconds=175),
                    answer={"option_id": "yes"},
                    kingslayer_eligible=True,
                    points_delta=6000,
                    graded=True,
                    is_correct=True,
                    admin_override=True,
                ),
                SwapEvent(
                    id=uuid4(),
                    question_id=question.id,
                    source_team_id=team.id,
                    target_team_id=target.id,
                    applied=True,
                ),
                AuthSession(
                    id=admin_sid,
                    user_id=admin_user.id,
                    team_id=None,
                    slot=None,
                    created_at=now,
                    last_seen_at=now,
                    expires_at=now + timedelta(hours=1),
                    device_label="admin laptop",
                    user_agent="pytest-admin",
                ),
                AuthSession(
                    id=team_sid,
                    user_id=team_user.id,
                    team_id=team.id,
                    slot=1,
                    created_at=now,
                    last_seen_at=now,
                    expires_at=now + timedelta(hours=1),
                    device_label="team tablet",
                    user_agent="pytest-team",
                ),
            ]
        )
        session.add_all(
            [
                AuditEvent(
                    actor="seed",
                    team_id=team.id,
                    question_id=question.id,
                    kind="event",
                    payload={"number": number},
                )
                for number in range(501)
            ]
        )
        await session.commit()

        snapshot = {
            "round": (await session.get(RoundState, 1)).version,
            "scores": {
                str(row.id): row.score
                for row in (await session.execute(select(Team))).scalars().all()
            },
            "team_question_count": await session.scalar(
                select(func.count()).select_from(TeamQuestion)
            ),
            "audit_count": await session.scalar(
                select(func.count()).select_from(AuditEvent)
            ),
        }

    async def session_dependency():
        async with maker() as session:
            yield session

    app = FastAPI()
    app.include_router(admin_routes.router)
    app.dependency_overrides[get_session] = session_dependency
    app.dependency_overrides[admin_routes.get_export_session] = session_dependency

    controller_sid = str(team_sid)

    async def controller(team_id):
        return controller_sid if team_id == str(team.id) else None

    async def frozen_board():
        return [{"id": str(team.id), "name": team.name, "score": 4000, "rank": 1}]

    monkeypatch.setattr(admin_routes, "get_controller", controller)
    monkeypatch.setattr("app.redisutil.get_frozen_leaderboard", frozen_board)

    admin_token = create_token(
        sub=str(admin_user.id),
        role="admin",
        team_id=None,
        session_id=str(admin_sid),
        issued_at=now,
        expires_at=now + timedelta(hours=1),
    )
    team_token = create_token(
        sub=str(team_user.id),
        role="team",
        team_id=str(team.id),
        session_id=str(team_sid),
        issued_at=now,
        expires_at=now + timedelta(hours=1),
    )

    yield {
        "app": app,
        "maker": maker,
        "admin_token": admin_token,
        "team_token": team_token,
        "team": team,
        "target": target,
        "question": question,
        "controller_sid": controller_sid,
        "snapshot": snapshot,
    }
    await engine.dispose()


async def download(ctx, token):
    async with AsyncClient(
        transport=ASGITransport(app=ctx["app"]), base_url="http://test"
    ) as client:
        return await client.get(
            "/api/v1/admin/export/event",
            headers={"Authorization": f"Bearer {token}"},
        )


async def test_admin_download_contains_complete_safe_snapshot_without_mutation(export_ctx):
    response = await download(export_ctx, export_ctx["admin_token"])

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.headers["content-disposition"].startswith(
        'attachment; filename="levelup-event-'
    )
    payload = response.json()
    assert set(payload) == {
        "schema_version", "exported_at", "application", "summary", "postgres", "redis"
    }
    assert payload["schema_version"] == 1
    assert payload["application"] == "levelup"
    assert payload["summary"] == {
        "current_phase": "live",
        "current_round_version": 7,
        "current_question": {
            "id": str(export_ctx["question"].id),
            "index": 8,
            "topic": "Recovery",
        },
    }

    postgres = payload["postgres"]
    assert len(postgres["round_states"]) == 1
    assert {row["name"] for row in postgres["teams"]} == {"Alpha", "Beta"}
    assert len(postgres["questions"]) == 1
    assert postgres["questions"][0]["answer_key"] == {"option_id": "yes"}
    assert len(postgres["team_questions"]) == 1
    assert postgres["team_questions"][0]["freeze_elapsed_ms"] == 3000
    assert len(postgres["swap_events"]) == 1
    assert len(postgres["audit_events"]) == 501
    assert len(postgres["auth_sessions"]) == 2
    assert postgres["published_leaderboard"]["published_by"] == "admin-export"
    assert postgres["published_leaderboard"]["rows"][0]["score"] == 4000
    assert postgres["published_leaderboard"]["card_usage_rows"][0]["freeze_used"] is True
    assert postgres["published_leaderboard"]["published_solution"] == {
        "question_index": 8,
        "type": "mcq_single",
        "correct_option_ids": ["yes"],
        "hint": "Inspect PostgreSQL",
    }
    assert postgres["auth_sessions"][1]["device_label"] in {
        "admin laptop", "team tablet"
    }

    serialized = json.dumps(payload)
    assert "password_hash" not in serialized
    assert "admin-password-never-export" not in serialized
    assert "team-password-never-export" not in serialized
    assert export_ctx["admin_token"] not in serialized
    assert "access_token" not in serialized
    assert "secret_key" not in serialized

    assert payload["redis"]["available"] is True
    assert payload["redis"]["controllers"][str(export_ctx["team"].id)] == export_ctx["controller_sid"]
    assert payload["redis"]["published_leaderboard"][0]["score"] == 4000

    async with export_ctx["maker"]() as session:
        assert (await session.get(RoundState, 1)).version == export_ctx["snapshot"]["round"]
        scores = {
            str(row.id): row.score
            for row in (await session.execute(select(Team))).scalars().all()
        }
        assert scores == export_ctx["snapshot"]["scores"]
        assert await session.scalar(select(func.count()).select_from(TeamQuestion)) == export_ctx["snapshot"]["team_question_count"]
        assert await session.scalar(select(func.count()).select_from(AuditEvent)) == export_ctx["snapshot"]["audit_count"]


async def test_non_admin_cannot_download_export(export_ctx):
    response = await download(export_ctx, export_ctx["team_token"])
    assert response.status_code == 403


async def test_redis_failure_does_not_fail_postgres_export(export_ctx, monkeypatch):
    async def unavailable(team_id):
        raise RuntimeError("redis://credentials-must-not-leak")

    monkeypatch.setattr(admin_routes, "get_controller", unavailable)
    response = await download(export_ctx, export_ctx["admin_token"])

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["postgres"]["audit_events"]) == 501
    assert payload["redis"] == {
        "available": False,
        "controllers": None,
        "published_leaderboard": None,
        "error": "unavailable",
    }
    assert "credentials-must-not-leak" not in response.text
