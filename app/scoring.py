"""Pure scoring / ranking / grading. No I/O. This is the source of truth for Round 2 math."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

BASE_SCORE = 2000
BASE_PENALTY = -500
BASE_DECAY_PER_SEC = 20
BASE_MIN = 500
BOUNTY_SCORE = 6000
BOUNTY_PENALTY = -1500
BOUNTY_DECAY_PER_SEC = 150
BOUNTY_MIN = 1500
BOUNTY_COST = 2000
FREEZE_COST = 750
PEEK_COST = 750
DECAY_START_MS = 60_000
DECAY_END_MS = 135_000
QUESTION_MS = 150_000
FREEZE_BONUS_MS = 30_000
KINGSLAYER_BONUS = 500


@dataclass(frozen=True)
class ScoreVars:
    correct_base: int
    penalty: int
    decay_per_sec: int
    minimum: int


def vars_for(bounty: bool) -> ScoreVars:
    if bounty:
        return ScoreVars(BOUNTY_SCORE, BOUNTY_PENALTY, BOUNTY_DECAY_PER_SEC, BOUNTY_MIN)
    return ScoreVars(BASE_SCORE, BASE_PENALTY, BASE_DECAY_PER_SEC, BASE_MIN)


def potential_points_ms(elapsed_ms: int, bounty: bool) -> int:
    """Point value of a correct answer at `elapsed_ms` after live start (or freeze snapshot)."""
    elapsed_ms = max(0, elapsed_ms)
    v = vars_for(bounty)
    if elapsed_ms <= DECAY_START_MS:
        return v.correct_base
    if elapsed_ms >= DECAY_END_MS:
        return v.minimum
    elapsed_after = elapsed_ms - DECAY_START_MS
    decayed = (v.decay_per_sec * elapsed_after) // 1000
    return max(v.minimum, v.correct_base - decayed)


def scoring_elapsed_ms(*, freeze_elapsed_ms: int | None, locked_elapsed_ms: int | None, close_elapsed_ms: int) -> int:
    if freeze_elapsed_ms is not None:
        return freeze_elapsed_ms
    if locked_elapsed_ms is not None:
        return locked_elapsed_ms
    return close_elapsed_ms


def points_for_outcome(
    *,
    locked: bool,
    has_answer: bool,
    correct: bool | None,
    bounty: bool,
    freeze_elapsed_ms: int | None,
    locked_elapsed_ms: int | None,
    close_elapsed_ms: int,
    kingslayer: bool,
) -> int:
    """
    Timeout / never locked / empty answer after close => 0 (card costs are separate).
    Wrong => penalty. Correct => time value + optional KingSlayer.
    """
    if not locked or not has_answer:
        return 0
    if correct is not True:
        return vars_for(bounty).penalty
    elapsed = scoring_elapsed_ms(
        freeze_elapsed_ms=freeze_elapsed_ms,
        locked_elapsed_ms=locked_elapsed_ms,
        close_elapsed_ms=close_elapsed_ms,
    )
    pts = potential_points_ms(elapsed, bounty)
    if kingslayer:
        pts += KINGSLAYER_BONUS
    return pts


def ranks_by_score(scores: dict[str, int]) -> dict[str, int]:
    """Competition rank: 1 + number of teams with a strictly higher score. Ties share rank."""
    return {tid: 1 + sum(1 for s in scores.values() if s > score) for tid, score in scores.items()}


def is_bottom_half(rank: int, team_count: int) -> bool:
    if team_count <= 0:
        return False
    return rank >= (team_count // 2) + 1


def kingslayer_applies(question_index: int, after_question: int = 5) -> bool:
    return question_index > after_question


def _norm_text(value: str) -> str:
    return " ".join(value.casefold().split())


def grade_answer(question_type: str, answer_key: dict[str, Any], submitted: dict[str, Any] | None) -> bool:
    if not submitted:
        return False
    if question_type == "mcq_single":
        return str(submitted.get("option_id", "")) == str(answer_key.get("option_id", ""))
    if question_type == "mcq_multi":
        got = {str(x) for x in (submitted.get("option_ids") or [])}
        want = {str(x) for x in (answer_key.get("option_ids") or [])}
        return got == want and len(want) > 0
    if question_type == "numeric":
        try:
            got = Decimal(str(submitted.get("value", "")).strip())
            want = Decimal(str(answer_key.get("value", "")).strip())
        except (InvalidOperation, TypeError, ValueError):
            return False
        tol = Decimal(str(answer_key.get("tolerance", "0") or "0"))
        return abs(got - want) <= tol
    if question_type == "text":
        return _norm_text(str(submitted.get("value", ""))) == _norm_text(str(answer_key.get("value", "")))
    return False


def validate_submission(question_type: str, submitted: dict[str, Any]) -> str | None:
    if question_type == "mcq_single":
        if not str(submitted.get("option_id", "")).strip():
            return "option_id required"
        return None
    if question_type == "mcq_multi":
        ids = submitted.get("option_ids")
        if not isinstance(ids, list) or not ids:
            return "option_ids required"
        return None
    if question_type in ("numeric", "text"):
        if submitted.get("value") is None or str(submitted.get("value")).strip() == "":
            return "value required"
        if question_type == "numeric":
            try:
                Decimal(str(submitted.get("value")).strip())
            except (InvalidOperation, TypeError, ValueError):
                return "numeric value required"
        return None
    return "unknown question type"


def apply_swaps_fifo(
    answers: dict[str, dict[str, Any] | None],
    locked: dict[str, bool],
    swaps: list[tuple[str, str]],
) -> tuple[dict[str, dict[str, Any] | None], dict[str, bool], list[tuple[str, str]]]:
    """
    Each swap exchanges current answers. Source must be locked (caller-enforced).
    If target was not locked, they become locked (answer received).
    Returns (answers, locked, applied_pairs).
    """
    a = dict(answers)
    lck = dict(locked)
    applied: list[tuple[str, str]] = []
    for src, dst in swaps:
        if src not in a or dst not in a:
            continue

        if not lck.get(dst) :
            continue
        
        a[src], a[dst] = a[dst], a[src]
        if not lck.get(dst):
            lck[dst] = True
        lck[src] = True
        applied.append((src, dst))
    return a, lck, applied
