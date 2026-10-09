from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.deps import Principal
from app.engine import get_round
from app.models import AuditEvent, Base, PublishedLeaderboard, Question, Team
from app.routers import admin as admin_routes
from app.routers import play as play_routes


class RedisUnavailable(RuntimeError):
    pass


def admin_principal():
    return Principal(
        user_id=uuid4(),
        username="publisher",
        role="admin",
        team_id=None,
        session_id=str(uuid4()),
    )


def team_principal(team):
    return Principal(
        user_id=uuid4(),
        username="alpha",
        role="team",
        team_id=team.id,
        session_id=str(uuid4()),
    )


@pytest.fixture
async def published_ctx(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async with maker() as session:
        alpha = Team(id=uuid4(), name="Alpha", score=100)
        beta = Team(id=uuid4(), name="Beta", score=200)
        question = Question(
            id=uuid4(),
            index=4,
            topic="Durability",
            prompt="Where is the board stored?",
            qtype="mcq_single",
            options=[{"id": "pg", "text": "PostgreSQL"}],
            answer_key={"option_id": "pg"},
        )
        session.add_all([alpha, beta, question])
        await session.flush()
        rnd = await get_round(session)
        rnd.phase = "topic"
        rnd.current_question_id = question.id
        rnd.topic_shown_at = datetime.now(timezone.utc)
        rnd.version = 9
        await session.commit()

        cached = []
        broadcasts = []

        async def cache(rows):
            cached.append(rows)

        async def broadcast(channel, payload):
            broadcasts.append((channel, payload))

        monkeypatch.setattr("app.redisutil.set_frozen_leaderboard", cache)
        monkeypatch.setattr(admin_routes.hub, "broadcast", broadcast)

        yield {
            "session": session,
            "maker": maker,
            "alpha": alpha,
            "beta": beta,
            "question": question,
            "cached": cached,
            "broadcasts": broadcasts,
        }

    await engine.dispose()


async def test_publish_persists_exact_rows_context_audit_and_survives_new_session(
    published_ctx,
):
    session = published_ctx["session"]
    response = await admin_routes.api_publish_leaderboard(
        session=session, admin=admin_principal()
    )

    assert response == {"ok": True, "published": True}
    expected = [
        {"id": str(published_ctx["beta"].id), "name": "Beta", "score": 200, "rank": 1},
        {"id": str(published_ctx["alpha"].id), "name": "Alpha", "score": 100, "rank": 2},
    ]
    snapshot = await session.get(PublishedLeaderboard, 1)
    assert snapshot.rows == expected
    assert snapshot.published_by == "publisher"
    assert snapshot.round_version == 9
    assert snapshot.phase == "topic"
    assert snapshot.question_id == published_ctx["question"].id
    assert snapshot.question_index == 4
    assert snapshot.published_at is not None
    assert published_ctx["cached"] == [expected]
    assert published_ctx["broadcasts"][-1] == (
        admin_routes.PUBLIC,
        {"type": "leaderboard", "leaderboard": expected},
    )

    event = (
        await session.execute(
            select(AuditEvent).where(AuditEvent.kind == "leaderboard_publish")
        )
    ).scalar_one()
    assert event.actor == "publisher"
    assert event.question_id == published_ctx["question"].id
    assert event.payload["rows"] == expected
    assert event.payload["round_version"] == 9

    async with published_ctx["maker"]() as restarted_session:
        reloaded = await restarted_session.get(PublishedLeaderboard, 1)
        assert reloaded.rows == expected


@pytest.mark.parametrize("failure", ["cache", "broadcast"])
async def test_redis_failures_do_not_undo_durable_publication(
    published_ctx, monkeypatch, failure
):
    async def unavailable(*args, **kwargs):
        raise RedisUnavailable(failure)

    if failure == "cache":
        monkeypatch.setattr("app.redisutil.set_frozen_leaderboard", unavailable)
    else:
        monkeypatch.setattr(admin_routes.hub, "broadcast", unavailable)

    response = await admin_routes.api_publish_leaderboard(
        session=published_ctx["session"], admin=admin_principal()
    )

    assert response == {"ok": True, "published": True}
    snapshot = await published_ctx["session"].get(PublishedLeaderboard, 1)
    assert [row["name"] for row in snapshot.rows] == ["Beta", "Alpha"]


async def test_player_uses_durable_board_despite_live_changes_and_redis_outage(
    published_ctx, monkeypatch
):
    session = published_ctx["session"]
    await admin_routes.api_publish_leaderboard(
        session=session, admin=admin_principal()
    )
    original_rows = (await session.get(PublishedLeaderboard, 1)).rows

    published_ctx["alpha"].score = 9999
    published_ctx["beta"].score = -100
    await session.commit()

    async def redis_down(*args, **kwargs):
        raise RedisUnavailable("stale or unavailable Redis must not decide standings")

    monkeypatch.setattr(play_routes, "get_controller", redis_down)
    monkeypatch.setattr(play_routes, "heartbeat_controller", redis_down)
    monkeypatch.setattr("app.redisutil.get_frozen_leaderboard", redis_down)

    player = await play_routes.state(session, team_principal(published_ctx["alpha"]))
    admin = await admin_routes.api_round(session, admin_principal())

    assert player["leaderboard"] == original_rows
    assert admin["leaderboard"][0]["name"] == "Alpha"
    assert admin["leaderboard"][0]["score"] == 9999


async def test_no_publication_returns_empty_and_republish_replaces_snapshot(
    published_ctx, monkeypatch
):
    session = published_ctx["session"]

    async def no_controller(team_id):
        return None

    monkeypatch.setattr(play_routes, "get_controller", no_controller)
    before = await play_routes.state(session, team_principal(published_ctx["alpha"]))
    assert before["leaderboard"] == []

    await admin_routes.api_publish_leaderboard(session=session, admin=admin_principal())
    published_ctx["alpha"].score = 300
    await session.commit()
    await admin_routes.api_publish_leaderboard(session=session, admin=admin_principal())

    after = await play_routes.state(session, team_principal(published_ctx["alpha"]))
    assert after["leaderboard"] == [
        {"id": str(published_ctx["alpha"].id), "name": "Alpha", "score": 300, "rank": 1},
        {"id": str(published_ctx["beta"].id), "name": "Beta", "score": 200, "rank": 2},
    ]
    assert len((await session.execute(select(PublishedLeaderboard))).scalars().all()) == 1


async def test_fanout_gives_players_durable_rows_and_admin_live_rows(published_ctx):
    session = published_ctx["session"]
    await admin_routes.api_publish_leaderboard(session=session, admin=admin_principal())
    durable_rows = (await session.get(PublishedLeaderboard, 1)).rows
    published_ctx["alpha"].score = 5000
    await session.commit()
    published_ctx["broadcasts"].clear()

    await admin_routes._fanout(session)

    public_payload = next(
        payload
        for channel, payload in published_ctx["broadcasts"]
        if channel == admin_routes.PUBLIC and payload.get("type") == "state"
    )
    admin_payload = next(
        payload
        for channel, payload in published_ctx["broadcasts"]
        if channel == admin_routes.ADMIN and payload.get("type") == "admin_state"
    )
    team_payloads = [
        payload
        for channel, payload in published_ctx["broadcasts"]
        if channel.startswith("ch:team:") and payload.get("type") == "state_tick"
    ]
    assert public_payload["leaderboard"] == durable_rows
    assert all(payload["leaderboard"] == durable_rows for payload in team_payloads)
    assert admin_payload["leaderboard"][0]["name"] == "Alpha"
    assert admin_payload["leaderboard"][0]["score"] == 5000
