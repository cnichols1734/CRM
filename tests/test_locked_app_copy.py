"""Locked empty-state and open-house copy."""
from pathlib import Path

from services.marketing import system_templates as st


ROOT = Path(__file__).resolve().parents[1]


def _read(*parts):
    return ROOT.joinpath(*parts).read_text()


class TestLockedEmptyCopy:
    def test_inbox_empty_title(self):
        text = _read("templates", "inbox", "home.html")
        assert "No inbound messages" in text
        assert "Nothing here yet" not in text
        assert (
            "Send your first email or photo to your address above. "
            "New contacts will appear here as they are created."
        ) in text

    def test_contacts_empty_helper(self):
        text = _read("templates", "contacts", "list.html")
        assert (
            "Add a contact, import a CSV, or forward an email to Magic Inbox. "
            "We save the contact for you."
        ) in text
        assert (
            "'Add a contact, import a CSV, or forward an email to Magic Inbox.'"
        ) not in text
        assert (
            "Add your first contact, import a CSV, or forward an email to "
            "your Magic Inbox"
        ) not in text
        assert "we'll save the contact for you" not in text
        assert "&mdash;" not in text
        assert "—" not in text

    def test_tasks_empty_helper(self):
        text = _read("templates", "tasks", "list.html")
        assert '_empty_msg = "Add your first task to start a follow-up."' in text
        assert '_empty_msg = "Add a task."' not in text
        assert "Get started by creating your first task." not in text

    def test_dashboard_task_empty_helper(self):
        text = _read("templates", "dashboard.html")
        assert "Add a follow-up task for a contact." in text
        assert "Add a task for a contact." not in text
        assert "Create a task to get started." not in text

    def test_dashboard_inbox_banner(self):
        text = _read("templates", "dashboard.html")
        assert "<h3>Forward emails or photos. We save the contact for you.</h3>" in text
        assert "<h3>Forward emails or photos. We save the contact.</h3>" not in text
        assert "Forward emails or photos — we save the contact for you." not in text

    def test_dashboard_briefing_banner(self):
        text = _read("templates", "dashboard.html")
        assert (
            "<p>A focused list of who to touch and what to move, "
            "from your contacts and tasks.</p>"
        ) in text
        assert (
            "<p>Who to touch and what to move, from your contacts and tasks.</p>"
        ) not in text
        assert "A focused list of who to touch and what to move — grounded in your CRM." not in text
        assert "A focused list of who to touch and what to move, grounded in your CRM." not in text

    def test_dashboard_market_insights_footer(self):
        text = _read("templates", "dashboard.html")
        assert "endpoint. <em>Asking</em> prices" in text
        assert "endpoint &mdash; <em>asking</em> prices" not in text
        assert "endpoint, <em>asking</em> prices" not in text
        assert "As of —" in text

    def test_tasks_pending_empty_helper(self):
        text = _read("templates", "tasks", "list.html")
        assert '_empty_msg = "No open tasks."' in text
        assert "You're all caught up." not in text
        assert '_empty_msg = "Add your first task to start a follow-up."' in text

    def test_inbox_address_helper(self):
        text = _read("templates", "inbox", "home.html")
        assert (
            "Forward emails, business card photos, or AirDrop a vCard. "
            "We extract the contact and save it to your CRM."
        ) in text
        assert "Forward emails, business card photos, or a vCard. We save the contact." not in text
        assert "Treat this like a contact in your phone." not in text
        assert "No inbound messages" in text

    def test_inbox_card_limit_helper(self):
        text = _read("templates", "inbox", "home.html")
        assert "Up to five cards per email. We read each one and create the contact." in text
        assert "Up to five cards per email — we read each one and create the contact." not in text


class TestLockedDailyBriefingCopy:
    def test_reconnect_empty(self):
        text = _read("frontend", "controllers", "daily_briefing_controller.js")
        assert "No contacts need a reconnect right now." in text
        assert "Sphere looks warm. No one's going cold right now." not in text

    def test_copy_failed_alert_has_no_em_dash(self):
        text = _read("frontend", "controllers", "daily_briefing_controller.js")
        assert "Couldn't copy. Select the text manually." in text
        assert "Couldn't copy — select the text manually." not in text
        assert "—" not in text
        assert "&mdash;" not in text


class TestLockedOpenHouseDescription:
    LOCKED = (
        "An invitation with the address, the date, the time, and one "
        "link. Fill in the address, date, and time before you send."
    )

    def test_source_has_locked_description(self):
        text = _read("services", "marketing", "system_templates.py")
        assert self.LOCKED in text
        assert "the date and time, and one clear" not in text
        assert "Fill in the bracketed details before you send." not in text

    def test_python_definition_has_locked_description(self):
        assert st.definition("open_house")["description"] == self.LOCKED
