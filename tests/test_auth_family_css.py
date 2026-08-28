"""Lock public auth screens off dark-theme crm-input and t-input leftovers."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PUBLIC_AUTH = (
    "templates/auth/login.html",
    "templates/auth/register.html",
    "templates/auth/reset_request.html",
    "templates/auth/reset_password.html",
    "templates/auth/accept_invite.html",
    "templates/auth/_styles.html",
    "templates/auth/terms_privacy.html",
)


def _read(*parts):
    return ROOT.joinpath(*parts).read_text()


def _public_auth_html():
    return {path: _read(*path.split("/")) for path in PUBLIC_AUTH}


class TestPublicAuthDropsCrmInputLeak:
    def test_public_auth_fields_do_not_use_crm_input_or_t_input(self):
        for path, html in _public_auth_html().items():
            assert "crm-input" not in html, path
            assert "t-input" not in html, path
            assert "t-input-wrap" not in html, path

    def test_login_and_register_keep_shipped_input_class(self):
        login = _read("templates", "auth", "login.html")
        register = _read("templates", "auth", "register.html")
        assert 'class="input"' in login
        assert 'class="input crm-input' not in login
        assert 'class="input"' in register
        assert "class=\"input crm-input" not in register

    def test_reset_and_invite_keep_auth_input_only(self):
        reset_request = _read("templates", "auth", "reset_request.html")
        reset_password = _read("templates", "auth", "reset_password.html")
        invite = _read("templates", "auth", "accept_invite.html")
        styles = _read("templates", "auth", "_styles.html")
        assert "auth-input" in reset_request
        assert "auth-input" in reset_password
        assert "auth-input" in invite
        assert ".auth-input" in styles
        assert "crm-input" not in reset_request
        assert "crm-input" not in reset_password
        assert "crm-input" not in invite

    def test_register_keeps_free_crm_sentence(self):
        register = _read("templates", "auth", "register.html")
        assert "More on what's included is on" in register
        assert "the free real estate CRM page" in register
        assert "url_for('main.free_real_estate_crm')" in register


class TestAuthFamilyCssLock:
    def test_base_links_auth_family_sheet_once(self):
        base = _read("templates", "base.html")
        assert base.count("css/auth-family.css") == 1

    def test_lock_beats_dark_theme_crm_input_color(self):
        css = _read("static", "css", "auth-family.css")
        assert "body.crm-auth-page" in css
        assert "input.crm-input" in css
        assert "-webkit-text-fill-color: rgba(0, 0, 0, 0.88) !important;" in css
        assert "color: rgba(0, 0, 0, 0.88) !important;" in css
        assert "html[data-theme=\"dark\"] body.crm-auth-page" in css
        assert "html.am-skin[data-theme=\"dark\"] body.crm-auth-page" in css
        assert "color: var(--ink)" not in css.split("near-white inherited type", 1)[1]
        assert "background: var(--paper)" not in css.split("near-white inherited type", 1)[1]

    def test_lock_beats_t_input_rest_halo(self):
        css = _read("static", "css", "auth-family.css")
        assert "body.crm-auth-page .t-input:not(:focus)" in css
        assert "body.crm-auth-page .t-input-wrap" in css
        assert "box-shadow: none !important;" in css
        wrap = css.split("t-input leftover", 1)[1]
        assert "box-shadow: none !important;" in wrap
        assert ".t-clear-glow" in wrap

    def test_light_form_placeholders_are_dark(self):
        css = _read("static", "css", "auth-family.css")
        assert "input::placeholder" in css
        assert "-webkit-text-fill-color: rgba(0, 0, 0, 0.35) !important;" in css

    def test_dark_auth_card_beats_crm_input_too(self):
        css = _read("static", "css", "auth-family.css")
        assert "body.crm-auth-page .auth-card input.crm-input" in css
        assert "-webkit-text-fill-color: hsla(0, 0%, 100%, 0.92) !important;" in css
        assert "color-scheme: dark;" in css

    def test_selection_is_not_accent_soft(self):
        css = _read("static", "css", "auth-family.css")
        assert "body.crm-auth-page ::selection" in css
        assert "var(--accent-soft)" not in css
        assert "var(--accent-ink)" not in css
        assert "background: rgba(0, 0, 0, 0.14);" in css


class TestAmSkinAuthDoesNotReintroduceLeak:
    def test_am_skin_auth_inputs_do_not_use_ink_tokens(self):
        css = _read("static", "css", "am_skin.css")
        auth = css.split("/* ── Auth pages", 1)[1].split("/* ── Shared CRM primitives", 1)[0]
        assert "body.crm-auth-page" in auth
        assert "var(--ink)" not in auth
        assert "var(--paper)" not in auth
        assert "var(--am-control-bg)" not in auth
        assert "-webkit-text-fill-color: rgba(0, 0, 0, 0.88) !important;" in auth
        assert "input.crm-input" in auth
        assert "box-shadow: none !important;" in auth
        assert "body.crm-auth-page ::selection" in auth
        assert "var(--accent-soft)" not in auth

    def test_am_skin_global_crm_input_still_exists_for_app(self):
        css = _read("static", "css", "am_skin.css")
        global_inputs = css.split("/* ── Auth pages", 1)[0]
        assert "html.am-skin input.crm-input" in global_inputs
        assert "color: var(--ink) !important;" in global_inputs
