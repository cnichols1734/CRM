"""Utility URLs stay out of the index via robots meta and X-Robots-Tag.

Does not rewrite robots.txt, sitemap.xml, marketing copy, or gold FAQ/H1s.
"""
from pathlib import Path

from app import NOINDEX_UTILITY_PATHS, X_ROBOTS_TAG_NOINDEX


ROOT = Path(__file__).resolve().parents[1]
ROBOTS = ROOT / "static" / "robots.txt"
SITEMAP = ROOT / "static" / "sitemap.xml"

ROBOTS_META = '<meta name="robots" content="noindex, nofollow">'

NOINDEX_PATHS = (
    "/reset_password",
    "/health",
    "/health/ui",
    "/registration-status",
)

HTML_NOINDEX_PATHS = (
    "/reset_password",
    "/health/ui",
)

INDEXABLE_PUBLIC_PATHS = (
    "/",
    "/login",
    "/register",
    "/terms-privacy",
)

PINNED_ROBOTS = """\
User-agent: *
Allow: /
Disallow: /dashboard
Disallow: /reset_password
Disallow: /registration-status
Disallow: /health
Disallow: /health/ui

User-agent: GPTBot
Allow: /

User-agent: ChatGPT-User
Allow: /

User-agent: PerplexityBot
Allow: /

User-agent: ClaudeBot
Allow: /

User-agent: Google-Extended
Allow: /

Sitemap: https://agentflow.origentechnolog.com/sitemap.xml
"""

PINNED_SITEMAP_LASTMODS = {
    "https://agentflow.origentechnolog.com/": "2026-09-02",
    "https://agentflow.origentechnolog.com/register": "2026-08-31",
    "https://agentflow.origentechnolog.com/login": "2026-08-31",
    "https://agentflow.origentechnolog.com/terms-privacy": "2026-09-03",
    "https://agentflow.origentechnolog.com/free-real-estate-crm": "2026-09-02",
    "https://agentflow.origentechnolog.com/follow-up-boss-alternative": "2026-09-02",
    "https://agentflow.origentechnolog.com/wise-agent-alternative": "2026-09-02",
    "https://agentflow.origentechnolog.com/kvcore-alternative": "2026-09-02",
}


def _head(html):
    close = html.lower().find("</head>")
    assert close != -1
    return html[:close]


class TestUtilityNoindexHtml:
    def test_reset_password_html_has_robots_meta(self, client, seed):
        resp = client.get("/reset_password")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert ROBOTS_META in _head(html)

    def test_health_ui_html_has_robots_meta(self, client, seed):
        resp = client.get("/health/ui")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert ROBOTS_META in _head(html)

    def test_public_marketing_pages_stay_indexable(self, client, seed):
        for path in INDEXABLE_PUBLIC_PATHS:
            html = client.get(path).get_data(as_text=True)
            assert "noindex" not in html.lower(), path


class TestUtilityNoindexHeaders:
    def test_utility_paths_send_x_robots_tag(self, client, seed):
        assert frozenset(NOINDEX_PATHS) == NOINDEX_UTILITY_PATHS
        for path in NOINDEX_PATHS:
            resp = client.get(path)
            assert resp.headers.get("X-Robots-Tag") == X_ROBOTS_TAG_NOINDEX, path

    def test_public_pages_do_not_send_x_robots_tag(self, client, seed):
        for path in INDEXABLE_PUBLIC_PATHS:
            resp = client.get(path)
            assert resp.headers.get("X-Robots-Tag") is None, path


class TestRobotsAndSitemapUntouched:
    def test_robots_txt_file_matches_pin(self):
        assert ROBOTS.read_text() == PINNED_ROBOTS

    def test_served_robots_txt_matches_pin(self, client):
        resp = client.get("/robots.txt")
        assert resp.status_code == 200
        assert resp.get_data(as_text=True) == PINNED_ROBOTS

    def test_sitemap_file_lastmods_and_locs_unchanged(self):
        from xml.etree import ElementTree as ET

        ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        urls = ET.parse(SITEMAP).getroot().findall("sm:url", ns)
        found = {}
        for url in urls:
            loc = (url.findtext("sm:loc", default="", namespaces=ns) or "").strip()
            lastmod = (url.findtext("sm:lastmod", default="", namespaces=ns) or "").strip()
            found[loc] = lastmod
        assert found == PINNED_SITEMAP_LASTMODS
        for path in NOINDEX_PATHS:
            assert all(path not in loc for loc in found), path

    def test_served_sitemap_matches_static_file(self, client):
        resp = client.get("/sitemap.xml")
        assert resp.status_code == 200
        assert resp.get_data(as_text=True) == SITEMAP.read_text()
