"""Guard public landing and register copy against leftover marketing slop."""
import re
from pathlib import Path

from feature_flags import TIER_FEATURES
from tier_config.tier_limits import get_tier_defaults


ROOT = Path(__file__).resolve().parents[1]
LANDING = (ROOT / "templates" / "landing.html").read_text()
REGISTER = (ROOT / "templates" / "auth" / "register.html").read_text()
TERMS = (ROOT / "templates" / "auth" / "terms_privacy.html").read_text()
FREE_CRM = (ROOT / "templates" / "free_real_estate_crm.html").read_text()
LLMS = (ROOT / "static" / "llms.txt").read_text()
FREE_LIMITS = get_tier_defaults("free")

_COMMENT = re.compile(r"<!--.*?-->", re.S)
_SCRIPT = re.compile(r"<script\b[^>]*>.*?</script>", re.S | re.I)
_STYLE = re.compile(r"<style\b[^>]*>.*?</style>", re.S | re.I)


def _visible_public_copy(html: str) -> str:
    html = _COMMENT.sub("", html)
    html = _SCRIPT.sub("", html)
    html = _STYLE.sub("", html)
    return html

BANNED_LEFTOVERS = (
    "Trusted by real estate professionals",
    "Great businesses don't just happen",
    "Ready to grow your business?",
    "Never miss a follow-up again",
    "closing more deals",
    "Join real estate professionals who are closing more deals",
)

DELETED_FAQ = (
    "Is the free tier actually free?",
    "Will you charge me later for what I get today?",
    "How long to set up?",
    "What do I get on free?",
    "Is this Follow Up Boss or HubSpot?",
    "Is this origentech.com or Origin CRM?",
    "Straight answers. No pitch.",
    "Questions people ask",
    "Origen TechnolOG (origentechnolog.com) is not Origen Tech, Origen-Tech, or Origin CRM.",
    "What is B.O.B.?",
    "B.O.B. is the built-in assistant.",
)

FAQ_ITEMS = (
    (
        "What does the free plan include?",
        "One user, up to 10,000 contacts, tasks, and a dashboard. You can send email through Gmail and sync tasks to Google Calendar. B.O.B. is included, with 25 messages a day.",
    ),
    (
        "Do I need a credit card?",
        "No. You can start without one. What's on the free plan stays free. Extra features later will be paid. Nothing paid is for sale today.",
    ),
    (
        "How long does it take to get started?",
        "Setup takes about two minutes.",
    ),
    (
        "Is this built for real estate agents?",
        "Yes. Contacts, follow-ups, and tasks are set up the way agents actually work, not as a generic CRM.",
    ),
    (
        "What can B.O.B. do?",
        "B.O.B. is the AI assistant built into Origen. Instead of clicking through the CRM, just tell B.O.B. what you need done. It can find and update contacts, manage tasks, log activity, organize clients, and much more. If you can do it in Origen, you can ask B.O.B. to do it for you.\n\nThe free plan includes 25 messages a day, and you can also chat with B.O.B. through Telegram after connecting it from your profile.",
    ),
)


def _faq_paragraphs(answer):
    return tuple(part for part in answer.split("\n\n") if part)


def _json_ld_answer(answer):
    return "\\n\\n".join(_faq_paragraphs(answer))


def _copy_without_bob_faq(html):
    question, answer = FAQ_ITEMS[4]
    stripped = html.replace(question, "")
    for para in _faq_paragraphs(answer):
        stripped = stripped.replace(para, "")
    return stripped


class TestLandingLeftoverCopy:
    def test_banned_leftovers_are_gone(self):
        for phrase in BANNED_LEFTOVERS:
            assert phrase not in LANDING
            assert phrase not in REGISTER

    def test_banned_slogan_words_stay_gone(self):
        assert "seamlessly" not in LANDING.lower()
        assert "seamlessly" not in REGISTER.lower()

    def test_exact_replacements_are_present(self):
        assert "What's on the free tier" in LANDING
        assert "Ready to try it?" in LANDING
        assert "Create an account when you're ready. No card." in LANDING
        assert "Set a due date and a reminder. The follow-up stays on your list." in LANDING
        assert "Contacts and follow-up tasks in one CRM" not in LANDING

    def test_no_em_dashes_in_public_copy(self):
        assert "—" not in LANDING
        assert "—" not in REGISTER

    def test_keeps_homepage_h1(self):
        assert "Your clients and follow-ups," in LANDING
        assert "in one place." in LANDING

    def test_keeps_start_free_cta(self):
        assert "Start Free" in LANDING

    def test_keeps_pro_coming_soon_and_pricing_tbd(self):
        assert "Pro" in LANDING
        assert "Coming Soon" in LANDING
        assert "Pricing TBD" in LANDING

    def test_keeps_visible_faq_and_matching_json_ld(self):
        assert '"@type": "FAQPage"' in LANDING
        assert LANDING.count("<summary>") == 5
        assert LANDING.count('"@type": "Question"') == 5
        for question, answer in FAQ_ITEMS:
            assert LANDING.count(question) == 2
            assert f"<summary>{question}</summary>" in LANDING
            assert f'"name": "{question}"' in LANDING
            assert f'"text": "{_json_ld_answer(answer)}"' in LANDING
            for para in _faq_paragraphs(answer):
                assert LANDING.count(para) == 2
                assert f'<p class="faq-answer">{para}</p>' in LANDING

    def test_old_faq_copy_is_gone(self):
        for phrase in DELETED_FAQ:
            assert phrase not in LANDING

    def test_faq_facts_match_product_limits(self):
        assert FREE_LIMITS["max_users"] == 1
        assert FREE_LIMITS["max_contacts"] == 10000
        assert FREE_LIMITS["daily_ai_chat_messages"] == 25
        include_answer = FAQ_ITEMS[0][1]
        bob_answer = FAQ_ITEMS[4][1]
        assert "One user" in include_answer
        assert "10,000 contacts" in include_answer
        assert "25 messages a day" in include_answer
        assert "25 messages a day" in bob_answer
        assert "10 messages a day" not in include_answer
        assert "10 messages a day" not in bob_answer
        assert "unlimited contacts" not in include_answer.lower()

    def test_landing_does_not_claim_old_free_limits(self):
        assert "Unlimited contacts" not in LANDING
        assert "unlimited contacts" not in LANDING
        assert "10 messages a day" not in LANDING
        assert "10 messages/day" not in LANDING
        assert "10/day free" not in LANDING
        assert "Up to 10,000 contacts" in LANDING
        assert "25 messages a day" in LANDING
        assert "25/day free" in LANDING
        assert "25 messages/day included free" in LANDING

    def test_free_pricing_card_matches_product_limits(self):
        start = LANDING.index("<!-- Free Plan -->")
        end = LANDING.index("<!-- Pro Plan -->")
        card = LANDING[start:end]
        assert "1 user account" in card
        assert "Up to 10,000 contacts" in card
        assert "25 messages/day included free" in card
        assert "Unlimited contacts" not in card
        assert "10 messages" not in card
        assert "10/day" not in card

    def test_keeps_foundation_title(self):
        assert "Free Real Estate CRM | Origen TechnolOG" in LANDING

    def test_does_not_claim_there_will_never_be_a_paid_plan(self):
        assert "never be a paid" not in LANDING.lower()
        assert "Extra features later will be paid" in LANDING

    def test_no_ai_word_outside_bob_faq(self):
        for source in (LANDING, FREE_CRM):
            visible = _visible_public_copy(_copy_without_bob_faq(source))
            assert re.search(r"\bAI\b", visible) is None
        assert re.search(r"\bAI\b", _visible_public_copy(REGISTER)) is None
        assert re.search(r"\bAI\b", LLMS) is None
        assert "AI assistant" not in LLMS

    def test_keeps_home_title_and_meta(self):
        assert "<title>Free Real Estate CRM | Origen TechnolOG</title>" in LANDING
        assert 'title="Free Real Estate CRM | Origen TechnolOG"' in LANDING
        assert 'description="Free real estate CRM for agents. No credit card. Set up in about 2 minutes."' in LANDING

    def test_public_limits_do_not_contradict_home(self):
        for source in (LANDING, REGISTER, FREE_CRM, LLMS):
            assert "10,000 contacts" in source
            assert "25 messages a day" in source or "25 B.O.B. messages a day" in source
            assert "Unlimited contacts" not in source
            assert "Changing an email waits for Confirm (15 minutes)." not in source
            assert "Confirm (15 minutes)" not in source
            assert "waits for a yes" not in source
            assert "until you say yes" not in source
        assert "One user" in LANDING or "1 user" in LANDING
        assert "one user" in FREE_CRM
        assert "One user" in LLMS

    def test_bob_is_a_headline_feature_with_real_tools(self):
        assert "Talk to B.O.B." in LANDING
        assert "Ask how many clients are in a ZIP or city." in LANDING
        assert "add a contact, change an email, complete a task, or log a call" in LANDING
        assert "change an email" in LANDING
        assert "Changing an email waits for Confirm (15 minutes)." not in LANDING
        assert "Confirm (15 minutes)" not in LANDING
        assert "waits for a yes" not in LANDING
        assert "until you say yes" not in LANDING
        assert "25 messages a day" in LANDING
        assert "25/day free" in LANDING
        assert "AI-Powered B.O.B." not in LANDING
        assert "B.O.B. AI Assistant" not in LANDING
        assert "Unlimited AI + daily todo" not in LANDING
        assert "AI chat assistant" not in LANDING
        assert "Unlimited B.O.B. AI chat" not in LANDING
        assert "AI daily todo" not in LANDING
        assert "drafts and questions about your work" not in LANDING
        assert "Business Optimization Buddy" not in LANDING

    def test_telegram_is_mentioned_with_official_logo(self):
        assert "B.O.B. on Telegram" in LANDING
        assert "Message B.O.B. on Telegram after you scan a QR from your profile." in LANDING
        assert 'fill="#229ED9"' in LANDING
        assert 'viewBox="0 0 240 240"' in LANDING
        assert "<circle cx=\"120\" cy=\"120\" r=\"120\" fill=\"#229ED9\"/>" in LANDING
        assert "fa-comment" not in LANDING
        assert "fa-comments" not in LANDING

    def test_telegram_does_not_claim_the_web_25_cap(self):
        for source in (LANDING, REGISTER):
            lower = source.lower()
            assert "same 25-message daily cap" not in lower
            assert "same daily cap" not in lower
            assert "same 25" not in lower
            assert "shares your 25" not in lower
            assert "100 messages" not in lower
            assert "500 messages" not in lower
            assert "100/user" not in lower
            assert "500/org" not in lower
            assert "unlimited extra" not in lower

    def test_bob_telegram_flag_is_on_for_every_tier(self):
        for tier in ("free", "pro", "enterprise"):
            assert TIER_FEATURES[tier]["BOB_TELEGRAM"] is True

    def test_bob_faq_mentions_crm_work_and_telegram(self):
        question, answer = FAQ_ITEMS[4]
        assert question == "What can B.O.B. do?"
        assert "AI assistant" in answer
        assert "If you can do it in Origen" in answer
        assert "25 messages a day" in answer
        assert "Telegram" in answer
        assert "from your profile" in answer
        assert "ZIP" not in answer
        assert "change an email" not in answer
        assert "Confirm (15 minutes)" not in answer
        assert "waits for Confirm" not in answer
        assert "waits for a yes" not in answer
        assert "until you say yes" not in answer
        assert "in the CRM or on Telegram" not in answer


class TestRegisterLeftoverCopy:
    def test_keeps_register_h1(self):
        assert "Create your account." in REGISTER

    def test_register_red_leftover_is_gone(self):
        assert "#fa243c" not in REGISTER
        assert "250, 36, 60" not in REGISTER
        assert "@keyframes glow" not in REGISTER

    def test_register_decorative_orbs_are_gone(self):
        assert ".orb" not in REGISTER
        assert "@keyframes pulse-soft" not in REGISTER
        assert "@keyframes shimmer" not in REGISTER
        assert "font-family: 'Inter'" not in REGISTER
        assert "premium-card" not in REGISTER

    def test_register_does_not_claim_unlimited_contacts(self):
        assert "Unlimited contacts" not in REGISTER
        assert "unlimited contacts" not in REGISTER
        assert "Unlimited Contacts" not in REGISTER
        assert "Up to 10,000 contacts" in REGISTER

    def test_register_describes_bob_with_real_facts(self):
        assert "Talk to B.O.B." in REGISTER
        assert "25 messages a day in the CRM" in REGISTER
        assert "Telegram after a QR from your profile" in REGISTER
        assert "add a contact" in REGISTER
        assert "in the CRM or on Telegram" not in REGISTER
        assert "Unlimited contacts" not in REGISTER


class TestTermsPrivacyLayout:
    def test_does_not_use_invertible_tailwind_surfaces(self):
        assert "bg-slate-900" not in TERMS
        assert "text-white" not in TERMS
        assert "toc-link" not in TERMS
        assert "Quick Navigation" not in TERMS
        assert "legal-page" in TERMS
        assert "On this page" in TERMS
