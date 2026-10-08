# Admin operations patch

Adds:
- Per-team live remaining time in TEAM_MATRIX, using each TeamQuestion.deadline_at. Freeze teams are marked with `F` and use the already-existing extended deadline; Freeze engine/scoring semantics are unchanged.
- Per-team full gameplay audit history endpoint and UI. Clicking a team switches AUDIT_STREAM to that team's complete AuditEvent history; ALL EVENT toggles back to the global stream.
- Full question viewer (prompt, image, options, answer key, hint) from the admin queue/control panel.
- NEXT QUESTION control. From IDLE it selects Q1; from CLOSED it advances to the next loaded question by index and shows its topic. It is disabled while TOPIC/LIVE.
- Removes the confusing Reveal Power-Ups button from admin UI. The backend `reveal_cards` flag/endpoint is left intact for compatibility, but the current player UI does not use it to gate cards.

No database migration is required.
No Freeze calculation, charge, scoring, deadline extension, or card activation logic was changed.
