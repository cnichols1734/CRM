"""Public /kvcore-alternative page: route, SEO, sitemap, and copy guards."""
import re
from pathlib import Path
from xml.etree import ElementTree as ET

from tier_config.tier_limits import get_tier_defaults


ROOT = Path(__file__).resolve().parents[1]
PAGE = (ROOT / "templates" / "kvcore_alternative.html").read_text()
LANDING = (ROOT / "templates" / "landing.html").read_text()
FUB = (ROOT / "templates" / "follow_up_boss_alternative.html").read_text()
SITEMAP = ROOT / "static" / "sitemap.xml"
LLMS = ROOT / "static" / "llms.txt"
FREE_LIMITS = get_tier_defaults("free")

PAGE_PATH = "/kvcore-alternative"
TITLE = "kvCORE Alternative for Real Estate Agents | Origen"
H1 = "Looking for a kvCORE alternative?"
META = (
    "kvCORE is now BoldTrail. See how Origen compares: a free plan with up to 10,000 contacts "
    "and B.O.B., its built-in AI assistant."
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
    "/wise-agent-alternative",
    PAGE_PATH,
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
        "How much does kvCORE / BoldTrail cost?",
        "They do not publish a standard public price. Their pricing page is quote-only (Talk to Sales). Their blog says they don't publish standard public pricing.",
    ),
    (
        "Is kvCORE the same as BoldTrail?",
        "BoldTrail is the current product. kvCORE is the earlier name.",
    ),
    (
        "Is Origen really free?",
        "Yes. Origen has a free plan, not just a free trial. You don't need a credit card to start.\n\nThe current free plan includes one user, up to 10,000 contacts and 25 B.O.B. messages per day.",
    ),
    (
        "What is B.O.B.?",
        "B.O.B. is the AI assistant inside Origen. You can ask it questions about your CRM or tell it to do CRM work for you.\n\nIf you can do something in Origen, you can ask B.O.B. to do it for you.",
    ),
    (
        "Can I try Origen without moving everything over?",
        "Yes. You can create a free account and try Origen with a few contacts first. There's no need to commit to a paid plan just to see whether it works for you.",
    ),
    (
        "Is Origen a full replacement for kvCORE / BoldTrail?",
        "That depends. If you need IDX websites, lead generation, recruiting, BackOffice, or the rest of the BoldTrail ecosystem, Origen doesn't currently try to duplicate all of that.",
    ),
)
FAKE_SEO_QUESTIONS = (
    "origentech",
    "Is this HubSpot",
    "Is this Follow Up Boss or HubSpot",
    "Is this origentech.com or Origin CRM",
    "What can B.O.B. do?",
    "How much is kvCORE?",
    "How much is BoldTrail?",
    "Does BoldTrail have a free plan?",
    "What does Origen include?",
    "Can I buy Pro today?",
)
TABLE_ROWS = (
    ("Starting price", "Quote-only", "Free"),
    ("Free plan", "No", "Yes"),
    ("IDX / lead gen / recruiting / BackOffice", "BoldTrail ecosystem", "Not included"),
    ("Credit card", "Not published", "No"),
    ("Contacts", "Not published", "Up to 10,000 on free"),
    ("Users", "Not published", "1"),
    ("Calendar", "Not published", "Google Calendar task sync"),
)
SECTION_HEADINGS = (
    "Origen vs. kvCORE / BoldTrail",
    "The biggest difference is what you actually need",
    "Then there's B.O.B.",
    "When BoldTrail is probably the better fit",
    "When Origen may be the better fit",
    "Common questions",
)
HERO_LINES = (
    "kvCORE is now BoldTrail, from Inside Real Estate. It is a large real estate platform for agents, teams and brokerages. Pricing is quote-only. There is no published free plan. That is a real product, and it is built for a lot more than a solo agent's daily CRM work.",
    "Origen gives individual agents a simpler place to manage contacts, tasks, follow-ups, with a free plan you can keep using.",
    "No credit card required.",
    "Free plan · 1 user · Up to 10,000 contacts · 25 B.O.B. messages per day",
)
DISCLAIMER = (
    "kvCORE and BoldTrail are trademarks of their owners. Origen TechnolOG is not affiliated. "
    "Facts from boldtrail.com as of 2026-08-18."
)
BOOMTOWN_LINE = (
    "Inside Real Estate acquired BoomTown in 2023 and official BoldTrail pages say "
    "BoomTown is being folded into BoldTrail."
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


class TestKvcoreAlternativeRoute:
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
        assert "https://www.origentechnolog.com/kvcore-alternative" in text
        assert "https://www.origentechnolog.com/kvcore-alternative" in LLMS.read_text()
        assert "https://www.origentechnolog.com/boldtrail-alternative" not in text
        assert "https://www.origentechnolog.com/boomtown-alternative" not in text
        for blocked in BLOCKED_SEO_PATHS:
            assert f"https://www.origentechnolog.com{blocked}" not in text
        assert "https://www.origentechnolog.com/dashboard" not in text
        assert "—" not in text

    def test_blocked_marketing_urls_are_not_built(self, client):
        for path in BLOCKED_SEO_PATHS:
            resp = client.get(path)
            assert resp.status_code == 404, f"{path} should not be a public page"


class TestKvcoreAlternativeSeo:
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


class TestKvcoreAlternativeCopy:
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
        assert BOOMTOWN_LINE in PAGE
        assert PAGE.count("BoomTown") == 2

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

    def test_no_invented_boldtrail_price_or_contact_cap(self):
        visible = _visible_copy(PAGE)
        assert "$" not in visible
        assert "quote-only" in visible.lower()
        assert re.search(r"Contacts\s+Not published\s+Up to 10,000 on free", visible) is not None
        lowered = PAGE.lower()
        assert "boldtrail costs" not in lowered
        assert "starts at $" not in lowered
        assert "unlimited contacts" not in lowered

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
            r"IDX / lead gen / recruiting / BackOffice\s+BoldTrail ecosystem\s+Not included",
            visible,
        ) is not None
        lowered = visible.lower()
        assert "origen includes calling" not in lowered
        assert "origen includes texting" not in lowered
        assert "origen calling" not in lowered
        assert "origen includes idx" not in lowered
        assert "lead routing" not in lowered
        assert "twilio" not in lowered
        assert "automated crm migration" not in lowered
        assert "import from kvcore" not in lowered
        assert "import from boldtrail" not in lowered
        assert "you will be moved" not in lowered

    def test_alternative_phrase_is_not_stuffed(self):
        body = _body_copy(PAGE)
        matches = re.findall(r"kvcore alternative", body, flags=re.I)
        assert len(matches) <= 1
        assert PAGE.lower().count("kvcore alternative") <= 3

    def test_boomtown_is_one_quiet_factual_sentence(self):
        assert BOOMTOWN_LINE in PAGE
        assert PAGE.count(BOOMTOWN_LINE) == 1
        assert PAGE.count("BoomTown") == 2
        assert "/boomtown-alternative" not in PAGE
        assert "url_for('main.boomtown" not in PAGE
        lowered = PAGE.lower()
        assert "moved on" not in lowered
        assert "migration deadline" not in lowered

    def test_footer_disclaimer_is_the_only_added_legal_line(self):
        visible = _visible_copy(PAGE)
        assert DISCLAIMER in visible
        assert visible.count(DISCLAIMER) == 1
        assert "—" not in visible
        assert "/pricing" not in PAGE
        assert "Inside Real Estate" in PAGE
        lowered = visible.lower()
        for phrase in BANNED_SLOP:
            assert phrase not in lowered

    def test_no_invented_legal_entity_name(self):
        lowered = PAGE.lower()
        assert "inside real estate llc" not in lowered
        assert "inside real estate, inc" not in lowered
        assert "holdco" not in lowered
        assert "zillow" not in lowered

    def test_no_banned_slop(self):
        lowered = _visible_copy(PAGE).lower()
        for phrase in BANNED_SLOP:
            assert phrase not in lowered

    def test_faq_matches_json_ld_and_repo_limits(self):
        assert '"@type": "FAQPage"' in PAGE
        assert PAGE.count("<summary>") == 6
        assert PAGE.count('"@type": "Question"') == 6
        for question, answer in FAQ_ITEMS:
            assert PAGE.count(question) == 2
            assert f"<summary>{question}</summary>" in PAGE
            assert f'"name": "{question}"' in PAGE
            assert f'"text": "{_json_ld_answer(answer)}"' in PAGE
            for para in _faq_paragraphs(answer):
                assert PAGE.count(para) == 2
                assert f'<p class="faq-answer">{para}</p>' in PAGE
        free_answer = FAQ_ITEMS[2][1]
        assert "one user" in free_answer
        assert "10,000 contacts" in free_answer
        assert "25 B.O.B. messages per day" in free_answer

    def test_no_fake_seo_questions(self):
        for phrase in FAKE_SEO_QUESTIONS:
            assert phrase.lower() not in PAGE.lower()

    def test_home_title_h1_and_bob_faq_unchanged(self):
        assert f"<title>{HOME_TITLE}</title>" in LANDING
        assert HOME_H1_PART in LANDING
        assert f"<summary>{HOME_BOB_QUESTION}</summary>" in LANDING
        assert LANDING.count(f"<summary>{HOME_BOB_QUESTION}</summary>") == 1
        assert HOME_BOB_P1 in LANDING
        assert HOME_BOB_P2 in LANDING
        assert LANDING.count(HOME_BOB_P1) == 2
        assert LANDING.count(HOME_BOB_P2) == 2
        assert "kvcore-alternative" not in LANDING
        assert "wise-agent-alternative" not in LANDING
        assert "the free real estate CRM page" in LANDING

    def test_fub_template_was_left_alone(self):
        assert "Looking for a Follow Up Boss alternative?" in FUB
        assert "kvcore-alternative" not in FUB
        assert "wise-agent-alternative" not in FUB
        assert "BoomTown" not in FUB
