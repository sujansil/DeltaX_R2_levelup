# Mobile controller + power-up gate fix

Changes:
- Adds a mobile-visible controller action in the arena rail.
- Mobile button claims control when free, releases control when this device owns it, and disables itself when another device owns control.
- Keeps the desktop controller card unchanged.
- Corrects the frontend so the legacy/admin `reveal_cards` flag does not gate whether teams can use power-ups. Gameplay availability is still governed by phase, controller ownership, score/cost, lock/use state, and backend rules.

No backend scoring, Freeze timing, or card engine semantics were changed.
