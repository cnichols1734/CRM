"""The starter templates every org gets, hand-built rather than generated.

An empty template library is the fastest way to lose a new feature. An agent
who opens Marketing on day one and finds a blank editor closes the tab; an agent
who finds six sends they would actually send picks one, changes two lines, and
becomes a user.

These are also the worked examples for the AI studio in phase 2. The model is
shown these blocks as reference for what good output looks like, which is a far
stronger signal than describing the house style in prose.

Starters ship with filled sample details so compliance and launch can accept
them on day one. An agent can still change the address, date, or numbers
before sending. ``blocks.find_placeholders`` stops a send that still says
"[address]". Merge fields resolve per recipient and need no attention.

The copy deliberately avoids the language the Fair Housing linter flags, and
should stay that way: a starter template that trips the compliance gate on first
open teaches agents to dismiss the gate.
"""
from __future__ import annotations

from typing import Any, Optional

from models import MarketingTemplate, db
from services.marketing import compliance
from services.marketing.blocks import validate_blocks

# Keyed on name within an org, since a system template has no owner to key on.
SYSTEM_TEMPLATES: tuple[dict[str, Any], ...] = (
    {
        'key': 'check_in',
        'name': 'Just checking in',
        'category': 'check_in',
        'description': 'A short note to past clients when you have nothing to sell.',
        'subject': 'Checking in, {{contact.first_name|there}}',
        'preheader': 'Checking in. How are you doing?',
        'blocks': [
            {'type': 'hero',
             'eyebrow': 'Checking in',
             'title': 'Just wanted to say hi.',
             'accent': 'Just saying hi.',
             'text': (
                 'Been thinking about you. Short note, no pitch.'
             )},
            {'type': 'paragraph', 'text': (
                'Hi {{contact.first_name|there}},\n\n'
                'Checking in. How are things on your end?'
            )},
            {'type': 'paragraph', 'text': (
                'If anything has changed on your end, or you are just curious '
                'what your place would sell for today, reply to this and I '
                'will put together the numbers. No obligation and no pitch to '
                'sit through.'
            )},
            {'type': 'paragraph', 'text': (
                'And if you know someone who could use a hand buying or '
                'selling, I would be glad to help them the same way.'
            )},
            {'type': 'signature'},
        ],
    },
    {
        'key': 'open_house',
        'name': 'Open house invitation',
        'category': 'open_house',
        'description': (
            'An invitation with the address, the date, the time, and one '
            'link. Fill in the address, date, and time before you send.'
        ),
        'subject': 'Open house this weekend: 123 Main St',
        'preheader': 'Stop by Saturday between 2 and 4pm. No appointment needed.',
        'blocks': [
            {'type': 'hero',
             'eyebrow': 'Open house',
             'title': 'Come take a look.',
             'accent': 'No appointment needed.',
             'text': (
                 'I am hosting an open house this weekend and would be glad '
                 'to see you there.'
             )},
            {'type': 'paragraph', 'text': (
                'Hi {{contact.first_name|there}},\n\n'
                'I am opening up 123 Main Street this weekend. Stop in for '
                'five minutes or stay for thirty, either is welcome.'
            )},
            {'type': 'callout', 'label': 'When',
             'text': 'Saturday, June 14 from 2:00 to 4:00pm'},
            {'type': 'listing_card',
             'address': '123 Main Street',
             'price': '$425,000',
             'beds': '3', 'baths': '2', 'sqft': '1,850',
             'caption': 'Light inside, and a backyard you will actually use.'},
            {'type': 'paragraph', 'text': (
                'Bring a friend who is house hunting. If the timing does not '
                'work, reply and I will walk you through it another day.'
            )},
            {'type': 'signature'},
        ],
    },
    {
        'key': 'market_update',
        'name': 'Monthly market update',
        'category': 'market_update',
        'description': 'Three numbers from last month and what they mean.',
        'subject': 'What homes are doing in {{contact.city|your area}}',
        'preheader': 'Three numbers from last month and what they mean for you.',
        'blocks': [
            {'type': 'hero',
             'eyebrow': 'July market update',
             'title': 'Here is where the market actually stands.',
             'accent': 'Numbers, not headlines.',
             'text': (
                 'A short read on what sold, what it sold for, and how long '
                 'it took.'
             )},
            {'type': 'paragraph', 'text': (
                'Hi {{contact.first_name|there}},\n\n'
                'Quick read on {{contact.city|the area}} for last month. '
                'Nothing to do with this unless you want to. I would rather '
                'you have the same numbers I do.'
            )},
            {'type': 'stat_row', 'stats': [
                {'value': '$425K', 'label': 'Median price'},
                {'value': '18', 'label': 'Days on market'},
                {'value': '2.4', 'label': 'Months of supply'},
            ]},
            {'type': 'heading', 'level': 'h2', 'text': 'What it means'},
            {'type': 'paragraph', 'text': (
                'Prices held. Homes sat a little longer than they did in the '
                'spring, which is room to talk if you are buying. If you are '
                'selling, last month sold prices still set the number.'
            )},
            {'type': 'paragraph', 'text': (
                'If you want the version of this for your street rather than '
                'the whole area, reply and I will run it.'
            )},
            {'type': 'signature'},
        ],
    },
    {
        'key': 'just_listed',
        'name': 'Just listed',
        'category': 'just_listed',
        'description': 'A new listing with the photo and the specs.',
        'subject': 'Just listed in Oak Hill',
        'preheader': 'New on the market this week. Here are the details.',
        'blocks': [
            {'type': 'hero',
             'eyebrow': 'Just listed',
             'title': 'New on the market.',
             'accent': 'Before it hits the portals.',
             'text': 'A quick look at what I just brought on, in case it fits.'},
            {'type': 'paragraph', 'text': (
                'Hi {{contact.first_name|there}},\n\n'
                'I listed this one this week and wanted you to see it first.'
            )},
            {'type': 'listing_card',
             'address': '123 Main Street',
             'price': '$425,000',
             'beds': '3', 'baths': '2', 'sqft': '1,850',
             'caption': 'Updated kitchen, a usable yard, and a layout that works.',
             'url': 'https://example.com/listing'},
            {'type': 'button', 'label': 'See all the photos',
             'url': 'https://example.com/listing'},
            {'type': 'paragraph', 'text': (
                'If it is not right for you but you know who it is right for, '
                'forward it along. And if you want to see it in person, reply '
                'with a couple of times that work.'
            )},
            {'type': 'signature'},
        ],
    },
    {
        'key': 'just_sold',
        'name': 'Just sold',
        'category': 'just_sold',
        'description': 'A nearby sale, plus an offer to run the same numbers for them.',
        'subject': 'Sold in Oak Hill: here is what it went for',
        'preheader': 'Closed this week. What it tells you about your own value.',
        'blocks': [
            {'type': 'hero',
             'eyebrow': 'Just sold',
             'title': 'Closed this week.',
             'accent': 'Here is what it tells you.',
             'text': (
                 'Every sale nearby is a data point on what your own place is '
                 'worth.'
             )},
            {'type': 'paragraph', 'text': (
                'Hi {{contact.first_name|there}},\n\n'
                'We closed 123 Main Street last week. Sharing it because a '
                'sale this close to you moves the number on your own house.'
            )},
            {'type': 'listing_card',
             'address': '123 Main Street',
             'price': '$425,000',
             'beds': '3', 'baths': '2', 'sqft': '1,850',
             'caption': 'Eighteen days on the market. Two offers, sold at list.'},
            {'type': 'paragraph', 'text': (
                'Curious what that means for you? Reply and I will put '
                'together what your place would list for today. Takes me an '
                'afternoon and costs you nothing.'
            )},
            {'type': 'signature'},
        ],
    },
    {
        'key': 'holiday',
        'name': 'Seasonal greeting',
        'category': 'holiday',
        'description': 'A short holiday note with nothing to sell.',
        'subject': 'Happy holidays, {{contact.first_name|friend}}',
        'preheader': 'A quick note from my family to yours.',
        'blocks': [
            {'type': 'hero',
             'eyebrow': 'Holidays',
             'title': 'Thinking of you this year.',
             'accent': 'Thank you for the trust.',
             'text': 'A short note, with nothing attached to it.'},
            {'type': 'paragraph', 'text': (
                'Hi {{contact.first_name|there}},\n\n'
                'Hope this year has been kind to you. Thank you for letting '
                'me be part of it. From my house to yours, happy holidays.'
            )},
            {'type': 'paragraph', 'text': (
                'Thank you for letting me work with you.'
            )},
            {'type': 'signature'},
        ],
    },
)

SYSTEM_TEMPLATE_KEYS: tuple[str, ...] = tuple(t['key'] for t in SYSTEM_TEMPLATES)


def definition(key: str) -> Optional[dict[str, Any]]:
    for template in SYSTEM_TEMPLATES:
        if template['key'] == key:
            return template
    return None


def validate_all() -> None:
    """Check every starter against the block rules and the linter.

    Called by the test suite. A starter template that fails validation, or that
    trips the Fair Housing linter, is a bug we want to hear about at build time
    rather than from an agent on their first open.
    """
    for template in SYSTEM_TEMPLATES:
        blocks = validate_blocks(template['blocks'])
        findings = compliance.scan_blocks(blocks)
        findings += compliance.scan_text(template['subject'], field='subject')
        findings += compliance.scan_text(template['preheader'], field='preheader')
        if findings:
            summary = '; '.join(f'{f.field}: {f.matched_text}' for f in findings)
            raise AssertionError(
                f'System template {template["key"]!r} trips the compliance '
                f'linter: {summary}'
            )


def seed_for_org(organization_id: int, *, commit: bool = True) -> list[MarketingTemplate]:
    """Give an org the starter library. Safe to run repeatedly.

    Missing starters are created. Existing ``source='system'`` rows are brought
    in line with the current definitions, so a header fix like this one lands
    the next time Marketing opens. Saved copies (``source`` is not ``system``)
    are left alone.
    """
    existing = {
        row.name: row
        for row in MarketingTemplate.query.filter_by(
            organization_id=organization_id,
            source='system',
        ).all()
    }

    created: list[MarketingTemplate] = []
    dirty: list[MarketingTemplate] = []
    for spec in SYSTEM_TEMPLATES:
        blocks = validate_blocks(spec['blocks'])
        row = existing.get(spec['name'])
        if row is None:
            row = MarketingTemplate(
                organization_id=organization_id,
                created_by_id=None,
                name=spec['name'],
                visibility='org',
                status='ready',
                source='system',
                compliance_state='pass',
                compliance_findings=[],
            )
            db.session.add(row)
            created.append(row)
            _apply_spec(row, spec, blocks)
            dirty.append(row)
            continue
        if _spec_matches(row, spec, blocks):
            continue
        _apply_spec(row, spec, blocks)
        dirty.append(row)

    if dirty:
        db.session.flush()
        from models import Organization
        org = db.session.get(Organization, organization_id)
        from services.marketing.templates import cache_render
        for template in dirty:
            cache_render(template, org)

    if dirty and commit:
        db.session.commit()
    return created


def _apply_spec(row: MarketingTemplate, spec: dict[str, Any], blocks: list) -> None:
    row.description = spec['description']
    row.category = spec['category']
    row.subject = spec['subject']
    row.preheader = spec['preheader']
    row.blocks = blocks
    row.status = 'ready'
    row.compliance_state = 'pass'
    row.compliance_findings = []


def _spec_matches(row: MarketingTemplate, spec: dict[str, Any], blocks: list) -> bool:
    return (
        row.description == spec['description']
        and row.category == spec['category']
        and row.subject == spec['subject']
        and row.preheader == spec['preheader']
        and row.blocks == blocks
    )
