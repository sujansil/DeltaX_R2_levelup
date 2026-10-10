# Delta X — Round 2: Level Up

Production platform for **Round 2 of Delta X**, a live team-based mathematics competition.

The system handles synchronized questions, team sessions, scoring, power-ups, admin controls, audience views, persistence and recovery during the competition.

The project is currently being tested and prepared for deployment.

---

## Core Features

### Team System

- Team-based authentication
- Up to four authenticated players per team
- Shared team state across devices
- Session and controller handling

### Live Question System

- Admin-controlled question start
- Server-authoritative 150-second question timer
- Same live question state across teams
- Automatic question closing
- Persisted deadlines so refreshing or restarting services does not reset the timer

### Scoring

The scoring engine handles:

- time-based scoring
- normal and bounty scoring modes
- answer penalties
- score floors
- fractional timing calculations
- scoring boundary cases

Scoring behaviour is covered by automated tests.

### Power-Ups

Round 2 includes several strategic power-up cards:

- **Bounty**
- **Peek**
- **Freeze**
- **Swap**

Power-up usage is handled on the backend so critical state changes do not depend only on the browser.

Some actions use database locking and transaction handling to prevent duplicate or conflicting requests.

### Admin Controls

The admin interface can control the live round, including:

- starting questions
- pausing/resuming gameplay
- controlling question state
- publishing leaderboard snapshots
- publishing card usage
- revealing solutions
- monitoring the running round

### Audience View

A separate audience/projector interface is available for displaying event information and published leaderboard state.

---

## Reliability and Recovery

The platform includes support for:

- persistent database state
- persisted question deadlines
- server restart recovery
- Redis-backed live/controller state
- operational logs
- backup/export tooling
- deployment rehearsal
- failure-recovery testing

---

## Architecture

```text
Player / Admin / Audience Browser
              |
              v
           FastAPI
              |
       +------+------+
       |             |
       v             v
   PostgreSQL      Redis
       |
       v
Persistent competition state
```

### PostgreSQL

Stores persistent competition data such as teams, questions, answers, scores and round state.

### Redis

Used for realtime/controller features and short-lived operational state.

### FastAPI

Handles authentication, gameplay APIs, scoring, power-ups, admin actions and server-authoritative timing.

### Frontend

Player, admin, login and audience interfaces are implemented using HTML, CSS and JavaScript.

---

## Tech Stack

- Python
- FastAPI
- SQLAlchemy
- PostgreSQL
- Redis
- Alembic
- Docker / Docker Compose
- HTML
- CSS
- JavaScript
- Pytest

---

## Project Structure

```text
app/
    Backend application and game logic

static/
    Login, player, admin and audience interfaces

tests/
    Automated test suite

alembic/
    Database migrations

scripts/
    Setup and operational utilities

Dockerfile
docker-compose.yml
requirements.txt
start.sh
.env.example
```

---

## Testing

The project contains automated tests for important competition behaviour, including:

- scoring logic
- scoring boundary conditions
- question timing
- player state
- power-up behaviour
- persistence
- duplicate-action protection
- selected concurrency-sensitive flows

Run the test suite with:

```bash
python -m pytest -q
```

Some integration and concurrency tests require PostgreSQL and Redis to be running.

---

## Running Locally

Clone the repository:

```bash
git clone https://github.com/sujansil/DeltaX_R2_levelup.git
cd DeltaX_R2_levelup
```

Create the local environment configuration:

```bash
cp .env.example .env
```

Fill the required values in `.env`.

Never commit real credentials, production database URLs, API keys or private environment files.

Start the application:

```bash
./start.sh
```

For a completely fresh local environment:

```bash
./start.sh --fresh
```

`--fresh` recreates the local Docker volumes and should only be used when a complete database reset is intended.

---

## Local URLs

After startup:

```text
Main application:
http://localhost:8000

Audience view:
http://localhost:8000/audience
```

---

## Useful Commands

Check running services:

```bash
docker compose ps
```

Follow API logs:

```bash
docker compose logs -f api
```

Apply database migrations:

```bash
docker compose run --rm api alembic upgrade head
```

Stop the stack:

```bash
docker compose down
```

---

## Current Status

The platform is currently in the testing and deployment-validation stage.

Current work includes:

- load testing
- production deployment rehearsal
- backup and restore validation
- failure-recovery testing
- event-day operational checks

---

## Project Context

Delta X is a multi-round mathematics competition.

This repository contains the platform specifically for **Round 2 — Level Up**.
