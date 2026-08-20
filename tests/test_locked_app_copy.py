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
        assert "Add a contact, import a CSV, or forward an email to Magic Inbox." in text
        assert (
            "Add your first contact, import a CSV, or forward an email to "
            "your Magic Inbox"
        ) not in text
        assert "we'll save the contact for you" not in text
        assert "&mdash;" not in text
        assert "—" not in text

    def test_tasks_empty_helper(self):
        text = _read("templates", "tasks", "list.html")
        assert '_empty_msg = "Add a task."' in text
        assert "Get started by creating your first task." not in text

    def test_dashboard_task_empty_helper(self):
        text = _read("templates", "dashboard.html")
        assert "Add a task for a contact." in text
        assert "Create a task to get started." not in text


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
