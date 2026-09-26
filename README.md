# Level Up — Round 2

Server-authoritative quiz engine for 16 teams and 250+ concurrent viewers (one shared login per team). The UI is intentionally plain; the rules live in `app/scoring.py` and `app/engine.py`.

## Rules implemented (as we locked them)

- **Admin** shows the topic, then starts the question. After start: 150s, cards, and auto-close are automatic (hard stop at 180s so a Freeze can finish). Admin can also force-close.
- **Timeout / never lock = 0** (no penalty). Card costs already spent still stay spent.
- **Lock is explicit and one-shot.** Wrong lock = −500 (−1500 with Bounty).
- **Scoring:** 2000 for 60s, then −20/s until 135s, floor 500.
- **Bounty** (during topic only, −2000): 6000 / −1500 / 60pts/s / min 1500. Once per question.
- **Freeze** (−750, once per question, anytime before that team’s timer ends): snapshots the current point value (decay stops) and **+30s** on that team’s deadline.
- **Peek** (−750, once per question): shows that question’s hint. Other teams never see it.
- **Swap** (free, **once for the whole round**): after you locked, before your timer ends, vs any team. Applied at question close, FIFO. Target is notified **after the question ends**.
- **KingSlayer:** question **index > 5**. Snapshot at topic-show. Rank = 1 + teams with a strictly higher score. Bottom half = rank ≥ n/2+1 (ranks 9–16 with 16 teams). Ties for 8th are **not** bottom half. +500 on a correct answer only.
- **Cards are private.** Other players cannot see who used what. Admin sees the live matrix. Optional end-of-game reveal flag exists (`POST /api/v1/admin/round/reveal-cards/1`) but is not auto-broadcast to the public board.
- **Controller:** first device to `Become controller` can submit/cards. Others are view-only. Heartbeat 40s TTL. Admin can force-release.

Card costs require enough score. Default starting score is `INITIAL_TEAM_SCORE` (0 unless you change it). Give teams a bank in admin if you want Bounty on Q1.

## Run (recommended)

```bash
cp .env.example .env   # set SECRET_KEY and ADMIN_PASSWORD
docker compose up --build
```

Then seed demo teams/questions:

```bash
docker compose exec api python -m scripts.seed
```

Open http://localhost:8000

- Admin: `ADMIN_USERNAME` / `ADMIN_PASSWORD` (defaults `admin` / `change-me`)
- Teams: `team01` … `team16` / `pass123`

Without Docker: PostgreSQL 16 + Redis 7, `pip install -r requirements.txt`, `uvicorn app.main:app --reload --host 0.0.0.0 --port 8000`.

```bash
pytest -q
```

## Why this holds 250 connections

- **Postgres** is the source of truth. Score and card mutations use **row locks** (`FOR UPDATE`) on the team / round / team-question rows.
- **WebSocket connections do not hold DB connections.** Workers fan out via **Redis pub/sub**, so you can run multiple Uvicorn workers.
- Timers are **timestamps**, not per-second jobs per user. Clients render countdown from `deadline_at` + `server_now`.
- Auto-close is a 250ms loop with a **Redis NX lock** so only one worker grades.
- Pool: 20+20 overflow per API process. 250 viewers ≠ 250 DB sessions.

Scale later: raise `--workers`, point `DATABASE_URL` at managed Postgres (RDS/Neon/Crunchy), `REDIS_URL` at ElastiCache/Upstash, put the API behind any reverse proxy. Sticky sessions are **not** required.

## API sketch

| Action | Endpoint |
|---|---|
| Login | `POST /api/v1/auth/login` |
| Play state | `GET /api/v1/play/state` |
| Claim writer | `POST /api/v1/play/controller/claim` |
| Cards / lock / swap | `POST /api/v1/play/{bounty,freeze,peek,lock,swap}` |
| Admin teams/questions | `POST/GET /api/v1/admin/teams`, `.../questions` |
| Show topic / start / close | `POST /api/v1/admin/round/show-topic/{id}`, `.../start`, `.../close` |
| Override grade | `POST /api/v1/admin/grade/override` |
| Live socket | `WS /ws?token=...` |

Question payload `qtype`: `mcq_single` | `mcq_multi` | `numeric` | `text`.

Answer key examples:

```json
{"option_id": "b"}
{"option_ids": ["b", "c"]}
{"value": "9", "tolerance": "0"}
{"value": "Texas Instruments"}
```

## Production checklist

- Strong `SECRET_KEY` and admin password
- HTTPS at the proxy
- Managed Postgres + Redis with persistence
- Do not expose Postgres/Redis ports
- Change team passwords after seed
- Set `INITIAL_TEAM_SCORE` or assign banks before round start
