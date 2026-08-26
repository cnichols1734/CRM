"""Assert transitions.dev hooks are installed once and wired on chrome."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(*parts):
    return ROOT.joinpath(*parts).read_text()


class TestRootInstall:
    def test_root_block_is_single(self):
        css = _read("static", "css", "transitions-root.css")
        assert css.count(":root {") == 1
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
            ".t-resize",
            ".t-success-check",
            ".t-shimmer",
            ".t-tt",
            ".t-stagger",
            ".t-toggle",
        ):
            assert selector in css
        assert css.count("@media (prefers-reduced-motion: reduce)") >= 16
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
        assert "burstConfetti" in tasks
        assert "whenConfettiSettled" in tasks
        assert "t-stagger" in todo
        todo_js = _read("static", "js", "todo-manager.js")
        assert "burstConfetti" in todo_js
        assert "whenConfettiSettled" in todo_js

    def test_checklist_has_check_and_confetti_hooks(self):
        checklist = _read("templates", "transactions", "_checklist.html")
        detail = _read("templates", "transactions", "detail.html")
        assert 'class="t-check"' in checklist
        assert "M1 5.52L3.92 9.17L9.17 1" in checklist
        assert "{{ 'true' if item.done else 'false' }}" in checklist
        assert "aria-checked=" in checklist
        assert "success_check()" in checklist
        assert "t-check-native" not in checklist
        assert "celebrateChecklistCheck" in detail
        assert "TMotion.burstConfetti(box)" in detail
        assert "localOrigin" not in detail
        assert "playSuccessCheck" in detail
        assert "setChecked" in detail
        assert "applyDone(item, done, done && !wasDone)" in detail
        assert "if (celebrate && done) celebrateChecklistCheck(item);" in detail

    def test_wave2_list_skeletons(self):
        tasks = _read("templates", "tasks", "list.html")
        inbox = _read("templates", "inbox", "home.html")
        deals = _read("templates", "transactions", "list.html")
        briefing = _read("templates", "briefing", "index.html")
        studio = _read("templates", "marketing", "studio.html")
        contact = _read("templates", "contacts", "view.html")
        deal_file = _read("templates", "transactions", "detail.html")
        assert "list_skel" in tasks
        assert "list_skel" in inbox
        assert "list_skel" in deals
        assert "t-skel crm-briefing-skel" in briefing
        assert "t-shimmer" in briefing
        assert "t-skel crm-panel-skel" in studio
        assert 'id="smartActionsLoading"' in contact
        assert "t-skel crm-panel-skel" in contact
        assert 'id="extraction-loading"' in deal_file
        assert "t-skel crm-panel-skel" in deal_file

    def test_wave2_save_toggle_tooltip_resize(self):
        ui = _read("templates", "components", "ui.html")
        listing = _read("templates", "contacts", "list.html")
        settings = _read("templates", "notifications", "settings.html")
        profile = _read("templates", "auth", "user_profile.html")
        base = _read("templates", "base.html")
        assert "t-stagger-line t-stagger-line--1" in ui
        assert "t-resize" in listing
        assert "t-toggle" in settings
        assert "data-t-save" in settings
        assert "success_check()" in settings
        assert "t-success-check" in ui
        assert "t-save" in profile
        assert "t-icon-swap" in profile
        assert "t-tt-wrap" in base
        assert 'className = \'t-tt\'' in base or 'tip.className = \'t-tt\'' in base
        assert "t-morph" not in settings
        assert "t-acc" not in settings

    def test_theme_toggle_glyphs_are_centered(self):
        base = _read("templates", "base.html")
        bridge = _read("static", "css", "transitions-bridge.css")
        theme_btn = base.split(".crm-theme-btn {", 1)[1].split("/* Smooth transition", 1)[0]
        assert "padding: 0;" in theme_btn
        assert "line-height: 0;" in theme_btn
        assert ".crm-theme-btn .t-icon {" in theme_btn
        assert "display: block;" in theme_btn
        assert "font-size: 0.9rem;" in theme_btn
        assert "display: inline;" not in theme_btn
        assert "place-content: center;" in bridge
        assert "width: 0.9rem;" in bridge.split(".crm-theme-btn .t-icon-swap {", 1)[1]

    def test_orchestration_reads_css_ms(self):
        js = _read("static", "js", "transitions.js")
        assert "getComputedStyle" in js
        assert "getPropertyValue" in js
        assert "openDropdown" in js
        assert "openModal" in js
        assert "openToast" in js
        assert "path.getTotalLength" in js
        assert "burstConfetti" in js
        assert "whenConfettiSettled" in js
        assert "useLocalOrigin" in js
        assert "originX + (Math.random() - 0.5) * spreadX" in js
        assert "p.y > b.bottom + 96" in js
        dash = _read("frontend", "controllers", "dashboard_page_controller.js")
        assert "burstConfetti" in dash
        assert "whenConfettiSettled" in dash
        assert "playSuccessCheck" in js
        assert "setSaveState" in js
        assert "initToggle" in js
        assert "showText" in js


class TestRestState:
    def test_clear_layers_stay_hidden_at_rest(self):
        snippets = _read("static", "css", "transitions-snippets.css")
        bridge = _read("static", "css", "transitions-bridge.css")
        assert ".t-clear-placeholder { opacity: 0; visibility: hidden; }" in snippets
        assert ".t-clear.is-clearing .t-clear-placeholder" in snippets
        assert ".t-clear.is-clearing .t-clear-glow { visibility: visible; }" in snippets
        assert "isolation: isolate" in bridge
        assert ".t-clear:not(.is-clearing) .t-clear-glow" in bridge

    def test_search_focus_ring_is_thin(self):
        bridge = _read("static", "css", "transitions-bridge.css")
        assert "0 0 0 2px rgba(249, 115, 22, 0.28)" in bridge
        assert "0 0 0 4px rgba(249, 115, 22, 0.35)" not in bridge
        assert "main#mainContent .crm-toolbar__search.t-clear" in bridge

    def test_clear_mirror_stays_off_at_rest(self):
        snippets = _read("static", "css", "transitions-snippets.css")
        bridge = _read("static", "css", "transitions-bridge.css")
        assert ".t-clear.has-value .t-clear-mirror" not in snippets
        assert ".t-clear.has-value > input" not in snippets
        assert ".t-clear.is-clearing .t-clear-mirror" in snippets
        assert ".t-clear.is-clearing > input" in snippets
        assert ".t-clear.has-value .t-clear-mirror" not in bridge

    def test_segment_selected_chip_yields_to_sliding_pill(self):
        bridge = _read("static", "css", "transitions-bridge.css")
        assert ".crm-segment.t-tabs:has(.t-tabs-pill)" in bridge
        assert "background: transparent !important;" in bridge
        assert "box-shadow: none !important;" in bridge
        assert ".crm-activity-tabs.t-tabs:has(.t-tabs-pill[data-ready])" in bridge

    def test_clear_duration_is_not_the_long_beat(self):
        root = _read("static", "css", "transitions-root.css")
        js = _read("static", "js", "transitions.js")
        assert "--clear-dur: 400ms;" in root
        assert "--clear-dur: 1000ms;" not in root
        assert "cssMs('--clear-dur', 400)" in js

    def test_contacts_slider_does_not_reserve_fifty_vh(self):
        bridge = _read("static", "css", "transitions-bridge.css")
        assert "min-height: 50vh" not in bridge
        assert "position: relative" in bridge
        assert 'data-page="1"] .t-page[data-page-id="1"]' in bridge

    def test_user_dropdown_does_not_force_paper(self):
        bridge = _read("static", "css", "transitions-bridge.css")
        assert ".crm-user-dropdown.t-dropdown {\n  background: var(--paper);\n}" not in bridge

    def test_checked_task_uses_accent(self):
        bridge = _read("static", "css", "transitions-bridge.css")
        assert "background: var(--accent, #f97316);" in bridge
        assert ".t-check[aria-checked=\"true\"]" in bridge
        assert "button.t-check[aria-checked=\"true\"]" in bridge
        assert "#transaction-checklist .t-check-wrap > .t-success-check" in bridge

    def test_like_tokens_stay_unhooked(self):
        root = _read("static", "css", "transitions-root.css")
        snippets = _read("static", "css", "transitions-snippets.css")
        bridge = _read("static", "css", "transitions-bridge.css")
        js = _read("static", "js", "transitions.js")
        assert "--like-color: #f40051;" in root
        for blob in (snippets, bridge, js):
            assert "--like-color" not in blob
            assert "t-tilt" not in blob
        assert "burstConfetti" in js
        assert "t-confetti-overlay" in bridge

    def test_wave2_rest_layers_stay_quiet(self):
        bridge = _read("static", "css", "transitions-bridge.css")
        js = _read("static", "js", "transitions.js")
        assert ".t-success-check[data-state=\"out\"]" in bridge
        assert "opacity: 0;" in bridge
        assert ".t-confetti-overlay:not(.is-running)" in bridge
        assert "visibility: hidden;" in bridge
        assert ".crm-topbar .t-tt" in bridge
        assert "top: calc(100% + 8px)" in bridge
        assert "--toggle-travel: 15px;" in bridge
        assert "transform: none;" in bridge
        assert "prefersReducedMotion()" in js
        assert "burstConfetti" in js
        assert "whenConfettiSettled" in js
