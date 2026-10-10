"""PostgreSQL-only audit coverage for the per-team session cap.

Set TEST_POSTGRES_URL to an asyncpg URL.
"""

import asyncio
import os
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import AuthSession, Base, Team, User
from app.routers import auth as auth_routes
from app.routers.auth import LoginIn
from app.security import hash_password


POSTGRES_URL = os.getenv("TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(not POSTGRES_URL, reason="requires TEST_POSTGRES_URL")


@pytest.fixture
async def pg_auth_ctx():
    schema = f"auth_audit_{uuid4().hex}"
    bootstrap = create_async_engine(POSTGRES_URL)
    async with bootstrap.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(
        POSTGRES_URL, connect_args={"server_settings": {"search_path": schema}}
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        team = Team(id=uuid4(), name="Race team", score=0)
        user = User(
            id=uuid4(), username="race", password_hash=hash_password("pass123"),
            role="team", team=team,
        )
        session.add_all([team, user])
        await session.commit()
        team_id = team.id
    yield maker, team_id
    await engine.dispose()
    async with bootstrap.begin() as connection:
        await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    await bootstrap.dispose()


async def _login(session, label):
    return await auth_routes.login(
        LoginIn(username="race", password="pass123", device_label=label),
        session=session, user_agent="postgres-audit",
    )


async def test_concurrent_fourth_and_fifth_login_never_exceed_cap(pg_auth_ctx):
    maker, team_id = pg_auth_ctx
    async with maker() as session:
        for number in range(3):
            await _login(session, f"existing-{number}")

    async def attempt(label):
        async with maker() as session:
            try:
                return await _login(session, label)
            except HTTPException as exc:
                return exc

    results = await asyncio.gather(attempt("racer-a"), attempt("racer-b"))
    assert sum(isinstance(result, dict) for result in results) == 1
    rejection = next(result for result in results if isinstance(result, HTTPException))
    assert rejection.status_code == 409
    assert rejection.detail["code"] == "session_limit"

    async with maker() as session:
        valid_count = await session.scalar(
            select(func.count()).select_from(AuthSession).where(
                AuthSession.team_id == team_id,
                AuthSession.revoked_at.is_(None),
                AuthSession.slot.is_not(None),
            )
        )
        slots = set((await session.scalars(
            select(AuthSession.slot).where(AuthSession.team_id == team_id)
        )).all())
    assert valid_count == 4
    assert slots == {1, 2, 3, 4}
