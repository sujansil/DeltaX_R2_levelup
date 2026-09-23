"""Seed admin-created demo data. Idempotent enough for local/dev.

Usage (from repo root, with API deps installed and DB up):

  python -m scripts.seed
"""

from __future__ import annotations

import asyncio

from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal, engine
from app.models import Base, Question, Team, User
from app.security import hash_password

QUESTIONS = [
    (1, "Kinematics", "A particle starts from rest with a = 2 m/s^2. Distance after 3 s?", "numeric", None, {"value": "9", "tolerance": "0"}, "s = ut + 1/2 at^2"),
    (2, "Combinatorics", "How many ways to choose 2 items from 5?", "mcq_single", [{"id": "a", "text": "5"}, {"id": "b", "text": "10"}, {"id": "c", "text": "20"}], {"option_id": "b"}, "nCk = n! / (k!(n-k)!)"),
    (3, "Boolean", "Which values make XOR true?", "mcq_multi", [{"id": "a", "text": "0,0"}, {"id": "b", "text": "0,1"}, {"id": "c", "text": "1,0"}, {"id": "d", "text": "1,1"}], {"option_ids": ["b", "c"]}, "XOR is 1 iff inputs differ"),
    (4, "Language", "TI-83 graphing calculator company (short name)?", "text", None, {"value": "Texas Instruments"}, None),
]


async def main() -> None:
    settings = get_settings()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with SessionLocal() as session:
        for i in range(1, 17):
            name = f"Team {i:02d}"
            username = f"team{i:02d}"
            if (await session.execute(select(Team).where(Team.name == name))).scalar_one_or_none():
                continue
            team = Team(name=name, score=settings.initial_team_score)
            session.add(team)
            await session.flush()
            session.add(
                User(
                    username=username,
                    password_hash=hash_password("pass123"),
                    role="team",
                    team_id=team.id,
                )
            )
        for row in QUESTIONS:
            idx, topic, prompt, qtype, options, key, hint = row
            if (await session.execute(select(Question).where(Question.index == idx))).scalar_one_or_none():
                continue
            session.add(
                Question(
                    index=idx,
                    topic=topic,
                    prompt=prompt,
                    qtype=qtype,
                    options=options,
                    answer_key=key,
                    hint=hint,
                )
            )
        await session.commit()
        print("Seeded 16 teams (team01..team16 / pass123) and sample questions 1-4.")
        print("Create questions 5-15 in the admin UI. Admin login comes from ADMIN_USERNAME / ADMIN_PASSWORD.")


if __name__ == "__main__":
    asyncio.run(main())
