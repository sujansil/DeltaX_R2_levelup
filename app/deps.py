from dataclasses import dataclass
from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends, Header, HTTPException
from jwt import InvalidTokenError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import User
from app.security import decode_token
from sqlalchemy import select


@dataclass
class Principal:
    user_id: UUID
    username: str
    role: str
    team_id: UUID | None
    session_id: str


async def current_principal(
    authorization: Annotated[str | None, Header()] = None,
    session: AsyncSession = Depends(get_session),
) -> Principal:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Missing token")
    token = authorization.split(" ", 1)[1]
    try:
        payload = decode_token(token)
    except InvalidTokenError:
        raise HTTPException(401, "Invalid token") from None
    user = await session.get(User, UUID(payload["sub"]))
    if user is None or not user.is_active:
        raise HTTPException(401, "User disabled")
    team_id = UUID(payload["team_id"]) if payload.get("team_id") else None
    return Principal(
        user_id=user.id,
        username=user.username,
        role=user.role,
        team_id=team_id,
        session_id=payload["sid"],
    )


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
