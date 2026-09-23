from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

import jwt
from pwdlib import PasswordHash

from app.config import get_settings

_hasher = PasswordHash.recommended()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return _hasher.verify(password, password_hash)


def create_token(*, sub: str, role: str, team_id: str | None, session_id: str) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    payload = {
        "sub": sub,
        "role": role,
        "team_id": team_id,
        "sid": session_id,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=settings.access_token_expire_hours)).timestamp()),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict[str, Any]:
    settings = get_settings()
    return jwt.decode(token, settings.secret_key, algorithms=[settings.jwt_algorithm])


def new_session_id() -> str:
    return str(uuid4())
