"""Public /free-real-estate-crm page: route, SEO, sitemap, and copy guards."""
import re
from pathlib import Path
from xml.etree import ElementTree as ET

from tier_config.tier_limits import get_tier_defaults


ROOT = Path(__file__).resolve().parents[1]
PAGE = (ROOT / "templates" / "free_real_estate_crm.html").read_text()
LANDING = (ROOT / "templates" / "landing.html").read_text()
SITEMAP = ROOT / "static" / "sitemap.xml"
FREE_LIMITS = get_tier_defaults("free")

PAGE_PATH = "/free-real-estate-crm"
TITLE = "Free real estate CRM | Origen TechnolOG"
H1 = "A real estate CRM you can start tonight."
LEAD = "Built for agents, by agents. The free tier stays free. No card. About two minutes."
SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
ALLOWED_SITEMAP_PATHS = {
    "/",
    "/register",
    "/login",
    "/terms-privacy",
    PAGE_PATH,
}
BLOCKED_SEO_PATHS = (
    "/pricing",
    "/about",
    "/blog",
    "/help",
    "/features",
    "/contact",
)
BANNED_SLOP = (
    "unlock",
    "empower",
    "elevate",
    "seamless",
    "seamlessly",
    "revolutionize",
    "transform",
    "supercharge",
    "robust",
    "comprehensive",
    "all-in-one solution",
    "take your business to the next level",
    "designed for your success",
    "focus on what matters most",
    "powerful platform",
)
FAQ_ITEMS = (
    (
        "What does the free tier include?",
        "One user, up to 10,000 contacts, and 25 B.O.B. messages a day. You can send email through Gmail and sync tasks to Google Calendar.",
    ),
    (
        "Do I need a credit card?",
        "No. Setup takes about two minutes.",
    ),
    (
        "Can I buy Pro today?",
        "No. Paid features come later. Nothing paid is for sale today.",
    ),
)
FAKE_SEO_QUESTIONS = (
    "origentech",
    "Is this HubSpot",
    "Is this Follow Up Boss or HubSpot",
    "Is this origentech.com or Origin CRM",
)


def _sitemap_paths():
    tree = ET.parse(SITEMAP)
    locs = [el.text or "" for el in tree.getroot().findall("sm:url/sm:loc", SITEMAP_NS)]
    paths = []
    for loc in locs:
        path = loc.replace("https://www.origentechnolog.com", "", 1) or "/"
        paths.append(path)
    return paths


def _visible_copy(html):
    html = re.sub(r"<script\b[^>]*>.*?</script>", " ", html, flags=re.I | re.S)
    html = re.sub(r"<style\b[^>]*>.*?</style>", " ", html, flags=re.I | re.S)
    html = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", html)


class TestFreeRealEstateCrmRoute:
    def test_page_returns_200(self, client):
        resp = client.get(PAGE_PATH)
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert H1 in html
        assert 'href="/register"' in html

    def test_logged_in_user_still_gets_200(self, owner_a_client):
        resp = owner_a_client.get(PAGE_PATH)
        assert resp.status_code == 200

    def test_sitemap_includes_page_as_only_extra_url(self, client):
        resp = client.get("/sitemap.xml")
        assert resp.status_code == 200
        paths = _sitemap_paths()
        assert paths.count(PAGE_PATH) == 1
        assert set(paths) == ALLOWED_SITEMAP_PATHS
        for blocked in BLOCKED_SEO_PATHS:
            assert blocked not in paths
        assert "/dashboard" not in paths

    def test_blocked_marketing_urls_are_not_built(self, client):
        for path in BLOCKED_SEO_PATHS:
            resp = client.get(path)
            assert resp.status_code == 404, f"{path} should not be a public page"


class TestFreeRealEstateCrmSeo:
    def test_unique_title_canonical_and_og(self, client):
        page = client.get(PAGE_PATH).get_data(as_text=True)
        home = client.get("/").get_data(as_text=True)

        assert f"<title>{TITLE}</title>" in page
        assert 'rel="canonical" href="https://www.origentechnolog.com/free-real-estate-crm"' in page
        assert 'property="og:title" content="Free real estate CRM | Origen TechnolOG"' in page
        assert 'property="og:url" content="https://www.origentechnolog.com/free-real-estate-crm"' in page
        assert 'property="og:description" content="Built for agents, by agents. The free tier stays free. No card. About two minutes."' in page

        assert "<title>Free Real Estate CRM | Origen TechnolOG</title>" in home
        assert 'rel="canonical" href="https://www.origentechnolog.com/"' in home
        assert page.count("<title>") == 1
        assert 'rel="canonical" href="https://www.origentechnolog.com/"' not in page
        assert TITLE not in home

    def test_software_application_json_ld_price_is_zero(self, client):
        html = client.get(PAGE_PATH).get_data(as_text=True)
        assert '"@type": "SoftwareApplication"' in html
        assert '"price": "0"' in html
        assert '"priceCurrency": "USD"' in html
        assert '"isAccessibleForFree": true' in html
        assert '"url": "https://www.origentechnolog.com/free-real-estate-crm"' in html


class TestFreeRealEstateCrmCopy:
    def test_required_human_copy(self):
        assert f"<title>{TITLE}</title>" in PAGE
        assert H1 in PAGE
        assert LEAD in PAGE
        assert "Start Free" in PAGE
        assert "url_for('auth.register')" in PAGE

    def test_h1_is_the_human_line(self, client):
        html = client.get(PAGE_PATH).get_data(as_text=True)
        match = re.search(r"<h1[^>]*>(.*?)</h1>", html, flags=re.S)
        assert match is not None
        h1 = re.sub(r"<[^>]+>", "", match.group(1))
        h1 = re.sub(r"\s+", " ", h1).strip()
        assert h1 == H1

    def test_limits_match_repo(self):
        assert FREE_LIMITS["max_users"] == 1
        assert FREE_LIMITS["max_contacts"] == 10000
        assert FREE_LIMITS["daily_ai_chat_messages"] == 25
        visible = _visible_copy(PAGE)
        assert "one user" in visible.lower()
        assert "10,000 contacts" in visible
        assert "25 B.O.B. messages a day" in visible
        assert "Unlimited contacts" not in PAGE
        assert "unlimited contacts" not in PAGE.lower()

    def test_cta_goes_to_register(self, client):
        html = client.get(PAGE_PATH).get_data(as_text=True)
        assert re.search(r'href="/register"[^>]*>\s*Start Free', html) is not None

    def test_no_em_dashes(self):
        assert "—" not in PAGE

    def test_no_unlimited_contacts_or_never_paid_plan(self):
        assert "Unlimited contacts" not in PAGE
        assert "never be a paid plan" not in PAGE.lower()
        assert "never be a paid" not in PAGE.lower()

    def test_no_banned_slop(self):
        lowered = _visible_copy(PAGE).lower()
        for phrase in BANNED_SLOP:
            assert phrase not in lowered

    def test_faq_matches_json_ld_and_repo_limits(self):
        assert '"@type": "FAQPage"' in PAGE
        assert PAGE.count("<summary>") == 3
        assert PAGE.count('"@type": "Question"') == 3
        for question, answer in FAQ_ITEMS:
            assert PAGE.count(question) == 2
            assert PAGE.count(answer) == 2
            assert f"<summary>{question}</summary>" in PAGE
            assert f'<p class="faq-answer">{answer}</p>' in PAGE
            assert f'"name": "{question}"' in PAGE
            assert f'"text": "{answer}"' in PAGE
        include_answer = FAQ_ITEMS[0][1]
        assert "One user" in include_answer
        assert "10,000 contacts" in include_answer
        assert "25 B.O.B. messages a day" in include_answer

    def test_no_fake_seo_questions(self):
        for phrase in FAKE_SEO_QUESTIONS:
            assert phrase.lower() not in PAGE.lower()
