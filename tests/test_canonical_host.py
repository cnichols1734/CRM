"""Canonical host, sitemap/robots/llms URLs, and www redirects."""
from pathlib import Path
from xml.etree import ElementTree as ET

from config import DEFAULT_APP_BASE_URL


ROOT = Path(__file__).resolve().parents[1]
SITEMAP = ROOT / "static" / "sitemap.xml"
ROBOTS = ROOT / "static" / "robots.txt"
LLMS = ROOT / "static" / "llms.txt"
SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
PUBLIC_PATHS = (
    "/",
    "/register",
    "/login",
    "/terms-privacy",
    "/free-real-estate-crm",
    "/follow-up-boss-alternative",
    "/wise-agent-alternative",
    "/kvcore-alternative",
)
SITEMAP_LASTMOD = {
    "/": "2026-08-18",
    "/register": "2026-08-20",
    "/login": "2026-08-20",
    "/terms-privacy": "2026-08-18",
    "/free-real-estate-crm": "2026-08-25",
    "/follow-up-boss-alternative": "2026-08-25",
    "/wise-agent-alternative": "2026-08-25",
    "/kvcore-alternative": "2026-08-25",
}


def test_default_app_base_url_is_agentflow():
    assert DEFAULT_APP_BASE_URL == "https://agentflow.origentechnolog.com"


def _sitemap_path(loc):
    return loc.replace("https://agentflow.origentechnolog.com", "", 1) or "/"


def test_sitemap_locs_use_agentflow_host():
    locs = [
        el.text or ""
        for el in ET.parse(SITEMAP).getroot().findall("sm:url/sm:loc", SITEMAP_NS)
    ]
    assert locs
    for loc in locs:
        assert loc.startswith("https://agentflow.origentechnolog.com"), loc
        assert "www.origentechnolog.com" not in loc


def test_sitemap_has_one_lastmod_per_url():
    urls = ET.parse(SITEMAP).getroot().findall("sm:url", SITEMAP_NS)
    lastmods = {}
    for url in urls:
        loc = url.findtext("sm:loc", default="", namespaces=SITEMAP_NS) or ""
        path = _sitemap_path(loc)
        mods = [el.text for el in url.findall("sm:lastmod", SITEMAP_NS)]
        assert len(mods) == 1, path
        lastmods[path] = mods[0]
        tags = [child.tag.split("}")[-1] for child in list(url)]
        assert tags == ["loc", "lastmod"], path
    assert lastmods == SITEMAP_LASTMOD


def test_robots_sitemap_points_at_agentflow():
    text = ROBOTS.read_text()
    assert "Sitemap: https://agentflow.origentechnolog.com/sitemap.xml" in text
    assert "www.origentechnolog.com" not in text


def test_llms_txt_lists_agentflow_public_pages():
    text = LLMS.read_text()
    for path in PUBLIC_PATHS:
        url = f"https://agentflow.origentechnolog.com{path if path != '/' else '/'}"
        assert url in text
    assert "www.origentechnolog.com" not in text
    assert "https://agentflow.origentechnolog.com/dashboard" not in text


def test_www_get_redirects_to_agentflow(client):
    resp = client.get(
        "/free-real-estate-crm?utm=1",
        headers={"Host": "www.origentechnolog.com"},
    )
    assert resp.status_code == 301
    assert (
        resp.headers["Location"]
        == "https://agentflow.origentechnolog.com/free-real-estate-crm?utm=1"
    )


def test_apex_get_redirects_to_agentflow(client):
    resp = client.get("/", headers={"Host": "origentechnolog.com"})
    assert resp.status_code == 301
    assert resp.headers["Location"] == "https://agentflow.origentechnolog.com/"


def test_agentflow_host_is_not_redirected(client):
    resp = client.get("/", headers={"Host": "agentflow.origentechnolog.com"})
    assert resp.status_code == 200


def test_www_webhook_post_is_not_redirected(client):
    resp = client.post(
        "/webhooks/sendgrid/inbound-parse",
        headers={"Host": "www.origentechnolog.com"},
    )
    assert resp.status_code != 301
