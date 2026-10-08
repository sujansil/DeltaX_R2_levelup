import asyncio

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from app.db import SessionLocal
from app.deps import principal_from_token
from app.ws import ADMIN, PUBLIC, hub, team_channel

router = APIRouter()


@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    token = ws.query_params.get("token")
    if not token:
        await ws.close(code=4401)
        return
    try:
        async with SessionLocal() as session:
            principal = await principal_from_token(token, session)
    except HTTPException:
        await ws.close(code=4401)
        return
    await ws.accept()
    channels = [PUBLIC]
    if principal.role == "admin":
        channels.append(ADMIN)
    if principal.team_id:
        channels.append(team_channel(str(principal.team_id)))
    for ch in channels:
        hub.add(ch, ws)
    try:
        await ws.send_json({"type": "hello", "role": principal.role, "channels": channels})
        while True:
            try:
                await asyncio.wait_for(ws.receive_text(), timeout=5)
            except TimeoutError:
                pass
            try:
                async with SessionLocal() as session:
                    await principal_from_token(token, session)
            except HTTPException:
                await ws.close(code=4401)
                break
    except WebSocketDisconnect:
        pass
    finally:
        for ch in channels:
            hub.remove(ch, ws)
