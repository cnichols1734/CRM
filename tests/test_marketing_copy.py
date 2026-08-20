"""Locked Copy strings for PR 334. Exact text only."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(*parts):
    return (ROOT.joinpath(*parts)).read_text()


class TestLockedMarketingCopy:
    def test_library_strings(self):
        text = _read('templates', 'marketing', 'library.html')
        assert 'Pick a template or write a new one.' in text
        assert 'Use one of these' in text
        assert 'Pick a template' in text
        assert 'Pick a template, change the details, and send it.' in text
        assert 'No templates yet' in text
        assert 'Refresh the page to load the templates.' in text
        assert '>New<' in text
        assert 'Create Template' in text
        assert 'Describe the email. You can edit it in the preview before you save.' in text
        assert 'Saved templates' in text
        assert 'My templates' in text
        assert 'Org templates' in text
        assert 'Private' in text
        assert 'Making the email' in text
        assert 'Templates you save show up here.' in text
        assert 'Start from one we already wrote, or describe a new one.' not in text
        assert 'Start here' not in text
        assert 'Start from a template' not in text
        assert 'Pick one. Change the details. Send it.' not in text
        assert 'No starters yet' not in text
        assert 'Open this page once more and the starter library should load.' not in text
        assert 'Or start from scratch' not in text
        assert 'Create your own' not in text
        assert 'Say what the email is for. We write it. You edit the copy in the preview.' not in text
        assert 'Write this email' not in text
        assert 'Already saved' not in text
        assert 'Your templates' not in text
        assert 'Writing the email' not in text
        assert "'Shared'" not in text
        assert '"Shared"' not in text

    def test_studio_strings(self):
        text = _read('templates', 'marketing', 'studio.html')
        assert 'Making the email' in text
        assert 'Rewrite this email' in text
        assert text.count('Rewrite this email') >= 2
        assert 'Writing the email' not in text
        assert 'Write a new version' not in text

    def test_wizard_strings(self):
        text = _read('templates', 'marketing', 'wizard.html')
        assert 'Pick a template, pick who gets it, then send.' in text
        assert (
            'The first email uses the template above and sends right away. '
            'You can add two more after that.'
        ) in text
        assert 'Template, audience, send.' not in text
        assert 'First email is the template above, sent on day 0. Add up to two more.' not in text

    def test_campaigns_empty_title(self):
        text = _read('templates', 'marketing', 'campaigns.html')
        assert "empty_state('No campaigns'" in text
        assert "empty_state('Nothing here'" not in text

    def test_campaign_detail_uses_a_comma(self):
        text = _read('templates', 'marketing', 'campaign_detail.html')
        assert (
            "Day {{ step.delay_days }} at {{ '%02d' % step.send_hour_local }}"
            ":00, {{ step.template.name if step.template else 'Template' }}"
        ) in text
        assert '— {{' not in text
        assert '—' not in text

    def test_generator_error(self):
        text = _read('services', 'marketing', 'studio.py')
        assert 'Could not finish that email. Try again.' in text
        assert 'The generator could not finish. Try again.' not in text

    def test_settings_empty_suppressions(self):
        text = _read('templates', 'marketing', 'settings.html')
        assert 'No unsubscribed addresses.' in text
        assert 'None yet.' not in text
