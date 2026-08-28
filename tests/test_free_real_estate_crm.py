"""Public /free-real-estate-crm page: route, SEO, sitemap, and copy guards."""
import json
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
TITLE = "Free real estate CRM | AgentFlow"
H1 = "A free real estate CRM you can start tonight."
LEAD = "Built for agents, by agents. The free tier stays free. No card. About two minutes."
SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
ALLOWED_SITEMAP_PATHS = {
    "/",
    "/register",
    "/login",
    "/terms-privacy",
    PAGE_PATH,
    "/follow-up-boss-alternative",
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
    (
        "What can B.O.B. do?",
        "B.O.B. is the AI assistant built into AgentFlow. Instead of clicking through the CRM, just tell B.O.B. what you need done. It can find and update contacts, manage tasks, log activity, organize clients, and much more. If you can do it in AgentFlow, you can ask B.O.B. to do it for you.\n\nThe free plan includes 25 messages a day, and you can also chat with B.O.B. through Telegram after connecting it from your profile.",
    ),
)


def _faq_paragraphs(answer):
    return tuple(part for part in answer.split("\n\n") if part)


def _json_ld_answer(answer):
    return "\\n\\n".join(_faq_paragraphs(answer))


def _copy_without_bob_faq(html):
    question, answer = FAQ_ITEMS[3]
    stripped = html.replace(question, "")
    for para in _faq_paragraphs(answer):
        stripped = stripped.replace(para, "")
    return stripped


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

    def test_llms_txt_lists_the_page(self, client):
        resp = client.get("/llms.txt")
        assert resp.status_code == 200
        text = resp.get_data(as_text=True)
        assert "https://agentflow.origentechnolog.com/free-real-estate-crm" in text
        for blocked in BLOCKED_SEO_PATHS:
            assert f"https://agentflow.origentechnolog.com{blocked}" not in text
        assert "https://agentflow.origentechnolog.com/dashboard" not in text
        assert "—" not in text
        assert "Unlimited contacts" not in text
        assert "AI" not in text
        assert "AI assistant" not in text
        assert "One user, up to 10,000 contacts." in text
        assert "25 messages a day on the free plan." in text
        assert "Tell it what you need done in the CRM." in text
        assert "It can find and update contacts, manage tasks, log activity, organize clients, and much more." in text
        assert "Changing an email waits for Confirm (15 minutes)." not in text
        assert "Confirm (15 minutes)" not in text

    def test_blocked_marketing_urls_are_not_built(self, client):
        for path in BLOCKED_SEO_PATHS:
            resp = client.get(path)
            assert resp.status_code == 404, f"{path} should not be a public page"


class TestFreeRealEstateCrmSeo:
    def test_unique_title_canonical_and_og(self, app, client):
        page = client.get(PAGE_PATH).get_data(as_text=True)
        home = client.get("/").get_data(as_text=True)
        base = _app_base_url(app)
        page_url = f"{base}{PAGE_PATH}"
        home_url = f"{base}/"

        assert f"<title>{TITLE}</title>" in page
        assert f'rel="canonical" href="{page_url}"' in page
        assert 'property="og:title" content="Free real estate CRM | AgentFlow"' in page
        assert f'property="og:url" content="{page_url}"' in page
        assert 'property="og:description" content="Built for agents, by agents. The free tier stays free. No card. About two minutes."' in page
        assert f'property="og:image" content="{base}/static/images/og-share.png"' in page
        assert f'name="twitter:image" content="{base}/static/images/og-share.png"' in page
        assert f'property="og:image" content="{base}/static/images/og-share.png"' in home
        assert f'name="twitter:image" content="{base}/static/images/og-share.png"' in home

        assert "<title>Free Real Estate CRM | AgentFlow</title>" in home
        assert f'rel="canonical" href="{home_url}"' in home
        assert page.count("<title>") == 1
        assert f'rel="canonical" href="{home_url}"' not in page
        assert TITLE not in home

    def test_software_application_json_ld_price_is_zero(self, app, client):
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
        assert html.count('<script type="application/ld+json">') == 1
        assert '"@type": "SoftwareApplication"' in html
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
                "name": "Free real estate CRM",
                "item": f"{base}{PAGE_PATH}",
            },
        ]
        visible = _visible_copy(html)
        assert "Home" not in visible
        assert "breadcrumb" not in visible.lower()


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
        assert '"@type": "BreadcrumbList"' in PAGE
        assert '"@id": "{{ app_base_url }}/free-real-estate-crm#breadcrumb"' in PAGE
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
        include_answer = FAQ_ITEMS[0][1]
        assert "One user" in include_answer
        assert "10,000 contacts" in include_answer
        assert "25 B.O.B. messages a day" in include_answer

    def test_no_fake_seo_questions(self):
        for phrase in FAKE_SEO_QUESTIONS:
            assert phrase.lower() not in PAGE.lower()

    def test_no_ai_word_outside_bob_faq(self):
        visible = _visible_copy(_copy_without_bob_faq(PAGE))
        assert re.search(r"\bAI\b", visible) is None
        assert re.search(r"\bAI\b", _copy_without_bob_faq(PAGE)) is None

    def test_bob_faq_is_verbatim_and_card_stays(self):
        question, answer = FAQ_ITEMS[3]
        assert question == "What can B.O.B. do?"
        assert "AI assistant" in answer
        assert "If you can do it in AgentFlow" in answer
        assert "The free plan includes 25 messages a day" in answer
        assert "Telegram" in answer
        assert "from your profile" in answer
        for para in _faq_paragraphs(answer):
            assert f'<p class="faq-answer">{para}</p>' in PAGE
            assert f'<p class="faq-answer">{para}</p>' in LANDING
        assert "Ask B.O.B. to find a client, add a contact, log a call, or complete a task." in PAGE
        assert "Message B.O.B. on Telegram after you connect it from your profile." in PAGE
        assert "change an email" not in answer
        assert "ZIP" not in answer
        assert "What is B.O.B.?" not in PAGE
        assert "B.O.B. is the built-in assistant." not in PAGE
        assert "Confirm (15 minutes)" not in PAGE
        assert "Confirm (15 minutes)" not in answer
        assert "waits for Confirm" not in answer
        assert "waits for a yes" not in answer
        assert "until you say yes" not in answer
        assert "in the CRM or on Telegram" not in answer
        assert "same 25" not in PAGE.lower()


OTHER_CRMS_HEADING = "What other CRMs publish"
OTHER_CRMS_PARAGRAPH = (
    "Follow Up Boss is $69 per user after a trial. "
    "Wise Agent is $49 after a trial. "
    "Sierra is $299.95 and up. "
    "BoldTrail and Lofty are quote-only. "
    "Here it's $0 to start, and you don't need a card."
)
OTHER_CRM_LINKS = (
    ("Follow Up Boss", "/follow-up-boss-alternative", "main.follow_up_boss_alternative"),
    ("Wise Agent", "/wise-agent-alternative", "main.wise_agent_alternative"),
    ("BoldTrail", "/kvcore-alternative", "main.kvcore_alternative"),
)


def _other_crms_section(html):
    match = re.search(
        r"<h2[^>]*>What other CRMs publish</h2>\s*<p\b[^>]*>.*?</p>",
        html,
        flags=re.S,
    )
    assert match is not None
    return match.group(0)


class TestFreeRealEstateCrmOtherCrmLinks:
    def test_paragraph_words_unchanged(self, client):
        html = client.get(PAGE_PATH).get_data(as_text=True)
        section = _other_crms_section(html)
        visible = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", section)).strip()
        assert visible == f"{OTHER_CRMS_HEADING} {OTHER_CRMS_PARAGRAPH}"
        assert OTHER_CRMS_PARAGRAPH in _visible_copy(html)

    def test_three_names_link_once_to_existing_paths(self, client):
        html = client.get(PAGE_PATH).get_data(as_text=True)
        section = _other_crms_section(html)
        paragraph = re.search(r"<p\b[^>]*>(.*?)</p>", section, flags=re.S).group(1)
        assert paragraph.count("<a ") == 3
        for name, path, endpoint in OTHER_CRM_LINKS:
            assert paragraph.count(f'href="{path}"') == 1
            assert re.search(
                rf'<a href="{re.escape(path)}"[^>]*>{re.escape(name)}</a>',
                paragraph,
            )
            assert f"url_for('{endpoint}')" in PAGE
        assert PAGE.count("url_for('main.follow_up_boss_alternative')") == 1
        assert PAGE.count("url_for('main.wise_agent_alternative')") == 1
        assert PAGE.count("url_for('main.kvcore_alternative')") == 1

    def test_sierra_and_lofty_are_not_wrapped(self, client):
        html = client.get(PAGE_PATH).get_data(as_text=True)
        section = _other_crms_section(html)
        paragraph = re.search(r"<p\b[^>]*>(.*?)</p>", section, flags=re.S).group(1)
        assert re.search(r"<a\b[^>]*>Sierra</a>", paragraph) is None
        assert re.search(r"<a\b[^>]*>Lofty</a>", paragraph) is None
        assert "Sierra is $299.95 and up." in _visible_copy(section)
        assert "BoldTrail and Lofty are quote-only." in _visible_copy(section)

    def test_no_kvcore_or_alternative_added_to_sentence(self, client):
        html = client.get(PAGE_PATH).get_data(as_text=True)
        section = _other_crms_section(html)
        visible = _visible_copy(section)
        assert "kvCORE" not in html
        assert "kvcore" not in visible.lower()
        assert "alternative" not in visible.lower()
        assert visible == f"{OTHER_CRMS_HEADING} {OTHER_CRMS_PARAGRAPH}"
