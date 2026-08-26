"""Pins for the signed-in phone layout and the B.O.B. sheet.

These read the shipped CSS/JS so a 100vw drawer or a missing sheet
open path fails in unit CI, not only in a headed browser.
"""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _read(relpath: str) -> str:
    return (ROOT / relpath).read_text(encoding='utf-8')


class TestMobileShell:
    def test_viewport_fit_cover(self):
        html = _read('templates/base.html')
        assert 'width=device-width' in html
        assert 'viewport-fit=cover' in html

    def test_main_flex_item_can_shrink(self):
        html = _read('templates/base.html')
        assert 'main#mainContent' in html
        assert 'min-width: 0' in html
        assert 'overflow-x: clip' in html

    def test_segments_scroll_inside_on_phone(self):
        css = _read('frontend/styles/app.css')
        assert '.crm-segment,' in css or '.crm-segment\n' in css
        mobile = css.split('@media (max-width: 767px)', 1)[1]
        assert 'overflow-x: auto' in mobile
        assert '.crm-table-wrap' in mobile


class TestBobMobileSheet:
    def test_css_drops_100vw_panel_width(self):
        css = _read('static/css/ai_chat.css')
        assert 'width: 100vw' not in css
        assert 'min-width: 100vw' not in css

    def test_sheet_rules_cover_drawer_and_modal(self):
        css = _read('static/css/ai_chat.css')
        assert '.bob-panel.sheet' in css
        assert 'body.bob-sheet-open' in css
        assert '--bob-sheet-height' in css
        assert 'env(safe-area-inset-bottom' in css
        assert '#bob-expand-btn' in css
        # Desktop modal used calc(50% - 410px); the phone block must
        # override message padding to a real inset.
        mobile = css.split('Phone sheet', 1)[1]
        assert 'padding: 12px' in mobile
        assert 'flex-direction: column !important' in mobile
        assert '100svh' in mobile
        assert 'var(--paper' in mobile
        assert 'var(--canvas' in mobile
        assert 'font-size: 16px' in mobile
        assert 'width: 44px' in mobile
        assert 'height: 44px' in mobile
        # Keyboard tracking must snap, not ease height/top.
        assert 'transition: transform' in mobile
        assert 'top 0.3s' not in mobile

    def test_js_opens_sheet_on_narrow(self):
        js = _read('static/js/ai_chat.js')
        assert 'openSheet' in js
        assert 'openForViewport' in js
        assert 'visualViewport' in js
        assert '--bob-sheet-height' in js
        assert "this.state = 'sheet'" in js
        assert 'max-width: 768px' in js
        sheet_fn = js.split(
            'openSheet({ skipEnsure = false } = {}) {', 1
        )[1].split('async ensurePageConversation', 1)[0]
        assert 'textarea.focus()' not in sheet_fn
        assert 'textarea.focus()' in js
        assert 'isNarrow() ? 96 : 120' in js
        assert 'enterkeyhint="send"' in js


class TestMobileNavTheme:
    def test_hamburger_drawer_uses_canvas_tokens(self):
        html = _read('templates/base.html')
        assert 'crm-mobile-nav-panel' in html
        block = html.split('#mobileNavMenu .crm-mobile-nav-panel', 1)[1]
        assert 'background: var(--canvas) !important' in block
        assert 'color: var(--ink) !important' in block
        assert 'var(--paper-2)' in block
        # Desktop sidebar keeps its own rules; this selector is menu-only.
        assert 'aside#sidebar' not in block.split('User dropdown menu', 1)[0]


@pytest.mark.usefixtures('seed')
class TestBobAssetsOnDashboard:
    def test_dashboard_ships_chat_assets(self, owner_a_client):
        resp = owner_a_client.get('/dashboard')
        assert resp.status_code == 200
        assert b'js/ai_chat.js' in resp.data
        assert b'css/ai_chat.css' in resp.data
        assert b'bob-toggle-mobile' in resp.data
        assert b'viewport-fit=cover' in resp.data
        assert b'crm-mobile-nav-panel' in resp.data
