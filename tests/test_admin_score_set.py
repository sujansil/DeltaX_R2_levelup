from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.deps import Principal
from app.engine import get_round
from app.models import AuditEvent, Base, PublishedLeaderboard, Question, Team
from app.routers import admin as admin_routes
from app.routers import play as play_routes
from app.routers.admin import ScoreIn


def admin_principal(username="score-admin"):
    return Principal(
        user_id=uuid4(),
        username=username,
        role="admin",
        team_id=None,
        session_id=str(uuid4()),
    )


def team_principal(team):
    return Principal(
        user_id=uuid4(),
        username="alpha",
        role="team",
        team_id=team.id,
        session_id=str(uuid4()),
    )


@pytest.fixture
async def score_ctx(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async with maker() as session:
        team = Team(id=uuid4(), name="Alpha", score=100, version=4)
        question = Question(
            id=uuid4(),
            index=3,
            topic="Safety",
            prompt="What is locked?",
            qtype="mcq_single",
            options=[{"id": "row", "text": "The row"}],
            answer_key={"option_id": "row"},
        )
        session.add_all([team, question])
        await session.flush()
        rnd = await get_round(session)
        rnd.phase = "closed"
        rnd.current_question_id = question.id
        rnd.version = 12
        session.add(
            PublishedLeaderboard(
                id=1,
                published_at=datetime.now(timezone.utc),
                published_by="publisher",
                round_version=11,
                phase="closed",
                question_id=question.id,
                question_index=question.index,
                rows=[{"id": str(team.id), "name": team.name, "score": 90, "rank": 1}],
            )
        )
        await session.commit()

        async def no_fanout(session, extra_admin=None):
            return None

        monkeypatch.setattr(admin_routes, "_fanout", no_fanout)
        yield {"session": session, "maker": maker, "team": team, "question": question}

    await engine.dispose()


async def test_score_set_is_atomic_and_records_complete_audit(score_ctx):
    session = score_ctx["session"]
    team = score_ctx["team"]
    response = await admin_routes.set_score(
        team.id,
        ScoreIn(score=250),
        session=session,
        admin=admin_principal(),
    )

    assert response == {"id": str(team.id), "score": 250}
    await session.refresh(team)
    assert team.score == 250
    assert team.version == 5

    event = (
        await session.execute(
            select(AuditEvent).where(AuditEvent.kind == "score_set")
        )
    ).scalar_one()
    assert event.actor == "score-admin"
    assert event.team_id == team.id
    assert event.question_id == score_ctx["question"].id
    assert event.payload == {
        "old_score": 100,
        "new_score": 250,
        "delta": 150,
        "round_version": 12,
        "phase": "closed",
        "current_question_id": str(score_ctx["question"].id),
    }


@pytest.mark.parametrize(
    "new_score,expected_delta",
    [(100, 0), (-50, -150)],
)
async def test_same_and_negative_scores_remain_valid(
    score_ctx, new_score, expected_delta
):
    session = score_ctx["session"]
    team = score_ctx["team"]
    await admin_routes.set_score(
        team.id,
        ScoreIn(score=new_score),
        session=session,
        admin=admin_principal(),
    )

    await session.refresh(team)
    assert team.score == new_score
    assert team.version == 5
    event = (
        await session.execute(
            select(AuditEvent).where(AuditEvent.kind == "score_set")
        )
    ).scalar_one()
    assert event.payload["delta"] == expected_delta


async def test_missing_team_creates_no_score_audit(score_ctx):
    with pytest.raises(Exception) as exc:
        await admin_routes.set_score(
            uuid4(),
            ScoreIn(score=200),
            session=score_ctx["session"],
            admin=admin_principal(),
        )
    assert getattr(exc.value, "status_code", None) == 404
    assert await score_ctx["session"].scalar(
        select(func.count()).select_from(AuditEvent).where(
            AuditEvent.kind == "score_set"
        )
    ) == 0
    await score_ctx["session"].rollback()


async def test_database_failure_rolls_back_score_and_audit(score_ctx):
    session = score_ctx["session"]
    team_id = score_ctx["team"].id

    class FailingCommitSession:
        def __init__(self, delegate):
            self.delegate = delegate

        async def execute(self, statement, *args, **kwargs):
            return await self.delegate.execute(statement, *args, **kwargs)

        def add(self, instance):
            self.delegate.add(instance)

        async def flush(self):
            return await self.delegate.flush()

        async def commit(self):
            await self.delegate.flush()
            raise RuntimeError("commit failed")

    with pytest.raises(RuntimeError, match="commit failed"):
        await admin_routes.set_score(
            team_id,
            ScoreIn(score=999),
            session=FailingCommitSession(session),
            admin=admin_principal(),
        )
    await session.rollback()

    async with score_ctx["maker"]() as verification:
        assert (await verification.get(Team, team_id)).score == 100
        assert await verification.scalar(
            select(func.count()).select_from(AuditEvent).where(
                AuditEvent.kind == "score_set"
            )
        ) == 0


async def test_postcommit_fanout_failure_cannot_undo_score_or_audit(
    score_ctx, monkeypatch
):
    session = score_ctx["session"]
    team_id = score_ctx["team"].id

    async def fail_fanout(session, extra_admin=None):
        raise RuntimeError("fanout failed")

    monkeypatch.setattr(admin_routes, "_fanout", fail_fanout)
    with pytest.raises(RuntimeError, match="fanout failed"):
        await admin_routes.set_score(
            team_id,
            ScoreIn(score=350),
            session=session,
            admin=admin_principal(),
        )

    async with score_ctx["maker"]() as verification:
        assert (await verification.get(Team, team_id)).score == 350
        assert await verification.scalar(
            select(func.count()).select_from(AuditEvent).where(
                AuditEvent.kind == "score_set"
            )
        ) == 1


async def test_score_edit_does_not_change_published_player_board(score_ctx, monkeypatch):
    session = score_ctx["session"]
    team = score_ctx["team"]
    published_before = list((await session.get(PublishedLeaderboard, 1)).rows)
    await admin_routes.set_score(
        team.id,
        ScoreIn(score=5000),
        session=session,
        admin=admin_principal(),
    )

    async def no_controller(team_id):
        return None

    monkeypatch.setattr(play_routes, "get_controller", no_controller)
    player_state = await play_routes.state(session, team_principal(team))
    published_after = (await session.get(PublishedLeaderboard, 1)).rows
    assert published_after == published_before
    assert player_state["leaderboard"] == published_before
    assert player_state["team"]["score"] == 5000


async def test_score_edit_requests_round_then_team_row_locks(score_ctx, monkeypatch):
    session = score_ctx["session"]
    locked_tables = []

    class RecordingSession:
        def __init__(self, delegate):
            self.delegate = delegate

        async def execute(self, statement, *args, **kwargs):
            if statement._for_update_arg is not None:
                locked_tables.extend(table.name for table in statement.get_final_froms())
            return await self.delegate.execute(statement, *args, **kwargs)

        def add(self, instance):
            self.delegate.add(instance)

        async def commit(self):
            await self.delegate.commit()

    async def no_fanout(session, extra_admin=None):
        return None

    monkeypatch.setattr(admin_routes, "_fanout", no_fanout)
    await admin_routes.set_score(
        score_ctx["team"].id,
        ScoreIn(score=200),
        session=RecordingSession(session),
        admin=admin_principal(),
    )
    assert locked_tables[:2] == ["round_state", "teams"]
