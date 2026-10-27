from datetime import datetime, timedelta, timezone
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.deps import Principal, current_principal
from app.engine import load_user_by_username
from app.models import AuthSession, Team
from app.security import create_token, new_session_id, verify_password

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)
    device_label: str | None = Field(default=None, max_length=120)


@router.post("/login")
async def login(
    body: LoginIn,
    session: AsyncSession = Depends(get_session),
    user_agent: Annotated[str | None, Header()] = None,
):
    user = await load_user_by_username(session, body.username)
    if user is None or not user.is_active or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "Invalid credentials")

    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(hours=get_settings().access_token_expire_hours)
    sid = UUID(new_session_id())
    slot = None

    if user.role == "team":
        if user.team_id is None:
            raise HTTPException(403, "Team login is not assigned to a team")
        team = (
            await session.execute(
                select(Team).where(Team.id == user.team_id).with_for_update()
            )
        ).scalar_one_or_none()
        if team is None:
            raise HTTPException(401, "Invalid credentials")

        await session.execute(
            update(AuthSession)
            .where(
                AuthSession.team_id == user.team_id,
                AuthSession.slot.is_not(None),
                or_(
                    AuthSession.revoked_at.is_not(None),
                    AuthSession.expires_at <= now,
                ),
            )
            .values(slot=None)
        )
        occupied = set(
            (
                await session.execute(
                    select(AuthSession.slot).where(
                        AuthSession.team_id == user.team_id,
                        AuthSession.slot.is_not(None),
                        AuthSession.revoked_at.is_(None),
                        AuthSession.expires_at > now,
                    )
                )
            ).scalars()
        )
        slot = next((candidate for candidate in range(1, 5) if candidate not in occupied), None)
        if slot is None:
            await session.rollback()
            raise HTTPException(
                409,
                {"code": "session_limit", "message": "This team already has 4 active sessions"},
            )

    auth_session = AuthSession(
        id=sid,
        user_id=user.id,
        team_id=user.team_id,
        slot=slot,
        created_at=now,
        last_seen_at=now,
        expires_at=expires_at,
        device_label=body.device_label,
        user_agent=user_agent[:512] if user_agent else None,
    )
    session.add(auth_session)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            409,
            {"code": "session_limit", "message": "This team already has 4 active sessions"},
        ) from None

    token = create_token(
        sub=str(user.id),
        role=user.role,
        team_id=str(user.team_id) if user.team_id else None,
        session_id=str(sid),
        issued_at=now,
        expires_at=expires_at,
    )
    return {
        "access_token": token,
        "token_type": "bearer",
        "role": user.role,
        "session_id": str(sid),
        "team_id": str(user.team_id) if user.team_id else None,
        "username": user.username,
        "team_name": user.team.name if user.team else None,
    }


@router.post("/logout")
async def logout(
    principal: Principal = Depends(current_principal),
    session: AsyncSession = Depends(get_session),
):
    row = (
        await session.execute(
            select(AuthSession)
            .where(AuthSession.id == UUID(principal.session_id))
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is not None and row.revoked_at is None:
        row.revoked_at = datetime.now(timezone.utc)
        row.slot = None
        await session.commit()
    return {"ok": True}


@router.post("/heartbeat")
async def heartbeat(
    principal: Principal = Depends(current_principal),
    session: AsyncSession = Depends(get_session),
):
    """Record presence for only the authenticated SID.

    Authentication validity remains governed by ``current_principal``; this
    endpoint does not alter slots, controller ownership, or gameplay state.
    """
    row = await session.get(AuthSession, UUID(principal.session_id))
    # The dependency has already validated this exact persisted session.
    row.last_seen_at = datetime.now(timezone.utc)
    await session.commit()
    return {"ok": True}
