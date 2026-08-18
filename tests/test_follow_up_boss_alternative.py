"""Public /follow-up-boss-alternative page: route, SEO, sitemap, and copy guards."""
import re
from pathlib import Path
from xml.etree import ElementTree as ET

from tier_config.tier_limits import get_tier_defaults


ROOT = Path(__file__).resolve().parents[1]
PAGE = (ROOT / "templates" / "follow_up_boss_alternative.html").read_text()
LANDING = (ROOT / "templates" / "landing.html").read_text()
SITEMAP = ROOT / "static" / "sitemap.xml"
FREE_LIMITS = get_tier_defaults("free")

PAGE_PATH = "/follow-up-boss-alternative"
TITLE = "Follow Up Boss alternative | Origen TechnolOG"
H1 = "A Follow Up Boss alternative you can start tonight."
META = "Follow Up Boss Grow is $69 per user per month. Origen is a free real estate CRM. No card. About two minutes."
SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
ALLOWED_SITEMAP_PATHS = {
    "/",
    "/register",
    "/login",
    "/terms-privacy",
    "/free-real-estate-crm",
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
        "How much is Follow Up Boss?",
        "Grow is $69 per user per month on followupboss.com/pricing. Pro is $499 a month for 10 users. Calling on Grow is $39 per user.",
    ),
    (
        "Does Follow Up Boss have a free plan?",
        "No. They offer a 14-day trial. Full access, no credit card required for those 14 days.",
    ),
    (
        "What does Origen include?",
        "One user, up to 10,000 contacts, tasks, and a dashboard. You can send email through Gmail and sync tasks to Google Calendar. B.O.B. is included, with 25 messages a day.",
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
    "What can B.O.B. do?",
)


def _faq_paragraphs(answer):
    return tuple(part for part in answer.split("\n\n") if part)


def _json_ld_answer(answer):
    return "\\n\\n".join(_faq_paragraphs(answer))


def _sitemap_paths():
    tree = ET.parse(SITEMAP)
    locs = [el.text or "" for el in tree.getroot().findall("sm:url/sm:loc", SITEMAP_NS)]
    paths = []
    for loc in locs:
        path = loc.replace("https://www.origentechnolog.com", "", 1) or "/"
        paths.append(path)
    return paths


def _app_base_url(app):
    return (app.config.get("APP_BASE_URL") or "https://www.origentechnolog.com").rstrip("/")


def _visible_copy(html):
    html = re.sub(r"<script\b[^>]*>.*?</script>", " ", html, flags=re.I | re.S)
    html = re.sub(r"<style\b[^>]*>.*?</style>", " ", html, flags=re.I | re.S)
    html = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", html)


class TestFollowUpBossAlternativeRoute:
    def test_page_returns_200(self, client):
        resp = client.get(PAGE_PATH)
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert H1 in html
        assert 'href="/register"' in html

    def test_logged_in_user_still_gets_200(self, owner_a_client):
        resp = owner_a_client.get(PAGE_PATH)
        assert resp.status_code == 200

    def test_sitemap_includes_public_set_only(self, client):
        resp = client.get("/sitemap.xml")
        assert resp.status_code == 200
        paths = _sitemap_paths()
        assert paths.count(PAGE_PATH) == 1
        assert set(paths) == ALLOWED_SITEMAP_PATHS
        for blocked in BLOCKED_SEO_PATHS:
            assert blocked not in paths
        assert "/dashboard" not in paths

    def test_llms_txt_lists_the_page(self, client):
        resp = client.get("/llms.txt")
        assert resp.status_code == 200
        text = resp.get_data(as_text=True)
        assert "https://www.origentechnolog.com/follow-up-boss-alternative" in text
        for blocked in BLOCKED_SEO_PATHS:
            assert f"https://www.origentechnolog.com{blocked}" not in text
        assert "https://www.origentechnolog.com/dashboard" not in text
        assert "—" not in text

    def test_blocked_marketing_urls_are_not_built(self, client):
        for path in BLOCKED_SEO_PATHS:
            resp = client.get(path)
            assert resp.status_code == 404, f"{path} should not be a public page"


class TestFollowUpBossAlternativeSeo:
    def test_unique_title_canonical_and_og(self, app, client):
        page = client.get(PAGE_PATH).get_data(as_text=True)
        home = client.get("/").get_data(as_text=True)
        base = _app_base_url(app)
        page_url = f"{base}{PAGE_PATH}"
        home_url = f"{base}/"

        assert f"<title>{TITLE}</title>" in page
        assert f'rel="canonical" href="{page_url}"' in page
        assert f'property="og:title" content="{TITLE}"' in page
        assert f'property="og:url" content="{page_url}"' in page
        assert f'property="og:description" content="{META}"' in page

        assert "<title>Free Real Estate CRM | Origen TechnolOG</title>" in home
        assert f'rel="canonical" href="{home_url}"' in home
        assert page.count("<title>") == 1
        assert f'rel="canonical" href="{home_url}"' not in page
        assert TITLE not in home

    def test_json_ld_types_and_price(self, app, client):
        html = client.get(PAGE_PATH).get_data(as_text=True)
        base = _app_base_url(app)
        assert '"@type": "Organization"' in html
        assert '"@type": "SoftwareApplication"' in html
        assert '"@type": "FAQPage"' in html
        assert "SearchAction" not in html
        assert "aggregateRating" not in html
        assert '"price": "0"' in html
        assert '"priceCurrency": "USD"' in html
        assert '"isAccessibleForFree": true' in html
        assert f'"url": "{base}{PAGE_PATH}"' in html

    def test_no_robots_noindex(self, client):
        html = client.get(PAGE_PATH).get_data(as_text=True)
        assert "noindex" not in html.lower()


class TestFollowUpBossAlternativeCopy:
    def test_required_human_copy(self):
        assert f"<title>{TITLE}</title>" in PAGE
        assert H1 in PAGE
        assert META in PAGE
        assert "Start Free" in PAGE
        assert "url_for('auth.register')" in PAGE
        assert "url_for('main.free_real_estate_crm')" in PAGE
        assert "url_for('main.landing')" in PAGE

    def test_h1_is_the_human_line(self, client):
        html = client.get(PAGE_PATH).get_data(as_text=True)
        match = re.search(r"<h1[^>]*>(.*?)</h1>", html, flags=re.S)
        assert match is not None
        h1 = re.sub(r"<[^>]+>", "", match.group(1))
        h1 = re.sub(r"\s+", " ", h1).strip()
        assert h1 == H1

    def test_lead_stays_sourced(self):
        assert (
            "Follow Up Boss Grow is $69 per user per month on their pricing page. "
            "There is no free plan. You get a 14-day trial with full access and no credit card. "
            "After that they charge. Origen is $0 to start, no credit card, and the free plan stays free. "
            "One user, up to 10,000 contacts, 25 B.O.B. messages a day."
        ) in PAGE

    def test_limits_match_repo(self):
        assert FREE_LIMITS["max_users"] == 1
        assert FREE_LIMITS["max_contacts"] == 10000
        assert FREE_LIMITS["daily_ai_chat_messages"] == 25
        visible = _visible_copy(PAGE)
        assert "one user" in visible.lower()
        assert "10,000 contacts" in visible
        assert "25 B.O.B. messages a day" in visible
        assert "25 messages a day" in visible
        assert "$0" in visible
        assert "unlimited contacts" in visible.lower()
        assert "Unlimited (their pricing)" in PAGE
        assert "up to 10,000" in visible.lower()

    def test_does_not_claim_origen_has_unlimited_contacts(self):
        visible = _visible_copy(PAGE)
        assert re.search(r"Origen[^.]*unlimited contacts", visible, flags=re.I) is None
        assert "Origen publishes unlimited contacts" not in PAGE
        assert "unlimited contacts on Origen" not in PAGE.lower()

    def test_cta_goes_to_register(self, client):
        html = client.get(PAGE_PATH).get_data(as_text=True)
        assert re.search(r'href="/register"[^>]*>\s*Start Free', html) is not None

    def test_no_em_dashes(self):
        assert "—" not in PAGE

    def test_no_never_paid_plan(self):
        assert "never be a paid plan" not in PAGE.lower()
        assert "never be a paid" not in PAGE.lower()
        assert "Paid features come later" in PAGE

    def test_does_not_claim_fub_trial_needs_a_card(self):
        lowered = PAGE.lower()
        assert "trial requires a card" not in lowered
        assert "trial requires a credit card" not in lowered
        assert "need a credit card for the trial" not in lowered
        assert "credit card required for those 14 days" in PAGE
        assert "The trial has no card. Then they charge." in PAGE
        assert "14-day trial with full access and no credit card" in PAGE

    def test_does_not_claim_we_replace_fub_products(self):
        lowered = PAGE.lower()
        assert "replace action plans" not in lowered
        assert "replace" in lowered
        assert "we are not a replacement for them" in lowered
        assert "200+ lead sources" not in PAGE
        assert "we have calling" not in lowered

    def test_no_banned_slop(self):
        lowered = _visible_copy(PAGE).lower()
        for phrase in BANNED_SLOP:
            assert phrase not in lowered

    def test_faq_matches_json_ld_and_repo_limits(self):
        assert '"@type": "FAQPage"' in PAGE
        assert PAGE.count("<summary>") == 4
        assert PAGE.count('"@type": "Question"') == 4
        for question, answer in FAQ_ITEMS:
            assert PAGE.count(question) == 2
            assert f"<summary>{question}</summary>" in PAGE
            assert f'"name": "{question}"' in PAGE
            assert f'"text": "{_json_ld_answer(answer)}"' in PAGE
            for para in _faq_paragraphs(answer):
                assert PAGE.count(para) == 2
                assert f'<p class="faq-answer">{para}</p>' in PAGE
        include_answer = FAQ_ITEMS[2][1]
        assert "One user" in include_answer
        assert "10,000 contacts" in include_answer
        assert "25 messages a day" in include_answer

    def test_no_fake_seo_questions(self):
        for phrase in FAKE_SEO_QUESTIONS:
            assert phrase.lower() not in PAGE.lower()

    def test_no_ai_word(self):
        visible = _visible_copy(PAGE)
        assert re.search(r"\bAI\b", visible) is None
        assert re.search(r"\bAI\b", PAGE) is None

    def test_no_confirm_ttl_or_forbidden_claims(self):
        assert "Confirm (15 minutes)" not in PAGE
        assert "waits for Confirm" not in PAGE
        assert "MLS" not in PAGE
        assert "transaction" not in PAGE.lower()
        assert "DocuSeal" not in PAGE
        assert "calendar sync via B.O.B" not in PAGE.lower()

    def test_sourced_fub_fit_line(self):
        assert 'Generates less than 30 leads per month.' in PAGE
        assert "We do not publish a lead count." in PAGE

    def test_home_title_h1_and_bob_faq_unchanged(self):
        assert "<title>Free Real Estate CRM | Origen TechnolOG</title>" in LANDING
        assert "Your clients and follow-ups," in LANDING
        assert "<summary>What can B.O.B. do?</summary>" in LANDING
        assert LANDING.count("<summary>What can B.O.B. do?</summary>") == 1
