"""Pins for the /dashboard phone stack.

A desktop table or doughnut sitting beside a legend on a 390px screen
fails CI here, not only in a headed browser.
"""
from datetime import date
from pathlib import Path

import pytest

from models import Contact, db


ROOT = Path(__file__).resolve().parent.parent


def _read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


def _dashboard_source():
    return _read("templates", "dashboard.html")


def _app_css():
    return _read("frontend", "styles", "app.css")


def _phone_block(css):
    marker = "/* Dashboard phone */"
    assert marker in css
    rest = css.split(marker, 1)[1]
    assert "@media (max-width: 767px)" in rest
    return rest.split("/* ── Magic Inbox banner", 1)[0]


class TestDashboardMobileContracts:
    def test_template_keeps_desktop_table_and_adds_phone_cards(self):
        html = _dashboard_source()
        table_at = html.index("crm-dash-contacts-table")
        cards_at = html.index("crm-dash-contacts-cards")
        assert table_at < cards_at
        assert '<table class="crm-table">' in html[table_at:cards_at]
        assert "<th>Name</th>" in html[table_at:cards_at]
        assert "<th>Last Contact</th>" in html[table_at:cards_at]
        assert "<th>Email</th>" in html[table_at:cards_at]
        assert "crm-dash-contact__name" in html[cards_at:]
        assert "crm-dash-contact__fact" in html[cards_at:]
        assert "ogtechnolo" not in html

    def test_groups_chart_is_marked_for_phone_hide(self):
        html = _dashboard_source()
        assert "crm-dash-groups-chart" in html
        assert "crm-dash-groups-legend" in html
        assert "crm-dash-groups-row" in html
        assert 'id="groupChart"' in html

    def test_tasks_and_todo_keep_existing_filters_and_copy(self):
        html = _dashboard_source()
        assert "crm-dash-tasks" in html
        assert "crm-dash-todo-form" in html
        assert "[7, 30, 60, 90]" in html
        assert "Add a follow-up task for a contact." in html
        assert 'placeholder="Add a personal to do..."' in html
        assert "Add to do" in html

    def test_css_hides_desktop_table_and_chart_only_on_phone(self):
        css = _app_css()
        desktop, phone_intro = css.split("/* Dashboard phone */", 1)
        assert ".crm-dash-contacts-cards { display: none; }" in desktop
        assert ".crm-dash-contacts-table { display: none; }" not in desktop

        phone = _phone_block(css)
        assert "@media (max-width: 767px)" in phone
        assert ".crm-dash-contacts-table { display: none; }" in phone
        assert ".crm-dash-groups-chart { display: none; }" in phone
        assert ".crm-dash-contacts-cards" in phone
        assert "display: flex" in phone
        assert "#dashboardActiveTodoList .empty-state" in phone
        assert "border-style: none" in phone
        assert ".crm-dash-todo-form .crm-btn" in phone
        assert "width: 100%" in phone
        assert "min-width: 768px" not in phone

    def test_phone_cards_do_not_truncate_email(self):
        html = _dashboard_source()
        cards = html.split("crm-dash-contacts-cards", 1)[1]
        cards = cards.split("{% else %}", 1)[0]
        assert "truncate" not in cards
        assert "overflow-wrap: anywhere" in _app_css()


@pytest.mark.usefixtures("seed")
class TestDashboardMobileRender:
    def test_wrappers_render_on_dashboard(self, owner_a_client):
        resp = owner_a_client.get("/dashboard")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert "crm-dash-tasks" in html
        assert "crm-dash-todo-form" in html
        assert "crm-dash-groups-chart" in html
        assert "crm-dash-groups-legend" in html
        assert "7d" in html and "30d" in html and "60d" in html and "90d" in html

    def test_top_contact_email_is_complete_in_phone_stack(
        self, owner_a_client, seed, app
    ):
        long_email = "ogtechnology.longemail@example.com"
        with app.app_context():
            contact = db.session.get(Contact, seed["contact_a"])
            prior = (
                contact.email,
                contact.potential_commission,
                contact.last_contact_date,
            )
            contact.email = long_email
            contact.potential_commission = 18500
            contact.last_contact_date = date(2026, 1, 24)
            db.session.commit()
        try:
            html = owner_a_client.get("/dashboard").get_data(as_text=True)
            assert "crm-dash-contacts-table" in html
            assert "crm-dash-contacts-cards" in html
            assert html.count(long_email) >= 2
            assert "ogtechnolo..." not in html
            assert "Jane Doe" in html
            cards = html.split("crm-dash-contacts-cards", 1)[1]
            assert long_email in cards
            assert "Jan 24, 2026" in cards
            assert "$18,500" in cards
        finally:
            with app.app_context():
                contact = db.session.get(Contact, seed["contact_a"])
                contact.email, contact.potential_commission, contact.last_contact_date = (
                    prior
                )
                db.session.commit()
