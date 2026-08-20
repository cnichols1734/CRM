"""Personalization tokens, and the only code that fills them in.

Tokens look like ``{{contact.first_name}}`` or ``{{contact.first_name|there}}``,
where the part after the pipe is the fallback used when a contact has no value.

Substitution is a regex sweep over a closed registry, deliberately not Jinja.
Template copy is authored by agents and by a language model, so an expression
evaluator would be a server-side template injection surface in exchange for
features nobody asked for. An unknown token fails validation when the template
is saved rather than leaking braces into a real send.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Optional
import html as html_lib

# Two segments, lowercase and underscores only. The optional group after the
# pipe is the fallback and may contain anything except a closing brace.
TOKEN_RE = re.compile(r'\{\{\s*([a-z][a-z_]*\.[a-z][a-z_]*)\s*(?:\|([^}]*))?\}\}')


@dataclass(frozen=True)
class MergeField:
    key: str
    label: str
    description: str
    example: str
    # (contact, user, organization) -> value or None
    resolver: Callable[[Any, Any, Any], Optional[str]]
    # Fallback applied when the author supplied none. Empty string means the
    # token collapses to nothing, which is right for optional address parts and
    # wrong for a greeting, hence 'there' on first_name.
    default_fallback: str = ''


def _s(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _contact_full_name(contact, _user, _org):
    parts = [_s(getattr(contact, 'first_name', None)), _s(getattr(contact, 'last_name', None))]
    joined = ' '.join(p for p in parts if p)
    return joined or None


def _user_full_name(_contact, user, _org):
    if user is None:
        return None
    for attr in ('full_name', 'name'):
        value = _s(getattr(user, attr, None))
        if value:
            return value
    parts = [_s(getattr(user, 'first_name', None)), _s(getattr(user, 'last_name', None))]
    joined = ' '.join(p for p in parts if p)
    return joined or None


def _user_first_name(_contact, user, _org):
    if user is None:
        return None
    first = _s(getattr(user, 'first_name', None))
    if first:
        return first
    full = _user_full_name(None, user, None)
    return full.split(' ')[0] if full else None


MERGE_FIELDS: tuple[MergeField, ...] = (
    MergeField(
        key='contact.first_name',
        label='Contact first name',
        description="The recipient's first name.",
        example='John',
        resolver=lambda c, u, o: _s(getattr(c, 'first_name', None)),
        default_fallback='there',
    ),
    MergeField(
        key='contact.last_name',
        label='Contact last name',
        description="The recipient's last name.",
        example='Smith',
        resolver=lambda c, u, o: _s(getattr(c, 'last_name', None)),
    ),
    MergeField(
        key='contact.full_name',
        label='Contact full name',
        description="The recipient's full name.",
        example='John Smith',
        resolver=_contact_full_name,
    ),
    MergeField(
        key='contact.city',
        label='Contact city',
        description="The city on the recipient's record.",
        example='Houston',
        resolver=lambda c, u, o: _s(getattr(c, 'city', None)),
    ),
    MergeField(
        key='contact.state',
        label='Contact state',
        description="The state on the recipient's record.",
        example='TX',
        resolver=lambda c, u, o: _s(getattr(c, 'state', None)),
    ),
    MergeField(
        key='contact.zip',
        label='Contact ZIP',
        description="The ZIP code on the recipient's record.",
        example='78130',
        resolver=lambda c, u, o: _s(getattr(c, 'zip_code', None)),
    ),
    MergeField(
        key='contact.street_address',
        label='Contact street address',
        description="The street address on the recipient's record.",
        example='6048 Heritage Creek',
        resolver=lambda c, u, o: _s(getattr(c, 'street_address', None)),
    ),
    MergeField(
        key='agent.first_name',
        label='Agent first name',
        description='Your first name.',
        example='Suzie',
        resolver=_user_first_name,
    ),
    MergeField(
        key='agent.full_name',
        label='Agent full name',
        description='Your full name, as licensed.',
        example='Suzie Harrington',
        resolver=_user_full_name,
    ),
    MergeField(
        key='agent.email',
        label='Agent email',
        description='Your email address.',
        example='suzie@example.com',
        resolver=lambda c, u, o: _s(getattr(u, 'email', None)),
    ),
    MergeField(
        key='agent.phone',
        label='Agent phone',
        description='Your phone number.',
        example='(830) 555-0134',
        resolver=lambda c, u, o: _s(getattr(u, 'phone', None)),
    ),
    MergeField(
        key='agent.brokerage',
        label='Brokerage name',
        description='Your brokerage, from organization settings.',
        example='Origen Realty',
        resolver=lambda c, u, o: _s(getattr(o, 'broker_name', None)),
    ),
    MergeField(
        key='agent.license',
        label='Brokerage license number',
        description='Your brokerage license number, from organization settings.',
        example='9003104',
        resolver=lambda c, u, o: _s(getattr(o, 'broker_license_number', None)),
    ),
    MergeField(
        key='org.name',
        label='Organization name',
        description='Your organization name in AgentFlow.',
        example='Origen Realty',
        resolver=lambda c, u, o: _s(getattr(o, 'name', None)),
    ),
)

MERGE_FIELDS_BY_KEY: dict[str, MergeField] = {f.key: f for f in MERGE_FIELDS}
MERGE_FIELD_KEYS: tuple[str, ...] = tuple(f.key for f in MERGE_FIELDS)

# Fields with no sensible fallback: an email that greets "Hi ," or signs off
# from nobody is worse than one the recipient never receives, so a contact
# missing these is skipped instead.
REQUIRED_FOR_SEND: frozenset[str] = frozenset({'agent.full_name'})


class MergeFieldError(ValueError):
    """A template references a token we cannot fill. Shown to the agent."""


def extract_tokens(text: str) -> list[tuple[str, Optional[str]]]:
    """Every ``(key, fallback)`` pair in the text, in order of appearance."""
    if not text:
        return []
    return [(m.group(1), m.group(2)) for m in TOKEN_RE.finditer(text)]


def extract_keys(text: str) -> set[str]:
    return {key for key, _ in extract_tokens(text)}


def unknown_keys(text: str) -> set[str]:
    return extract_keys(text) - set(MERGE_FIELD_KEYS)


def validate_text(text: str, *, where: str = 'This template') -> None:
    unknown = unknown_keys(text)
    if unknown:
        listed = ', '.join(sorted(unknown))
        raise MergeFieldError(
            f'{where} uses merge fields that do not exist: {listed}. '
            f'Available: {", ".join(MERGE_FIELD_KEYS)}.'
        )


def resolve_values(contact, user, organization) -> dict[str, Optional[str]]:
    """Every token's value for one recipient. Resolver errors read as missing."""
    values: dict[str, Optional[str]] = {}
    for field in MERGE_FIELDS:
        try:
            values[field.key] = field.resolver(contact, user, organization)
        except Exception:
            values[field.key] = None
    return values


def sample_values() -> dict[str, str]:
    """Example values, for template preview before a recipient is chosen."""
    return {f.key: f.example for f in MERGE_FIELDS}


def studio_sample_values(user=None, organization=None) -> dict[str, str]:
    """Preview defaults: fictional contact, real agent and brokerage when known."""
    values = sample_values()
    live = resolve_values(None, user, organization)
    for key, value in live.items():
        if key.startswith('contact.') or not value:
            continue
        values[key] = value
    return values


def coerce_sample_values(raw, base: Optional[dict[str, str]] = None) -> dict[str, str]:
    """Take a posted preview map and keep only known keys.

    Blank posted values leave the example in place so a sample-data preview
    still looks finished when the agent has not typed over a field.
    """
    values = dict(base or sample_values())
    if not isinstance(raw, dict):
        return values
    for key, value in raw.items():
        if key not in MERGE_FIELDS_BY_KEY or not isinstance(value, str):
            continue
        stripped = value.strip()[:120]
        if stripped:
            values[key] = stripped
    return values


def short_label(field: MergeField) -> str:
    """Picker / chip label, without the Contact / Agent / Organization prefix."""
    label = field.label
    for prefix in ('Contact ', 'Agent ', 'Organization '):
        if label.startswith(prefix):
            label = label[len(prefix):]
            break
    return label[:1].upper() + label[1:] if label else label


def chip_label(key: str) -> str:
    field = MERGE_FIELDS_BY_KEY.get(key)
    return short_label(field) if field else key


def used_keys(subject: str = '', preheader: str = '', blocks: Optional[list] = None) -> set[str]:
    """Merge keys referenced in subject, inbox line, or block copy."""
    parts = [subject or '', preheader or '']
    for block in blocks or []:
        if not isinstance(block, dict):
            continue
        for value in block.values():
            if isinstance(value, str):
                parts.append(value)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, str):
                        parts.append(item)
                    elif isinstance(item, dict):
                        parts.extend(str(v) for v in item.values() if v)
    return extract_keys('\n'.join(parts))


TOKEN_CHIP_STYLE = (
    'background-color:#fff7ed;color:#c2410c;border:1px solid #fdba74;'
    'border-radius:4px;padding:1px 6px;font-weight:700;white-space:nowrap'
)


def wrap_tokens_for_preview(html: str) -> str:
    """Mark merge tokens as chips that show the field name.

    Tokens inside HTML tags (href, src) are left alone so we do not break
    attributes. The chip keeps the key and fallback; visible text is the
    short field name until a sample-data pass fills it. Token styles are
    inline because this HTML is an email document — a ``<style>`` rule
    that never applied is how they used to vanish into body copy.
    """
    if not html:
        return html

    def wrap_match(match: re.Match) -> str:
        key = match.group(1)
        fallback = match.group(2)
        fallback_attr = html_lib.escape(fallback, quote=True) if fallback else ''
        extra = f' data-mkt-fallback="{fallback_attr}"' if fallback else ''
        return (
            f'<span contenteditable="false" data-mkt-merge="{html_lib.escape(key, quote=True)}"'
            f'{extra}'
            f' style="{TOKEN_CHIP_STYLE}">'
            f'{html_lib.escape(chip_label(key))}</span>'
        )

    parts = re.split(r'(<[^>]+>)', html)
    out: list[str] = []
    for part in parts:
        if part.startswith('<'):
            out.append(part)
        else:
            out.append(TOKEN_RE.sub(wrap_match, part))
    return ''.join(out)


_CHIP_RE = re.compile(
    r'(<span\b[^>]*\bdata-mkt-merge="([^"]+)"[^>]*>)([^<]*)(</span>)',
    re.IGNORECASE,
)


def fill_preview_chips(html: str, values: dict[str, str]) -> str:
    """Replace chip labels with sample values. Leaves the token identity in attributes."""
    if not html:
        return html

    def repl(match: re.Match) -> str:
        key = match.group(2)
        value = (values.get(key) or '').strip()
        if not value:
            field = MERGE_FIELDS_BY_KEY.get(key)
            value = field.example if field else chip_label(key)
        open_tag = match.group(1)
        open_tag = re.sub(r'\sstyle="[^"]*"', '', open_tag)
        if 'data-mkt-filled' not in open_tag:
            open_tag = open_tag[:-1] + ' data-mkt-filled="1">'
        return f'{open_tag}{html_lib.escape(value)}{match.group(4)}'

    return _CHIP_RE.sub(repl, html)


def substitute(
    text: str,
    values: dict[str, Optional[str]],
    *,
    escape: Optional[Callable[[str], str]] = None,
) -> tuple[str, set[str]]:
    """Fill tokens from ``values``.

    Returns the filled text and the set of keys that had no value and no usable
    fallback. Callers decide what an empty required field means; the sender
    treats it as a reason to skip the recipient.

    ``escape`` must be supplied when filling an HTML document. Rendering escapes
    author copy, but substitution happens afterwards, so a contact whose name
    contains markup would otherwise land in the document unescaped.
    """
    if not text:
        return '', set()

    missing: set[str] = set()

    def replace(match: re.Match) -> str:
        key = match.group(1)
        author_fallback = match.group(2)
        field = MERGE_FIELDS_BY_KEY.get(key)

        if field is None:
            # Validation runs before save, so reaching here means a stored
            # template predates a registry change. Leaving the raw token in a
            # real email is worse than dropping it.
            missing.add(key)
            return ''

        value = values.get(key)
        if not value:
            fallback = (
                author_fallback if author_fallback is not None
                else field.default_fallback
            )
            value = (fallback or '').strip()
            if not value:
                missing.add(key)
                return ''

        return escape(value) if escape else value

    return TOKEN_RE.sub(replace, text), missing


def describe_for_agent() -> list[dict]:
    """Registry reference for the UI's merge-field picker and the MCP guidelines tool."""
    return [
        {
            'token': '{{' + f.key + '}}',
            'key': f.key,
            'label': f.label,
            'description': f.description,
            'example': f.example,
            'default_fallback': f.default_fallback or None,
            'required': f.key in REQUIRED_FOR_SEND,
        }
        for f in MERGE_FIELDS
    ]
