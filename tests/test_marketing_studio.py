"""AI template generation: repair unusable model output so create can finish."""
import pytest

from services.marketing.studio import generate
from services.marketing.templates import TemplateError


def test_generate_requires_a_prompt():
    with pytest.raises(TemplateError, match='Describe the email you want'):
        generate('   ')


def test_generate_drops_an_empty_button_and_saves(monkeypatch):
    def fake_structured(**kwargs):
        return {
            'subject': 'Checking in',
            'preheader': 'Just a note',
            'blocks': [
                {'type': 'paragraph', 'text': 'Hi there. How are things?'},
                {'type': 'button', 'label': 'See more', 'url': None},
                {'type': 'signature'},
            ],
        }, 'test-model'

    monkeypatch.setattr(
        'services.marketing.studio.generate_structured_response',
        fake_structured,
    )
    out = generate('Check in with past clients')
    assert out['subject'] == 'Checking in'
    assert [block['type'] for block in out['blocks']] == ['paragraph', 'signature']
    assert out['status'] == 'ready'


def test_generate_keeps_a_button_with_a_real_url(monkeypatch):
    def fake_structured(**kwargs):
        return {
            'subject': 'Open house Saturday',
            'preheader': 'Stop by if you can',
            'blocks': [
                {'type': 'paragraph', 'text': 'Open house this weekend.'},
                {
                    'type': 'button',
                    'label': 'See the listing',
                    'url': 'https://example.com/listing',
                },
                {'type': 'signature'},
            ],
        }, 'test-model'

    monkeypatch.setattr(
        'services.marketing.studio.generate_structured_response',
        fake_structured,
    )
    out = generate('Invite people to the open house')
    types = [block['type'] for block in out['blocks']]
    assert 'button' in types
    button = next(block for block in out['blocks'] if block['type'] == 'button')
    assert button['url'] == 'https://example.com/listing'


def test_generate_surfaces_a_missing_subject(monkeypatch):
    def fake_structured(**kwargs):
        return {
            'subject': '',
            'preheader': 'Just a note',
            'blocks': [
                {'type': 'paragraph', 'text': 'Hi there.'},
                {'type': 'signature'},
            ],
        }, 'test-model'

    monkeypatch.setattr(
        'services.marketing.studio.generate_structured_response',
        fake_structured,
    )
    with pytest.raises(TemplateError, match='subject'):
        generate('Check in with past clients')
