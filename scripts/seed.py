"""Seed admin-created demo data. Idempotent enough for local/dev.

Usage (from repo root, with API deps installed and DB up):

  python -m scripts.seed
"""

from __future__ import annotations

import asyncio

from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.models import Question, Team, User
from app.security import hash_password

# Added an 8th element to each tuple for the main question's media file path.
# Notice Question 7 demonstrates adding 'media_url' directly into the options JSON.
QUESTIONS = [
    (1, "Testing", "What is 1 + 1?", "mcq_single", 
     [{"id": "a", "text": "1"}, {"id": "b", "text": "2"}, {"id": "c", "text": "3"}], 
     {"option_id": "b"}, "Basic addition.", None),
    
    (2, "Testing", "What color is a clear sky?", "mcq_single", 
     [{"id": "a", "text": "Blue"}, {"id": "b", "text": "Green"}, {"id": "c", "text": "Red"}], 
     {"option_id": "a"}, "Look up during the day.", "/static/uploads/sky.jpg"), 
    
    (3, "Testing", "Which animal is known to bark?", "mcq_single", 
     [{"id": "a", "text": "Cat"}, {"id": "b", "text": "Dog"}, {"id": "c", "text": "Cow"}], 
     {"option_id": "b"}, "Man's best friend.", None),
    
    (4, "Testing", "What is H2O commonly known as?", "mcq_single", 
     [{"id": "a", "text": "Air"}, {"id": "b", "text": "Dirt"}, {"id": "c", "text": "Water"}], 
     {"option_id": "c"}, "You drink it when thirsty.", None),
    
    (5, "Testing", "How many days are in a standard week?", "mcq_single", 
     [{"id": "a", "text": "5"}, {"id": "b", "text": "7"}, {"id": "c", "text": "10"}], 
     {"option_id": "b"}, "Starts with Sunday, ends with Saturday.", None),
    
    (6, "Testing", "What is 10 divided by 2?", "mcq_single", 
     [{"id": "a", "text": "5"}, {"id": "b", "text": "20"}, {"id": "c", "text": "2"}], 
     {"option_id": "a"}, "Exactly half of ten.", None),
    
    (7, "Testing", "Which fruit is yellow and curved?", "mcq_single", 
     [
         {"id": "a", "text": "Apple", "media_url": "/static/uploads/apple.jpg"}, 
         {"id": "b", "text": "Banana", "media_url": "/static/uploads/banana.jpg"}, 
         {"id": "c", "text": "Grape", "media_url": "/static/uploads/grape.png"}
     ], 
     {"option_id": "b"}, "Monkeys love to eat them.", None),
    
    (8, "Testing", "How many sides does a triangle have?", "mcq_single", 
     [{"id": "a", "text": "3"}, {"id": "b", "text": "4"}, {"id": "c", "text": "5"}], 
     {"option_id": "a"}, "The prefix 'tri' gives it away.", None),
    
    (9, "Testing", "What is the opposite of North?", "mcq_single", 
     [{"id": "a", "text": "East"}, {"id": "b", "text": "West"}, {"id": "c", "text": "South"}], 
     {"option_id": "c"}, "Points to the bottom of a standard map.", None),
    
    (10, "Testing", "What falls from the clouds when it rains?", "mcq_single", 
     [{"id": "a", "text": "Water"}, {"id": "b", "text": "Rocks"}, {"id": "c", "text": "Sand"}], 
     {"option_id": "a"}, "Liquid precipitation.", None),
    
    (11, "Testing", "What is the first letter of the English alphabet?", "mcq_single", 
     [{"id": "a", "text": "Z"}, {"id": "b", "text": "A"}, {"id": "c", "text": "B"}], 
     {"option_id": "b"}, "It's the very beginning.", None),
    
    (12, "Testing", "How many wheels does a standard bicycle have?", "mcq_single", 
     [{"id": "a", "text": "1"}, {"id": "b", "text": "2"}, {"id": "c", "text": "4"}], 
     {"option_id": "b"}, "The prefix 'bi' means two.", None),
    
    (13, "Testing", "How many hours are in a single day?", "mcq_single", 
     [{"id": "a", "text": "12"}, {"id": "b", "text": "24"}, {"id": "c", "text": "48"}], 
     {"option_id": "b"}, "Double of 12.", None),
    
    (14, "Testing", "What device is commonly used to click items on a computer screen?", "mcq_single", 
     [{"id": "a", "text": "Keyboard"}, {"id": "b", "text": "Monitor"}, {"id": "c", "text": "Mouse"}], 
     {"option_id": "c"}, "Named after a small rodent.", None),
    
    (15, "Testing", "Is fire hot or cold?", "mcq_single", 
     [{"id": "a", "text": "Cold"}, {"id": "b", "text": "Hot"}, {"id": "c", "text": "Room Temperature"}], 
     {"option_id": "b"}, "Don't touch it!", None)
]


async def main() -> None:
    settings = get_settings()
    async with SessionLocal() as session:
        # Seed 16 teams
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
            
        # Seed 15 simple MCQ questions with media mapping
        for row in QUESTIONS:
            idx, topic, prompt, qtype, options, key, hint, media_path = row
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
                    media_url=media_path,
                )
            )
            
        await session.commit()
        print("Seeded 16 teams (team01..team16 / password: pass123).")
        print("Seeded 15 sample mcq_single test questions.")
        print("Admin login comes from ADMIN_USERNAME / ADMIN_PASSWORD.")


if __name__ == "__main__":
    asyncio.run(main())
