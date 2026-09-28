from types import SimpleNamespace

from services.client_portal_auth import org_branding


def organization(**changes):
    values = dict(id=1, slug='origen-realty', name='Origen Realty', logo_url=None, brand_accent=None)
    values.update(changes)
    return SimpleNamespace(**values)


def test_origen_uses_configured_email_assets(app):
    with app.app_context():
        result = org_branding(organization())
        assert result['brand_style'] == 'origen'
        assert result['logo_url'] == app.config['CLIENT_EMAIL_BRAND_MARK']
        assert result['wordmark_url'] == app.config['CLIENT_EMAIL_BRAND_WORDMARK']
        assert result['accent'] == '#14807b'


def test_uploaded_logo_and_accent_override_origen_defaults(app):
    with app.app_context():
        result = org_branding(organization(logo_url='https://example.com/custom.png', brand_accent='#123456'))
        assert result['logo_url'] == 'https://example.com/custom.png'
        assert result['accent'] == '#123456'
        assert result['wordmark_url'] is None and result['brand_style'] is None


def test_other_brokerages_never_inherit_origen_identity(app):
    with app.app_context():
        result = org_branding(organization(slug='another-brokerage'))
        assert result['logo_url'] is None and result['wordmark_url'] is None
        assert result['brand_style'] is None
        assert result['accent'] == '#f97316'
