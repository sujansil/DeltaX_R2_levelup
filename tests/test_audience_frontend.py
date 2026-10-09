from pathlib import Path

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


def test_audience_phase_placeholders_reduced_motion_and_responsive_layout_exist():
    html = (ROOT / "static" / "audience.html").read_text()
    css = (ROOT / "static" / "audience.css").read_text()
    script = (ROOT / "static" / "audience.js").read_text()

    assert "ROUND STANDBY" in html
    assert "STANDINGS NOT YET PUBLISHED" in html
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
