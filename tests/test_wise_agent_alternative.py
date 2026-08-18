"""Public /wise-agent-alternative page: route, SEO, sitemap, and copy guards."""
import re
from pathlib import Path
from xml.etree import ElementTree as ET

from tier_config.tier_limits import get_tier_defaults


ROOT = Path(__file__).resolve().parents[1]
PAGE = (ROOT / "templates" / "wise_agent_alternative.html").read_text()
LANDING = (ROOT / "templates" / "landing.html").read_text()
FUB = (ROOT / "templates" / "follow_up_boss_alternative.html").read_text()
SITEMAP = ROOT / "static" / "sitemap.xml"
LLMS = ROOT / "static" / "llms.txt"
FREE_LIMITS = get_tier_defaults("free")

PAGE_PATH = "/wise-agent-alternative"
TITLE = "Wise Agent Alternative for Real Estate Agents | Origen"
H1 = "Looking for a Wise Agent alternative?"
META = (
    "Comparing Origen with Wise Agent? See pricing, features and where each CRM fits. "
    "Origen includes a free plan with up to 10,000 contacts and B.O.B., its built-in AI assistant."
)
HOME_TITLE = "Free Real Estate CRM | Origen TechnolOG"
HOME_H1_PART = "Your clients and follow-ups,"
HOME_BOB_QUESTION = "What can B.O.B. do?"
HOME_BOB_P1 = (
    "B.O.B. is the AI assistant built into Origen. Instead of clicking through the CRM, "
    "just tell B.O.B. what you need done. It can find and update contacts, manage tasks, "
    "log activity, organize clients, and much more. If you can do it in Origen, you can "
    "ask B.O.B. to do it for you."
)
HOME_BOB_P2 = (
    "The free plan includes 25 messages a day, and you can also chat with B.O.B. "
    "through Telegram after connecting it from your profile."
)
SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
ALLOWED_SITEMAP_PATHS = {
    "/",
    "/register",
    "/login",
    "/terms-privacy",
    "/free-real-estate-crm",
    "/follow-up-boss-alternative",
    PAGE_PATH,
    "/kvcore-alternative",
}
BLOCKED_SEO_PATHS = (
    "/pricing",
    "/about",
    "/blog",
    "/help",
    "/features",
    "/contact",
    "/boomtown-alternative",
    "/boldtrail-alternative",
    "/top-producer-alternative",
)
BANNED_SLOP = (
    "unlock",
    "empower",
    "elevate",
    "seamless",
    "seamlessly",
    "streamline",
    "streamlined",
    "revolutionize",
    "transform",
    "supercharge",
    "robust",
    "comprehensive",
    "game-changing",
    "all-in-one solution",
    "take your business to the next level",
    "designed for your success",
    "focus on what matters most",
    "powerful platform",
)
FAQ_ITEMS = (
    (
        "What is B.O.B.?",
        "B.O.B. is the AI assistant inside Origen. You can ask it questions about your CRM or tell it to do CRM work for you.\n\nIf you can do something in Origen, you can ask B.O.B. to do it for you.",
    ),
    (
        "Is Origen really free?",
        "Yes. Origen has a free plan, not just a free trial. You don't need a credit card to start.\n\nThe current free plan includes one user, up to 10,000 contacts and 25 B.O.B. messages per day.",
    ),
    (
        "Can I try Origen without moving everything over?",
        "Yes. You can create a free account and try Origen with a few contacts first. There's no need to commit to a paid plan just to see whether it works for you.",
    ),
    (
        "Is Origen a full replacement for Wise Agent?",
        "That depends. If you need paid team logins, WiseText, WiseSocial or the transaction and marketing tools on their pricing page, Origen doesn't currently try to duplicate all of that.\n\nIf what you need is a straightforward CRM for contacts, tasks, follow-ups and day-to-day client work, Origen may be a much simpler fit.",
    ),
    (
        "How much does Wise Agent cost?",
        "Their pricing page lists CRM at $49 a month, or $499 a year ($42 a month billed annually). There is a 14-day free trial. There is no published free plan.",
    ),
)
FAKE_SEO_QUESTIONS = (
    "origentech",
    "Is this HubSpot",
    "Is this Follow Up Boss or HubSpot",
    "Is this origentech.com or Origin CRM",
    "What can B.O.B. do?",
    "How much is Wise Agent?",
    "Does Wise Agent have a free plan?",
    "What does Origen include?",
    "Can I buy Pro today?",
)
TABLE_ROWS = (
    ("Starting price", "$49/month on CRM", "Free"),
    ("Free plan", "No, 14-day free trial", "Yes"),
    ("Credit card", "Not published", "No"),
    ("Contacts", "Not published", "Up to 10,000 on free"),
    ("Users", "N/A", "1"),
    ("Calendar", "Team-friendly calendar", "Google Calendar task sync"),
    ("Calling & texting", "WiseText paid add-on", "Not included"),
    ("Shared team logins", "Up to 5 on one login", "1 user"),
)
SECTION_HEADINGS = (
    "Origen vs. Wise Agent",
    "The biggest difference is what you actually need",
    "Then there's B.O.B.",
    "When Wise Agent is probably the better fit",
    "When Origen may be the better fit",
    "Common questions",
)
HERO_LINES = (
    "Wise Agent is a real estate CRM with published pricing and a 14-day trial. It starts at $49 a month on their pricing page. That is a real product, and a lot of agents use it. Not every agent wants another monthly bill to manage contacts, tasks and follow-ups.",
    "Origen gives individual real estate agents a simpler place to do that work, with a free plan you can keep using.",
    "No credit card required.",
    "Free plan · 1 user · Up to 10,000 contacts · 25 B.O.B. messages per day",
)
DISCLAIMER = (
    "Wise Agent is a trademark of its owner. Origen TechnolOG is not affiliated. "
    "Facts from wiseagent.com/pricing as of 2026-08-18."
)
BOB_SECTION_LINES = (
    "Then there's B.O.B.",
    "B.O.B. is the AI assistant built into Origen.",
    "Instead of clicking through the CRM every time you want to get something done, tell B.O.B. what you need.",
    "Find a client. Update a contact. Add a note. Complete a task. Log activity. Organize contacts. Ask questions about what's already in your CRM.",
    "If you can do it in Origen, you can ask B.O.B. to do it for you.",
    "The free plan includes 25 B.O.B. messages per day.",
    "B.O.B. can also be connected to Telegram, so you don't always have to be sitting inside the CRM to use it.",
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


def _body_copy(html):
    match = re.search(r"<body\b[^>]*>(.*)</body>", html, flags=re.I | re.S)
    body = match.group(1) if match else html
    return _visible_copy(body)


class TestWiseAgentAlternativeRoute:
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
        assert "https://www.origentechnolog.com/wise-agent-alternative" in text
        assert "https://www.origentechnolog.com/wise-agent-alternative" in LLMS.read_text()
        for blocked in BLOCKED_SEO_PATHS:
            assert f"https://www.origentechnolog.com{blocked}" not in text
        assert "https://www.origentechnolog.com/dashboard" not in text
        assert "—" not in text

    def test_blocked_marketing_urls_are_not_built(self, client):
        for path in BLOCKED_SEO_PATHS:
            resp = client.get(path)
            assert resp.status_code == 404, f"{path} should not be a public page"


class TestWiseAgentAlternativeSeo:
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

        assert f"<title>{HOME_TITLE}</title>" in home
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


class TestWiseAgentAlternativeCopy:
    def test_required_human_copy(self):
        assert f"<title>{TITLE}</title>" in PAGE
        assert H1 in PAGE
        assert META in PAGE
        assert "Start Free" in PAGE
        assert "Try Origen Free" in PAGE
        assert "url_for('auth.register')" in PAGE
        assert "url_for('main.free_real_estate_crm')" not in PAGE
        assert "url_for('main.landing')" in PAGE
        assert DISCLAIMER in PAGE
        assert PAGE.count(DISCLAIMER) == 1

    def test_h1_is_the_human_line(self, client):
        html = client.get(PAGE_PATH).get_data(as_text=True)
        match = re.search(r"<h1[^>]*>(.*?)</h1>", html, flags=re.S)
        assert match is not None
        h1 = re.sub(r"<[^>]+>", "", match.group(1))
        h1 = re.sub(r"\s+", " ", h1).strip()
        assert h1 == H1

    def test_hero_uses_specified_lines(self):
        for line in HERO_LINES:
            assert line in PAGE

    def test_section_headings_are_present(self):
        for heading in SECTION_HEADINGS:
            assert heading in PAGE

    def test_comparison_table_matches_specified_rows(self, client):
        html = client.get(PAGE_PATH).get_data(as_text=True)
        visible = _visible_copy(html)
        for label, other, origen in TABLE_ROWS:
            assert label in visible
            assert other in visible
            assert origen in visible
            assert label in PAGE
            assert other in PAGE
            assert origen in PAGE

    def test_no_invented_wise_agent_contact_cap_or_ai_row(self):
        visible = _visible_copy(PAGE)
        assert re.search(r"Wise Agent[^.]*unlimited contacts", visible, flags=re.I) is None
        assert "unlimited contacts" not in PAGE.lower()
        assert ">AI<" not in PAGE
        assert "<th" in PAGE
        assert re.search(r">\s*AI\s*<", PAGE) is None
        assert "FUB AI" not in PAGE
        assert "Wise Agent AI" not in PAGE
        assert "Wise Agent contact cap" not in PAGE
        assert re.search(r"Contacts\s+Not published\s+Up to 10,000 on free", visible) is not None

    def test_does_not_guess_wise_agent_credit_card(self):
        visible = _visible_copy(PAGE)
        assert re.search(r"Credit card\s+Not published\s+No", visible) is not None
        lowered = PAGE.lower()
        assert "trial requires a card" not in lowered
        assert "trial requires a credit card" not in lowered
        assert "need a credit card for the trial" not in lowered

    def test_limits_match_repo(self):
        assert FREE_LIMITS["max_users"] == 1
        assert FREE_LIMITS["max_contacts"] == 10000
        assert FREE_LIMITS["daily_ai_chat_messages"] == 25
        visible = _visible_copy(PAGE)
        assert "1 user" in visible
        assert "one user" in visible.lower()
        assert "10,000 contacts" in visible
        assert "25 B.O.B. messages per day" in visible
        assert "Up to 10,000 on free" in visible

    def test_cta_goes_to_register(self, client):
        html = client.get(PAGE_PATH).get_data(as_text=True)
        assert re.search(r'href="/register"[^>]*>\s*Start Free', html) is not None
        assert re.search(r'href="/register"[^>]*>\s*Try Origen Free', html) is not None

    def test_no_em_dashes(self):
        assert "—" not in PAGE
        assert "—" not in LANDING

    def test_no_exclamation_in_visible_copy(self):
        assert "!" not in _visible_copy(PAGE)

    def test_bob_section_matches_fub_wording(self):
        for line in BOB_SECTION_LINES:
            assert line in PAGE
            assert line in FUB

    def test_does_not_claim_origen_calling_idx_routing_or_migration(self, client):
        html = client.get(PAGE_PATH).get_data(as_text=True)
        visible = _visible_copy(html)
        assert re.search(
            r"Calling & texting\s+WiseText paid add-on\s+Not included",
            visible,
        ) is not None
        lowered = visible.lower()
        assert "origen includes calling" not in lowered
        assert "origen includes texting" not in lowered
        assert "origen calling" not in lowered
        assert "lead routing" not in lowered
        assert "idx" not in lowered
        assert "twilio" not in lowered
        assert "automated crm migration" not in lowered
        assert "import from wise agent" not in lowered

    def test_alternative_phrase_is_not_stuffed(self):
        body = _body_copy(PAGE)
        matches = re.findall(r"wise agent alternative", body, flags=re.I)
        assert len(matches) <= 1
        assert PAGE.lower().count("wise agent alternative") <= 3

    def test_footer_disclaimer_is_the_only_added_legal_line(self):
        visible = _visible_copy(PAGE)
        assert DISCLAIMER in visible
        assert visible.count(DISCLAIMER) == 1
        assert "BoomTown" not in PAGE
        assert "—" not in visible
        assert 'href="/pricing"' not in PAGE
        assert "url_for('main.pricing')" not in PAGE
        lowered = visible.lower()
        for phrase in BANNED_SLOP:
            assert phrase not in lowered

    def test_no_invented_legal_entity_name(self):
        lowered = PAGE.lower()
        assert "wise agent llc" not in lowered
        assert "wise agent, inc" not in lowered
        assert "wiseagent inc" not in lowered
        assert "holdco" not in lowered

    def test_no_banned_slop(self):
        lowered = _visible_copy(PAGE).lower()
        for phrase in BANNED_SLOP:
            assert phrase not in lowered

    def test_faq_matches_json_ld_and_repo_limits(self):
        assert '"@type": "FAQPage"' in PAGE
        assert PAGE.count("<summary>") == 5
        assert PAGE.count('"@type": "Question"') == 5
        for question, answer in FAQ_ITEMS:
            assert PAGE.count(question) == 2
            assert f"<summary>{question}</summary>" in PAGE
            assert f'"name": "{question}"' in PAGE
            assert f'"text": "{_json_ld_answer(answer)}"' in PAGE
            for para in _faq_paragraphs(answer):
                assert PAGE.count(para) == 2
                assert f'<p class="faq-answer">{para}</p>' in PAGE
        free_answer = FAQ_ITEMS[1][1]
        assert "one user" in free_answer
        assert "10,000 contacts" in free_answer
        assert "25 B.O.B. messages per day" in free_answer

    def test_no_fake_seo_questions(self):
        for phrase in FAKE_SEO_QUESTIONS:
            assert phrase.lower() not in PAGE.lower()

    def test_no_extra_wise_agent_prices_in_table(self):
        assert "$11" not in PAGE
        assert "$80" not in PAGE
        assert "$69" not in PAGE
        assert "$499" in PAGE
        assert "$42" in PAGE

    def test_home_title_h1_and_bob_faq_unchanged(self):
        assert f"<title>{HOME_TITLE}</title>" in LANDING
        assert HOME_H1_PART in LANDING
        assert f"<summary>{HOME_BOB_QUESTION}</summary>" in LANDING
        assert LANDING.count(f"<summary>{HOME_BOB_QUESTION}</summary>") == 1
        assert HOME_BOB_P1 in LANDING
        assert HOME_BOB_P2 in LANDING
        assert LANDING.count(HOME_BOB_P1) == 2
        assert LANDING.count(HOME_BOB_P2) == 2
        assert "wise-agent-alternative" not in LANDING
        assert "kvcore-alternative" not in LANDING
        assert "the free real estate CRM page" in LANDING

    def test_fub_template_was_left_alone(self):
        assert "Looking for a Follow Up Boss alternative?" in FUB
        assert "wise-agent-alternative" not in FUB
        assert "kvcore-alternative" not in FUB
