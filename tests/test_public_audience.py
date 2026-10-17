import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import get_session
from app.deps import Principal
from app.engine import get_round, restart_round
from app.models import Base, PublishedLeaderboard, Question, Team, TeamQuestion, SwapEvent
from app.routers import admin as admin_routes
from app.routers import public as public_routes
from app.routers import play as play_routes


def admin_principal() -> Principal:
    return Principal(
        user_id=uuid4(), username="publisher", role="admin", team_id=None,
        session_id=str(uuid4()),
    )


@pytest.fixture
async def audience_ctx(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async with maker() as session:
        alpha = Team(id=uuid4(), name="Alpha", score=100)
        beta = Team(id=uuid4(), name="Beta", score=200)
        question = Question(
            id=uuid4(), index=4, topic="Safety", prompt="What is public?",
            qtype="mcq_single", media_url="/question.png",
            options=[{
                "id": "safe", "text": "Whitelisted data", "media_url": "/option.png",
                "correct": True, "answer": "secret", "internal_note": "never expose",
            }],
            answer_key={"option_id": "safe"}, hint="Private hint",
        )
        session.add_all([alpha, beta, question])
        await session.flush()
        rnd = await get_round(session)
        rnd.phase = "topic"
        rnd.current_question_id = question.id
        rnd.topic_shown_at = datetime.now(timezone.utc)
        session.add_all([
            TeamQuestion(
                team_id=alpha.id, question_id=question.id,
                bounty=True, freeze=False, peek=True, swap_used=False,
                answer={"option_id": "safe"}, locked=True,
            ),
            TeamQuestion(
                team_id=beta.id, question_id=question.id,
                bounty=False, freeze=True, peek=False, swap_used=True,
                freeze_elapsed_ms=1000, points_delta=999, graded=True,
                is_correct=True, kingslayer_eligible=True,
            ),
            SwapEvent(
                question_id=question.id,
                source_team_id=beta.id,
                target_team_id=alpha.id,
                applied=False,
            )
        ])
        await session.commit()

    async def no_cache(_rows):
        return None

    async def no_broadcast(_channel, _payload):
        return None

    monkeypatch.setattr("app.redisutil.set_frozen_leaderboard", no_cache)
    monkeypatch.setattr(admin_routes.hub, "broadcast", no_broadcast)

    async def session_dependency():
        async with maker() as session:
            yield session

    app = FastAPI()
    app.include_router(public_routes.router)
    app.dependency_overrides[get_session] = session_dependency
    yield {
        "app": app, "maker": maker, "alpha": alpha, "beta": beta,
        "question": question,
    }
    await engine.dispose()


async def audience_response(ctx, headers=None):
    async with AsyncClient(
        transport=ASGITransport(app=ctx["app"]), base_url="http://test"
    ) as client:
        return await client.get("/api/v1/public/audience", headers=headers)


async def test_public_endpoint_requires_no_auth_and_ignores_invalid_jwt(audience_ctx):
    without_auth = await audience_response(audience_ctx)
    invalid_auth = await audience_response(
        audience_ctx, {"Authorization": "Bearer definitely-invalid"}
    )

    assert without_auth.status_code == 200
    assert invalid_auth.status_code == 200
    assert set(without_auth.json()) == {
        "phase", "paused", "paused_at", "server_now", "global_deadline_at",
        "question", "publication"
    }


@pytest.mark.parametrize("phase", ["topic", "live", "paused", "closed"])
async def test_question_visibility_and_strict_option_whitelist(audience_ctx, phase):
    async with audience_ctx["maker"]() as session:
        rnd = await get_round(session)
        rnd.phase = phase
        rnd.paused_at = datetime.now(timezone.utc) if phase == "paused" else None
        await session.commit()

    payload = (await audience_response(audience_ctx)).json()
    question = payload["question"]
    assert set(question) == {"index", "topic", "type", "prompt", "media_url", "options"}
    if phase == "topic":
        assert question["prompt"] is None
        assert question["media_url"] is None
        assert question["options"] is None
    else:
        assert question["prompt"] == "What is public?"
        assert question["media_url"] == "/question.png"
        assert question["options"] == [{
            "id": "safe", "text": "Whitelisted data", "media_url": "/option.png"
        }]
    assert payload["paused"] is (phase == "paused")
    assert (payload["paused_at"] is not None) is (phase == "paused")

    serialized = json.dumps(payload)
    for secret in [
        "answer_key", "Private hint", "internal_note", "never expose",
        "controller_session_id", "freeze_elapsed_ms", "points_delta",
        "is_correct", "kingslayer", "locked", "password_hash", "published_by",
    ]:
        assert secret not in serialized


async def test_idle_has_no_question_and_no_publication_has_empty_boards(audience_ctx):
    async with audience_ctx["maker"]() as session:
        rnd = await get_round(session)
        rnd.phase = "idle"
        rnd.current_question_id = None
        await session.commit()

    payload = (await audience_response(audience_ctx)).json()
    assert payload["question"] is None
    assert payload["global_deadline_at"] is None
    assert payload["publication"] == {
        "published_at": None, "phase": None, "question_index": None,
        "leaderboard": [], "card_usage": [], "solution": None,
    }


async def test_publish_freezes_ordered_scores_and_card_usage_until_republish(audience_ctx):
    async with audience_ctx["maker"]() as session:
        await admin_routes.api_publish_leaderboard(session=session, admin=admin_principal())

    first = (await audience_response(audience_ctx)).json()["publication"]
    assert first["solution"] == {
        "question_index": 4,
        "type": "mcq_single",
        "correct_option_ids": ["safe"],
        "hint": "Private hint",
    }
    assert [row["team_name"] for row in first["card_usage"]] == ["Beta", "Alpha"]
    assert first["leaderboard"] == [
        {"id": str(audience_ctx["beta"].id), "name": "Beta", "score": 200, "rank": 1},
        {"id": str(audience_ctx["alpha"].id), "name": "Alpha", "score": 100, "rank": 2},
    ]
    assert first["card_usage"] == [
        {
            "team_id": str(audience_ctx["beta"].id), 
            "team_name": "Beta",
            "bounty_used": False, 
            "freeze_used": True,
            "peek_used": False, 
            "swap_used": True,
            "swap_target_team_id": str(audience_ctx["alpha"].id),
            "swap_target_team_name": "Alpha",
        },
        {
            "team_id": str(audience_ctx["alpha"].id), 
            "team_name": "Alpha",
            "bounty_used": True, 
            "freeze_used": False,
            "peek_used": True, 
            "swap_used": False,
            "swap_target_team_id": None,
            "swap_target_team_name": None,
        },
    ]

    async with audience_ctx["maker"]() as session:
        alpha = await session.get(Team, audience_ctx["alpha"].id)
        alpha.score = 9999
        row = (
            await session.execute(
                select(TeamQuestion).where(
                    TeamQuestion.team_id == audience_ctx["alpha"].id,
                    TeamQuestion.question_id == audience_ctx["question"].id,
                )
            )
        ).scalar_one()
        row.freeze = True
        await session.commit()

    unchanged = (await audience_response(audience_ctx)).json()["publication"]
    assert unchanged["leaderboard"] == first["leaderboard"]
    assert unchanged["card_usage"] == first["card_usage"]

    async with audience_ctx["maker"]() as session:
        await admin_routes.api_publish_leaderboard(session=session, admin=admin_principal())
    replaced = (await audience_response(audience_ctx)).json()["publication"]
    assert replaced["leaderboard"][0]["name"] == "Alpha"
    assert replaced["leaderboard"][0]["score"] == 9999
    alpha_cards = next(
        row for row in replaced["card_usage"] if row["team_name"] == "Alpha"
    )
    assert alpha_cards["freeze_used"] is True


async def test_twenty_teams_remain_in_publication_admin_and_player_data(audience_ctx, monkeypatch):
    async with audience_ctx["maker"]() as session:
        session.add_all([
            Team(id=uuid4(), name=f"Extra {index:02d}", score=1000 - index)
            for index in range(18)
        ])
        await session.commit()
        await admin_routes.api_publish_leaderboard(session=session, admin=admin_principal())
        snapshot = await session.get(PublishedLeaderboard, 1)
        assert len(snapshot.rows) == 20
        assert len(snapshot.card_usage_rows) == 20

    public_payload = (await audience_response(audience_ctx)).json()
    assert len(public_payload["publication"]["leaderboard"]) == 20
    assert len(public_payload["publication"]["card_usage"]) == 20

    async def no_controller(_team_id):
        return None

    monkeypatch.setattr(play_routes, "get_controller", no_controller)
    async with audience_ctx["maker"]() as session:
        admin_payload = await admin_routes.api_round(session=session, _=admin_principal())
        player_payload = await play_routes.state(
            session=session,
            principal=Principal(
                user_id=uuid4(), username="alpha", role="team",
                team_id=audience_ctx["alpha"].id, session_id=str(uuid4()),
            ),
        )
    assert len(admin_payload["leaderboard"]) == 20
    assert len(player_payload["leaderboard"]) == 20


async def test_publish_without_question_has_empty_card_usage(audience_ctx):
    async with audience_ctx["maker"]() as session:
        rnd = await get_round(session)
        rnd.phase = "idle"
        rnd.current_question_id = None
        await session.commit()
        await admin_routes.api_publish_leaderboard(session=session, admin=admin_principal())
        snapshot = await session.get(PublishedLeaderboard, 1)
        assert snapshot.card_usage_rows == []
        assert snapshot.published_solution is None


async def test_later_question_solution_never_leaks_until_republish(audience_ctx):
    async with audience_ctx["maker"]() as session:
        await admin_routes.api_publish_leaderboard(session=session, admin=admin_principal())
        later = Question(
            id=uuid4(), index=5, topic="Next", prompt="Still private?",
            qtype="mcq_multi", options=[{"id": "x", "text": "X"}],
            answer_key={"option_ids": ["x", "y"], "internal": "do not publish"},
            hint="Next secret hint",
        )
        session.add(later)
        await session.flush()
        rnd = await get_round(session)
        rnd.current_question_id = later.id
        await session.commit()

    stale = (await audience_response(audience_ctx)).json()
    assert stale["question"]["index"] == 5
    assert stale["publication"]["solution"]["question_index"] == 4
    assert "Next secret hint" not in json.dumps(stale)

    async with audience_ctx["maker"]() as session:
        await admin_routes.api_publish_leaderboard(session=session, admin=admin_principal())
    replaced = (await audience_response(audience_ctx)).json()["publication"]["solution"]
    assert replaced == {
        "question_index": 5, "type": "mcq_multi",
        "correct_option_ids": ["x", "y"], "hint": "Next secret hint",
    }
    assert "internal" not in json.dumps(replaced)


async def test_audience_survives_new_session_redis_failure_and_restart_clear(
    audience_ctx, monkeypatch
):
    async with audience_ctx["maker"]() as session:
        await admin_routes.api_publish_leaderboard(session=session, admin=admin_principal())

    async def redis_down(*_args, **_kwargs):
        raise RuntimeError("redis unavailable")

    monkeypatch.setattr("app.redisutil.get_redis", redis_down)
    persisted = (await audience_response(audience_ctx)).json()["publication"]
    assert persisted["leaderboard"]
    assert persisted["card_usage"]

    async with audience_ctx["maker"]() as session:
        rnd = await get_round(session)
        rnd.phase = "closed"
        await session.commit()
        await restart_round(session, "admin", rnd.version)
        await session.commit()

    cleared = (await audience_response(audience_ctx)).json()
    assert cleared["question"] is None
    assert cleared["publication"]["leaderboard"] == []
    assert cleared["publication"]["card_usage"] == []


@pytest.mark.parametrize("phase", ["idle", "topic"])
async def test_non_running_phase_has_no_audience_deadline(audience_ctx, phase):
    async with audience_ctx["maker"]() as session:
        rnd = await get_round(session)
        rnd.phase = phase
        rnd.global_deadline_at = None
        await session.commit()

    payload = (await audience_response(audience_ctx)).json()
    assert payload["phase"] == phase
    assert payload["global_deadline_at"] is None


async def test_live_and_paused_expose_only_global_timer_state(audience_ctx):
    deadline = datetime.now(timezone.utc) + timedelta(seconds=149)
    paused_at = deadline - timedelta(seconds=83)
    async with audience_ctx["maker"]() as session:
        rnd = await get_round(session)
        rnd.phase = "live"
        rnd.global_deadline_at = deadline
        await session.commit()

    live = (await audience_response(audience_ctx)).json()
    assert datetime.fromisoformat(live["global_deadline_at"]) == deadline
    assert live["paused_at"] is None

    async with audience_ctx["maker"]() as session:
        rnd = await get_round(session)
        rnd.phase = "paused"
        rnd.paused_at = paused_at
        await session.commit()

    paused = (await audience_response(audience_ctx)).json()
    assert datetime.fromisoformat(paused["global_deadline_at"]) == deadline
    assert datetime.fromisoformat(paused["paused_at"]) == paused_at
    serialized = json.dumps(paused)
    assert "deadline_at" not in serialized.replace("global_deadline_at", "")


async def test_closed_retains_authoritative_deadline_for_zero_display(audience_ctx):
    deadline = datetime.now(timezone.utc) - timedelta(seconds=1)
    async with audience_ctx["maker"]() as session:
        rnd = await get_round(session)
        rnd.phase = "closed"
        rnd.global_deadline_at = deadline
        await session.commit()

    payload = (await audience_response(audience_ctx)).json()
    assert payload["phase"] == "closed"
    assert datetime.fromisoformat(payload["global_deadline_at"]) == deadline


async def test_next_question_replaces_previous_global_deadline(audience_ctx):
    first = datetime.now(timezone.utc) + timedelta(seconds=40)
    second = first + timedelta(seconds=110)
    async with audience_ctx["maker"]() as session:
        rnd = await get_round(session)
        rnd.phase = "live"
        rnd.global_deadline_at = first
        await session.commit()
    first_payload = (await audience_response(audience_ctx)).json()
    assert datetime.fromisoformat(first_payload["global_deadline_at"]) == first

    async with audience_ctx["maker"]() as session:
        rnd = await get_round(session)
        rnd.global_deadline_at = second
        await session.commit()
    second_payload = (await audience_response(audience_ctx)).json()
    assert datetime.fromisoformat(second_payload["global_deadline_at"]) == second
