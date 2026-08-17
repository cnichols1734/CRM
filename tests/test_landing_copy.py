"""Guard public landing and register copy against leftover marketing slop."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LANDING = (ROOT / "templates" / "landing.html").read_text()
REGISTER = (ROOT / "templates" / "auth" / "register.html").read_text()

BANNED_LEFTOVERS = (
    "Trusted by real estate professionals",
    "Great businesses don't just happen",
    "Ready to grow your business?",
    "Never miss a follow-up again",
    "closing more deals",
    "Join real estate professionals who are closing more deals",
)

FAQ_QUESTIONS = (
    "Is the free tier actually free?",
    "Will you charge me later for what I get today?",
    "How long to set up?",
    "What do I get on free?",
    "Is this Follow Up Boss or HubSpot?",
    "Is this origentech.com or Origin CRM?",
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
        for question in FAQ_QUESTIONS:
            assert LANDING.count(question) >= 2
            assert f"<summary>{question}</summary>" in LANDING
            assert f'"name": "{question}"' in LANDING

    def test_keeps_disambiguation_and_foundation_title(self):
        assert "Origen TechnolOG (origentechnolog.com) is not Origen Tech, Origen-Tech, or Origin CRM." in LANDING
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
