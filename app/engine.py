from __future__ import annotations

from datetime import timezone
from typing import Any
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.clock import Clock, SystemClock
from app.config import get_settings
from app.models import AuditEvent, Question, RoundState, SwapEvent, Team, TeamQuestion, User
from app.redisutil import release_controller
from app.scoring import (
    BOUNTY_COST,
    FREEZE_BONUS_MS,
    FREEZE_COST,
    PEEK_COST,
    QUESTION_MS,
    apply_swaps_fifo,
    grade_answer,
    is_bottom_half,
    kingslayer_applies,
    points_for_outcome,
    potential_points_ms,
    ranks_by_score,
    validate_submission,
)


class EngineError(Exception):
    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


async def get_round(session: AsyncSession, *, for_update: bool = False) -> RoundState:
    stmt = select(RoundState).where(RoundState.id == 1)
    if for_update:
        stmt = stmt.with_for_update()
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        row = RoundState(id=1, phase="idle")
        session.add(row)
        await session.flush()
    return row


async def audit(
    session: AsyncSession,
    *,
    actor: str,
    kind: str,
    team_id: Any | None = None,
    question_id: Any | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    session.add(
        AuditEvent(
            actor=actor,
            kind=kind,
            team_id=team_id,
            question_id=question_id,
            payload=payload or {},
        )
    )


def _elapsed_ms(started, now) -> int:
    started_utc = _normalize_utc(started)
    now_utc = _normalize_utc(now)
    return max(0, int((now_utc - started_utc).total_seconds() * 1000))


async def _team_for_update(session: AsyncSession, team_id: UUID) -> Team:
    team = (
        await session.execute(select(Team).where(Team.id == team_id).with_for_update())
    ).scalar_one_or_none()
    if team is None:
        raise EngineError("not_found", "Team not found", 404)
    return team


async def _charge(team: Team, cost: int) -> None:
    if team.score < cost:
        raise EngineError("insufficient_score", f"Need {cost} points, have {team.score}")
    team.score -= cost
    team.version += 1


async def snapshot_kingslayer(session: AsyncSession, question: Question) -> dict[UUID, bool]:
    teams = (await session.execute(select(Team))).scalars().all()
    scores = {t.id: t.score for t in teams}
    ranks = ranks_by_score({str(k): v for k, v in scores.items()})
    n = len(teams)
    apply = kingslayer_applies(question.index, get_settings().kingslayer_after_question)
    out: dict[UUID, bool] = {}
    for t in teams:
        out[t.id] = apply and is_bottom_half(ranks[str(t.id)], n)
    return out


async def show_topic(session: AsyncSession, question_id: UUID, actor: str, clock: Clock | None = None) -> RoundState:
    clock = clock or SystemClock()
    rnd = await get_round(session, for_update=True)
    if rnd.phase == "live":
        raise EngineError("bad_phase", "Cannot change question while live")
    q = await session.get(Question, question_id)
    if q is None:
        raise EngineError("not_found", "Question not found", 404)

    rnd.phase = "topic"
    rnd.current_question_id = q.id
    rnd.topic_shown_at = clock.now()
    rnd.live_started_at = None
    rnd.closed_at = None
    rnd.version += 1

    existing = (
        await session.execute(select(TeamQuestion).where(TeamQuestion.question_id == q.id))
    ).scalars().all()
    if any(row.graded for row in existing):
        raise EngineError("already_played", "This question was already closed/graded")
    if not existing:
        eligible = await snapshot_kingslayer(session, q)
        teams = (await session.execute(select(Team))).scalars().all()
        for t in teams:
            session.add(
                TeamQuestion(
                    team_id=t.id,
                    question_id=q.id,
                    kingslayer_eligible=eligible.get(t.id, False),
                )
            )
    await audit(session, actor=actor, kind="show_topic", question_id=q.id, payload={"index": q.index})
    await session.flush()
    return rnd


async def start_question(session: AsyncSession, actor: str, clock: Clock | None = None) -> RoundState:
    clock = clock or SystemClock()
    rnd = await get_round(session, for_update=True)
    if rnd.phase != "topic" or rnd.current_question_id is None:
        raise EngineError("bad_phase", "Show a topic first")
    now = clock.now()
    rnd.phase = "live"
    rnd.live_started_at = now
    rnd.closed_at = None
    rnd.version += 1
    rows = (
        await session.execute(select(TeamQuestion).where(TeamQuestion.question_id == rnd.current_question_id))
    ).scalars().all()
    from datetime import timedelta

    deadline = now + timedelta(milliseconds=QUESTION_MS)
    for row in rows:
        row.deadline_at = deadline
    await audit(session, actor=actor, kind="start_question", question_id=rnd.current_question_id)
    await session.flush()
    return rnd


async def close_question(session: AsyncSession, actor: str, clock: Clock | None = None) -> RoundState:
    clock = clock or SystemClock()
    rnd = await get_round(session, for_update=True)
    if rnd.phase != "live" or rnd.current_question_id is None:
        raise EngineError("bad_phase", "No live question to close")
    q = await session.get(Question, rnd.current_question_id)
    if q is None:
        raise EngineError("not_found", "Question missing", 404)
    now = clock.now()
    assert rnd.live_started_at is not None
    close_elapsed = _elapsed_ms(rnd.live_started_at, now)

    rows = (
        await session.execute(
            select(TeamQuestion)
            .where(TeamQuestion.question_id == q.id)
            .with_for_update()
        )
    ).scalars().all()
    by_team = {row.team_id: row for row in rows}

    swaps = (
        await session.execute(
            select(SwapEvent)
            .where(SwapEvent.question_id == q.id, SwapEvent.applied.is_(False))
            .order_by(SwapEvent.created_at.asc())
        )
    ).scalars().all()

    answers = {tid: row.answer for tid, row in by_team.items()}
    locked = {tid: row.locked for tid, row in by_team.items()}
    answers, locked, _applied = apply_swaps_fifo(
        answers,
        locked,
        [(s.source_team_id, s.target_team_id) for s in swaps],
    )
    for s in swaps:
        s.applied = True
    for tid, row in by_team.items():
        row.answer = answers.get(tid)
        row.locked = locked.get(tid, row.locked)

    teams = (
        await session.execute(select(Team).with_for_update())
    ).scalars().all()
    team_map = {t.id: t for t in teams}

    for row in rows:
        if row.graded:
            continue
        has_answer = bool(row.locked and row.answer)
        correct = grade_answer(q.qtype, q.answer_key, row.answer) if has_answer else None
        delta = points_for_outcome(
            locked=row.locked,
            has_answer=bool(row.answer),
            correct=correct,
            bounty=row.bounty,
            freeze_elapsed_ms=row.freeze_elapsed_ms,
            locked_elapsed_ms=row.locked_elapsed_ms,
            close_elapsed_ms=close_elapsed,
            kingslayer=bool(row.kingslayer_eligible and correct is True),
        )
        row.is_correct = correct if has_answer else None
        row.points_delta = delta
        row.graded = True
        team = team_map[row.team_id]
        team.score += delta
        team.version += 1

    rnd.phase = "closed"
    rnd.closed_at = now
    rnd.version += 1
    await audit(session, actor=actor, kind="close_question", question_id=q.id)
    await session.flush()
    return rnd


async def restart_round(session: AsyncSession, actor: str, clock: Clock | None = None) -> RoundState:
    clock = clock or SystemClock()
    rnd = await get_round(session, for_update=True)
    settings = get_settings()

    await session.execute(delete(TeamQuestion))
    await session.execute(update(Team).values(score=settings.initial_team_score, swap_available=True, version=Team.version + 1))

    rnd.phase = "idle"
    rnd.current_question_id = None
    rnd.topic_shown_at = None
    rnd.live_started_at = None
    rnd.closed_at = None
    rnd.reveal_cards = False
    rnd.version += 1

    teams = (await session.execute(select(Team))).scalars().all()
    for team in teams:
        try:
            await release_controller(str(team.id), force=True)
        except Exception:
            pass

    await audit(session, actor=actor, kind="restart_round", payload={"initial_team_score": settings.initial_team_score})
    await session.flush()
    return rnd


async def reorder_questions(session: AsyncSession, ordered_ids: list[UUID], actor: str | None = None) -> list[Question]:
    if not ordered_ids:
        raise EngineError("bad_request", "Question order cannot be empty")
    if len(set(ordered_ids)) != len(ordered_ids):
        raise EngineError("bad_request", "Question order contains duplicates")

    qs = (await session.execute(select(Question).where(Question.id.in_(ordered_ids)))).scalars().all()
    ids_in_db = {q.id for q in qs}
    if len(qs) != len(ordered_ids) or any(qid not in ids_in_db for qid in ordered_ids):
        raise EngineError("not_found", "All ordered questions must exist")

    by_id = {q.id: q for q in qs}
    ordered_qs = [by_id[qid] for qid in ordered_ids]
    for position, q in enumerate(ordered_qs, start=1):
        q.index = 100_000 + position
    await session.flush()

    for position, q in enumerate(ordered_qs, start=1):
        q.index = position

    if actor is not None:
        await audit(session, actor=actor, kind="reorder_questions", payload={"order": [str(qid) for qid in ordered_ids]})
    await session.flush()
    return ordered_qs


async def activate_bounty(session: AsyncSession, team_id: UUID, actor: str, clock: Clock | None = None) -> TeamQuestion:
    clock = clock or SystemClock()
    rnd = await get_round(session, for_update=True)
    if rnd.phase != "topic" or rnd.current_question_id is None:
        raise EngineError("bad_phase", "Bounty only while the topic is showing, before the question starts")
    team = await _team_for_update(session, team_id)
    row = await _tq(session, team_id, rnd.current_question_id, for_update=True)
    if row.bounty:
        raise EngineError("already_used", "Bounty already active on this question")
    await _charge(team, BOUNTY_COST)
    row.bounty = True
    await audit(
        session,
        actor=actor,
        kind="bounty",
        team_id=team_id,
        question_id=rnd.current_question_id,
        payload={"cost": BOUNTY_COST},
    )
    await session.flush()
    return row


def _normalize_utc(value):
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _still_time(row: TeamQuestion, now) -> None:
    deadline_at = _normalize_utc(row.deadline_at)
    now_utc = _normalize_utc(now)
    if deadline_at is None or now_utc >= deadline_at:
        raise EngineError("time_up", "Team timer has ended")


async def _tq(session: AsyncSession, team_id: UUID, question_id: UUID, *, for_update: bool) -> TeamQuestion:
    stmt = select(TeamQuestion).where(TeamQuestion.team_id == team_id, TeamQuestion.question_id == question_id)
    if for_update:
        stmt = stmt.with_for_update()
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise EngineError("not_found", "Team is not in this question", 404)
    return row


async def use_freeze(session: AsyncSession, team_id: UUID, actor: str, clock: Clock | None = None) -> TeamQuestion:
    clock = clock or SystemClock()
    rnd = await get_round(session, for_update=True)
    if rnd.phase != "live" or rnd.current_question_id is None or rnd.live_started_at is None:
        raise EngineError("bad_phase", "Freeze only during a live question")
    now = clock.now()
    team = await _team_for_update(session, team_id)
    row = await _tq(session, team_id, rnd.current_question_id, for_update=True)
    _still_time(row, now)
    if row.freeze:
        raise EngineError("already_used", "Freeze already used on this question")
    await _charge(team, FREEZE_COST)
    from datetime import timedelta

    row.freeze = True
    row.freeze_elapsed_ms = _elapsed_ms(rnd.live_started_at, now)
    if row.deadline_at is not None :
        row.deadline_at = row.deadline_at + timedelta(milliseconds=FREEZE_BONUS_MS)
    else :
        row.deadline_at = rnd.live_started_at + timedelta(milliseconds=QUESTION_MS + FREEZE_BONUS_MS)
    await audit(
        session,
        actor=actor,
        kind="freeze",
        team_id=team_id,
        question_id=rnd.current_question_id,
        payload={"cost": FREEZE_COST, "elapsed_ms": row.freeze_elapsed_ms},
    )
    await session.flush()
    return row


async def use_peek(session: AsyncSession, team_id: UUID, actor: str, clock: Clock | None = None) -> TeamQuestion:
    clock = clock or SystemClock()
    rnd = await get_round(session, for_update=True)
    if rnd.phase != "live" or rnd.current_question_id is None:
        raise EngineError("bad_phase", "Peek only during a live question")
    now = clock.now()
    team = await _team_for_update(session, team_id)
    row = await _tq(session, team_id, rnd.current_question_id, for_update=True)
    _still_time(row, now)
    if row.peek:
        raise EngineError("already_used", "Peek already used on this question")
    await _charge(team, PEEK_COST)
    row.peek = True
    await audit(
        session,
        actor=actor,
        kind="peek",
        team_id=team_id,
        question_id=rnd.current_question_id,
        payload={"cost": PEEK_COST},
    )
    await session.flush()
    return row


async def lock_answer(
    session: AsyncSession,
    team_id: UUID,
    submitted: dict[str, Any],
    actor: str,
    clock: Clock | None = None,
) -> TeamQuestion:
    clock = clock or SystemClock()
    rnd = await get_round(session, for_update=True)
    if rnd.phase != "live" or rnd.current_question_id is None or rnd.live_started_at is None:
        raise EngineError("bad_phase", "Can only lock during a live question")
    q = await session.get(Question, rnd.current_question_id)
    if q is None:
        raise EngineError("not_found", "Question missing", 404)
    err = validate_submission(q.qtype, submitted)
    if err:
        raise EngineError("bad_answer", err)
    now = clock.now()
    row = await _tq(session, team_id, rnd.current_question_id, for_update=True)
    _still_time(row, now)
    if row.locked:
        raise EngineError("already_locked", "Answer already locked")
    row.answer = submitted
    row.locked = True
    row.locked_elapsed_ms = _elapsed_ms(rnd.live_started_at, now)
    await audit(
        session,
        actor=actor,
        kind="lock",
        team_id=team_id,
        question_id=q.id,
        payload={"elapsed_ms": row.locked_elapsed_ms},
    )
    await session.flush()
    return row


async def use_swap(
    session: AsyncSession,
    team_id: UUID,
    target_team_id: UUID,
    actor: str,
    clock: Clock | None = None,
) -> SwapEvent:
    clock = clock or SystemClock()
    if team_id == target_team_id:
        raise EngineError("bad_target", "Cannot swap with yourself")
    rnd = await get_round(session, for_update=True)
    if rnd.phase != "live" or rnd.current_question_id is None:
        raise EngineError("bad_phase", "Swap only during a live question")
    now = clock.now()
    team = await _team_for_update(session, team_id)
    if not team.swap_available:
        raise EngineError("already_used", "Swap is one-time for the whole round")
    target = await session.get(Team, target_team_id)
    if target is None:
        raise EngineError("not_found", "Target team not found", 404)
    row = await _tq(session, team_id, rnd.current_question_id, for_update=True)
    _still_time(row, now)
    if not row.locked:
        raise EngineError("not_locked", "Lock your answer before swapping")
    if row.swap_used:
        raise EngineError("already_used", "Swap already used on this question")
    team.swap_available = False
    row.swap_used = True
    ev = SwapEvent(
        question_id=rnd.current_question_id,
        source_team_id=team_id,
        target_team_id=target_team_id,
    )
    session.add(ev)
    await audit(
        session,
        actor=actor,
        kind="swap",
        team_id=team_id,
        question_id=rnd.current_question_id,
        payload={"target": str(target_team_id)},
    )
    await session.flush()
    return ev


async def override_grade(
    session: AsyncSession,
    team_id: UUID,
    question_id: UUID,
    correct: bool,
    actor: str,
    clock: Clock | None = None,
) -> TeamQuestion:
    clock = clock or SystemClock()
    rnd = await get_round(session, for_update=True)
    q = await session.get(Question, question_id)
    if q is None:
        raise EngineError("not_found", "Question not found", 404)
    if rnd.phase == "live" and rnd.current_question_id == question_id:
        raise EngineError("bad_phase", "Wait until the question is closed")
    team = await _team_for_update(session, team_id)
    row = await _tq(session, team_id, question_id, for_update=True)
    if not row.graded:
        raise EngineError("not_graded", "Question has not been graded yet")
    started = rnd.live_started_at if rnd.current_question_id == question_id else None
    close_elapsed = 0
    if started and rnd.closed_at:
        close_elapsed = _elapsed_ms(started, rnd.closed_at)
    new_delta = points_for_outcome(
        locked=row.locked,
        has_answer=bool(row.answer),
        correct=correct,
        bounty=row.bounty,
        freeze_elapsed_ms=row.freeze_elapsed_ms,
        locked_elapsed_ms=row.locked_elapsed_ms,
        close_elapsed_ms=close_elapsed,
        kingslayer=bool(row.kingslayer_eligible and correct),
    )
    team.score += new_delta - row.points_delta
    team.version += 1
    row.points_delta = new_delta
    row.is_correct = correct
    row.admin_override = True
    await audit(
        session,
        actor=actor,
        kind="override",
        team_id=team_id,
        question_id=question_id,
        payload={"correct": correct, "delta": new_delta},
    )
    await session.flush()
    return row


async def try_autostart(session: AsyncSession, clock: Clock | None = None) -> bool:
    clock = clock or SystemClock()
    rnd = await get_round(session)
    if rnd.phase != "topic" or rnd.current_question_id is None or rnd.topic_shown_at is None:
        return False
    if _elapsed_ms(rnd.topic_shown_at, clock.now()) < get_settings().topic_duration_sec * 1000:
        return False
    try:
        from app.redisutil import acquire_lock

        if not await acquire_lock("auto_start_question", ttl_sec=5):
            return False
    except Exception:
        pass
    await start_question(session, actor="system:autostart", clock=clock)
    return True


async def try_autoclose(session: AsyncSession, clock: Clock | None = None) -> bool:
    clock = clock or SystemClock()
    rnd = await get_round(session)
    if rnd.phase != "live" or rnd.live_started_at is None:
        return False
    max_ms = QUESTION_MS
    if _elapsed_ms(rnd.live_started_at, clock.now()) < max_ms:
        return False
    try:
        from app.redisutil import acquire_lock

        if not await acquire_lock("close_question", ttl_sec=10):
            return False
    except Exception:
        pass
    await close_question(session, actor="system:autoclose", clock=clock)
    return True


def team_public_view(team: Team) -> dict[str, Any]:
    return {"id": str(team.id), "name": team.name, "score": team.score}


async def leaderboard(session: AsyncSession) -> list[dict[str, Any]]:
    teams = (await session.execute(select(Team))).scalars().all()
    scores = {str(t.id): t.score for t in teams}
    ranks = ranks_by_score(scores)
    rows = [
        {"id": str(t.id), "name": t.name, "score": t.score, "rank": ranks[str(t.id)]}
        for t in teams
    ]
    rows.sort(key=lambda r: (r["rank"], r["name"]))
    return rows


def question_public(q: Question, *, phase: str, include_body: bool) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": str(q.id),
        "index": q.index,
        "topic": q.topic,
        "type": q.qtype,
    }
    if include_body:
        data["prompt"] = q.prompt
        data["options"] = q.options
    return data


def tq_player_view(
    row: TeamQuestion,
    q: Question | None,
    *,
    live_started_at,
    now,
    include_hint: bool,
) -> dict[str, Any]:
    elapsed = _elapsed_ms(live_started_at, now) if live_started_at else 0
    scoring_ms = row.freeze_elapsed_ms if row.freeze else elapsed
    return {
        "bounty": row.bounty,
        "freeze": row.freeze,
        "peek": row.peek,
        "swap_used": row.swap_used,
        "locked": row.locked,
        "answer": row.answer if row.locked else None,
        "deadline_at": row.deadline_at.isoformat() if row.deadline_at else None,
        "kingslayer_eligible": row.kingslayer_eligible,
        "potential_points": potential_points_ms(scoring_ms, row.bounty) if live_started_at else potential_points_ms(0, row.bounty),
        "graded": row.graded,
        "is_correct": row.is_correct if row.graded else None,
        "points_delta": row.points_delta if row.graded else None,
        "hint": (q.hint if q and include_hint and row.peek else None),
    }


async def admin_question_matrix(session: AsyncSession, question_id: UUID) -> dict[str, Any]:
    q = await session.get(Question, question_id)
    if q is None:
        raise EngineError("not_found", "Question not found", 404)
    rows = (
        await session.execute(select(TeamQuestion).where(TeamQuestion.question_id == question_id))
    ).scalars().all()
    teams = {t.id: t for t in (await session.execute(select(Team))).scalars().all()}
    swaps = (
        await session.execute(select(SwapEvent).where(SwapEvent.question_id == question_id).order_by(SwapEvent.created_at))
    ).scalars().all()
    return {
        "question": {
            "id": str(q.id),
            "index": q.index,
            "topic": q.topic,
            "prompt": q.prompt,
            "type": q.qtype,
            "options": q.options,
            "answer_key": q.answer_key,
            "hint": q.hint,
        },
        "teams": [
            {
                "team_id": str(r.team_id),
                "team_name": teams[r.team_id].name if r.team_id in teams else "?",
                "score": teams[r.team_id].score if r.team_id in teams else None,
                "bounty": r.bounty,
                "freeze": r.freeze,
                "peek": r.peek,
                "swap_used": r.swap_used,
                "locked": r.locked,
                "answer": r.answer,
                "kingslayer_eligible": r.kingslayer_eligible,
                "graded": r.graded,
                "is_correct": r.is_correct,
                "points_delta": r.points_delta,
                "deadline_at": r.deadline_at.isoformat() if r.deadline_at else None,
            }
            for r in rows
        ],
        "swaps": [
            {
                "source": str(s.source_team_id),
                "target": str(s.target_team_id),
                "applied": s.applied,
                "at": s.created_at.isoformat() if s.created_at else None,
            }
            for s in swaps
        ],
    }


async def load_user_by_username(session: AsyncSession, username: str) -> User | None:
    return (
        await session.execute(select(User).options(selectinload(User.team)).where(User.username == username))
    ).scalar_one_or_none()
