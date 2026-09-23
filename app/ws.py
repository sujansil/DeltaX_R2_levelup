from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

from fastapi import WebSocket

from app.redisutil import get_redis, publish as redis_publish

PUBLIC = "ch:public"
ADMIN = "ch:admin"


def team_channel(team_id: str) -> str:
    return f"ch:team:{team_id}"


class Hub:
    def __init__(self) -> None:
        self._local: dict[str, set[WebSocket]] = defaultdict(set)

    def add(self, channel: str, ws: WebSocket) -> None:
        self._local[channel].add(ws)

    def remove(self, channel: str, ws: WebSocket) -> None:
        self._local[channel].discard(ws)

    async def local_send(self, channel: str, payload: dict[str, Any]) -> None:
        dead: list[WebSocket] = []
        text = json.dumps(payload, default=str)
        for ws in list(self._local.get(channel, ())):
            try:
                await ws.send_text(text)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._local[channel].discard(ws)

    async def broadcast(self, channel: str, payload: dict[str, Any]) -> None:
        await redis_publish(channel, payload)


hub = Hub()


async def redis_subscriber() -> None:
    r = await get_redis()
    pubsub = r.pubsub()
    await pubsub.psubscribe("ch:*")
    async for msg in pubsub.listen():
        if msg.get("type") not in ("pmessage", "message"):
            continue
        channel = msg.get("channel") or msg.get("pattern")
        data = msg.get("data")
        if not channel or not data or data == 1:
            continue
        try:
            payload = json.loads(data) if isinstance(data, str) else json.loads(data.decode())
        except Exception:
            continue
        await hub.local_send(str(channel), payload)
