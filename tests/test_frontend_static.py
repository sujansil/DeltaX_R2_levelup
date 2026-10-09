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
    assert "recent · ${validCount} / 4 valid" in js
    assert "CONTROLLER UNKNOWN" in js
    assert "CONTROLLER LEASE STALE" in js
    assert "controller state unavailable" in js
    assert 'confirmation, expected_round_version: roundState.version' in js


def test_admin_frontend_integrates_round_publication_and_recovery_controls():
    html = (ROOT / "static" / "admin.html").read_text()
    js = (ROOT / "static" / "admin.js").read_text()

    for control_id in (
        "next-button", "start-button", "pause-button", "resume-button",
        "close-button", "publish-button", "restart-open",
    ):
        assert f'id="{control_id}"' in html
    assert "PUBLISH STANDINGS + CARDS" in html
    assert 'id="publication-last"' in html
    assert 'id="publication-current"' in html
    assert 'id="recovery-export-button"' in html
    assert 'href="/audience"' in html
    assert 'target="_blank"' in html
    assert 'fetch("/api/v1/admin/export/event"' in js
    assert 'api("/api/v1/public/audience")' in js
    assert 'phase !== "live"' in js
    assert 'phase !== "paused"' in js
    assert 'classList.toggle("phase-primary", phase === "live")' in js
    assert 'classList.toggle("phase-primary", phase === "paused")' in js


def test_admin_frontend_keeps_pause_timers_and_session_semantics_explicit():
    html = (ROOT / "static" / "admin.html").read_text()
    js = (ROOT / "static" / "admin.js").read_text()

    assert js.count('roundState?.phase === "paused" && roundState?.paused_at') >= 3
    assert "new Date(roundState.paused_at).getTime()" in js
    assert 'const text = `${sec}s`' in js
    css = (ROOT / "static" / "admin.css").read_text()
    assert ".session-card.status-active" in css
    for status in ("stale", "inactive", "revoked", "expired"):
        assert f'data-status="{status}"' in css
    assert "recent_session_count" in js
    assert "valid_session_count" in js
    assert "controllerUnknown ? \"UNKNOWN\"" in js
    assert "CONTROLLER UNKNOWN" in js
    assert "CONTROLLER LEASE STALE" in js
    assert 'id="paused-at-label"' in html


def test_login_keeps_relative_auth_and_optional_three_background():
    html = (ROOT / "static" / "login.html").read_text()
    css = (ROOT / "static" / "login.css").read_text()
    background_js = (ROOT / "static" / "login-background.js").read_text()
    right_field_js = (ROOT / "static" / "login-right-field.js").read_text()

    assert 'id="f"' in html
    assert 'name="username"' in html
    assert 'name="password"' in html
    assert 'api("/api/v1/auth/login"' in html
    assert "saveAuth(data)" in html
    assert 'data.role === "admin" ? "/admin" : "/play"' in html
    assert "localhost" not in html
    assert "/static/login-background.js" in html
    assert 'import("/static/vendor/three.module.min.js")' in background_js
    assert "document.hidden" in background_js
    assert 'prefers-reduced-motion: reduce' in background_js
    assert "Math.min(window.devicePixelRatio || 1" in background_js
    assert 'pointerTarget.x = THREE.MathUtils.clamp' in background_js
    assert 'THREE.MathUtils.lerp(pointerCurrent.x, pointerTarget.x' in background_js
    assert 'document.documentElement.addEventListener("pointerleave"' in background_js
    assert 'id="delta-value"' in html
    assert 'id="separation-value"' in html
    assert "THREE.DynamicDrawUsage" in background_js
    assert "updateLorenz(primaryPositions" in background_js
    assert "updateLorenz(perturbedPositions" in background_js
    assert "createMobius" in background_js
    assert "ambientStructure.rotation.y" in background_js
    assert 'class="spectator-link" href="/audience"' in html
    assert "@media (min-width:821px)" in css
    assert "border-color:transparent; background:transparent; box-shadow:none" not in css
    assert ".event-copy { min-height:350px; padding-top:85px; padding-bottom:19px; }" in css
    assert ".login-card { margin-top:22px; transform:translateY(-88px); }" in css
    assert "visualPane.getBoundingClientRect()" in background_js
    assert 'id="right-field-canvas"' in html
    assert "/static/login-right-field.js" in html
    assert 'new THREE.WebGLRenderer' in right_field_js
    assert 'import("/static/vendor/three.module.min.js")' in right_field_js
    assert "createMobius" in right_field_js
    assert ".scene::after" not in (ROOT / "static" / "login.css").read_text()
    assert 'window.addEventListener("pointermove"' in right_field_js
    assert "document.hidden" in right_field_js
