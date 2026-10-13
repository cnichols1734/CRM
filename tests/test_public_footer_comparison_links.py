"""Public footer links and order, including auth pages without a marketing footer."""
import html
import re

PUBLIC_PAGES = ('/', '/free-real-estate-crm', '/follow-up-boss-alternative',
                '/wise-agent-alternative', '/kvcore-alternative', '/terms-privacy')
FOOTER_LINKS = [
    ('Login', '/login'), ('Register', '/register'), ('Terms & Privacy', '/terms-privacy'),
    ('Contact', '#'), ('Free CRM', '/free-real-estate-crm'),
    ('Follow Up Boss', '/follow-up-boss-alternative'),
    ('Wise Agent', '/wise-agent-alternative'), ('kvCORE', '/kvcore-alternative'),
]
_FOOTER = re.compile(r'<footer\b[^>]*>.*?</footer>', re.S)


def test_public_footer_has_expected_links_in_order(client):
    for path in PUBLIC_PAGES:
        response = client.get(path)
        assert response.status_code == 200, path
        footer = _FOOTER.search(response.get_data(as_text=True))
        assert footer is not None, path
        links = re.findall(r'<a\b[^>]*href="([^"]*)"[^>]*>(.*?)</a>', footer.group(), re.S)
        visible_links = [(html.unescape(re.sub(r'<[^>]+>', '', label)).strip(), href)
                         for href, label in links]
        assert visible_links == FOOTER_LINKS, path
        assert "openContactUsModal('landing')" in footer.group(), path


def test_auth_pages_have_no_marketing_footer(client):
    for path in ('/login', '/register'):
        response = client.get(path)
        assert response.status_code == 200, path
        page = response.get_data(as_text=True)
        assert _FOOTER.search(page) is None, path
        for _, href in FOOTER_LINKS[-3:]:
            assert f'href="{href}"' not in page, path
