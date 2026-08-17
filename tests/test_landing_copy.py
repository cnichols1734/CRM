"""Guard public landing and register copy against leftover marketing slop."""
from pathlib import Path

from tier_config.tier_limits import get_tier_defaults


ROOT = Path(__file__).resolve().parents[1]
LANDING = (ROOT / "templates" / "landing.html").read_text()
REGISTER = (ROOT / "templates" / "auth" / "register.html").read_text()
FREE_LIMITS = get_tier_defaults("free")

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
        "What is B.O.B.?",
        "B.O.B. is the built-in assistant. On the free plan you get 25 messages a day for drafts and questions about your work.",
    ),
)


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
            assert LANDING.count(answer) == 2
            assert f"<summary>{question}</summary>" in LANDING
            assert f'<p class="faq-answer">{answer}</p>' in LANDING
            assert f'"name": "{question}"' in LANDING
            assert f'"text": "{answer}"' in LANDING

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


class TestRegisterLeftoverCopy:
    def test_keeps_register_h1(self):
        assert "Create your account." in REGISTER

    def test_register_red_leftover_is_gone(self):
        assert "#fa243c" not in REGISTER
        assert "250, 36, 60" not in REGISTER
        assert "@keyframes glow" not in REGISTER

    def test_register_orbs_use_brand_orange(self):
        assert "249, 115, 22" in REGISTER
        assert "234, 88, 12" in REGISTER

    def test_register_does_not_claim_unlimited_contacts(self):
        assert "Unlimited contacts" not in REGISTER
        assert "unlimited contacts" not in REGISTER
        assert "Unlimited Contacts" not in REGISTER
        assert "Up to 10,000 contacts" in REGISTER
