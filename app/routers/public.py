from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.engine import get_round
from app.models import PublishedLeaderboard, Question


router = APIRouter(prefix="/api/v1/public", tags=["public"])


def audience_question(question: Question, phase: str) -> dict[str, Any]:
    include_body = phase in {"live", "paused", "closed"}
    options = None
    if include_body:
        source_options = question.options if isinstance(question.options, list) else []
        options = [
            {
                "id": option.get("id"),
                "text": option.get("text"),
                "media_url": option.get("media_url"),
            }
            for option in source_options
            if isinstance(option, dict)
        ]
    return {
        "index": question.index,
        "topic": question.topic,
        "type": question.qtype,
        "prompt": question.prompt if include_body else None,
        "media_url": question.media_url if include_body else None,
        "options": options,
    }


@router.get("/audience")
async def audience(session: AsyncSession = Depends(get_session)):
    rnd = await get_round(session)
    question = (
        await session.get(Question, rnd.current_question_id)
        if rnd.current_question_id is not None and rnd.phase != "idle"
        else None
    )
    snapshot = await session.get(PublishedLeaderboard, 1)
    publication = {
        "published_at": snapshot.published_at.isoformat() if snapshot else None,
        "phase": snapshot.phase if snapshot else None,
        "question_index": snapshot.question_index if snapshot else None,
        "leaderboard": list(snapshot.rows) if snapshot else [],
        "card_usage": list(snapshot.card_usage_rows) if snapshot else [],
        "solution": snapshot.published_solution if snapshot else None,
    }
    return {
        "phase": rnd.phase,
        "paused": rnd.phase == "paused",
        "paused_at": rnd.paused_at.isoformat() if rnd.paused_at else None,
        "server_now": datetime.now(timezone.utc).isoformat(),
        "question": audience_question(question, rnd.phase) if question else None,
        "publication": publication,
    }
