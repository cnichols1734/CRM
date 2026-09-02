"""Public /follow-up-boss-alternative page: route, SEO, sitemap, and copy guards."""
import json
import re
from pathlib import Path
from xml.etree import ElementTree as ET

from tier_config.tier_limits import get_tier_defaults


ROOT = Path(__file__).resolve().parents[1]
PAGE = (ROOT / "templates" / "follow_up_boss_alternative.html").read_text()
LANDING = (ROOT / "templates" / "landing.html").read_text()
SITEMAP = ROOT / "static" / "sitemap.xml"
LLMS = ROOT / "static" / "llms.txt"
FREE_LIMITS = get_tier_defaults("free")

PAGE_PATH = "/follow-up-boss-alternative"
TITLE = "Follow Up Boss Alternative for Real Estate Agents | AgentFlow"
H1 = "Looking for a Follow Up Boss alternative?"
META = (
    "Comparing AgentFlow with Follow Up Boss? See pricing, features and where each CRM fits. "
    "AgentFlow includes a free plan with up to 10,000 contacts and B.O.B., its built-in AI assistant."
)
HOME_TITLE = "Free Real Estate CRM | AgentFlow"
HOME_H1_PART = "Keep up with every client"
HOME_BOB_QUESTION = "What can B.O.B. do?"
HOME_BOB_P1 = (
    "B.O.B. is the AI assistant built into AgentFlow. Instead of clicking through the CRM, "
    "just tell B.O.B. what you need done. It can find and update contacts, manage tasks, "
    "log activity, organize clients, and much more. If you can do it in AgentFlow, you can "
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
    PAGE_PATH,
    "/wise-agent-alternative",
    "/kvcore-alternative",
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
        "How much does Follow Up Boss cost?",
        "Follow Up Boss currently lists its Grow plan at $69 per user per month, or $58 per user per month when billed annually. It offers a 14-day free trial rather than a permanent free plan.",
    ),
    (
        "Is AgentFlow really free?",
        "Yes. AgentFlow has a free plan, not just a free trial. You don't need a credit card to start.\n\nThe current free plan includes one user, up to 10,000 contacts and 25 B.O.B. messages per day.",
    ),
    (
        "Is AgentFlow a full replacement for Follow Up Boss?",
        "That depends on what you use Follow Up Boss for. If you need its advanced lead routing, calling, texting, team management or large integration ecosystem, AgentFlow doesn't currently try to duplicate all of that.\n\nIf what you need is a straightforward CRM for contacts, tasks, follow-ups and day-to-day client work, AgentFlow may be a much simpler fit.",
    ),
    (
        "Can I try AgentFlow without moving everything over?",
        "Yes. You can create a free account and try AgentFlow with a few contacts first. There's no need to commit to a paid plan just to see whether it works for you.",
    ),
    (
        "What is B.O.B.?",
        "B.O.B. is the AI assistant inside AgentFlow. You can ask it questions about your CRM or tell it to do CRM work for you.\n\nIf you can do something in AgentFlow, you can ask B.O.B. to do it for you.",
    ),
)
FAKE_SEO_QUESTIONS = (
    "origentech",
    "Is this HubSpot",
    "Is this Follow Up Boss or HubSpot",
    "Is this origentech.com or Origin CRM",
    "What can B.O.B. do?",
    "How much is Follow Up Boss?",
    "Does Follow Up Boss have a free plan?",
    "What does AgentFlow include?",
    "Can I buy Pro today?",
)
TABLE_ROWS = (
    ("Starting price", "$69/user/month on Grow", "Free"),
    ("Free plan", "No, 14-day free trial", "Yes"),
    ("Credit card to start", "No", "No"),
    ("Contacts", "Unlimited", "Up to 10,000 on free"),
    ("Free-plan users", "N/A", "1"),
    ("Gmail", "Supported", "Send email through Gmail"),
    ("Calendar", "Calendar and email sync", "Google Calendar task sync"),
    ("AI", "FUB AI features", "B.O.B. AI assistant"),
    ("Calling & texting", "Available, with Calling included on higher plans or added to Grow", "Not included"),
    ("Advanced lead routing", "Yes", "Not included"),
    ("Large integration ecosystem", "Yes", "Not included"),
)
SECTION_HEADINGS = (
    "AgentFlow vs. Follow Up Boss",
    "The biggest difference is what you actually need",
    "Then there's B.O.B.",
    "When Follow Up Boss is probably the better fit",
    "When AgentFlow may be the better fit",
    "Common questions",
)
HERO_LINES = (
    "Follow Up Boss is a powerful CRM, especially for teams managing a large volume of leads. But not every agent needs everything it offers or wants another $69-per-user monthly subscription.",
    "AgentFlow gives individual real estate agents a simpler place to manage contacts, tasks, follow-ups and day-to-day CRM work, with a free plan you can keep using.",
    "No credit card required.",
    "Free plan · 1 user · Up to 10,000 contacts · 25 B.O.B. messages per day",
)
DISCLAIMER = (
    "Follow Up Boss is a trademark of its owner. Zillow Group acquired it in 2023. "
    "AgentFlow is not affiliated with Follow Up Boss or Zillow Group, and they did not "
    "review or endorse this page. Prices and features here come from followupboss.com "
    "as of August 18, 2026."
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
        path = loc.replace("https://agentflow.origentechnolog.com", "", 1) or "/"
        paths.append(path)
    return paths


def _app_base_url(app):
    return (app.config.get("APP_BASE_URL") or "https://agentflow.origentechnolog.com").rstrip("/")


def _json_ld_graph(html):
    match = re.search(r'<script type="application/ld\+json">(.*?)</script>', html, flags=re.S)
    assert match is not None
    data = json.loads(match.group(1))
    assert data.get("@context") == "https://schema.org"
    return data["@graph"]


def _visible_copy(html):
    html = re.sub(r"<script\b[^>]*>.*?</script>", " ", html, flags=re.I | re.S)
    html = re.sub(r"<style\b[^>]*>.*?</style>", " ", html, flags=re.I | re.S)
    html = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", html)


def _body_copy(html):
    match = re.search(r"<body\b[^>]*>(.*)</body>", html, flags=re.I | re.S)
    body = match.group(1) if match else html
    return _visible_copy(body)


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
        assert "https://agentflow.origentechnolog.com/follow-up-boss-alternative" in text
        assert "https://agentflow.origentechnolog.com/follow-up-boss-alternative" in LLMS.read_text()
        for blocked in BLOCKED_SEO_PATHS:
            assert f"https://agentflow.origentechnolog.com{blocked}" not in text
        assert "https://agentflow.origentechnolog.com/dashboard" not in text
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
        assert f'property="og:image" content="{base}/static/images/og-share.png"' in page
        assert f'name="twitter:image" content="{base}/static/images/og-share.png"' in page
        assert f'property="og:image" content="{base}/static/images/og-share.png"' in home
        assert f'name="twitter:image" content="{base}/static/images/og-share.png"' in home

        assert f"<title>{HOME_TITLE}</title>" in home
        assert f'rel="canonical" href="{home_url}"' in home
        assert page.count("<title>") == 1
        assert f'rel="canonical" href="{home_url}"' not in page
        assert TITLE not in home

    def test_json_ld_types_and_price(self, app, client):
        html = client.get(PAGE_PATH).get_data(as_text=True)
        base = _app_base_url(app)
        graph = _json_ld_graph(html)
        types = [node.get("@type") for node in graph]
        assert types == [
            "Organization",
            "SoftwareApplication",
            "FAQPage",
            "BreadcrumbList",
        ]
        assert "SearchAction" not in html
        assert "aggregateRating" not in html
        assert html.count('<script type="application/ld+json">') == 1
        assert '"price": "0"' in html
        assert '"priceCurrency": "USD"' in html
        assert '"isAccessibleForFree": true' in html
        assert f'"url": "{base}{PAGE_PATH}"' in html

        crumb = next(node for node in graph if node["@type"] == "BreadcrumbList")
        assert crumb["@id"] == f"{base}{PAGE_PATH}#breadcrumb"
        assert crumb["itemListElement"] == [
            {
                "@type": "ListItem",
                "position": 1,
                "name": "Home",
                "item": f"{base}/",
            },
            {
                "@type": "ListItem",
                "position": 2,
                "name": "Follow Up Boss alternative",
                "item": f"{base}{PAGE_PATH}",
            },
        ]
        visible = _visible_copy(html)
        assert "Home" not in visible
        assert "breadcrumb" not in visible.lower()

    def test_no_robots_noindex(self, client):
        html = client.get(PAGE_PATH).get_data(as_text=True)
        assert "noindex" not in html.lower()


class TestFollowUpBossAlternativeCopy:
    def test_required_human_copy(self):
        assert f"<title>{TITLE}</title>" in PAGE
        assert H1 in PAGE
        assert META in PAGE
        assert "Start Free" in PAGE
        assert "Try AgentFlow Free" in PAGE
        assert "url_for('auth.register')" in PAGE
        assert PAGE.count("url_for('main.free_real_estate_crm')") == 2
        assert "url_for('main.landing')" in PAGE
        assert "More on Origen is on" not in PAGE
        assert "More on AgentFlow is on" not in PAGE
        assert PAGE.count("the free real estate CRM page") == 1
        assert "More on what's included is on" in PAGE
        assert DISCLAIMER in PAGE
        assert PAGE.count(DISCLAIMER) == 1

    def test_h1_is_the_human_line(self, client):
        html = client.get(PAGE_PATH).get_data(as_text=True)
        match = re.search(r"<h1[^>]*>(.*?)</h1>", html, flags=re.S)
        assert match is not None
        h1 = re.sub(r"<[^>]+>", "", match.group(1))
        h1 = re.sub(r"\s+", " ", h1).strip()
        assert h1 == H1

    def test_hero_uses_his_lines(self):
        for line in HERO_LINES:
            assert line in PAGE

    def test_section_headings_are_present(self):
        for heading in SECTION_HEADINGS:
            assert heading in PAGE

    def test_comparison_table_matches_his_rows(self, client):
        html = client.get(PAGE_PATH).get_data(as_text=True)
        visible = _visible_copy(html)
        for label, fub, origen in TABLE_ROWS:
            assert label in visible
            assert fub in visible
            assert origen in visible
            assert label in PAGE
            assert fub in PAGE
            assert origen in PAGE

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

    def test_does_not_claim_origen_has_unlimited_contacts(self):
        visible = _visible_copy(PAGE)
        assert re.search(r"AgentFlow[^.]*unlimited contacts", visible, flags=re.I) is None
        assert "AgentFlow publishes unlimited contacts" not in PAGE
        assert "unlimited contacts on AgentFlow" not in PAGE.lower()

    def test_cta_goes_to_register(self, client):
        html = client.get(PAGE_PATH).get_data(as_text=True)
        assert re.search(r'href="/register"[^>]*>\s*Start Free', html) is not None
        assert re.search(r'href="/register"[^>]*>\s*Try AgentFlow Free', html) is not None

    def test_no_em_dashes(self):
        assert "—" not in PAGE
        assert "—" not in LANDING

    def test_no_exclamation_in_visible_copy(self):
        assert "!" not in _visible_copy(PAGE)

    def test_does_not_claim_never_paid_plan(self):
        assert "never be a paid plan" not in PAGE.lower()
        assert "never be a paid" not in PAGE.lower()

    def test_does_not_claim_fub_trial_needs_a_card(self):
        lowered = PAGE.lower()
        assert "trial requires a card" not in lowered
        assert "trial requires a credit card" not in lowered
        assert "need a credit card for the trial" not in lowered
        assert "credit card required for those 14 days" not in PAGE
        assert "Then they charge." not in PAGE
        assert "After that they charge" not in PAGE
        visible = _visible_copy(PAGE)
        assert "Credit card to start" in visible
        assert re.search(r"Credit card to start\s+No\s+No", visible) is not None

    def test_does_not_say_charges_you_after_14_days(self):
        lowered = PAGE.lower()
        assert "charges you after 14 days" not in lowered
        assert "after that they charge" not in lowered
        assert "14-day free trial rather than a permanent free plan" in PAGE

    def test_does_not_say_we_are_not_a_replacement(self):
        lowered = PAGE.lower()
        assert "we are not a replacement for follow up boss" not in lowered
        assert "we are not a replacement" not in lowered
        assert "Is AgentFlow a full replacement for Follow Up Boss?" in PAGE

    def test_does_not_claim_built_by_agents(self):
        lowered = PAGE.lower()
        assert "built by real estate agents" not in lowered
        assert "by real estate agents" not in lowered
        assert "built for agents, by agents" not in lowered
        assert "by agents" not in lowered

    def test_does_not_claim_origen_calling_routing_or_integrations(self, client):
        html = client.get(PAGE_PATH).get_data(as_text=True)
        visible = _visible_copy(html)
        assert "Calling & texting" in visible
        assert "Available, with Calling included on higher plans or added to Grow" in visible
        assert "Advanced lead routing" in visible
        assert "Large integration ecosystem" in visible
        assert re.search(
            r"Calling & texting\s+Available, with Calling included on higher plans or added to Grow\s+Not included",
            visible,
        ) is not None
        assert re.search(r"Advanced lead routing\s+Yes\s+Not included", visible) is not None
        assert re.search(r"Large integration ecosystem\s+Yes\s+Not included", visible) is not None
        lowered = visible.lower()
        assert "origen includes calling" not in lowered
        assert "origen includes texting" not in lowered
        assert "origen calling" not in lowered
        assert "twilio" not in lowered
        assert "automated crm migration" not in lowered
        assert "automated fub migration" not in lowered
        assert "import from follow up boss" not in lowered

    def test_does_not_imply_fub_lacks_ai(self):
        visible = _visible_copy(PAGE)
        assert "FUB AI features" in visible
        assert "B.O.B. AI assistant" in visible
        lowered = visible.lower()
        assert "follow up boss does not have ai" not in lowered
        assert "fub has no ai" not in lowered
        assert "fub lacks ai" not in lowered
        assert "without ai" not in lowered

    def test_alternative_phrase_is_not_stuffed(self):
        body = _body_copy(PAGE)
        matches = re.findall(r"follow up boss alternative", body, flags=re.I)
        assert len(matches) <= 1
        page_without_json_ld = re.sub(
            r'<script type="application/ld\+json">.*?</script>',
            "",
            PAGE,
            flags=re.S,
        )
        assert page_without_json_ld.lower().count("follow up boss alternative") <= 3

    def test_no_thirty_leads_gotcha(self):
        lowered = PAGE.lower()
        assert "30 leads" not in lowered
        assert "less than 30" not in lowered
        assert "we do not publish a lead count" not in lowered

    def test_footer_disclaimer_is_the_only_added_legal_line(self):
        visible = _visible_copy(PAGE)
        assert DISCLAIMER in visible
        assert visible.count(DISCLAIMER) == 1
        assert "More on Origen is on" not in visible
        assert "More on AgentFlow is on" not in visible
        assert "30 leads" not in visible.lower()
        assert "—" not in visible
        assert "MFTB" not in PAGE
        assert "Holdco" not in PAGE
        assert "lawyer" not in visible.lower()
        assert "/pricing" not in PAGE
        lowered = visible.lower()
        for phrase in BANNED_SLOP:
            assert phrase not in lowered

    def test_no_banned_slop(self):
        lowered = _visible_copy(PAGE).lower()
        for phrase in BANNED_SLOP:
            assert phrase not in lowered

    def test_faq_matches_json_ld_and_repo_limits(self):
        assert '"@type": "FAQPage"' in PAGE
        assert '"@type": "BreadcrumbList"' in PAGE
        assert '"@id": "{{ app_base_url }}/follow-up-boss-alternative#breadcrumb"' in PAGE
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

    def test_no_confirm_ttl_or_forbidden_claims(self):
        assert "Confirm (15 minutes)" not in PAGE
        assert "waits for Confirm" not in PAGE
        assert "MLS" not in PAGE
        assert "transaction" not in PAGE.lower()
        assert "DocuSeal" not in PAGE
        assert "calendar sync via B.O.B" not in PAGE.lower()
        assert "document generation" not in PAGE.lower()
        assert "doc gen" not in PAGE.lower()

    def test_home_title_h1_and_bob_faq_unchanged(self):
        assert f"<title>{HOME_TITLE}</title>" in LANDING
        assert HOME_H1_PART in LANDING
        assert f"<summary>{HOME_BOB_QUESTION}</summary>" in LANDING
        assert LANDING.count(f"<summary>{HOME_BOB_QUESTION}</summary>") == 1
        assert HOME_BOB_P1 in LANDING
        assert HOME_BOB_P2 in LANDING
        assert LANDING.count(HOME_BOB_P1) == 2
        assert LANDING.count(HOME_BOB_P2) == 2
        assert "follow-up-boss-alternative" not in LANDING
        assert "wise-agent-alternative" not in LANDING
        assert "kvcore-alternative" not in LANDING
        assert "the free real estate CRM page" in LANDING
