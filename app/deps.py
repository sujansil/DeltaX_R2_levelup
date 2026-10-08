from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, HTTPException
from jwt import InvalidTokenError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import AuthSession, User
from app.security import decode_token


@dataclass
class Principal:
    user_id: UUID
    username: str
    role: str
    team_id: UUID | None
    session_id: str


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


async def principal_from_token(
    token: str,
    session: AsyncSession,
    *,
    update_last_seen: bool = True,
) -> Principal:
    try:
        payload = decode_token(token)
        user_id = UUID(payload["sub"])
        session_id = UUID(payload["sid"])
        token_team_id = UUID(payload["team_id"]) if payload.get("team_id") else None
    except (InvalidTokenError, KeyError, TypeError, ValueError):
        raise HTTPException(401, "Invalid token") from None

    user = await session.get(User, user_id)
    auth_session = await session.get(AuthSession, session_id)
    now = datetime.now(timezone.utc)
    if user is None or not user.is_active:
        raise HTTPException(401, "User disabled")
    if (
        auth_session is None
        or auth_session.user_id != user.id
        or auth_session.team_id != token_team_id
        or user.team_id != token_team_id
        or auth_session.revoked_at is not None
        or (auth_session.team_id is not None and auth_session.slot is None)
        or _as_utc(auth_session.expires_at) <= now
    ):
        raise HTTPException(401, "Session inactive")

    if update_last_seen and now - _as_utc(auth_session.last_seen_at) >= timedelta(seconds=60):
        auth_session.last_seen_at = now
        await session.commit()

    return Principal(
        user_id=user.id,
        username=user.username,
        role=user.role,
        team_id=auth_session.team_id,
        session_id=str(auth_session.id),
    )


async def current_principal(
    authorization: Annotated[str | None, Header()] = None,
    session: AsyncSession = Depends(get_session),
) -> Principal:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Missing token")
    token = authorization.split(" ", 1)[1]
    return await principal_from_token(token, session)


async def require_admin(principal: Principal = Depends(current_principal)) -> Principal:
    if principal.role != "admin":
        raise HTTPException(403, "Admin only")
    return principal


async def require_team(principal: Principal = Depends(current_principal)) -> Principal:
    if principal.role != "team" or principal.team_id is None:
        raise HTTPException(403, "Team login required")
    return principal


async def db_session(session: AsyncSession = Depends(get_session)) -> AsyncSession:
    return session
