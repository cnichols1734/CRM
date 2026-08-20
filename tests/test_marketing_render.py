"""Blocks, merge fields, rendering, and the Fair Housing linter.

Pure unit tests: no app context, no database. The renderer is the only thing
that emits marketing markup, so it is worth pinning down hard.
"""
import pytest

from services.marketing import compliance
from services.marketing import merge_fields as mf
from services.marketing.blocks import (
    BlockError,
    ai_generation_schema,
    collect_text,
    normalize_blocks,
    validate_blocks,
)
from services.marketing.render import personalize, preview, render
from services.marketing.shell import ShellContext


def ctx(**overrides) -> ShellContext:
    base = dict(
        header_title='Origen Realty',
        agent_name='Suzie Harrington',
        agent_title='REALTOR®',
        agent_email='suzie@origenrealty.com',
        agent_phone='(830) 555-0134',
        brokerage_name='Origen Realty',
        brokerage_license='9003104',
        brokerage_address='1401 Common St, New Braunfels, TX 78130',
        unsubscribe_url='https://app.example/u/7.abc',
    )
    base.update(overrides)
    return ShellContext(**base)


SIMPLE = [
    {'type': 'heading', 'text': 'Checking in'},
    {'type': 'paragraph', 'text': 'Hi {{contact.first_name}}, how is the house?'},
]


# ---------------------------------------------------------------------------
# Blocks
# ---------------------------------------------------------------------------

class TestBlockValidation:
    def test_accepts_a_simple_email(self):
        blocks = validate_blocks(SIMPLE)
        assert [b['type'] for b in blocks] == ['heading', 'paragraph']

    def test_strips_nulls_the_model_emits(self):
        # Strict JSON schema makes the model fill every property, so nulls
        # arrive on fields the block does not use.
        raw = [{
            'type': 'paragraph', 'text': 'Hello', 'level': None, 'items': None,
            'label': None, 'url': None, 'image_url': None, 'alt': None,
            'caption': None, 'link_url': None, 'address': None, 'price': None,
            'beds': None, 'baths': None, 'sqft': None, 'stats': None,
            'attribution': None,
        }]
        assert normalize_blocks(raw) == [{'type': 'paragraph', 'text': 'Hello'}]

    def test_drops_fields_a_block_type_does_not_use(self):
        raw = [{'type': 'paragraph', 'text': 'Hello', 'price': '$400,000'}]
        assert normalize_blocks(raw) == [{'type': 'paragraph', 'text': 'Hello'}]

    def test_rejects_unknown_block_type(self):
        with pytest.raises(BlockError, match='Unknown block type'):
            validate_blocks([{'type': 'carousel', 'text': 'x'}])

    def test_rejects_empty_template(self):
        with pytest.raises(BlockError, match='at least one block'):
            validate_blocks([])

    def test_rejects_chrome_only_template(self):
        with pytest.raises(BlockError, match='actual content'):
            validate_blocks([{'type': 'divider'}, {'type': 'signature'}])

    def test_rejects_missing_required_field(self):
        with pytest.raises(BlockError, match='is missing url'):
            validate_blocks([{'type': 'button', 'label': 'Go'}])

    @pytest.mark.parametrize('bad_url', [
        'javascript:alert(1)',
        'data:text/html;base64,PHNjcmlwdD4=',
        'file:///etc/passwd',
        ' JavaScript:alert(1)',
    ])
    def test_rejects_dangerous_urls(self, bad_url):
        with pytest.raises(BlockError, match='unsupported link'):
            validate_blocks([{'type': 'button', 'label': 'Go', 'url': bad_url}])

    @pytest.mark.parametrize('ok_url', [
        'https://example.com/x',
        'http://example.com',
        'mailto:someone@example.com',
        'tel:+18305550134',
    ])
    def test_allows_expected_url_schemes(self, ok_url):
        validate_blocks([{'type': 'button', 'label': 'Go', 'url': ok_url}])

    def test_enforces_length_limits(self):
        with pytest.raises(BlockError, match='over 40 characters'):
            validate_blocks([{
                'type': 'button', 'label': 'x' * 41,
                'url': 'https://example.com',
            }])

    def test_defaults_heading_level(self):
        blocks = validate_blocks([{'type': 'heading', 'text': 'Hi', 'level': 'h9'}])
        assert blocks[0]['level'] == 'h2'

    def test_collect_text_gathers_every_author_string(self):
        blocks = validate_blocks([
            {'type': 'heading', 'text': 'Title'},
            {'type': 'bullets', 'items': ['One', 'Two']},
            {'type': 'stat_row', 'stats': [{'value': '$1', 'label': 'Price'}]},
        ])
        text = collect_text(blocks)
        for expected in ('Title', 'One', 'Two', 'Price'):
            assert expected in text

    def test_ai_schema_lists_every_property_as_required(self):
        # OpenAI strict mode rejects a schema with optional properties.
        item = ai_generation_schema()['properties']['blocks']['items']
        assert set(item['required']) == set(item['properties'])
        assert item['additionalProperties'] is False


# ---------------------------------------------------------------------------
# Merge fields
# ---------------------------------------------------------------------------

class TestMergeFields:
    def test_extracts_keys(self):
        text = 'Hi {{contact.first_name}} in {{contact.city}}'
        assert mf.extract_keys(text) == {'contact.first_name', 'contact.city'}

    def test_parses_author_fallback(self):
        assert mf.extract_tokens('{{contact.city|your area}}') == [
            ('contact.city', 'your area')
        ]

    def test_flags_unknown_field(self):
        with pytest.raises(mf.MergeFieldError, match='do not exist'):
            mf.validate_text('Hi {{contact.nickname}}')

    def test_known_fields_pass_validation(self):
        mf.validate_text('Hi {{contact.first_name}} from {{agent.full_name}}')

    def test_uses_value_when_present(self):
        out, missing = mf.substitute(
            'Hi {{contact.first_name}}', {'contact.first_name': 'Sarah'},
        )
        assert out == 'Hi Sarah'
        assert not missing

    def test_uses_author_fallback_when_value_missing(self):
        out, missing = mf.substitute(
            'in {{contact.city|your area}}', {'contact.city': None},
        )
        assert out == 'in your area'
        assert not missing

    def test_uses_registry_fallback_when_author_gave_none(self):
        out, missing = mf.substitute(
            'Hi {{contact.first_name}}', {'contact.first_name': None},
        )
        assert out == 'Hi there'
        assert not missing

    def test_reports_missing_when_no_fallback_exists(self):
        out, missing = mf.substitute(
            'from {{agent.phone}}', {'agent.phone': None},
        )
        assert out == 'from '
        assert missing == {'agent.phone'}

    def test_drops_tokens_that_left_the_registry(self):
        # A stored template can outlive a registry change; a raw token in a
        # real email is worse than nothing.
        out, missing = mf.substitute('Hi {{contact.nickname}}', {})
        assert out == 'Hi '
        assert missing == {'contact.nickname'}

    def test_escapes_values_when_asked(self):
        import html
        out, _ = mf.substitute(
            'Hi {{contact.first_name}}',
            {'contact.first_name': '<script>alert(1)</script>'},
            escape=lambda v: html.escape(v, quote=True),
        )
        assert '<script>' not in out
        assert '&lt;script&gt;' in out

    def test_resolves_from_records(self):
        class C:
            first_name, last_name, city, state, zip_code = 'Sarah', 'Mitchell', 'Seguin', 'TX', '78155'
            street_address = None

        class U:
            first_name, last_name, email, phone = 'Suzie', 'Harrington', 'x@y.com', '555'
            full_name = None

        class O:
            name, broker_name, broker_license_number = 'Origen', 'Origen Realty', '9003104'

        values = mf.resolve_values(C(), U(), O())
        assert values['contact.full_name'] == 'Sarah Mitchell'
        assert values['agent.full_name'] == 'Suzie Harrington'
        assert values['agent.brokerage'] == 'Origen Realty'
        assert values['contact.street_address'] is None

    def test_resolver_failure_reads_as_missing(self):
        class Exploding:
            @property
            def first_name(self):
                raise RuntimeError('detached instance')

        values = mf.resolve_values(Exploding(), None, None)
        assert values['contact.first_name'] is None


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

class TestRender:
    def test_produces_a_full_document_and_plain_text(self):
        out = render(SIMPLE, ctx())
        assert out.html.startswith('<!DOCTYPE html>')
        assert 'Checking in' in out.text

    def test_leaves_merge_tokens_for_later(self):
        # One render serves a whole campaign step; personalization is per
        # recipient and happens afterwards.
        out = render(SIMPLE, ctx())
        assert '{{contact.first_name}}' in out.html

    def test_escapes_author_copy(self):
        out = render(
            [{'type': 'paragraph', 'text': 'Hi <script>alert(1)</script>'}], ctx(),
        )
        assert '<script>alert(1)</script>' not in out.html
        assert '&lt;script&gt;' in out.html

    def test_carries_the_brokerage_not_our_brand(self):
        # A note from an agent to her client should not advertise our SaaS.
        out = render(SIMPLE, ctx())
        assert 'Origen Realty' in out.html
        assert 'AgentFlow' not in out.html

    def test_footer_carries_required_disclosures(self):
        out = render(SIMPLE, ctx())
        assert '9003104' in out.html
        assert '1401 Common St' in out.html
        assert 'Unsubscribe' in out.html

    def test_footer_disclosures_reach_plain_text_too(self):
        out = render(SIMPLE, ctx())
        assert 'License #9003104' in out.text
        assert 'https://app.example/u/7.abc' in out.text

    def test_footer_does_not_repeat_the_signature(self):
        out = render(SIMPLE + [{'type': 'signature'}], ctx())
        # The agent's phone belongs to the signature block alone.
        assert out.html.count('(830) 555-0134') == 1

    def test_preview_without_a_send_shows_an_inert_optout(self):
        out = render(SIMPLE, ctx(unsubscribe_url=None))
        assert 'Unsubscribe' in out.html
        assert 'href' not in out.html.split('Unsubscribe')[0][-120:]

    def test_collapses_divider_above_signature(self):
        # The signature draws its own rule; the pair would read as a double line.
        with_divider = render(
            SIMPLE + [{'type': 'divider'}, {'type': 'signature'}], ctx(),
        )
        without = render(SIMPLE + [{'type': 'signature'}], ctx())
        assert with_divider.html == without.html

    def test_keeps_a_divider_between_content(self):
        out = render(
            [SIMPLE[0], {'type': 'divider'}, SIMPLE[1]], ctx(),
        )
        assert 'height:1px' in out.html

    def test_renders_every_block_type(self):
        blocks = [
            {'type': 'heading', 'text': 'H'},
            {'type': 'paragraph', 'text': 'P'},
            {'type': 'bullets', 'items': ['a', 'b']},
            {'type': 'button', 'label': 'Go', 'url': 'https://example.com'},
            {'type': 'image', 'image_url': 'https://example.com/i.png', 'alt': 'A house'},
            {'type': 'listing_card', 'address': '1 Main', 'price': '$1', 'beds': '3'},
            {'type': 'stat_row', 'stats': [{'value': '1', 'label': 'One'}]},
            {'type': 'quote', 'text': 'Q', 'attribution': 'Someone'},
            {'type': 'divider'},
            {'type': 'signature'},
        ]
        out = render(blocks, ctx())
        for expected in ('H', 'P', 'Go', 'A house', '1 Main', 'One', 'Q', 'Someone'):
            assert expected in out.html

    def test_paragraph_blank_line_becomes_two_paragraphs(self):
        out = render([{'type': 'paragraph', 'text': 'One\n\nTwo'}], ctx())
        assert out.html.count('<p style="margin:0 0 18px 0') == 2

    def test_signature_is_omitted_without_an_agent(self):
        out = render(SIMPLE + [{'type': 'signature'}], ctx(agent_name=None))
        assert 'Suzie' not in out.html

    def test_validates_by_default(self):
        with pytest.raises(BlockError):
            render([{'type': 'nope'}], ctx())


class TestPersonalize:
    def test_fills_subject_html_and_text(self):
        rendered = render(SIMPLE, ctx())
        subject, html_out, text_out, missing = personalize(
            rendered, 'Hi {{contact.first_name}}',
            {'contact.first_name': 'Sarah'},
        )
        assert subject == 'Hi Sarah'
        assert 'Hi Sarah' in html_out
        assert 'Hi Sarah' in text_out
        assert not missing

    def test_escapes_recipient_data_in_html(self):
        # Rendering escaped the author's copy, but substitution happens after,
        # so the value itself has to be escaped here or it lands raw.
        rendered = render(SIMPLE, ctx())
        _, html_out, _, _ = personalize(
            rendered, 'x', {'contact.first_name': '<img onerror=alert(1)>'},
        )
        assert '<img onerror' not in html_out
        assert '&lt;img' in html_out

    def test_subject_is_not_html_escaped(self):
        # A subject is a header, not markup; escaping would show entities.
        rendered = render(SIMPLE, ctx())
        subject, _, _, _ = personalize(
            rendered, 'A & B {{contact.first_name}}', {'contact.first_name': "O'Hara"},
        )
        assert subject == "A & B O'Hara"

    def test_reports_missing_required_field(self):
        rendered = render([{'type': 'paragraph', 'text': 'from {{agent.phone}}'}], ctx())
        _, _, _, missing = personalize(rendered, 'x', {'agent.phone': None})
        assert 'agent.phone' in missing

    def test_preview_uses_example_values(self):
        subject, html_out = preview(SIMPLE, ctx(), 'Hi {{contact.first_name}}')
        assert subject == 'Hi Sarah'
        assert '{{' not in html_out


# ---------------------------------------------------------------------------
# Fair Housing linter
# ---------------------------------------------------------------------------

class TestFairHousingLinter:
    @pytest.mark.parametrize('copy,protected', [
        ('This home is perfect for families.', 'familial status'),
        ('A family-friendly street.', 'familial status'),
        ('Adults only, no children.', 'familial status'),
        ('Homes for Christian buyers.', 'religion'),
        ("Walking distance to St. Mary's church.", 'religion'),
        ('A changing neighborhood, get in early.', 'race, color, or national origin'),
        ('An Asian neighborhood near downtown.', 'race, color, or national origin'),
        ('Not suitable for the disabled.', 'disability'),
        ('Great for a single man.', 'sex'),
    ])
    def test_blocks_clear_violations(self, copy, protected):
        findings = compliance.scan_text(copy)
        assert findings, f'no finding for {copy!r}'
        assert findings[0].severity == compliance.SEVERITY_BLOCK
        assert findings[0].protected_class == protected

    @pytest.mark.parametrize('copy', [
        'A safe neighborhood to raise a family in.',
        'Great schools nearby.',
        'An exclusive enclave of custom homes.',
        'Ideal for empty nesters.',
        'The nicer part of town.',
        'Huge master bedroom.',
    ])
    def test_warns_on_proxies_and_soft_language(self, copy):
        findings = compliance.scan_text(copy)
        assert findings, f'no finding for {copy!r}'
        assert any(f.severity == compliance.SEVERITY_WARN for f in findings)

    @pytest.mark.parametrize('copy', [
        'This market is crazy right now.',
        'Four bedrooms, a fenced yard, and a new roof in 2024.',
        'The house is in Comal ISD.',
        'Primary bedroom is downstairs with a walk-in closet.',
        'Open house Saturday from 2 to 4pm.',
        'Corner lot on a cul-de-sac, walkable to the greenbelt trail.',
        'Median price is $412,000 and days on market is 28.',
        'Walking distance to the greenbelt trail.',
        'Steps from the neighborhood pool.',
        'I wanted to check in and see how the house is treating you.',
    ])
    def test_leaves_ordinary_copy_alone(self, copy):
        # A linter that cries wolf gets clicked through, so false positives
        # are as damaging as misses.
        assert compliance.scan_text(copy) == []

    def test_reports_where_the_phrase_came_from(self):
        blocks = validate_blocks([
            {'type': 'heading', 'text': 'Hello'},
            {'type': 'paragraph', 'text': 'A safe neighborhood.'},
        ])
        findings = compliance.scan_blocks(blocks, subject='Perfect for families')
        by_field = {(f.field, f.block_index) for f in findings}
        assert ('subject', None) in by_field
        assert ('text', 1) in by_field

    def test_scans_bullets_and_stat_labels(self):
        blocks = validate_blocks([
            {'type': 'paragraph', 'text': 'Details below.'},
            {'type': 'bullets', 'items': ['Great schools']},
        ])
        findings = compliance.scan_blocks(blocks)
        assert any(f.field == 'items' for f in findings)

    def test_state_rolls_up_to_the_worst_finding(self):
        assert compliance.state_for([]) == 'pass'
        assert compliance.state_for(compliance.scan_text('Great schools')) == 'warn'
        assert compliance.state_for(
            compliance.scan_text('Perfect for families')
        ) == 'blocked'

    def test_deduplicates_overlapping_rules(self):
        findings = compliance.scan_text('Perfect for families. Perfect for families.')
        # Same phrase, same field: one finding is enough to act on.
        assert len(findings) == 1

    def test_summarize_counts_both_severities(self):
        findings = compliance.scan_blocks(validate_blocks([
            {'type': 'paragraph', 'text': 'Perfect for families in a safe neighborhood.'},
        ]))
        assert compliance.summarize(findings) == '1 blocking issue and 1 warning.'

    def test_findings_serialize_for_storage(self):
        finding = compliance.scan_text('Perfect for families')[0]
        payload = finding.to_dict()
        assert payload['severity'] == 'block'
        assert 'suggestion' in payload


class TestOrgDisclosure:
    def test_lists_every_missing_field(self):
        class Org:
            broker_name = None
            broker_license_number = '  '
            broker_address = None

        missing = compliance.missing_org_disclosure(Org())
        assert missing == [
            'brokerage name', 'brokerage license number', 'brokerage mailing address',
        ]

    def test_passes_when_complete(self):
        class Org:
            broker_name = 'Origen Realty'
            broker_license_number = '9003104'
            broker_address = '1401 Common St'

        assert compliance.missing_org_disclosure(Org()) == []
