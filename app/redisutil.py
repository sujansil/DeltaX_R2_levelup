from __future__ import annotations

import json
from typing import Any

from redis.asyncio import Redis

from app.config import get_settings

_redis: Redis | None = None


async def get_redis() -> Redis:
    global _redis
    if _redis is None:
        _redis = Redis.from_url(get_settings().redis_url, decode_responses=True)
    return _redis


async def close_redis() -> None:
    global _redis
    if _redis is not None:
        await _redis.aclose()
        _redis = None


def controller_key(team_id: str) -> str:
    return f"ctrl:{team_id}"


async def claim_controller(team_id: str, session_id: str, ttl: int) -> tuple[bool, str | None]:
    r = await get_redis()
    key = controller_key(team_id)
    ok = await r.set(key, session_id, ex=ttl, nx=True)
    if ok:
        return True, session_id
    current = await r.get(key)
    if current == session_id:
        await r.expire(key, ttl)
        return True, session_id
    return False, current


async def heartbeat_controller(team_id: str, session_id: str, ttl: int) -> bool:
    r = await get_redis()
    key = controller_key(team_id)
    current = await r.get(key)
    if current != session_id:
        return False
    await r.expire(key, ttl)
    return True


async def release_controller(team_id: str, session_id: str | None = None, *, force: bool = False) -> bool:
    r = await get_redis()
    key = controller_key(team_id)
    current = await r.get(key)
    if current is None:
        return True
    if force or current == session_id:
        await r.delete(key)
        return True
    return False


async def get_controller(team_id: str) -> str | None:
    r = await get_redis()
    return await r.get(controller_key(team_id))


async def publish(channel: str, payload: dict[str, Any]) -> None:
    r = await get_redis()
    await r.publish(channel, json.dumps(payload, default=str))


async def acquire_lock(name: str, ttl_sec: int = 5) -> bool:
    r = await get_redis()
    return bool(await r.set(f"lock:{name}", "1", ex=ttl_sec, nx=True))


async def set_frozen_leaderboard(payload: list[dict[str, Any]]) -> None:
    r = await get_redis()
    await r.set("frozen_leaderboard", json.dumps(payload))


async def clear_frozen_leaderboard() -> None:
    r = await get_redis()
    await r.delete("frozen_leaderboard")


async def get_frozen_leaderboard() -> list[dict[str, Any]] | None:
    r = await get_redis()
    data = await r.get("frozen_leaderboard")
    if data:
        return json.loads(data)
    return None
