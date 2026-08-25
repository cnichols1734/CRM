"""Pin the dashboard workspace start after the Next actions cut."""
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GONE = (
    "Next actions",
    "Work the next relationship, then move on.",
)
CHROME = frozenset(("header", "segment", "banner"))


def _dashboard_source():
    return ROOT.joinpath("templates", "dashboard.html").read_text()


def _workspace_order(html):
    order = []
    for match in re.finditer(
        r'class="[^"]*(?:crm-page-header|crm-segment|crm-magic-banner|'
        r'crm-friction|crm-kpi-skel|crm-surface)[^"]*"',
        html,
    ):
        cls = match.group(0)
        if "crm-page-header" in cls:
            order.append("header")
        elif "crm-magic-banner" in cls:
            order.append("banner")
        elif "crm-kpi-skel" in cls:
            order.append("kpi")
        elif "crm-friction" in cls:
            order.append("friction")
        elif "crm-segment" in cls:
            order.append("segment")
        elif "crm-surface" in cls:
            order.append("surface")
    return [item for item in order if item not in CHROME]


class TestDashboardNextActionsRemoved:
    def test_source_drops_next_actions_block(self):
        text = _dashboard_source()
        for phrase in GONE:
            assert phrase not in text
        assert "section_header('Today'" not in text

        follow_up = text.index(
            "{% if show_activation_onboarding and "
            "activation_state.mode == 'follow_up' %}"
        )
        after_follow_up = text.index("{% endif %}", follow_up)
        after = text[after_follow_up + len("{% endif %}"):].lstrip()
        assert after.startswith('<div class="t-skel crm-kpi-skel"')
        assert text.index("t-skel crm-kpi-skel") < text.index(
            "Upcoming Client Tasks"
        )

    def test_rendered_kpi_is_first_main_content(self, owner_a_client, seed):
        resp = owner_a_client.get("/dashboard")
        html = resp.get_data(as_text=True)
        assert resp.status_code == 200
        for phrase in GONE:
            assert phrase not in html
        assert "Potential commission" in html
        assert "Tracked contacts" in html
        assert "Average commission" in html
        assert "crm-kpi-skel" in html

        inner = html.split('class="crm-page__inner"', 1)[1]
        kpi = inner.index("crm-kpi-skel")
        upcoming = inner.index("Upcoming Client Tasks")
        assert kpi < upcoming
        assert _workspace_order(inner)[0] == "kpi"
