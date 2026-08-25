"""Assert transitions.dev hooks are installed once and wired on chrome."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(*parts):
    return ROOT.joinpath(*parts).read_text()


class TestRootInstall:
    def test_root_block_is_single(self):
        css = _read("static", "css", "transitions-root.css")
        assert css.count(":root") == 1
        assert "--dropdown-open-dur" in css
        assert "--panel-open-dur" in css
        assert "--page-slide-dur" in css
        assert "--toast-open" in css
        assert "--check-draw" in css

    def test_snippets_do_not_repeat_root(self):
        snippets = _read("static", "css", "transitions-snippets.css")
        bridge = _read("static", "css", "transitions-bridge.css")
        assert ":root" not in snippets
        assert ":root" not in bridge

    def test_base_links_root_once(self):
        html = _read("templates", "base.html")
        assert html.count("css/transitions-root.css") == 1
        assert html.count("css/transitions-snippets.css") == 1
        assert html.count("js/transitions.js") == 1


class TestSnippetGuards:
    def test_shipped_snippets_keep_reduced_motion(self):
        css = _read("static", "css", "transitions-snippets.css")
        for selector in (
            ".t-digit-group",
            ".t-badge",
            ".t-dropdown",
            ".t-modal",
            ".t-panel-slide",
            ".t-page-slide",
            ".t-clear",
            ".t-skel",
            ".t-tabs",
            ".t-toast",
            ".t-check",
        ):
            assert selector in css
        assert css.count("@media (prefers-reduced-motion: reduce)") >= 11
        assert "transition: all" not in css


class TestChromeHooks:
    def test_base_menus_and_search(self):
        html = _read("templates", "base.html")
        assert 'id="userDropdown"' in html
        assert "t-dropdown" in html
        assert 'id="crmNotificationPopover"' in html
        assert 'id="crmGlobalSearchResults"' in html
        assert "crm-utility-search t-clear" in html
        assert "toast-message" in html
        assert 'id="crmNotifBadgeWrap"' in html
        assert "t-badge-dot" in html

    def test_contacts_rail_and_pages(self):
        preview = _read("templates", "contacts", "_preview.html")
        listing = _read("templates", "contacts", "list.html")
        view = _read("templates", "contacts", "view.html")
        assert "t-panel-slide" in preview
        assert 'id="crmContactsPages"' in listing
        assert 'id="crmContactsPages"' in view
        assert "t-page-slide" in listing
        assert 'data-page-id="2"' in listing
        assert "crm-toolbar__search t-clear" in listing
        assert "crm-activity-tabs t-tabs" in view

    def test_modals_use_t_modal(self):
        for name in (
            "contact_modal.html",
            "contact_us_modal.html",
            "email_modal.html",
            "import_help_modal.html",
        ):
            html = _read("templates", "modals", name)
            assert "t-modal" in html

    def test_inbox_badge_and_dashboard_digits(self):
        inbox = _read("templates", "inbox", "home.html")
        dash = _read("templates", "dashboard.html")
        assert "t-badge" in inbox
        assert "{{ total_inbound }} total" in inbox
        assert "t-digit-group" in dash
        assert "t-skel crm-kpi-skel" in dash
        assert "t-check" in dash

    def test_tasks_and_todos_use_check(self):
        tasks = _read("templates", "tasks", "list.html")
        todo = _read("templates", "user_todo.html")
        assert "t-check" in tasks
        assert "t-check" in todo
        assert "M1 5.52L3.92 9.17L9.17 1" in tasks

    def test_orchestration_reads_css_ms(self):
        js = _read("static", "js", "transitions.js")
        assert "getComputedStyle" in js
        assert "getPropertyValue" in js
        assert "openDropdown" in js
        assert "openModal" in js
        assert "openToast" in js
        assert "path.getTotalLength" in js
