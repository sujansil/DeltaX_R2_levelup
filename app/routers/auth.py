from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.engine import load_user_by_username
from app.security import create_token, new_session_id, verify_password

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


@router.post("/login")
async def login(body: LoginIn, session: AsyncSession = Depends(get_session)):
    user = await load_user_by_username(session, body.username)
    if user is None or not user.is_active or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "Invalid credentials")
    sid = new_session_id()
    token = create_token(
        sub=str(user.id),
        role=user.role,
        team_id=str(user.team_id) if user.team_id else None,
        session_id=sid,
    )
    return {
        "access_token": token,
        "token_type": "bearer",
        "role": user.role,
        "session_id": sid,
        "team_id": str(user.team_id) if user.team_id else None,
        "username": user.username,
        "team_name": user.team.name if user.team else None,
    }
