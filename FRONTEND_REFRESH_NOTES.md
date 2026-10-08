# R2 frontend refresh

## Player
- Responsive single codebase: 3-column laptop arena and single-column phone arena.
- Laptop published leaderboard sidebar; phone/tablet leaderboard drawer.
- Published rank in header, team score, controller state, power-ups, hint, locked-answer state.
- Live potential-points value/bar using server-authoritative timestamps and the existing R2 scoring rules.
- Potential points now freeze visually at the persisted `locked_elapsed_ms` after answer lock.
- Question/option images use `object-fit: contain` and are never intentionally cropped.
- Mobile power-up dock and swap modal.
- 5-second state polling fallback in addition to WebSocket updates.
- Server-side logout/session-revocation handling.
- No Tailwind/Lucide/CDN dependency on player page.

## Admin
- Laptop-only multi-pane control-console layout with local panel scrolling.
- Round control, question queue, team matrix, ops monitor, selected-team inspector, audit stream, session monitor.
- Active session counts, controller visibility, revoke-one/revoke-all, release controller.
- Start/close, power-up visibility, leaderboard publish, safe restart UI using confirmation + expected round version.
- Manual score and grade override tools remain available.
- Health/readiness indicators via `/api/health` and `/api/ready`.
- No Tailwind/Lucide/CDN dependency on admin page.

## Backend display-only adjustment
`app/engine.py::tq_player_view()` now:
- uses `locked_elapsed_ms` for displayed potential points after a lock;
- exposes `freeze_elapsed_ms` and `locked_elapsed_ms` to the authenticated team's frontend.

This does not change final scoring or Freeze engine semantics.

## Validation performed here
- `node --check static/player.js`
- `node --check static/admin.js`
- `python -m py_compile app/engine.py`
- `python -m pytest tests/test_frontend_static.py -q` -> 2 passed
- duplicate-ID/static reference checks

The full Python suite could not be executed in this container because the uploaded `.venv` is bound to the original `/home/sujan/...` Python 3.14 installation and this runtime does not have the project's `asyncpg`/`aiosqlite` dependencies. Run the full suite locally after copying the files.
