from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.deps import Principal, require_team
from app.engine import (
    EngineError,
    activate_bounty,
    get_round,
    leaderboard,
    lock_answer,
    tq_player_view,
    use_freeze,
    use_peek,
    use_swap,
)
from app.models import Question, Team, TeamQuestion
from app.redisutil import claim_controller, get_controller, heartbeat_controller, release_controller
from app.ws import PUBLIC, hub, team_channel
from sqlalchemy import select

router = APIRouter(prefix="/api/v1/play", tags=["play"])


class AnswerIn(BaseModel):
    answer: dict[str, Any]


class SwapIn(BaseModel):
    target_team_id: UUID


def _err(e: EngineError) -> HTTPException:
    return HTTPException(e.status, {"code": e.code, "message": e.message})


async def _require_controller(principal: Principal) -> None:
    current = await get_controller(str(principal.team_id))
    if current != principal.session_id:
        raise HTTPException(403, {"code": "not_controller", "message": "This device is view-only. Claim controller first."})


@router.post("/controller/claim")
async def claim(principal: Principal = Depends(require_team)):
    ttl = get_settings().controller_ttl_sec
    ok, current = await claim_controller(str(principal.team_id), principal.session_id, ttl)
    if not ok:
        raise HTTPException(409, {"code": "controller_taken", "message": "Another device is controller", "session": current})
    await hub.broadcast(
        team_channel(str(principal.team_id)),
        {"type": "controller", "session_id": principal.session_id},
    )
    return {"ok": True, "controller": True}


@router.post("/controller/heartbeat")
async def beat(principal: Principal = Depends(require_team)):
    ok = await heartbeat_controller(str(principal.team_id), principal.session_id, get_settings().controller_ttl_sec)
    if not ok:
        raise HTTPException(409, {"code": "not_controller", "message": "Lost controller"})
    return {"ok": True}


@router.post("/controller/release")
async def release(principal: Principal = Depends(require_team)):
    await release_controller(str(principal.team_id), principal.session_id)
    await hub.broadcast(team_channel(str(principal.team_id)), {"type": "controller_released"})
    return {"ok": True}


@router.get("/state")
async def state(session: AsyncSession = Depends(get_session), principal: Principal = Depends(require_team)):
    rnd = await get_round(session)
    team = await session.get(Team, principal.team_id)
    q = await session.get(Question, rnd.current_question_id) if rnd.current_question_id else None
    row = None
    if q:
        row = (
            await session.execute(
                select(TeamQuestion).where(
                    TeamQuestion.team_id == principal.team_id, TeamQuestion.question_id == q.id
                )
            )
        ).scalar_one_or_none()
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    include_body = rnd.phase in ("live", "closed")
    ctrl = await get_controller(str(principal.team_id))
    view = None
    if row and q:
        view = tq_player_view(row, q, live_started_at=rnd.live_started_at, now=now, include_hint=True)
    question = None
    if q:
        question = {
            "id": str(q.id),
            "index": q.index,
            "topic": q.topic,
            "type": q.qtype,
            "prompt": q.prompt if include_body else None,
            "options": q.options if include_body else None,
        }
    return {
        "phase": rnd.phase,
        "server_now": now.isoformat(),
        "topic_shown_at": rnd.topic_shown_at.isoformat() if rnd.topic_shown_at else None,
        "live_started_at": rnd.live_started_at.isoformat() if rnd.live_started_at else None,
        "question": question,
        "team": {
            "id": str(team.id) if team else None,
            "name": team.name if team else None,
            "score": team.score if team else None,
            "swap_available": team.swap_available if team else None,
        },
        "me": view,
        "controller_session_id": ctrl,
        "i_am_controller": ctrl == principal.session_id,
        "leaderboard": await leaderboard(session),
        "reveal_cards": rnd.reveal_cards,
    }


@router.post("/bounty")
async def bounty(session: AsyncSession = Depends(get_session), principal: Principal = Depends(require_team)):
    await _require_controller(principal)
    try:
        await activate_bounty(session, principal.team_id, actor=principal.username)
        await session.commit()
    except EngineError as e:
        await session.rollback()
        raise _err(e) from None
    await hub.broadcast(team_channel(str(principal.team_id)), {"type": "card", "card": "bounty"})
    await hub.broadcast(PUBLIC, {"type": "leaderboard", "leaderboard": await leaderboard(session)})
    return {"ok": True}


@router.post("/freeze")
async def freeze(session: AsyncSession = Depends(get_session), principal: Principal = Depends(require_team)):
    await _require_controller(principal)
    try:
        await use_freeze(session, principal.team_id, actor=principal.username)
        await session.commit()
    except EngineError as e:
        await session.rollback()
        raise _err(e) from None
    await hub.broadcast(team_channel(str(principal.team_id)), {"type": "card", "card": "freeze"})
    return {"ok": True}


@router.post("/peek")
async def peek(session: AsyncSession = Depends(get_session), principal: Principal = Depends(require_team)):
    await _require_controller(principal)
    try:
        await use_peek(session, principal.team_id, actor=principal.username)
        await session.commit()
    except EngineError as e:
        await session.rollback()
        raise _err(e) from None
    await hub.broadcast(team_channel(str(principal.team_id)), {"type": "card", "card": "peek"})
    return {"ok": True}


@router.post("/lock")
async def lock(
    body: AnswerIn,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(require_team),
):
    await _require_controller(principal)
    try:
        await lock_answer(session, principal.team_id, body.answer, actor=principal.username)
        await session.commit()
    except EngineError as e:
        await session.rollback()
        raise _err(e) from None
    await hub.broadcast(team_channel(str(principal.team_id)), {"type": "locked"})
    return {"ok": True}


@router.post("/swap")
async def swap(
    body: SwapIn,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(require_team),
):
    await _require_controller(principal)
    try:
        await use_swap(session, principal.team_id, body.target_team_id, actor=principal.username)
        await session.commit()
    except EngineError as e:
        await session.rollback()
        raise _err(e) from None
    await hub.broadcast(team_channel(str(principal.team_id)), {"type": "swap_queued"})
    return {"ok": True}
