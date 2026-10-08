from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import jwt
import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import get_settings
from app.deps import current_principal
from app.models import Base, Team, User
from app.routers import auth as auth_routes
from app.routers import play as play_routes
from app.routers.auth import LoginIn
from app.security import decode_token, hash_password


@pytest.fixture
async def auth_ctx():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async with maker() as session:
        team = Team(id=uuid4(), name="Alpha", score=0)
        user = User(
            id=uuid4(),
            username="alpha",
            password_hash=hash_password("pass123"),
            role="team",
            team=team,
        )
        session.add_all([team, user])
        await session.commit()
        yield session, team, user

    await engine.dispose()


async def login_team(session):
    return await auth_routes.login(
        LoginIn(username="alpha", password="pass123"),
        session=session,
    )


async def principal_for(session, token):
    return await current_principal(
        authorization=f"Bearer {token}",
        session=session,
    )


async def test_same_team_can_log_in_five_times_with_unique_untracked_sessions(auth_ctx):
    session, team, user = auth_ctx

    logins = [await login_team(session) for _ in range(5)]

    assert len({result["session_id"] for result in logins}) == 5
    assert len({result["access_token"] for result in logins}) == 5
    assert {result["team_id"] for result in logins} == {str(team.id)}
    assert "auth_sessions" not in Base.metadata.tables
    assert "sessions" not in Base.metadata.tables


async def test_same_token_can_be_reused_by_two_clients(auth_ctx):
    session, team, user = auth_ctx
    logged_in = await login_team(session)

    first_client = await principal_for(session, logged_in["access_token"])
    second_client = await principal_for(session, logged_in["access_token"])

    assert first_client == second_client
    assert first_client.session_id == logged_in["session_id"]


async def test_no_server_logout_exists_and_client_logout_does_not_revoke_token(auth_ctx):
    session, team, user = auth_ctx
    logged_in = await login_team(session)
    auth_paths = {route.path for route in auth_routes.router.routes}

    assert "/api/v1/auth/logout" not in auth_paths
    assert auth_paths == {"/api/v1/auth/login"}

    # Clearing browser localStorage does not communicate with the server.
    principal = await principal_for(session, logged_in["access_token"])
    assert principal.session_id == logged_in["session_id"]


async def test_tokens_expire_but_have_no_individual_revocation_check(auth_ctx):
    session, team, user = auth_ctx
    logged_in = await login_team(session)
    settings = get_settings()
    claims = decode_token(logged_in["access_token"])

    assert claims["exp"] - claims["iat"] == settings.access_token_expire_hours * 3600

    now = datetime.now(timezone.utc)
    expired_token = jwt.encode(
        {
            "sub": str(user.id),
            "role": user.role,
            "team_id": str(team.id),
            "sid": str(uuid4()),
            "iat": int((now - timedelta(hours=2)).timestamp()),
            "exp": int((now - timedelta(hours=1)).timestamp()),
        },
        settings.secret_key,
        algorithm=settings.jwt_algorithm,
    )

    with pytest.raises(HTTPException) as expired:
        await principal_for(session, expired_token)
    assert expired.value.status_code == 401
    assert expired.value.detail == "Invalid token"


class FakeControllerRedis:
    def __init__(self):
        self.store = {}
        self.expirations = []

    async def set(self, key, value, *, ex, nx):
        if nx and key in self.store:
            return False
        self.store[key] = value
        self.expirations.append((key, ex))
        return True

    async def get(self, key):
        return self.store.get(key)

    async def expire(self, key, ttl):
        self.expirations.append((key, ttl))
        return key in self.store


async def test_many_authenticated_sessions_still_allow_only_one_controller_sid(
    auth_ctx, monkeypatch
):
    session, team, user = auth_ctx
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

    assert await play_routes.claim(principal=first) == {
        "ok": True,
        "controller": True,
    }
    with pytest.raises(HTTPException) as conflict:
        await play_routes.claim(principal=second)
    assert conflict.value.status_code == 409
    assert conflict.value.detail["code"] == "controller_taken"

    # Losing the controller race does not invalidate the second login.
    assert await principal_for(session, second_login["access_token"]) == second


async def test_copied_token_represents_the_same_controller_on_multiple_devices(
    auth_ctx, monkeypatch
):
    session, team, user = auth_ctx
    logged_in = await login_team(session)
    first_device = await principal_for(session, logged_in["access_token"])
    copied_token_device = await principal_for(session, logged_in["access_token"])
    fake_redis = FakeControllerRedis()

    async def fake_get_redis():
        return fake_redis

    async def no_broadcast(channel, payload):
        return None

    monkeypatch.setattr("app.redisutil.get_redis", fake_get_redis)
    monkeypatch.setattr(play_routes.hub, "broadcast", no_broadcast)

    await play_routes.claim(principal=first_device)
    await play_routes._require_controller(copied_token_device)

    assert first_device.session_id == copied_token_device.session_id


async def test_websocket_token_decoding_has_no_user_or_session_lookup(auth_ctx):
    session, team, user = auth_ctx
    logged_in = await login_team(session)
    token = logged_in["access_token"]

    user.is_active = False
    await session.commit()

    with pytest.raises(HTTPException) as disabled_http_user:
        await principal_for(session, token)
    assert disabled_http_user.value.status_code == 401

    # websocket_endpoint currently performs only this stateless decode.
    claims = decode_token(token)
    assert UUID(claims["sub"]) == user.id
    assert claims["sid"] == logged_in["session_id"]
