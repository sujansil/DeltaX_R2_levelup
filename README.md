# Delta X — Round 2: Level Up

Round 2 platform for **Delta X**, a team-based mathematics competition.

This repository contains the backend, frontend, database migrations, automated tests, and deployment configuration used to run the round.

## What it includes

- Team authentication and session management
- Up to four authenticated players per team
- Server-authoritative question timing
- Live scoring and grading
- Power-up cards such as Bounty, Freeze, Peek, and Swap
- Admin controls for running the round
- Pause / resume support
- Published leaderboard snapshots
- Audience / projector view
- Published card usage and solution reveal
- PostgreSQL persistence
- Redis-backed realtime/controller features
- Recovery and operational tooling

## Tech stack

- FastAPI
- PostgreSQL
- Redis
- SQLAlchemy
- Alembic
- Docker / Docker Compose
- HTML / CSS / JavaScript

## Project structure

```text
app/          Backend application
static/       Login, player, admin, and audience interfaces
tests/        Automated tests
alembic/      Database migrations
scripts/      Utility and setup scripts
Dockerfile
docker-compose.yml
requirements.txt
start.sh
.env.example
```

## Setup

Clone the repository and enter the project directory:

```bash
git clone https://github.com/sujansil/DeltaX_R2_levelup.git
cd levelup
```

Create a local environment file from the example:

```bash
cp .env.example .env
```

Edit `.env` with your own local or deployment values.

> Never commit real credentials, secrets, API keys, production database URLs, or private environment files.

## Starting the application

For a normal startup:

```bash
./start.sh
```

For a completely fresh local environment:

```bash
./start.sh --fresh
```

`--fresh` recreates local Docker volumes and should only be used when a database reset is intentional.

## Local URLs

After startup:

```text
Main application:
http://localhost:8000

Audience view:
http://localhost:8000/audience
```

## Tests

Run the automated test suite with:

```bash
python -m pytest -q
```

Some integration and concurrency tests require PostgreSQL and Redis services to be running.

## Useful Docker commands

Check service status:

```bash
docker compose ps
```

Follow API logs:

```bash
docker compose logs -f api
```

Stop the stack:

```bash
docker compose down
```

Apply database migrations:

```bash
docker compose run --rm api alembic upgrade head
```

## Status

The project is currently in the testing and deployment-validation stage.

Current focus areas include:

- load testing
- deployment rehearsal
- backup and restore validation
- failure recovery testing
- event-day operational checks
