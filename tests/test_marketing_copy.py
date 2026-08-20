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

    def test_addendum_24_to_28(self):
        contact = _read('templates', 'contacts', 'view.html')
        assert 'Unknown, still gets campaigns' in contact
        assert 'Unknown — still receives campaigns' not in contact
        starters = _read('services', 'marketing', 'system_templates.py')
        assert 'A short note to past clients when you have nothing to sell.' in starters
        assert 'Three numbers from last month and what they mean.' in starters
        assert 'A nearby sale, plus an offer to run the same numbers for them.' in starters
        assert 'A short holiday note with nothing to sell.' in starters

    def test_addendum_29_to_38(self):
        wizard = _read('templates', 'marketing', 'wizard.html')
        assert 'Pick a group, ZIP, or city to see how many contacts get this.' in wizard
        assert 'Pick a filter to see how many people this reaches.' not in wizard
        assert 'Pick people or a filter to see how many this reaches.' not in wizard

        unsubscribe = _read('templates', 'marketing', 'unsubscribe.html')
        assert 'You&rsquo;re subscribed again.' in unsubscribe
        assert 'You&rsquo;re back on the list' not in unsubscribe

        overview = _read('templates', 'marketing', 'overview.html')
        assert 'Use New campaign to send a template to contacts in your CRM.' in overview
        assert 'Pick a template, pick who it goes to, and send.' not in overview

        settings = _read('templates', 'marketing', 'settings.html')
        assert (
            'Brokerage details go in every email footer. '
            'Fill them in before you send a campaign.'
        ) in settings
        assert 'Without them, nothing sends.' not in settings

        blocks = _read('services', 'marketing', 'blocks.py')
        assert 'One button per email. A second button usually gets ignored.' in blocks
        assert 'The primary call to action.' not in blocks

        starters = _read('services', 'marketing', 'system_templates.py')
        assert 'Checking in. How are you doing?' in starters
        assert 'Just saying hi.' in starters
        assert 'Been thinking about you. Short note, no pitch.' in starters
        assert 'Checking in. How are things on your end?' in starters
        assert 'Thank you for letting me work with you.' in starters
        assert 'No agenda here. Just wanted to see how you are doing.' not in starters
        assert 'Nothing to sell.' not in starters
        assert 'You crossed my mind. A short note, with no pitch attached.' not in starters
        assert 'No agenda with this one.' not in starters
        assert 'Getting to know the people I work with' not in starters

    def test_addendum_39_to_48(self):
        library = _read('templates', 'marketing', 'library.html')
        assert 'Hang on.' in library
        assert 'Usually takes a few seconds.' not in library

        studio = _read('templates', 'marketing', 'studio.html')
        assert 'Hang on.' in studio
        assert 'Usually takes a few seconds.' not in studio
        assert 'Click any line in the email to edit it.' in studio
        assert 'The layout stays.' not in studio
        assert 'This overwrites the current email. Save first if you want to keep it.' in studio
        assert 'Replaces the email. Save first if you want to keep this one.' not in studio

        overview = _read('templates', 'marketing', 'overview.html')
        assert 'Send a one-time email or a drip to your contacts.' in overview
        assert 'Send one email, or a short sequence, to the people already in your CRM.' not in overview
        assert "empty_state('No campaigns'" in overview
        assert "empty_state('No campaigns yet'" not in overview
        assert 'Use New campaign to send a template to contacts in your CRM.' in overview

        coming_soon = _read('templates', 'marketing.html')
        assert '>Marketing<' in coming_soon or '            Marketing\n' in coming_soon
        assert 'Marketing Hub - Coming Soon' not in coming_soon
        assert 'Email campaigns are not included in this plan.' in coming_soon
        assert "We're building something amazing!" not in coming_soon

        starters = _read('services', 'marketing', 'system_templates.py')
        assert 'What sold last month.' in starters
        assert 'Numbers, not headlines.' not in starters

        studio_js = _read('frontend', 'controllers', 'marketing_template_studio_controller.js')
        assert 'Could not read this template. Try again.' in studio_js
        assert 'The template content is not valid JSON.' not in studio_js
