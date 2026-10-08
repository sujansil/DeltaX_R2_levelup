import os
import shutil
from fastapi import File, UploadFile
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.deps import Principal, require_admin
from app.engine import (
    EngineError,
    admin_question_matrix,
    close_question,
    get_round,
    leaderboard,
    override_grade,
    reorder_questions,
    restart_round,
    show_topic,
    start_question,
)
from app.models import AuditEvent, Question, Team, User
from app.redisutil import release_controller
from app.security import hash_password
from app.ws import ADMIN, PUBLIC, hub, notify_after_commit, team_channel

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


class TeamIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=3, max_length=128)
    score: int | None = None


class ScoreIn(BaseModel):
    score: int


class QuestionIn(BaseModel):
    index: int = Field(ge=1, le=99)
    topic: str
    prompt: str
    qtype: str = Field(pattern="^(mcq_single|mcq_multi)$")
    options: Any | None = None
    answer_key: dict[str, Any]
    hint: str | None = None
    media_url: str | None = None


class QuestionOrderIn(BaseModel):
    question_ids: list[UUID]


class OverrideIn(BaseModel):
    team_id: UUID
    question_id: UUID
    correct: bool


def _err(e: EngineError) -> HTTPException:
    return HTTPException(e.status, {"code": e.code, "message": e.message})


async def _fanout(session: AsyncSession, extra_admin: dict | None = None) -> None:
    from app.engine import get_round as gr
    from app.models import Question as Q
    from app.redisutil import get_frozen_leaderboard

    rnd = await gr(session)
    live_board = await leaderboard(session)
    
    # Try to get the frozen board, fallback to live if it's the very first run
    frozen_board = await notify_after_commit(
        get_frozen_leaderboard(),
        operation="admin.fanout.read_frozen_leaderboard",
        context={"phase": rnd.phase},
    )
    if frozen_board is None:
        frozen_board = live_board

    q = await session.get(Q, rnd.current_question_id) if rnd.current_question_id else None
    
    # PUBLIC gets the FROZEN leaderboard
    public = {
        "type": "state",
        "phase": rnd.phase,
        "server_now": None,
        "topic_shown_at": rnd.topic_shown_at.isoformat() if rnd.topic_shown_at else None,
        "live_started_at": rnd.live_started_at.isoformat() if rnd.live_started_at else None,
        "question": (
            {
                "id": str(q.id),
                "index": q.index,
                "topic": q.topic,
                "type": q.qtype,
                "prompt": q.prompt if rnd.phase in ("live", "closed") else None,
                "media_url": q.media_url if rnd.phase in ("live", "closed") else None,
                "options": q.options if rnd.phase in ("live", "closed") else None,
            }
            if q
            else None
        ),
        "leaderboard": frozen_board, 
    }
    from datetime import datetime, timezone

    public["server_now"] = datetime.now(timezone.utc).isoformat()
    await notify_after_commit(
        hub.broadcast(PUBLIC, public),
        operation="admin.fanout.public",
        context={"phase": rnd.phase},
    )
    
    # ADMIN payload gets the LIVE leaderboard so you can monitor point spending in real-time
    admin_payload = dict(public)
    admin_payload["type"] = "admin_state"
    admin_payload["leaderboard"] = live_board 
    if extra_admin:
        admin_payload.update(extra_admin)
        
    await notify_after_commit(
        hub.broadcast(ADMIN, admin_payload),
        operation="admin.fanout.admin",
        context={"phase": rnd.phase},
    )
    
    if q:
        matrix = await admin_question_matrix(session, q.id)
        await notify_after_commit(
            hub.broadcast(ADMIN, {"type": "matrix", "matrix": matrix}),
            operation="admin.fanout.matrix",
            context={"question_id": str(q.id)},
        )
        for t in live_board:
            # Tell teams to update, but only give them the frozen board
            await notify_after_commit(
                hub.broadcast(team_channel(t["id"]), {"type": "state_tick", "phase": rnd.phase, "leaderboard": frozen_board}),
                operation="admin.fanout.team",
                context={"team_id": t["id"], "phase": rnd.phase},
            )

@router.post("/teams")
async def create_team(
    body: TeamIn,
    session: AsyncSession = Depends(get_session),
    _: Principal = Depends(require_admin),
):
    if (await session.execute(select(User).where(User.username == body.username))).scalar_one_or_none():
        raise HTTPException(409, "Username taken")
    if (await session.execute(select(Team).where(Team.name == body.name))).scalar_one_or_none():
        raise HTTPException(409, "Team name taken")
    settings = get_settings()
    team = Team(name=body.name, score=body.score if body.score is not None else settings.initial_team_score)
    session.add(team)
    await session.flush()
    user = User(
        username=body.username,
        password_hash=hash_password(body.password),
        role="team",
        team_id=team.id,
    )
    session.add(user)
    await session.commit()
    return {"team_id": str(team.id), "username": body.username, "name": team.name, "score": team.score}


@router.get("/teams")
async def list_teams(session: AsyncSession = Depends(get_session), _: Principal = Depends(require_admin)):
    teams = (await session.execute(select(Team).order_by(Team.name))).scalars().all()
    return [{"id": str(t.id), "name": t.name, "score": t.score, "swap_available": t.swap_available} for t in teams]


@router.patch("/teams/{team_id}/score")
async def set_score(
    team_id: UUID,
    body: ScoreIn,
    session: AsyncSession = Depends(get_session),
    admin: Principal = Depends(require_admin),
):
    team = await session.get(Team, team_id)
    if team is None:
        raise HTTPException(404, "Team not found")
    team.score = body.score
    team.version += 1
    await session.commit()
    await _fanout(session)
    return {"id": str(team.id), "score": team.score}


@router.post("/questions")
async def create_question(
    body: QuestionIn,
    session: AsyncSession = Depends(get_session),
    _: Principal = Depends(require_admin),
):
    if (await session.execute(select(Question).where(Question.index == body.index))).scalar_one_or_none():
        raise HTTPException(409, "Question index already exists")
    q = Question(
        index=body.index,
        topic=body.topic,
        prompt=body.prompt,
        qtype=body.qtype,
        options=body.options,
        answer_key=body.answer_key,
        hint=body.hint,
        media_url = body.media_url,
    )
    session.add(q)
    await session.commit()
    await session.refresh(q)
    return {"id": str(q.id), "index": q.index}


@router.post("/questions/reorder")
async def reorder_question_sequence(
    body: QuestionOrderIn,
    session: AsyncSession = Depends(get_session),
    admin: Principal = Depends(require_admin),
):
    try:
        await reorder_questions(session, body.question_ids, actor=admin.username)
        await session.commit()
    except EngineError as e:
        await session.rollback()
        raise _err(e) from None
    return {"ok": True}


@router.put("/questions/{question_id}")
async def update_question(
    question_id: UUID,
    body: QuestionIn,
    session: AsyncSession = Depends(get_session),
    _: Principal = Depends(require_admin),
):
    q = await session.get(Question, question_id)
    if q is None:
        raise HTTPException(404, "Not found")
    if body.index != q.index:
        dup = (await session.execute(select(Question).where(Question.index == body.index, Question.id != question_id))).scalar_one_or_none()
        if dup is not None:
            raise HTTPException(409, "Question index already exists")
    q.index = body.index
    q.topic = body.topic
    q.prompt = body.prompt
    q.qtype = body.qtype
    q.options = body.options
    q.answer_key = body.answer_key
    q.hint = body.hint
    q.media_url = body.media_url
    await session.commit()
    return {"id": str(q.id)}


@router.get("/questions")
async def list_questions(session: AsyncSession = Depends(get_session), _: Principal = Depends(require_admin)):
    qs = (await session.execute(select(Question).order_by(Question.index))).scalars().all()
    return [
        {
            "id": str(q.id),
            "index": q.index,
            "topic": q.topic,
            "prompt": q.prompt,
            "type": q.qtype,
            "options": q.options,
            "answer_key": q.answer_key,
            "hint": q.hint,
        }
        for q in qs
    ]


@router.post("/round/show-topic/{question_id}")
async def api_show_topic(
    question_id: UUID,
    session: AsyncSession = Depends(get_session),
    admin: Principal = Depends(require_admin),
):
    try:
        await show_topic(session, question_id, actor=admin.username)
        await session.commit()
    except EngineError as e:
        await session.rollback()
        raise _err(e) from None
    await _fanout(session)
    return {"ok": True}


@router.post("/round/start")
async def api_start(
    session: AsyncSession = Depends(get_session),
    admin: Principal = Depends(require_admin),
):
    try:
        await start_question(session, actor=admin.username)
        await session.commit()
    except EngineError as e:
        await session.rollback()
        raise _err(e) from None
    await _fanout(session)
    return {"ok": True}


@router.post("/round/close")
async def api_close(
    session: AsyncSession = Depends(get_session),
    admin: Principal = Depends(require_admin),
):
    try:
        rnd = await close_question(session, actor=admin.username)
        await session.commit()
    except EngineError as e:
        await session.rollback()
        raise _err(e) from None

    extra = {}
    if rnd.current_question_id:
        extra["matrix"] = await admin_question_matrix(session, rnd.current_question_id)
        extra["swap_notices"] = extra["matrix"]["swaps"]
        for s in extra["matrix"]["swaps"]:
            await notify_after_commit(
                hub.broadcast(
                    team_channel(s["target"]),
                    {"type": "swap_notice", "source": s["source"], "question_id": str(rnd.current_question_id)},
                ),
                operation="admin.close.swap_target",
                context={"target_team_id": s["target"], "question_id": str(rnd.current_question_id)},
            )
            await notify_after_commit(
                hub.broadcast(
                    team_channel(s["source"]),
                    {"type": "swap_applied", "target": s["target"], "question_id": str(rnd.current_question_id)},
                ),
                operation="admin.close.swap_source",
                context={"source_team_id": s["source"], "question_id": str(rnd.current_question_id)},
            )
    await _fanout(session, extra_admin=extra)
    return {"ok": True}


@router.post("/round/restart")
async def api_restart(
    session: AsyncSession = Depends(get_session),
    admin: Principal = Depends(require_admin),
):
    try:
        await restart_round(session, actor=admin.username)
        await session.commit()
    except EngineError as e:
        await session.rollback()
        raise _err(e) from None
    await _fanout(session)
    return {"ok": True}


@router.get("/round")
async def api_round(session: AsyncSession = Depends(get_session), _: Principal = Depends(require_admin)):
    rnd = await get_round(session)
    q = await session.get(Question, rnd.current_question_id) if rnd.current_question_id else None
    board = await leaderboard(session)
    matrix = None
    if rnd.current_question_id:
        matrix = await admin_question_matrix(session, rnd.current_question_id)
    from datetime import datetime, timezone, timedelta

    global_deadline_ms = None

    # everything converted to milliseconds
    if rnd.phase == "topic" and rnd.topic_shown_at:
        dt = rnd.topic_shown_at.replace(tzinfo=timezone.utc) if not rnd.topic_shown_at.tzinfo else rnd.topic_shown_at
        global_deadline_ms = int(dt.timestamp() * 1000) + 45000

    elif rnd.phase == "live" and rnd.live_started_at:
        dt = rnd.live_started_at.replace(tzinfo=timezone.utc) if not rnd.live_started_at.tzinfo else rnd.live_started_at
        global_deadline_ms = int(dt.timestamp() * 1000) + 150000
        if rnd.global_deadline_at:
            g_dt = rnd.global_deadline_at.replace(tzinfo=timezone.utc) if not rnd.global_deadline_at.tzinfo else rnd.global_deadline_at
            global_deadline_ms = int(g_dt.timestamp() * 1000)

    return {
        "phase": rnd.phase,
        "server_now": datetime.now(timezone.utc).isoformat(),
        "topic_shown_at": rnd.topic_shown_at.isoformat() if rnd.topic_shown_at else None,
        "live_started_at": rnd.live_started_at.isoformat() if rnd.live_started_at else None,
        "global_deadline_ms": global_deadline_ms,
        "current_question_id": str(rnd.current_question_id) if rnd.current_question_id else None,
        "question": (
            {
                "id": str(q.id),
                "index": q.index,
                "topic": q.topic,
                "type": q.qtype,
                "prompt": q.prompt if rnd.phase in ("live", "closed") else None,
                "media_url": q.media_url if rnd.phase in ("live", "closed") else None,
                "options": q.options if rnd.phase in ("live", "closed") else None,
            }
            if q
            else None
        ),
        "leaderboard": board,
        "matrix": matrix,
        "reveal_cards": rnd.reveal_cards,
    }

@router.post("/grade/override")
async def api_override(
    body: OverrideIn,
    session: AsyncSession = Depends(get_session),
    admin: Principal = Depends(require_admin),
):
    try:
        await override_grade(session, body.team_id, body.question_id, body.correct, actor=admin.username)
        await session.commit()
    except EngineError as e:
        await session.rollback()
        raise _err(e) from None
    await _fanout(session)
    return {"ok": True}


@router.post("/teams/{team_id}/release-controller")
async def api_release(
    team_id: UUID,
    session: AsyncSession = Depends(get_session),
    admin: Principal = Depends(require_admin),
):
    await release_controller(str(team_id), force=True)
    await hub.broadcast(team_channel(str(team_id)), {"type": "controller_released"})
    return {"ok": True}


@router.get("/audit")
async def api_audit(session: AsyncSession = Depends(get_session), _: Principal = Depends(require_admin)):
    rows = (
        await session.execute(select(AuditEvent).order_by(AuditEvent.at.desc()).limit(500))
    ).scalars().all()
    return [
        {
            "at": r.at.isoformat() if r.at else None,
            "actor": r.actor,
            "kind": r.kind,
            "team_id": str(r.team_id) if r.team_id else None,
            "question_id": str(r.question_id) if r.question_id else None,
            "payload": r.payload,
        }
        for r in rows
    ]


@router.post("/round/reveal-cards/{on}")
async def reveal_cards(
    on: int,
    session: AsyncSession = Depends(get_session),
    _: Principal = Depends(require_admin),
):
    rnd = await get_round(session, for_update=True)
    rnd.reveal_cards = bool(on)
    await session.commit()
    return {"reveal_cards": rnd.reveal_cards}


@router.post("/round/publish-leaderboard")
async def api_publish_leaderboard(
    session: AsyncSession = Depends(get_session),
    admin: Principal = Depends(require_admin),
):
    from app.redisutil import set_frozen_leaderboard
    live_board = await leaderboard(session)
    
    # Save current live scores to Redis
    await set_frozen_leaderboard(live_board)
    
    # Broadcast the newly published leaderboard to all player screens instantly
    await hub.broadcast(PUBLIC, {"type": "leaderboard", "leaderboard": live_board})
    
    return {"ok": True, "published": True}

# Ensure the upload directory exists
UPLOAD_DIR = "app/static/uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

@router.post("/upload")
async def upload_image(
    file: UploadFile = File(...),
    _: Principal = Depends(require_admin)
):
    # Save the file locally
    file_location = f"{UPLOAD_DIR}/{file.filename}"
    with open(file_location, "wb+") as file_object:
        shutil.copyfileobj(file.file, file_object)
    
    # Return the relative URL path to be saved in the database
    return {"media_url": f"/static/uploads/{file.filename}"}
