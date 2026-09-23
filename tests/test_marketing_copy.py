"""Email content copy retained while the marketing workspace changes."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(*parts):
    return (ROOT.joinpath(*parts)).read_text()


class TestLockedMarketingCopy:





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



    def test_addendum_49_to_53(self):
        render = _read('services', 'marketing', 'render.py')
        assert (
            'f\'font-weight:600;color:{INK_MUTED};">{mark(esc(block["attribution"]), "attribution")}</p>\''
        ) in render
        assert '&mdash; {mark(esc(block["attribution"]), "attribution")}' not in render
        assert 'text += f\'\\n{block["attribution"]}\'' in render
        assert 'text += f\'\\n— {block["attribution"]}\'' not in render
        assert 'f\'{s.get("value")} ({s.get("label")})\'' in render
        assert 'f\'{s.get("value")} — {s.get("label")}\'' not in render

        contact = _read('templates', 'contacts', 'view.html')
        assert 'Unknown still gets campaigns. Opted out does not get them.' in contact
        assert 'Unknown still gets campaigns. Opted out does not.</p>' not in contact
        assert 'Unknown, still gets campaigns' in contact

        starters = _read('services', 'marketing', 'system_templates.py')
        assert 'A new listing with the photo and the specs.' in starters
        assert 'A new listing announcement built around the photo and the specs.' not in starters
        assert 'Send it before the portals do.' not in starters
        assert 'Numbers, not headlines.' in starters
        assert 'What sold last month.' not in starters

    def test_open_house_description(self):
        starters = _read('services', 'marketing', 'system_templates.py')
        assert (
            'An invitation with the address, the date, the time, and one '
            'link. Fill in the address, date, and time before you send.'
        ) in starters
        assert 'the date and time, and one clear' not in starters
        assert 'Fill in the bracketed details before you send.' not in starters
