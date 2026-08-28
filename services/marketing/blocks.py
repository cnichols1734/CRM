"""The authored content format for marketing emails.

A template is an ordered list of typed blocks, never raw HTML. The AI, the
in-app editor, and MCP clients all produce this same structure, which buys three
things: the brand shell cannot be edited away, every string is escaped on the
way into HTML so there is no markup injection to sanitize, and re-prompting a
single block does not risk the rest of the email.

This module is the single source of truth for the format. The AI generation
schema, the MCP guidelines tool, and server-side validation all read from
``BLOCK_SPECS`` so they cannot drift apart.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

# Only these schemes may appear in a block URL. Everything else (javascript:,
# data:, file:) is rejected at validation rather than escaped at render, so a
# bad link never reaches a stored template.
ALLOWED_URL_SCHEMES = ('http://', 'https://', 'mailto:', 'tel:')

MAX_BLOCKS = 40
MAX_STATS = 4
MAX_BULLETS = 8
MAX_STEPS = 4


@dataclass(frozen=True)
class BlockSpec:
    """One block type: which fields it uses and how long they may be."""
    type: str
    label: str
    description: str
    required: tuple[str, ...] = ()
    optional: tuple[str, ...] = ()
    limits: dict[str, int] = field(default_factory=dict)


BLOCK_SPECS: tuple[BlockSpec, ...] = (
    BlockSpec(
        type='hero',
        label='Hero',
        description=(
            'The title band at the top of the email: a small label, a teal '
            'hairline, a large headline, an optional second line, and a '
            'sentence underneath. Sets the tone for the whole email. Must be '
            'the first block, and there can only be one.'
        ),
        required=('title',),
        optional=('eyebrow', 'accent', 'text'),
        limits={'title': 70, 'eyebrow': 32, 'accent': 60, 'text': 260},
    ),
    BlockSpec(
        type='heading',
        label='Heading',
        description='A section title. Use one near the top, then sparingly.',
        required=('text',),
        optional=('level',),
        limits={'text': 120},
    ),
    BlockSpec(
        type='paragraph',
        label='Paragraph',
        description=(
            'A paragraph of body copy. Merge fields are allowed inline. '
            'Two to four sentences reads best in email.'
        ),
        required=('text',),
        limits={'text': 1200},
    ),
    BlockSpec(
        type='bullets',
        label='Bullet list',
        description='A short list. Good for features, dates, or next steps.',
        required=('items',),
        limits={'items': MAX_BULLETS, 'item': 200},
    ),
    BlockSpec(
        type='button',
        label='Button',
        description=(
            'One button per email. A second button usually gets ignored.'
        ),
        required=('label', 'url'),
        limits={'label': 40, 'url': 900},
    ),
    BlockSpec(
        type='image',
        label='Image',
        description=(
            'A full-width image. Requires alt text, which is what recipients '
            'with images turned off will actually read.'
        ),
        required=('image_url', 'alt'),
        optional=('caption', 'link_url'),
        limits={'image_url': 900, 'alt': 200, 'caption': 200, 'link_url': 900},
    ),
    BlockSpec(
        type='listing_card',
        label='Listing card',
        description=(
            'A property with its photo, address, price, and specs. Use for '
            'just listed, just sold, and open house emails.'
        ),
        required=('address',),
        optional=('image_url', 'price', 'beds', 'baths', 'sqft', 'url', 'caption'),
        limits={
            'address': 200, 'price': 40, 'beds': 12, 'baths': 12,
            'sqft': 20, 'url': 900, 'image_url': 900, 'caption': 200,
        },
    ),
    BlockSpec(
        type='stat_row',
        label='Stat row',
        description=(
            'Two to four figures side by side, each with a label. Built for '
            'market updates: median price, days on market, months of supply.'
        ),
        required=('stats',),
        limits={'stats': MAX_STATS, 'value': 24, 'label': 40},
    ),
    BlockSpec(
        type='steps',
        label='Numbered steps',
        description=(
            'Two to four numbered items, each with a bold line and an '
            'explanation. Use when the email asks the reader to do something '
            'in order. Numbering is added automatically.'
        ),
        required=('steps',),
        limits={'steps': MAX_STEPS, 'title': 90, 'text': 300},
    ),
    BlockSpec(
        type='callout',
        label='Callout box',
        description=(
            'A tinted box for the one detail the reader must not miss: an open '
            'house date and time, a deadline, an address.'
        ),
        required=('text',),
        optional=('label',),
        limits={'text': 300, 'label': 48},
    ),
    BlockSpec(
        type='quote',
        label='Quote',
        description='A testimonial or a pulled-out line.',
        required=('text',),
        optional=('attribution',),
        limits={'text': 400, 'attribution': 100},
    ),
    BlockSpec(
        type='divider',
        label='Divider',
        description='A hairline rule between sections.',
    ),
    BlockSpec(
        type='signature',
        label='Signature',
        description=(
            "The sending agent's name, title, phone, and brokerage, filled in "
            'automatically. Put it last.'
        ),
    ),
)

BLOCK_SPECS_BY_TYPE: dict[str, BlockSpec] = {s.type: s for s in BLOCK_SPECS}
BLOCK_TYPES: tuple[str, ...] = tuple(s.type for s in BLOCK_SPECS)

HEADING_LEVELS = ('h2', 'h3')

# Every field any block can carry. The AI schema is one flat object with all of
# these nullable rather than a discriminated union, because OpenAI strict mode
# requires every declared property to be listed as required; a union of ten
# variants is far more schema for the model to get wrong, and semantic checks
# happen in validate_blocks either way.
_ALL_FIELDS: tuple[str, ...] = (
    'type', 'text', 'level', 'items', 'label', 'url', 'image_url', 'alt',
    'caption', 'link_url', 'address', 'price', 'beds', 'baths', 'sqft',
    'stats', 'attribution', 'title', 'eyebrow', 'accent', 'steps',
)

_STRING_FIELDS = frozenset(_ALL_FIELDS) - {'items', 'stats', 'steps'}
_URL_FIELDS = ('url', 'image_url', 'link_url')

# Blocks that own the top of the email and cannot be repeated.
_SINGLETON_FIRST = 'hero'

# Single-value fields holding author copy. The compliance linter and
# ``collect_text`` both read this, so a new copy field is scanned by both or
# neither rather than silently by one.
SCANNED_FIELDS: tuple[str, ...] = (
    'text', 'label', 'alt', 'caption', 'address', 'attribution',
    'title', 'eyebrow', 'accent',
)


class BlockError(ValueError):
    """A template's blocks are not usable. The message is shown to the agent."""


def ai_generation_schema() -> dict:
    """Strict JSON schema for AI template generation.

    Shared with the MCP guidelines tool so an external agent authors against
    exactly the shape we validate.
    """
    return {
        'type': 'object',
        'additionalProperties': False,
        'required': ['subject', 'preheader', 'blocks'],
        'properties': {
            'subject': {
                'type': 'string',
                'description': (
                    'Subject line, under 60 characters so it is not truncated '
                    'on phones. Merge fields allowed.'
                ),
            },
            'preheader': {
                'type': 'string',
                'description': (
                    'The preview line shown after the subject in the inbox. '
                    'One sentence that adds to the subject rather than '
                    'repeating it.'
                ),
            },
            'blocks': {
                'type': 'array',
                'description': 'The email body, in order.',
                'items': {
                    'type': 'object',
                    'additionalProperties': False,
                    'required': list(_ALL_FIELDS),
                    'properties': {
                        'type': {
                            'type': 'string',
                            'enum': list(BLOCK_TYPES),
                            'description': 'Which kind of block this is.',
                        },
                        'text': {
                            'type': ['string', 'null'],
                            'description': 'Body copy for heading, paragraph, or quote.',
                        },
                        'level': {
                            'type': ['string', 'null'],
                            'enum': [*HEADING_LEVELS, None],
                            'description': 'Heading size. Defaults to h2.',
                        },
                        'items': {
                            'type': ['array', 'null'],
                            'items': {'type': 'string'},
                            'description': 'List entries for a bullets block.',
                        },
                        'label': {
                            'type': ['string', 'null'],
                            'description': 'Button text. Use a verb, not "Click here".',
                        },
                        'url': {
                            'type': ['string', 'null'],
                            'description': 'Button or listing destination.',
                        },
                        'image_url': {
                            'type': ['string', 'null'],
                            'description': (
                                'A URL previously returned by an image upload. '
                                'Never invent one.'
                            ),
                        },
                        'alt': {
                            'type': ['string', 'null'],
                            'description': 'What the image shows, for anyone who cannot see it.',
                        },
                        'caption': {
                            'type': ['string', 'null'],
                            'description': 'Small text under an image or listing.',
                        },
                        'link_url': {
                            'type': ['string', 'null'],
                            'description': 'Makes an image clickable.',
                        },
                        'address': {
                            'type': ['string', 'null'],
                            'description': 'Property address for a listing card.',
                        },
                        'price': {
                            'type': ['string', 'null'],
                            'description': 'Formatted price, for example $412,000.',
                        },
                        'beds': {'type': ['string', 'null']},
                        'baths': {'type': ['string', 'null']},
                        'sqft': {'type': ['string', 'null']},
                        'stats': {
                            'type': ['array', 'null'],
                            'items': {
                                'type': 'object',
                                'additionalProperties': False,
                                'required': ['value', 'label'],
                                'properties': {
                                    'value': {'type': 'string'},
                                    'label': {'type': 'string'},
                                },
                            },
                            'description': 'Figures for a stat_row block.',
                        },
                        'attribution': {
                            'type': ['string', 'null'],
                            'description': 'Who said it, for a quote block.',
                        },
                        'title': {
                            'type': ['string', 'null'],
                            'description': 'The large headline in a hero block.',
                        },
                        'eyebrow': {
                            'type': ['string', 'null'],
                            'description': (
                                'Two or three words above a hero headline, '
                                'shown in small uppercase.'
                            ),
                        },
                        'accent': {
                            'type': ['string', 'null'],
                            'description': (
                                'A second hero line, set in italics. Use it to '
                                'complete the thought the headline starts.'
                            ),
                        },
                        'steps': {
                            'type': ['array', 'null'],
                            'items': {
                                'type': 'object',
                                'additionalProperties': False,
                                'required': ['title', 'text'],
                                'properties': {
                                    'title': {'type': 'string'},
                                    'text': {'type': 'string'},
                                },
                            },
                            'description': 'Entries for a steps block.',
                        },
                    },
                },
            },
        },
    }


def describe_blocks_for_agent() -> list[dict]:
    """Human-readable block reference for the MCP guidelines tool."""
    out = []
    for spec in BLOCK_SPECS:
        out.append({
            'type': spec.type,
            'label': spec.label,
            'description': spec.description,
            'required_fields': list(spec.required),
            'optional_fields': list(spec.optional),
            'limits': dict(spec.limits),
        })
    return out


def is_safe_url(value: str) -> bool:
    stripped = (value or '').strip()
    lowered = stripped.lower()
    if lowered.startswith(ALLOWED_URL_SCHEMES):
        return True
    # Starter templates leave the destination as a bracketed placeholder.
    # A real send still refuses unfilled placeholders; this just lets the
    # library exist without a fake https URL in every listing email.
    return bool(re.fullmatch(r'\[[^\[\]]{2,60}\]', stripped))


def is_real_url(value: str) -> bool:
    """A clickable destination, not a bracketed 'fill this in later' token."""
    return (value or '').strip().lower().startswith(ALLOWED_URL_SCHEMES)


def missing_button_url_message(blocks: list[dict]) -> Optional[str]:
    """Why a template with a button cannot be saved yet, or None if it can."""
    for block in blocks or []:
        if block.get('type') != 'button':
            continue
        url = (block.get('url') or '').strip()
        label = (block.get('label') or 'button').strip() or 'button'
        if not is_real_url(url):
            return f'Add a URL for the "{label}" button before saving.'
    return None


def _clean_str(value: Any) -> str:
    if value is None:
        return ''
    if not isinstance(value, str):
        value = str(value)
    # Collapse the control characters that break table layout in Outlook.
    return value.replace('\r', '').replace('\x00', '').strip()


def _clean_pairs(value: Any, keys: tuple[str, str]) -> list[dict]:
    """Clean a list of two-field objects, dropping entries that are all empty."""
    if not isinstance(value, list):
        return []
    out: list[dict] = []
    for entry in value:
        if not isinstance(entry, dict):
            continue
        cleaned = {key: _clean_str(entry.get(key)) for key in keys}
        if any(cleaned.values()):
            out.append(cleaned)
    return out


def normalize_blocks(raw: Any) -> list[dict]:
    """Drop nulls and unknown keys, coerce to the canonical block shape.

    Models reliably emit every schema property with nulls in the ones they do
    not need, so stripping empties before validation keeps the error messages
    about real problems.
    """
    if not isinstance(raw, list):
        raise BlockError('Template content must be a list of blocks.')

    blocks: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            raise BlockError('Each block must be an object.')

        block_type = _clean_str(item.get('type')).lower()
        spec = BLOCK_SPECS_BY_TYPE.get(block_type)
        if spec is None:
            raise BlockError(
                f'Unknown block type {block_type or "(missing)"!r}. '
                f'Allowed: {", ".join(BLOCK_TYPES)}.'
            )

        block: dict[str, Any] = {'type': block_type}
        allowed = set(spec.required) | set(spec.optional)

        for key in allowed:
            value = item.get(key)
            if value is None:
                continue
            if key == 'items':
                cleaned = [_clean_str(v) for v in value if _clean_str(v)]
                if cleaned:
                    block['items'] = cleaned
            elif key == 'stats':
                cleaned_stats = _clean_pairs(value, ('value', 'label'))
                if cleaned_stats:
                    block['stats'] = cleaned_stats
            elif key == 'steps':
                cleaned_steps = _clean_pairs(value, ('title', 'text'))
                if cleaned_steps:
                    block['steps'] = cleaned_steps
            else:
                cleaned = _clean_str(value)
                if cleaned:
                    block[key] = cleaned

        if block_type == 'heading':
            level = block.get('level', '').lower()
            block['level'] = level if level in HEADING_LEVELS else HEADING_LEVELS[0]

        blocks.append(block)

    return blocks


def validate_blocks(raw: Any) -> list[dict]:
    """Normalize then check. Raises ``BlockError`` with an agent-facing message."""
    blocks = normalize_blocks(raw)

    if not blocks:
        raise BlockError('An email needs at least one block.')
    if len(blocks) > MAX_BLOCKS:
        raise BlockError(f'An email can hold at most {MAX_BLOCKS} blocks.')

    has_content = any(
        b['type'] not in ('divider', 'signature') for b in blocks
    )
    if not has_content:
        raise BlockError('An email needs some actual content, not only a divider or signature.')

    # The hero is a full-width banner rendered outside the padded content area,
    # so it only works at the very top. Enforced here rather than quietly moved,
    # because a model that put it in the middle meant something else.
    heroes = [i for i, b in enumerate(blocks) if b['type'] == _SINGLETON_FIRST]
    if len(heroes) > 1:
        raise BlockError('An email can only have one hero block.')
    if heroes and heroes[0] != 0:
        raise BlockError('A hero block has to be the first block in the email.')

    for index, block in enumerate(blocks, start=1):
        spec = BLOCK_SPECS_BY_TYPE[block['type']]
        where = f'Block {index} ({spec.label})'

        for name in spec.required:
            if not block.get(name):
                if block['type'] == 'button' and name == 'url':
                    label = block.get('label') or 'button'
                    raise BlockError(
                        f'Add a URL for the "{label}" button before saving.'
                    )
                raise BlockError(f'{where} is missing {name}.')

        if block['type'] == 'steps':
            for position, entry in enumerate(block.get('steps') or [], start=1):
                if not entry.get('title'):
                    raise BlockError(f'{where} step {position} is missing its title.')

        for name in _URL_FIELDS:
            value = block.get(name)
            # A bracketed placeholder is a legal draft value; the send path
            # refuses any template that still carries one.
            if value and not is_safe_url(value) and not is_placeholder(value):
                raise BlockError(
                    f'{where} has an unsupported link. Links must start with '
                    'http://, https://, mailto:, or tel:, or be a placeholder '
                    'in brackets to fill in later.'
                )

        for name, limit in spec.limits.items():
            if name == 'items':
                if len(block.get('items') or []) > limit:
                    raise BlockError(f'{where} can hold at most {limit} list items.')
            elif name == 'stats':
                if len(block.get('stats') or []) > limit:
                    raise BlockError(f'{where} can hold at most {limit} figures.')
            elif name == 'steps':
                if len(block.get('steps') or []) > limit:
                    raise BlockError(f'{where} can hold at most {limit} steps.')
            elif name == 'item':
                for entry in block.get('items') or []:
                    if len(entry) > limit:
                        raise BlockError(f'{where} has a list item over {limit} characters.')
            elif name in ('value', 'label') and block['type'] == 'stat_row':
                for entry in block.get('stats') or []:
                    if len(entry.get(name, '')) > limit:
                        raise BlockError(
                            f'{where} has a figure {name} over {limit} characters.'
                        )
            elif name in ('title', 'text') and block['type'] == 'steps':
                for entry in block.get('steps') or []:
                    if len(entry.get(name, '')) > limit:
                        raise BlockError(
                            f'{where} has a step {name} over {limit} characters.'
                        )
            elif name in _STRING_FIELDS:
                value = block.get(name) or ''
                if len(value) > limit:
                    raise BlockError(
                        f'{where} has {name} over {limit} characters '
                        f'({len(value)} given).'
                    )

    return blocks


# Bracketed placeholder, as in "Open house Saturday at [address]". Starter
# templates use them for the details only the agent can supply.
PLACEHOLDER_RE = re.compile(r'\[[^\[\]]{2,60}\]')
# A field that is nothing but a placeholder. URL fields allow this and only
# this: a link is either real or obviously unfilled, never half of each.
PLACEHOLDER_ONLY_RE = re.compile(r'^\[[^\[\]]{2,60}\]$')


def is_placeholder(value: str) -> bool:
    return bool(PLACEHOLDER_ONLY_RE.match((value or '').strip()))


def find_placeholders(blocks: list[dict], *extra_text: str) -> list[str]:
    """Unfilled placeholders, in the order they appear.

    Sending "[address]" to three hundred clients is the kind of mistake that is
    obvious afterwards and invisible beforehand, so the send path checks. Drafts
    are allowed to carry them; that is the whole point of a starter template.

    URL fields are included, because a button pointing at "[listing link]" is
    the most embarrassing version of this bug.
    """
    found: list[str] = []
    seen: set[str] = set()
    urls = [
        value for block in blocks for name in _URL_FIELDS
        if (value := block.get(name))
    ]
    haystack = '\n'.join([*(t for t in extra_text if t), collect_text(blocks), *urls])
    for match in PLACEHOLDER_RE.finditer(haystack):
        token = match.group(0)
        if token not in seen:
            seen.add(token)
            found.append(token)
    return found


def collect_text(blocks: list[dict]) -> str:
    """Every author-supplied string in one blob, for compliance scanning."""
    parts: list[str] = []
    for block in blocks:
        for key in SCANNED_FIELDS:
            value = block.get(key)
            if value:
                parts.append(value)
        for entry in block.get('items') or []:
            parts.append(entry)
        for entry in block.get('stats') or []:
            if entry.get('label'):
                parts.append(entry['label'])
        for entry in block.get('steps') or []:
            parts.extend(v for v in (entry.get('title'), entry.get('text')) if v)
    return '\n'.join(parts)


def insert_before_signature(blocks: list, extra: list) -> list:
    """Slide extra blocks in above the closing signature, if one exists."""
    extras = [block for block in extra if isinstance(block, dict)]
    if not extras:
        return list(blocks or [])
    out = list(blocks or [])
    for index in range(len(out) - 1, -1, -1):
        if out[index].get('type') == 'signature':
            return out[:index] + extras + out[index:]
    return out + extras
