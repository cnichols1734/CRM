"""Lock public auth screens off dark-theme crm-input and t-input leftovers."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(*parts):
    return ROOT.joinpath(*parts).read_text()


class TestAuthFamilyCssLock:

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
