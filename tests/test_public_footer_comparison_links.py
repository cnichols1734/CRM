"""Pin comparison links in the five public footers."""
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

PUBLIC_PAGES = (
    ("/", "templates/landing.html"),
    ("/free-real-estate-crm", "templates/free_real_estate_crm.html"),
    ("/follow-up-boss-alternative", "templates/follow_up_boss_alternative.html"),
    ("/wise-agent-alternative", "templates/wise_agent_alternative.html"),
    ("/kvcore-alternative", "templates/kvcore_alternative.html"),
)

FOOTER_COMPARISON_LINKS = (
    ("Follow Up Boss", "/follow-up-boss-alternative", "main.follow_up_boss_alternative"),
    ("Wise Agent", "/wise-agent-alternative", "main.wise_agent_alternative"),
    ("kvCORE", "/kvcore-alternative", "main.kvcore_alternative"),
)

KEPT_FOOTER_LABELS = ("Login", "Register", "Terms & Privacy", "Contact")

_FOOTER = re.compile(r"<footer\b[^>]*>.*?</footer>", re.S)
_LINK_LABEL = re.compile(r"<a\b[^>]*>(.*?)</a>", re.S)


def _footer(html):
    match = _FOOTER.search(html)
    assert match is not None
    return match.group(0)


def _footer_labels(footer):
    labels = []
    for raw in _LINK_LABEL.findall(footer):
        text = re.sub(r"<[^>]+>", "", raw)
        text = re.sub(r"\s+", " ", text).strip()
        if text:
            labels.append(text)
    return labels


def _template_link(endpoint, label):
    return (
        f'<a href="{{{{ url_for(\'{endpoint}\') }}}}" '
        f'class="hover:text-white transition-colors">{label}</a>'
    )


class TestPublicFooterComparisonLinks:
    def test_each_template_footer_has_exact_labels_and_endpoints(self):
        for _path, relpath in PUBLIC_PAGES:
            source = (ROOT / relpath).read_text()
            footer = _footer(source)
            labels = _footer_labels(footer)
            for kept in KEPT_FOOTER_LABELS:
                assert kept in labels
            for label, _href, endpoint in FOOTER_COMPARISON_LINKS:
                assert _template_link(endpoint, label) in footer
                assert label in labels
            for label in labels:
                assert "alternative" not in label.lower()

    def test_each_rendered_footer_has_exact_hrefs_and_labels(self, client):
        for path, _relpath in PUBLIC_PAGES:
            html = client.get(path).get_data(as_text=True)
            footer = _footer(html)
            labels = _footer_labels(footer)
            for kept in KEPT_FOOTER_LABELS:
                assert kept in labels
            for label, href, _endpoint in FOOTER_COMPARISON_LINKS:
                assert label in labels
                assert (
                    f'<a href="{href}" class="hover:text-white transition-colors">'
                    f"{label}</a>"
                ) in footer
            for label in labels:
                assert "alternative" not in label.lower()
