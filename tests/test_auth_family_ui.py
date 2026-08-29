"""Rendered HTML contracts for every public auth page.

Browser contrast lives in tests/run_tests.py. This file is the Flask-client
half: all five public screens stay off crm-input / t-input, keep
auth-family.css, and login/register have the free-CRM sentence.

Do not point these checks at marketing or listing-package chrome.
Do not weaken tests/test_landing_copy.py TestRegisterLeftoverCopy.
"""
import re
from datetime import datetime, timedelta

from models import OrganizationInvite, User, db

_LEAKY_INPUT = re.compile(
    r"<input\b[^>]*\bclass=\"[^\"]*\b(?:crm-input|t-input)\b[^\"]*\"",
    re.I,
)


def _html(resp):
    assert resp.status_code == 200, resp.status_code
    return resp.get_data(as_text=True)


def _without_contact_modal(html):
    """The global contact-us modal still uses t-input. That is not an auth field."""
    start = html.find("<!-- Contact Us Modal -->")
    if start < 0:
        marker = html.find('id="contactUsModal"')
        if marker < 0:
            return html
        start = html.rfind("<div", 0, marker + 1)
    end = html.find("</script>", start)
    if start < 0 or end < 0:
        return html
    return html[:start] + html[end + len("</script>"):]


def _assert_public_auth_lock(html, *must_have):
    assert "crm-auth-page" in html
    assert "css/auth-family.css" in html
    scoped = _without_contact_modal(html)
    assert 'class="input crm-input' not in scoped
    assert 'class="input t-input' not in scoped
    assert 'class="t-input-wrap' not in scoped
    leaky = _LEAKY_INPUT.findall(scoped)
    assert leaky == [], leaky
    for snippet in must_have:
        assert snippet in html, snippet


class TestPublicAuthPagesStayLocked:
    def test_login_page(self, client, seed):
        html = _html(client.get("/login"))
        _assert_public_auth_lock(
            html,
            "Sign in",
            "Forgot password?",
            "More on what's included is on",
            "the free real estate CRM page",
            'href="/free-real-estate-crm"',
        )
        assert html.count("the free real estate CRM page") == 1
        assert html.count('href="/free-real-estate-crm"') == 1

    def test_register_page_keeps_368_sentence(self, client, seed):
        html = _html(client.get("/register"))
        _assert_public_auth_lock(
            html,
            "Create your account.",
            "More on what's included is on",
            "the free real estate CRM page",
            'href="/free-real-estate-crm"',
        )
        assert html.count("the free real estate CRM page") == 1
        assert html.count('href="/free-real-estate-crm"') == 1

    def test_forgot_password_page(self, client, seed):
        html = _html(client.get("/reset_password"))
        _assert_public_auth_lock(html, "Forgot Password?", "auth-input")

    def test_reset_password_page(self, client, app, seed):
        with app.app_context():
            user = db.session.get(User, seed["owner_a"])
            token = user.get_reset_token()
        html = _html(client.get(f"/reset_password/{token}"))
        _assert_public_auth_lock(html, "Create New Password", "auth-input")
        assert "Forgot Password?" not in html

    def test_accept_invite_page(self, client, app, seed):
        token = "auth-family-ui-invite-token"
        with app.app_context():
            existing = OrganizationInvite.query.filter_by(token=token).first()
            if existing is None:
                db.session.add(
                    OrganizationInvite(
                        organization_id=seed["org_a"],
                        email="auth-family-ui@test.com",
                        invited_by_id=seed["owner_a"],
                        role="agent",
                        token=token,
                        expires_at=datetime.utcnow() + timedelta(days=2),
                    )
                )
                db.session.commit()
        html = _html(client.get(f"/invite/{token}"))
        _assert_public_auth_lock(html, "You're invited to join", "auth-input")


def test_register_368_source_contract_is_untouched():
    """Guard the #368 test itself so a later edit cannot dilute it."""
    from pathlib import Path

    source = Path(__file__).resolve().parent.joinpath("test_landing_copy.py").read_text()
    assert "class TestRegisterLeftoverCopy" in source
    assert "def test_one_human_link_to_free_real_estate_crm" in source
    assert 'assert REGISTER.count("url_for(\'main.free_real_estate_crm\')") == 1' in source
    assert 'assert REGISTER.count("the free real estate CRM page") == 1' in source
    assert '"More on what\'s included is on" in REGISTER' in source


def test_login_source_contract_pins_free_crm_sentence():
    from pathlib import Path

    source = Path(__file__).resolve().parent.joinpath("test_landing_copy.py").read_text()
    assert "class TestLoginLeftoverCopy" in source
    assert 'assert LOGIN.count("url_for(\'main.free_real_estate_crm\')") == 1' in source
    assert 'assert LOGIN.count("the free real estate CRM page") == 1' in source
    assert '"More on what\'s included is on" in LOGIN' in source
