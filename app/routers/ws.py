from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.security import decode_token
from app.ws import ADMIN, PUBLIC, hub, team_channel

router = APIRouter()


@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    token = ws.query_params.get("token")
    if not token:
        await ws.close(code=4401)
        return
    try:
        payload = decode_token(token)
    except Exception:
        await ws.close(code=4401)
        return
    await ws.accept()
    channels = [PUBLIC]
    if payload.get("role") == "admin":
        channels.append(ADMIN)
    if payload.get("team_id"):
        channels.append(team_channel(str(payload["team_id"])))
    for ch in channels:
        hub.add(ch, ws)
    try:
        await ws.send_json({"type": "hello", "role": payload.get("role"), "channels": channels})
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        for ch in channels:
            hub.remove(ch, ws)
