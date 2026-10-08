import asyncio
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import jwt
import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import get_settings
from app.deps import Principal, current_principal
from app.models import AuthSession, Base, Team, User
from app.routers import admin as admin_routes
from app.routers import auth as auth_routes
from app.routers import play as play_routes
from app.routers import ws as ws_routes
from app.routers.auth import LoginIn
from app.security import create_token, decode_token, hash_password


@pytest.fixture
async def auth_ctx(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'auth.db'}",
        connect_args={"timeout": 10},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async with maker() as session:
        first_team = Team(id=uuid4(), name="Alpha", score=0)
        second_team = Team(id=uuid4(), name="Beta", score=0)
        first_user = User(
            id=uuid4(), username="alpha", password_hash=hash_password("pass123"),
            role="team", team=first_team,
        )
        second_user = User(
            id=uuid4(), username="beta", password_hash=hash_password("pass123"),
            role="team", team=second_team,
        )
        session.add_all([first_team, second_team, first_user, second_user])
        await session.commit()
        context = {
            "maker": maker,
            "team_id": first_team.id,
            "user_id": first_user.id,
            "other_team_id": second_team.id,
        }

    yield context
    await engine.dispose()


async def login_team(session, *, username="alpha", device_label=None):
    return await auth_routes.login(
        LoginIn(username=username, password="pass123", device_label=device_label),
        session=session,
        user_agent="pytest-client",
    )


async def principal_for(session, token):
    return await current_principal(authorization=f"Bearer {token}", session=session)


def admin_principal():
    return Principal(
        user_id=uuid4(), username="admin", role="admin", team_id=None,
        session_id=str(uuid4()),
    )


async def active_count(session, team_id):
    return await session.scalar(
        select(func.count()).select_from(AuthSession).where(
            AuthSession.team_id == team_id,
            AuthSession.revoked_at.is_(None),
            AuthSession.slot.is_not(None),
        )
    )


async def test_first_four_logins_succeed_and_fifth_is_rejected(auth_ctx):
    async with auth_ctx["maker"]() as session:
        logins = [
            await login_team(session, device_label=f"device-{number}")
            for number in range(1, 5)
        ]
        assert len({result["session_id"] for result in logins}) == 4
        assert await active_count(session, auth_ctx["team_id"]) == 4

        with pytest.raises(HTTPException) as rejected:
            await login_team(session, device_label="device-5")
        assert rejected.value.status_code == 409
        assert rejected.value.detail["code"] == "session_limit"
        assert await active_count(session, auth_ctx["team_id"]) == 4


async def test_revoking_one_session_allows_a_replacement_login(auth_ctx):
    async with auth_ctx["maker"]() as session:
        logins = [await login_team(session) for _ in range(4)]
        revoked_id = UUID(logins[0]["session_id"])
        result = await admin_routes.revoke_team_session(
            auth_ctx["team_id"], revoked_id, session, admin_principal()
        )
        replacement = await login_team(session, device_label="replacement")

        assert result == {"ok": True, "session_id": str(revoked_id)}
        assert replacement["session_id"] != str(revoked_id)
        assert await active_count(session, auth_ctx["team_id"]) == 4


async def test_revoke_all_invalidates_every_existing_token(auth_ctx):
    async with auth_ctx["maker"]() as session:
        logins = [await login_team(session) for _ in range(4)]
        result = await admin_routes.revoke_all_team_sessions(
            auth_ctx["team_id"], session, admin_principal()
        )

        assert result == {"ok": True, "revoked_count": 4}
        assert await active_count(session, auth_ctx["team_id"]) == 0
        for logged_in in logins:
            with pytest.raises(HTTPException) as rejected:
                await principal_for(session, logged_in["access_token"])
            assert rejected.value.status_code == 401
            assert rejected.value.detail == "Session inactive"


async def test_revoked_http_token_is_rejected(auth_ctx):
    async with auth_ctx["maker"]() as session:
        logged_in = await login_team(session)
        await admin_routes.revoke_team_session(
            auth_ctx["team_id"], UUID(logged_in["session_id"]), session,
            admin_principal(),
        )
        with pytest.raises(HTTPException) as rejected:
            await principal_for(session, logged_in["access_token"])
        assert rejected.value.status_code == 401
        assert rejected.value.detail == "Session inactive"


class RejectedWebSocket:
    def __init__(self, token):
        self.query_params = {"token": token}
        self.accepted = False
        self.closed_with = None

    async def accept(self):
        self.accepted = True

    async def close(self, code):
        self.closed_with = code


class ConnectedWebSocket(RejectedWebSocket):
    def __init__(self, token):
        super().__init__(token)
        self.sent = []

    async def send_json(self, payload):
        self.sent.append(payload)

    async def receive_text(self):
        return "heartbeat"


async def test_revoked_websocket_token_is_rejected(auth_ctx, monkeypatch):
    async with auth_ctx["maker"]() as session:
        logged_in = await login_team(session)
        await admin_routes.revoke_team_session(
            auth_ctx["team_id"], UUID(logged_in["session_id"]), session,
            admin_principal(),
        )

    monkeypatch.setattr(ws_routes, "SessionLocal", auth_ctx["maker"])
    websocket = RejectedWebSocket(logged_in["access_token"])
    await ws_routes.websocket_endpoint(websocket)
    assert websocket.accepted is False
    assert websocket.closed_with == 4401


async def test_connected_websocket_revalidates_and_closes_after_revocation(
    auth_ctx, monkeypatch
):
    principal = Principal(
        user_id=auth_ctx["user_id"], username="alpha", role="team",
        team_id=auth_ctx["team_id"], session_id=str(uuid4()),
    )
    validations = 0

    async def validate(token, session):
        nonlocal validations
        validations += 1
        if validations == 1:
            return principal
        raise HTTPException(401, "Session inactive")

    monkeypatch.setattr(ws_routes, "SessionLocal", auth_ctx["maker"])
    monkeypatch.setattr(ws_routes, "principal_from_token", validate)
    websocket = ConnectedWebSocket("token")
    await ws_routes.websocket_endpoint(websocket)

    assert websocket.accepted is True
    assert websocket.closed_with == 4401
    assert validations == 2


async def test_copied_token_is_the_same_session_not_a_new_login(auth_ctx):
    async with auth_ctx["maker"]() as session:
        logged_in = await login_team(session)
        first = await principal_for(session, logged_in["access_token"])
        copied = await principal_for(session, logged_in["access_token"])
        assert first == copied
        assert first.session_id == logged_in["session_id"]
        assert await active_count(session, auth_ctx["team_id"]) == 1


async def test_simultaneous_login_attempts_cannot_exceed_four(auth_ctx):
    async with auth_ctx["maker"]() as session:
        for _ in range(3):
            await login_team(session)

    async def attempt_login(label):
        async with auth_ctx["maker"]() as session:
            try:
                return await login_team(session, device_label=label)
            except HTTPException as exc:
                return exc

    results = await asyncio.gather(attempt_login("racer-a"), attempt_login("racer-b"))
    assert sum(isinstance(result, dict) for result in results) == 1
    rejection = next(result for result in results if isinstance(result, HTTPException))
    assert rejection.status_code == 409
    assert rejection.detail["code"] == "session_limit"
    async with auth_ctx["maker"]() as session:
        assert await active_count(session, auth_ctx["team_id"]) == 4


class FakeControllerRedis:
    def __init__(self):
        self.store = {}

    async def set(self, key, value, *, ex, nx):
        if nx and key in self.store:
            return False
        self.store[key] = value
        return True

    async def get(self, key):
        return self.store.get(key)

    async def expire(self, key, ttl):
        return key in self.store


async def test_controller_remains_one_per_team_and_independent_of_sessions(
    auth_ctx, monkeypatch
):
    async with auth_ctx["maker"]() as session:
        first_login = await login_team(session)
        second_login = await login_team(session)
        first = await principal_for(session, first_login["access_token"])
        second = await principal_for(session, second_login["access_token"])
    fake_redis = FakeControllerRedis()

    async def fake_get_redis():
        return fake_redis

    async def no_broadcast(channel, payload):
        return None

    monkeypatch.setattr("app.redisutil.get_redis", fake_get_redis)
    monkeypatch.setattr(play_routes.hub, "broadcast", no_broadcast)
    assert await play_routes.claim(principal=first) == {"ok": True, "controller": True}
    with pytest.raises(HTTPException) as conflict:
        await play_routes.claim(principal=second)
    assert conflict.value.status_code == 409
    assert conflict.value.detail["code"] == "controller_taken"

    async with auth_ctx["maker"]() as session:
        assert await active_count(session, auth_ctx["team_id"]) == 2
        assert (await principal_for(session, second_login["access_token"])) == second


async def test_expired_jwt_still_fails_normally(auth_ctx):
    settings = get_settings()
    now = datetime.now(timezone.utc)
    expired_token = jwt.encode(
        {
            "sub": str(auth_ctx["user_id"]), "role": "team",
            "team_id": str(auth_ctx["team_id"]), "sid": str(uuid4()),
            "iat": int((now - timedelta(hours=2)).timestamp()),
            "exp": int((now - timedelta(hours=1)).timestamp()),
        },
        settings.secret_key,
        algorithm=settings.jwt_algorithm,
    )

    async with auth_ctx["maker"]() as session:
        with pytest.raises(HTTPException) as expired:
            await principal_for(session, expired_token)
    assert expired.value.status_code == 401
    assert expired.value.detail == "Invalid token"


async def test_logout_revokes_the_current_session(auth_ctx):
    async with auth_ctx["maker"]() as session:
        logged_in = await login_team(session)
        principal = await principal_for(session, logged_in["access_token"])
        assert await auth_routes.logout(principal, session) == {"ok": True}
        with pytest.raises(HTTPException) as rejected:
            await principal_for(session, logged_in["access_token"])
        assert rejected.value.detail == "Session inactive"
        assert await active_count(session, auth_ctx["team_id"]) == 0


async def test_team_scoping_prevents_cross_team_session_revocation(auth_ctx):
    async with auth_ctx["maker"]() as session:
        alpha_login = await login_team(session)
        beta_login = await login_team(session, username="beta")
        with pytest.raises(HTTPException) as not_found:
            await admin_routes.revoke_team_session(
                auth_ctx["team_id"], UUID(beta_login["session_id"]), session,
                admin_principal(),
            )
        assert not_found.value.status_code == 404
        assert (await principal_for(session, beta_login["access_token"])).team_id == auth_ctx["other_team_id"]
        assert (await principal_for(session, alpha_login["access_token"])).team_id == auth_ctx["team_id"]


async def test_token_cannot_claim_another_teams_persisted_session(auth_ctx):
    async with auth_ctx["maker"]() as session:
        beta_login = await login_team(session, username="beta")
        forged = create_token(
            sub=str(auth_ctx["user_id"]),
            role="team",
            team_id=str(auth_ctx["team_id"]),
            session_id=beta_login["session_id"],
        )

        with pytest.raises(HTTPException) as rejected:
            await principal_for(session, forged)
        assert rejected.value.status_code == 401
        assert rejected.value.detail == "Session inactive"


async def test_last_seen_updates_are_throttled(auth_ctx):
    async with auth_ctx["maker"]() as session:
        logged_in = await login_team(session)
        row = await session.get(AuthSession, UUID(logged_in["session_id"]))
        row.last_seen_at = datetime.now(timezone.utc) - timedelta(minutes=2)
        await session.commit()

        await principal_for(session, logged_in["access_token"])
        first_update = row.last_seen_at
        await principal_for(session, logged_in["access_token"])

        assert first_update > datetime.now(timezone.utc) - timedelta(seconds=5)
        assert row.last_seen_at == first_update


async def test_admin_inventory_includes_metadata_and_controller(auth_ctx, monkeypatch):
    async with auth_ctx["maker"]() as session:
        logged_in = await login_team(session, device_label="Stage laptop")

        async def current_controller(team_id):
            assert team_id == str(auth_ctx["team_id"])
            return logged_in["session_id"]

        monkeypatch.setattr(admin_routes, "get_controller", current_controller)
        result = await admin_routes.list_team_sessions(
            auth_ctx["team_id"], session, admin_principal()
        )

    row = result["sessions"][0]
    assert result["active_count"] == 1
    assert result["controller_session_id"] == logged_in["session_id"]
    assert row["session_id"] == logged_in["session_id"]
    assert row["device_label"] == "Stage laptop"
    assert row["user_agent"] == "pytest-client"
    assert row["active"] is True
    assert row["is_controller"] is True
    assert row["login_time"] and row["last_seen"]


async def test_login_token_sid_matches_persisted_session(auth_ctx):
    async with auth_ctx["maker"]() as session:
        logged_in = await login_team(session)
        claims = decode_token(logged_in["access_token"])
        stored = await session.get(AuthSession, UUID(logged_in["session_id"]))

    assert claims["sid"] == logged_in["session_id"]
    assert stored is not None
    assert stored.team_id == auth_ctx["team_id"]
