from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_player_frontend_is_local_responsive_and_has_clean_timeout_refresh():
    html = (ROOT / "static" / "play.html").read_text()
    js = (ROOT / "static" / "player.js").read_text()

    assert "sreturn;" not in html
    assert "sreturn;" not in js
    assert "/static/player.css" in html
    assert "/static/player.js" in html
    assert "cdn.tailwindcss.com" not in html
    assert "unpkg.com" not in html
    assert 'id="leaderboard-drawer"' in html
    assert 'id="potential-value"' in html
    assert 'id="mobile-power-dock"' not in html  # class-based dock, no duplicate legacy id
    assert 'class="mobile-power-dock"' in html
    assert "remainingMs <= 0" in js
    assert "refreshState({ quiet: true })" in js


def test_admin_frontend_is_local_and_exposes_session_and_restart_controls():
    html = (ROOT / "static" / "admin.html").read_text()
    js = (ROOT / "static" / "admin.js").read_text()

    assert "/static/admin.css" in html
    assert "/static/admin.js" in html
    assert "cdn.tailwindcss.com" not in html
    assert "unpkg.com" not in html
    assert 'id="team-matrix"' in html
    assert 'id="session-list"' in html
    assert 'id="restart-modal"' in html
    assert "/sessions/revoke-all" in js
    assert 'confirmation, expected_round_version: roundState.version' in js
