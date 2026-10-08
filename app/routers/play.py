import math
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
from app.ws import PUBLIC, emit_admin_notice, hub, notify_after_commit, team_channel
from sqlalchemy import select

router = APIRouter(prefix="/api/v1/play", tags=["play"])


class AnswerIn(BaseModel):
    answer: dict[str, Any]


class SwapIn(BaseModel):
    target_team_id: UUID


def _err(e: EngineError) -> HTTPException:
    return HTTPException(e.status, {"code": e.code, "message": e.message})


async def _require_controller(principal: Principal) -> None:
    team_id = str(principal.team_id)
    current = await get_controller(team_id)
    if current != principal.session_id:
        raise HTTPException(403, {"code": "not_controller", "message": "This device is view-only. Claim controller first."})

    ok = await heartbeat_controller(team_id, principal.session_id, get_settings().controller_ttl_sec)
    if not ok:
        raise HTTPException(409, {"code": "not_controller", "message": "Lost controller"})


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
    ok = await release_controller(str(principal.team_id), principal.session_id)
    if not ok:
        raise HTTPException(409, {"code": "not_controller", "message": "This device is not the current controller"})
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
    if ctrl == principal.session_id:
        await heartbeat_controller(str(principal.team_id), principal.session_id, get_settings().controller_ttl_sec)
    view = None
    if row and q:
        has_peeked = row.peek
        view = tq_player_view(row, q, live_started_at=rnd.live_started_at, now=now, include_hint=has_peeked)

    

    # --- PURE MILLISECONDS LOGIC ---
    timer_total_sec = 0
    deadline_ms = None
    time_left_sec = None

    if rnd.phase == "topic" and rnd.topic_shown_at is not None:
        timer_total_sec = get_settings().topic_duration_sec
        dt = rnd.topic_shown_at.replace(tzinfo=timezone.utc) if not rnd.topic_shown_at.tzinfo else rnd.topic_shown_at
        deadline_ms = int(dt.timestamp() * 1000) + (timer_total_sec * 1000)

    elif rnd.phase == "live" and rnd.live_started_at is not None:
        timer_total_sec = get_settings().question_duration_sec
        if row and row.deadline_at is not None:
            dt = row.deadline_at.replace(tzinfo=timezone.utc) if not row.deadline_at.tzinfo else row.deadline_at
            deadline_ms = int(dt.timestamp() * 1000)
        else:
            dt = rnd.live_started_at.replace(tzinfo=timezone.utc) if not rnd.live_started_at.tzinfo else rnd.live_started_at
            deadline_ms = int(dt.timestamp() * 1000) + (timer_total_sec * 1000)

        if deadline_ms is not None:
            now_ms = int(now.timestamp() * 1000)
            time_left_sec = max(0, math.ceil((deadline_ms - now_ms) / 1000))
    # -------------------------------
    question = None
    if q:

        safe_options = None
        if include_body and q.options:
            # Update this line to also extract media_url for the options
            safe_options = [{"id": opt.get("id"), "text": opt.get("text"), "media_url": opt.get("media_url")} for opt in q.options]
        
        question = { 
            "id": str(q.id),
            "index": q.index,
            "topic": q.topic,
            "type": q.qtype,
            "prompt": q.prompt if include_body else None,
            "media_url": q.media_url if include_body else None, # Add the main question image here
            "options": safe_options,
        }

    from app.redisutil import get_frozen_leaderboard
    frozen_board = await get_frozen_leaderboard()
    if frozen_board is None:
        frozen_board = await leaderboard(session)
        
    return {
        "phase": rnd.phase,
        "server_now": now.isoformat(),
        "topic_shown_at": rnd.topic_shown_at.isoformat() if rnd.topic_shown_at else None,
        "live_started_at": rnd.live_started_at.isoformat() if rnd.live_started_at else None,
        
        "timer_total_sec": timer_total_sec,
        "deadline_ms": deadline_ms,
        "time_left_sec": time_left_sec,
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
        "leaderboard": frozen_board,
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
    team = await session.get(Team, principal.team_id)
    notification_context = {"team_id": str(principal.team_id), "card": "bounty"}
    await notify_after_commit(
        hub.broadcast(team_channel(str(principal.team_id)), {"type": "card", "card": "bounty"}),
        operation="play.bounty.team",
        context=notification_context,
    )
    #await hub.broadcast(PUBLIC, {"type": "leaderboard", "leaderboard": await leaderboard(session)})
    await notify_after_commit(
        emit_admin_notice(
            kind="bounty",
            team_id=str(principal.team_id),
            team_name=team.name if team else None,
            question_id=str((await get_round(session)).current_question_id) if (await get_round(session)).current_question_id else None,
            payload={"card": "bounty"},
            message=f"{team.name if team else 'A team'} used Bounty",
        ),
        operation="play.bounty.admin",
        context=notification_context,
    )
    return {"ok": True}


@router.post("/freeze")
async def freeze(session: AsyncSession = Depends(get_session), principal: Principal = Depends(require_team)):
    await _require_controller(principal)
    
    try:
        row = await use_freeze(session, principal.team_id, actor=principal.username)
        await session.commit()
    except EngineError as e:
        await session.rollback()
        raise _err(e) from None
        
    team = await session.get(Team, principal.team_id)
    rnd = await get_round(session)
    
    # Convert the datetime directly to milliseconds!
    deadline_ms = int(row.deadline_at.timestamp() * 1000) if row.deadline_at else None
    
    # Send pure milliseconds to the Player
    notification_context = {"team_id": str(principal.team_id), "card": "freeze"}
    await notify_after_commit(
        hub.broadcast(
            team_channel(str(principal.team_id)),
            {"type": "card", "card": "freeze", "deadline_ms": deadline_ms},
        ),
        operation="play.freeze.team",
        context=notification_context,
    )
    
    # Send pure milliseconds to the Admin
    await notify_after_commit(
        emit_admin_notice(
            kind="freeze",
            team_id=str(principal.team_id),
            team_name=team.name if team else None,
            question_id=str(rnd.current_question_id) if rnd.current_question_id else None,
            payload={"card": "freeze", "deadline_ms": deadline_ms},
            message=f"{team.name if team else 'A team'} used Freeze",
        ),
        operation="play.freeze.admin",
        context=notification_context,
    )
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
    team = await session.get(Team, principal.team_id)
    notification_context = {"team_id": str(principal.team_id), "card": "peek"}
    await notify_after_commit(
        hub.broadcast(team_channel(str(principal.team_id)), {"type": "card", "card": "peek"}),
        operation="play.peek.team",
        context=notification_context,
    )
    await notify_after_commit(
        emit_admin_notice(
            kind="peek",
            team_id=str(principal.team_id),
            team_name=team.name if team else None,
            question_id=str((await get_round(session)).current_question_id) if (await get_round(session)).current_question_id else None,
            payload={"card": "peek"},
            message=f"{team.name if team else 'A team'} used Peek",
        ),
        operation="play.peek.admin",
        context=notification_context,
    )
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
    team = await session.get(Team, principal.team_id)
    rnd = await get_round(session)
    notification_context = {"team_id": str(principal.team_id), "question_id": str(rnd.current_question_id)}
    await notify_after_commit(
        hub.broadcast(team_channel(str(principal.team_id)), {"type": "locked"}),
        operation="play.lock.team",
        context=notification_context,
    )
    await notify_after_commit(
        emit_admin_notice(
            kind="lock",
            team_id=str(principal.team_id),
            team_name=team.name if team else None,
            question_id=str(rnd.current_question_id) if rnd.current_question_id else None,
            payload={"answer": body.answer, "locked": True},
            message=f"{team.name if team else 'A team'} locked an answer",
        ),
        operation="play.lock.admin",
        context=notification_context,
    )
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
    team = await session.get(Team, principal.team_id)
    target = await session.get(Team, body.target_team_id)
    rnd = await get_round(session)
    notification_context = {
        "team_id": str(principal.team_id),
        "target_team_id": str(body.target_team_id),
    }
    await notify_after_commit(
        hub.broadcast(team_channel(str(principal.team_id)), {"type": "swap_queued"}),
        operation="play.swap.team",
        context=notification_context,
    )
    await notify_after_commit(
        emit_admin_notice(
            kind="swap",
            team_id=str(principal.team_id),
            team_name=team.name if team else None,
            question_id=str(rnd.current_question_id) if rnd.current_question_id else None,
            payload={"target_team_id": str(body.target_team_id), "target_team_name": target.name if target else None},
            message=f"{team.name if team else 'A team'} queued a swap with {target.name if target else 'another team'}",
        ),
        operation="play.swap.admin",
        context=notification_context,
    )
    return {"ok": True}
