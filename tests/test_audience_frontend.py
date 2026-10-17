import json
from pathlib import Path
import subprocess

import pytest

from httpx import ASGITransport, AsyncClient

from app.main import create_app


ROOT = Path(__file__).parents[1]


async def test_audience_page_and_assets_are_publicly_served_without_login():
    app = create_app()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        page = await client.get("/audience")
        css = await client.get("/static/audience.css")
        script = await client.get("/static/audience.js")

    assert page.status_code == 200
    assert css.status_code == 200
    assert script.status_code == 200
    assert "/static/audience.css" in page.text
    assert "/static/audience.js" in page.text
    assert "levelup_auth" not in page.text


def test_audience_script_only_uses_public_api_and_has_publication_gated_animation():
    script = (ROOT / "static" / "audience.js").read_text()

    assert 'const ENDPOINT = "/api/v1/public/audience"' in script
    assert "/api/v1/admin" not in script
    assert "/api/v1/play" not in script
    assert "/api/v1/auth" not in script
    assert "publication.published_at !== lastPublicationAt" in script
    assert "if (!initialized || publicationChanged)" in script
    assert "renderPublication(publication, previousPublication, publicationChanged)" in script
    assert "requestAnimationFrame" in script
    assert "element.textContent = number(to)" in script
    assert "previousTop - row.getBoundingClientRect().top" in script
    assert "used && before && !before[field]" in script
    assert "renderSolution(publication.solution, currentQuestion, publicationChanged)" in script
    assert "Number(solution.question_index) === Number(question.index)" in script
    assert 'marker.textContent = "✓ CORRECT"' not in script
    assert 'nextMarker.textContent = "✓ CORRECT"' in script


def test_audience_phase_placeholders_reduced_motion_and_responsive_layout_exist():
    html = (ROOT / "static" / "audience.html").read_text()
    css = (ROOT / "static" / "audience.css").read_text()
    script = (ROOT / "static" / "audience.js").read_text()

    assert "ROUND STANDBY" in html
    assert "STANDINGS NOT YET PUBLISHED" in html
    assert '<h2 id="standings-heading">Top 16 Standings</h2>' in html
    assert "CARD USAGE NOT YET PUBLISHED" in html
    assert 'phase === "topic"' in script
    assert 'phase === "closed"' in script
    assert 'document.body.dataset.phase !== "paused"' in script
    assert 'matchMedia("(prefers-reduced-motion: reduce)")' in script
    assert "@media (prefers-reduced-motion: reduce)" in css
    assert "@media (max-width: 1100px)" in css
    assert "@media (max-width: 680px)" in css
    assert "overflow-x: hidden" in css
    assert "grid-template-columns: 1fr" in css
    assert "height: 100dvh" in css
    assert "grid-template-rows: repeat(8" in css
    assert ".option-row.correct-option" in css
    assert ".published-hint" in css
    assert ".previous-solution" in css
    assert "solution-reveal" in css
    assert 'id="question-timer"' in html
    assert "font-variant-numeric: tabular-nums" in css


@pytest.mark.parametrize("team_count", [8, 16, 20, 32])
def test_audience_selects_top_sixteen_and_matching_cards_without_mutating_publication(team_count):
    script_path = ROOT / "static" / "audience.js"
    harness = f"""
const fs = require('fs');
global.window = {{ matchMedia: () => ({{ matches: true }}) }};
global.document = {{ addEventListener: () => {{}} }};
eval(fs.readFileSync({json.dumps(str(script_path))}, 'utf8'));
const rows = Array.from({{ length: {team_count} }}, (_, i) => ({{
  id: `team-${{i + 1}}`, name: `Team ${{i + 1}}`,
  rank: i === 14 ? 14 : i === 16 ? 16 : i + 1, score: 100 - i,
}}));
const cardUsage = [...rows].reverse().map((row) => ({{ team_id: row.id, team_name: row.name }}));
cardUsage.push({{ team_id: 'not-ranked', team_name: 'Not ranked' }});
const publication = {{ leaderboard: rows, card_usage: cardUsage }};
const selected = window.__audienceTest.visiblePublicationRows(publication);
console.log(JSON.stringify({{
  displayedIds: selected.visibleRows.map((row) => row.id),
  displayedRanks: selected.visibleRows.map((row) => row.rank),
  cardIds: selected.visibleCardRows.map((row) => row.team_id),
  originalLeaderboardLength: publication.leaderboard.length,
  originalCardLength: publication.card_usage.length,
}}));
"""
    result = subprocess.run(["node", "-e", harness], check=True, capture_output=True, text=True)
    selected = json.loads(result.stdout)
    visible_count = min(team_count, 16)
    visible_ids = [f"team-{index}" for index in range(1, visible_count + 1)]
    assert selected["displayedIds"] == visible_ids
    assert selected["cardIds"] == visible_ids
    expected_ranks = [14 if index == 15 else index for index in range(1, visible_count + 1)]
    assert selected["displayedRanks"] == expected_ranks
    assert selected["originalLeaderboardLength"] == team_count
    assert selected["originalCardLength"] == team_count + 1


def test_authoritative_timer_math_pause_rounding_and_single_interval():
    script_path = ROOT / "static" / "audience.js"
    harness = f"""
const fs = require('fs');
global.window = {{
  matchMedia: () => ({{ matches: true }}),
  setInterval: () => 1,
}};
global.document = {{
  addEventListener: () => {{}},
  getElementById: () => ({{}}),
}};
eval(fs.readFileSync({json.dumps(str(script_path))}, 'utf8'));
const t = window.__audienceTest;
const live = t.makeTimerState({{
  phase: 'live', server_now: '2026-01-01T00:00:10.000Z',
  global_deadline_at: '2026-01-01T00:02:39.001Z', paused_at: null,
}}, Date.parse('2026-01-01T00:00:00.000Z'));
const paused = t.makeTimerState({{
  phase: 'paused', server_now: '2026-01-01T01:00:00.000Z',
  global_deadline_at: '2026-01-01T00:02:30.000Z',
  paused_at: '2026-01-01T00:01:00.000Z',
}}, Date.parse('2025-12-31T23:00:00.000Z'));
const resumed = t.makeTimerState({{
  phase: 'live', server_now: '2026-01-01T00:03:00.000Z',
  global_deadline_at: '2026-01-01T00:04:30.000Z', paused_at: null,
}}, Date.parse('2026-01-01T00:03:00.000Z'));
console.log(JSON.stringify({{
  offset: live.serverOffsetMs,
  live: t.remainingTimerMs(live, Date.parse('2026-01-01T00:00:00.000Z')),
  liveLater: t.remainingTimerMs(live, Date.parse('2026-01-01T00:00:00.001Z')),
  paused: t.remainingTimerMs(paused, Date.parse('2030-01-01T00:00:00.000Z')),
  resumed: t.remainingTimerMs(resumed, Date.parse('2026-01-01T00:03:01.000Z')),
  negative: t.remainingTimerMs(live, Date.parse('2027-01-01T00:00:00.000Z')),
  idle: t.formatTimer(t.remainingTimerMs(t.makeTimerState({{phase: 'idle'}}, 0), 0)),
  topic: t.formatTimer(t.remainingTimerMs(t.makeTimerState({{phase: 'topic'}}, 0), 0)),
  closed: t.formatTimer(t.remainingTimerMs(t.makeTimerState({{phase: 'closed'}}, 0), 0)),
  ceilA: t.formatTimer(149001), ceilB: t.formatTimer(149000),
}}));
"""
    result = subprocess.run(
        ["node", "-e", harness], check=True, capture_output=True, text=True
    )
    values = json.loads(result.stdout)
    assert values == {
        "offset": 10_000,
        "live": 149_001,
        "liveLater": 149_000,
        "paused": 90_000,
        "resumed": 89_000,
        "negative": 0,
        "idle": "--:--",
        "topic": "--:--",
        "closed": "00:00",
        "ceilA": "02:30",
        "ceilB": "02:29",
    }

    script = script_path.read_text()
    assert script.count("timerInterval = window.setInterval") == 1
    assert "seconds--" not in script
