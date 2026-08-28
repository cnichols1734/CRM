"""The offer summary an agent emails to their own client.

One button on an offer produces a finished email: the price and the handful of
terms a seller or buyer actually asks about, written as sentences instead of
contract fields. Figures are read off the saved offer row when the draft is
built, so a summary written after a terms edit carries the new numbers.

Two shapes. A single offer reads as a short note with the price up top. Several
offers read as the compare matrix from the offers screen, trimmed to the rows a
client can act on.

The agent owns the wording. Every piece of prose and every displayed figure in
``OfferEmailDraft`` can be overridden before the send, and the composer sends
those overrides back through :func:`build_draft` to repaint the preview.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Optional, Sequence

from flask import render_template

from services.email_send_guard import skip_outbound_send
from services.offer_side import side_for_transaction

logger = logging.getLogger(__name__)

# Rows a client is asked to make a decision about. Deliberately shorter than the
# internal compare matrix: title policy payers and survey language belong in the
# contract review, not in the note that explains the offer.
CLIENT_TERMS: tuple[tuple[str, str], ...] = (
    ('offer_price', 'Price'),
    ('financing_type', 'Financing'),
    ('earnest_money', 'Earnest money'),
    ('option_period', 'Option period'),
    ('seller_concessions_amount', 'Seller concessions'),
    ('proposed_close_date', 'Closing date'),
)

_UNKNOWN_MATTERS = frozenset({
    'offer_price', 'financing_type', 'earnest_money', 'proposed_close_date',
})

NET_ROW_KEY = 'estimated_net'
NET_ROW_LABEL = 'Estimated net to you'
NET_CAVEAT = (
    'Estimated net uses the costs we know today. It is not a settlement statement.'
)
MISSING_TERMS_NOTE = (
    "Anything not listed here is still open. I'll send an update once I have it."
)

# Missing figures used to render as an em dash. Keep those tokens empty so
# a preview saved before the copy change still parses.
EMPTY_TERM_DISPLAY = 'Not set'
_EMPTY_TERM_TOKENS = frozenset({
    '',
    '-',
    'not set',
    '\u2014',
    '\u2013',
})

# Offers that no longer need a client decision. A summary of a withdrawn offer
# only confuses the person reading it.
_INACTIVE_STATUSES = frozenset({
    'declined', 'withdrawn', 'expired', 'replaced',
})

_CLIENT_ROLES = {
    'seller': ('seller', 'co_seller'),
    'buyer': ('buyer', 'co_buyer'),
}

_TERM_ALIASES = {
    'offer_price': ('offer_price', 'sales_price', 'purchase_price'),
    'financing_type': ('financing_type', 'loan_type'),
    'earnest_money': ('earnest_money',),
    'option_fee': ('option_fee',),
    'option_period_days': ('option_period_days', 'option_days'),
    'seller_concessions_amount': ('seller_concessions_amount', 'seller_concessions'),
    'proposed_close_date': ('proposed_close_date', 'closing_date', 'close_date'),
}

_FINANCING_LABEL = {
    'cash': 'Cash',
    'conventional': 'Conventional loan',
    'fha': 'FHA loan',
    'va': 'VA loan',
    'usda': 'USDA loan',
    'texasveterans': 'Texas Veterans loan',
    'reversemortgage': 'Reverse mortgage',
    'sellerfinancing': 'Seller financing',
}


# ---------------------------------------------------------------------------
# Draft model
# ---------------------------------------------------------------------------

@dataclass
class TermCell:
    """One displayed figure. ``edited`` means the agent typed over ours."""

    key: str
    value: str
    edited: bool = False
    wins: bool = False


@dataclass
class OfferBlock:
    offer_id: int
    label: str
    sublabel: Optional[str]
    status: str
    cells: dict[str, TermCell] = field(default_factory=dict)

    def value(self, key: str) -> str:
        cell = self.cells.get(key)
        if cell is None or _is_empty_term(cell.value):
            return EMPTY_TERM_DISPLAY
        return cell.value


@dataclass
class Recipient:
    name: Optional[str]
    email: str
    role: Optional[str] = None


@dataclass
class OfferEmailDraft:
    mode: str
    side: str
    subject: str
    preheader: str
    greeting: str
    intro: str
    note: str
    closing: str
    property_label: str
    offers: list[OfferBlock]
    row_specs: list[dict[str, str]]
    signature: dict[str, Optional[str]]
    brand: dict[str, Optional[str]]
    recipients: list[Recipient]
    headline: Optional[dict[str, str]] = None
    include_net: bool = False
    net_available: bool = False
    footnote: Optional[str] = None
    offer_ids: list[int] = field(default_factory=list)

    @property
    def eyebrow(self) -> str:
        return 'Offer comparison' if self.mode == 'compare' else 'Offer summary'

    @property
    def has_winners(self) -> bool:
        return any(
            cell.wins for block in self.offers for cell in block.cells.values()
        )

    def as_payload(self) -> dict[str, Any]:
        """What the composer needs to render its own form fields."""
        return {
            'mode': self.mode,
            'side': self.side,
            'subject': self.subject,
            'preheader': self.preheader,
            'greeting': self.greeting,
            'intro': self.intro,
            'note': self.note,
            'closing': self.closing,
            'property_label': self.property_label,
            'include_net': self.include_net,
            'net_available': self.net_available,
            'offer_ids': list(self.offer_ids),
            'row_specs': list(self.row_specs),
            'recipients': [
                {'name': r.name, 'email': r.email, 'role': r.role}
                for r in self.recipients
            ],
            'offers': [
                {
                    'offer_id': block.offer_id,
                    'label': block.label,
                    'sublabel': block.sublabel,
                    'status': block.status,
                    'terms': {
                        key: {'value': cell.value, 'edited': cell.edited}
                        for key, cell in block.cells.items()
                    },
                }
                for block in self.offers
            ],
        }


# ---------------------------------------------------------------------------
# Building the draft
# ---------------------------------------------------------------------------

def build_draft(
    transaction,
    offers: Sequence,
    *,
    agent=None,
    organization=None,
    side: Optional[str] = None,
    net_sheets: Optional[dict[int, Any]] = None,
    overrides: Optional[dict[str, Any]] = None,
) -> OfferEmailDraft:
    """Assemble the email for one or more offers on ``transaction``."""
    overrides = overrides or {}
    resolved_side = side or side_for_transaction(transaction) or 'seller'
    ordered = _ordered_offers(offers)
    if not ordered:
        raise ValueError('Pick at least one offer to summarize.')

    mode = 'compare' if len(ordered) > 1 else 'single'
    property_label = _property_label(transaction)
    net_sheets = net_sheets or {}
    net_available = resolved_side == 'seller' and any(
        _net_value(net_sheets.get(offer.id)) is not None for offer in ordered
    )
    include_net = _flag(overrides.get('include_net'), default=net_available) and net_available

    term_overrides = overrides.get('terms') or {}
    blocks = [
        _offer_block(
            offer,
            net_sheet=net_sheets.get(offer.id),
            include_net=include_net,
            overrides=_offer_overrides(term_overrides, offer.id),
        )
        for offer in ordered
    ]
    row_specs = _row_specs(blocks, mode=mode, include_net=include_net)
    if resolved_side == 'seller':
        _mark_winners(blocks, ordered, net_sheets, include_net=include_net)

    headline = _headline(blocks[0]) if mode == 'single' else None
    generated = _generated_copy(
        mode=mode,
        side=resolved_side,
        property_label=property_label,
        blocks=blocks,
        offers=ordered,
    )

    return OfferEmailDraft(
        mode=mode,
        side=resolved_side,
        subject=_override(overrides, 'subject', generated['subject']),
        preheader=_override(overrides, 'preheader', generated['preheader']),
        greeting=_override(overrides, 'greeting', _greeting(transaction, resolved_side)),
        intro=_override(overrides, 'intro', generated['intro']),
        note=_text(overrides.get('note')) or '',
        closing=_override(overrides, 'closing', generated['closing']),
        property_label=property_label,
        offers=blocks,
        row_specs=row_specs,
        signature=_signature(agent, organization),
        brand=_brand(organization),
        recipients=resolve_recipients(transaction, resolved_side),
        headline=headline,
        include_net=include_net,
        net_available=net_available,
        footnote=_footnote(blocks),
        offer_ids=[offer.id for offer in ordered],
    )


def selectable_offers(offers: Iterable) -> list:
    """Offers still worth summarizing for a client."""
    return [
        offer for offer in offers
        if (getattr(offer, 'status', '') or '') not in _INACTIVE_STATUSES
    ]


def resolve_recipients(transaction, side: str) -> list[Recipient]:
    """The agent's own client on this transaction, plus any co-client."""
    roles = _CLIENT_ROLES.get(side, ())
    found: list[Recipient] = []
    seen: set[str] = set()
    for participant in _participants(transaction):
        if (getattr(participant, 'role', None) or '') not in roles:
            continue
        email = _participant_email(participant)
        if not email or email.lower() in seen:
            continue
        seen.add(email.lower())
        found.append(Recipient(
            name=_participant_name(participant),
            email=email,
            role=getattr(participant, 'role', None),
        ))
    return found


def _ordered_offers(offers: Sequence) -> list:
    """Highest price first, so the strongest offer leads the matrix."""
    ordered = [offer for offer in offers if offer is not None]

    def sort_key(offer):
        price = _decimal(_pick(offer, 'offer_price'))
        return (0 if price is None else 1, price or Decimal(0))

    return sorted(ordered, key=sort_key, reverse=True)


def _offer_block(offer, *, net_sheet, include_net: bool, overrides: dict) -> OfferBlock:
    generated = {
        'offer_price': _money(_pick(offer, 'offer_price')),
        'financing_type': _financing(_pick(offer, 'financing_type')),
        'earnest_money': _money(_pick(offer, 'earnest_money')),
        'option_period': _option_period(offer),
        'seller_concessions_amount': _money(_pick(offer, 'seller_concessions_amount')),
        'proposed_close_date': _long_date(_pick(offer, 'proposed_close_date')),
    }
    if include_net:
        # An estimate carrying cents invites arithmetic the client can't win.
        generated[NET_ROW_KEY] = _money(_net_value(net_sheet), whole=True)

    cells: dict[str, TermCell] = {}
    for key, produced in generated.items():
        supplied = _text(overrides.get(key))
        if _is_empty_term(supplied):
            supplied = None
        if supplied is not None and supplied != (produced or ''):
            cells[key] = TermCell(key=key, value=supplied, edited=True)
        elif produced:
            cells[key] = TermCell(key=key, value=produced)

    return OfferBlock(
        offer_id=offer.id,
        label=_offer_label(offer),
        sublabel=_offer_sublabel(offer),
        status=getattr(offer, 'status', '') or '',
        cells=cells,
    )


def _row_specs(blocks: list[OfferBlock], *, mode: str, include_net: bool) -> list[dict[str, str]]:
    """Rows with something to say. An all-blank row is noise in a client email."""
    specs = list(CLIENT_TERMS)
    if include_net:
        specs = specs + [(NET_ROW_KEY, NET_ROW_LABEL)]

    skip = {'offer_price', 'financing_type'} if mode == 'single' else set()
    rows = []
    for key, label in specs:
        if key in skip:
            continue
        if any(key in block.cells for block in blocks):
            rows.append({'key': key, 'label': label})
    return rows


def _mark_winners(blocks, offers, net_sheets, *, include_net: bool) -> None:
    by_id = {offer.id: offer for offer in offers}
    if len(blocks) < 2:
        return

    _mark_best(
        blocks,
        'offer_price',
        {b.offer_id: _decimal(_pick(by_id[b.offer_id], 'offer_price')) for b in blocks},
        highest=True,
    )
    _mark_best(
        blocks,
        'proposed_close_date',
        {b.offer_id: _as_date(_pick(by_id[b.offer_id], 'proposed_close_date')) for b in blocks},
        highest=False,
    )
    if include_net:
        _mark_best(
            blocks,
            NET_ROW_KEY,
            {b.offer_id: _net_value(net_sheets.get(b.offer_id)) for b in blocks},
            highest=True,
        )


def _mark_best(blocks, key: str, values: dict, *, highest: bool) -> None:
    """Flag the leading cell, but only when the values actually differ."""
    known = {oid: v for oid, v in values.items() if v is not None}
    if len(known) < 2 or len(set(known.values())) < 2:
        return
    best = max(known.values()) if highest else min(known.values())
    for block in blocks:
        cell = block.cells.get(key)
        if cell and not cell.edited and known.get(block.offer_id) == best:
            cell.wins = True


def _headline(block: OfferBlock) -> dict[str, str]:
    price = block.cells.get('offer_price')
    financing = block.cells.get('financing_type')
    return {
        'value': price.value if price else 'Price not set yet',
        'caption': financing.value if financing else '',
    }


def _footnote(blocks) -> Optional[str]:
    """Say so when a term is unknown rather than letting a gap imply zero.

    Only the terms whose absence means "we don't know yet". No concessions and
    no option period usually mean the buyer asked for neither, and calling that
    an open item would make every clean offer look incomplete.
    """
    if any(_UNKNOWN_MATTERS - set(block.cells) for block in blocks):
        return MISSING_TERMS_NOTE
    return None


# ---------------------------------------------------------------------------
# Copy
# ---------------------------------------------------------------------------

def _generated_copy(*, mode: str, side: str, property_label: str, blocks, offers) -> dict[str, str]:
    where = property_label or 'the property'
    if mode == 'single':
        return _single_copy(side, where, blocks[0], offers[0])
    return _compare_copy(side, where, blocks)


def _single_copy(side: str, where: str, block: OfferBlock, offer) -> dict[str, str]:
    price = block.cells.get('offer_price')
    lead = 'Your offer' if side == 'buyer' else 'New offer'
    subject = f'{lead} on {where}'
    if price:
        subject = f'{subject}: {price.value}'

    detail = []
    if 'financing_type' in block.cells:
        detail.append(block.value('financing_type'))
    if 'proposed_close_date' in block.cells:
        detail.append(f"closing {block.value('proposed_close_date')}")
    preheader = ', '.join(detail) if detail else f'{lead} on {where}.'

    counterparty = _text(getattr(offer, 'buyer_names', None))
    if side == 'buyer':
        intro = f"Here's the offer we submitted on {where}."
        closing = "I'll let you know as soon as we hear back."
    else:
        intro = (
            f'We received an offer on {where} from {counterparty}.'
            if counterparty else f'We received an offer on {where}.'
        )
        closing = "Tell me how you'd like to respond and I'll take it from there."
    return {
        'subject': subject,
        'preheader': preheader,
        'intro': intro,
        'closing': closing,
    }


def _compare_copy(side: str, where: str, blocks) -> dict[str, str]:
    count = len(blocks)
    prices = [
        _decimal_from_display(block.value('offer_price'))
        for block in blocks
        if 'offer_price' in block.cells
    ]
    prices = [p for p in prices if p is not None]
    subject = f'{count} offers on {where}'
    if len(prices) >= 2 and min(prices) != max(prices):
        subject = f'{subject}: {_money(min(prices))} to {_money(max(prices))}'
    elif prices:
        subject = f'{subject}: {_money(prices[0])}'

    if side == 'buyer':
        intro = f'Here are the {count} offers we have in on {where}, side by side.'
        closing = "Tell me which one you want to push on and I'll handle it."
    else:
        intro = f'You have {count} offers on {where}. Here they are side by side.'
        closing = (
            "Let me know which one you're leaning toward and I'll walk you "
            'through what it means for you.'
        )
    return {
        'subject': subject,
        'preheader': 'What each buyer is offering, side by side.',
        'intro': intro,
        'closing': closing,
    }


def _greeting(transaction, side: str) -> str:
    names = _client_first_names(transaction, side)
    if not names:
        return 'Hi,'
    if len(names) == 1:
        return f'Hi {names[0]},'
    return f'Hi {names[0]} and {names[1]},'


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def render_html(draft: OfferEmailDraft) -> str:
    return render_template(
        'email/offer_summary.html',
        draft=draft,
        net_caveat=NET_CAVEAT,
        year=date.today().year,
    )


def render_text(draft: OfferEmailDraft) -> str:
    """Plain-text alternative. Mail without one looks like bulk mail."""
    lines = [draft.greeting, '', draft.intro, '']

    if draft.mode == 'single':
        block = draft.offers[0]
        if draft.headline:
            headline = draft.headline['value']
            if draft.headline.get('caption'):
                headline = f"{headline} · {draft.headline['caption']}"
            lines.append(headline)
        for spec in draft.row_specs:
            lines.append(f"{spec['label']}: {block.value(spec['key'])}")
    else:
        for block in draft.offers:
            heading = block.label
            if block.sublabel:
                heading = f'{heading} ({block.sublabel})'
            lines.append(heading)
            for spec in draft.row_specs:
                lines.append(f"  {spec['label']}: {block.value(spec['key'])}")
            lines.append('')

    if draft.include_net:
        lines.extend(['', NET_CAVEAT])
    if draft.footnote:
        lines.extend(['', draft.footnote])
    if draft.note:
        lines.extend(['', draft.note])
    lines.extend(['', draft.closing, ''])

    for key in ('name', 'brokerage', 'phone', 'email'):
        value = draft.signature.get(key)
        if value:
            lines.append(value)
    return '\n'.join(lines).strip() + '\n'


# ---------------------------------------------------------------------------
# Delivery
# ---------------------------------------------------------------------------

class OfferEmailError(Exception):
    """Raised when the send cannot be attempted or the provider refused it."""


def resolve_sender(agent=None) -> dict[str, Any]:
    """Where the client will see the email come from."""
    from config import Config

    integration = _gmail_integration(agent)
    if integration is not None:
        return {
            'via': 'gmail',
            'from_email': integration.connected_email,
            'connected': True,
        }
    return {
        'via': 'sendgrid',
        'from_email': Config.CLIENT_EMAIL_FROM_EMAIL,
        'connected': False,
    }


def send_draft(
    draft: OfferEmailDraft,
    *,
    to_emails: Sequence[str],
    cc_emails: Sequence[str] = (),
    agent=None,
    organization=None,
    transaction_id: Optional[int] = None,
) -> dict[str, Any]:
    """Send the rendered draft. Gmail if the agent has it linked, else SendGrid."""
    recipients = [addr for addr in (_clean_email(e) for e in to_emails) if addr]
    if not recipients:
        raise OfferEmailError('Add at least one valid recipient email address.')
    copies = [
        addr for addr in (_clean_email(e) for e in cc_emails)
        if addr and addr not in recipients
    ]

    subject = _text(draft.subject)
    if not subject:
        raise OfferEmailError('Give the email a subject line.')

    html = render_html(draft)
    text = render_text(draft)
    sender = resolve_sender(agent)

    if skip_outbound_send(recipients[0]):
        return {
            'sent': False,
            'skipped': True,
            'recipients': recipients,
            'cc': copies,
            'via': sender['via'],
            'from_email': sender['from_email'],
        }

    if sender['via'] == 'gmail':
        message_id = _send_via_gmail(
            _gmail_integration(agent),
            html=html,
            subject=subject,
            recipients=recipients,
            copies=copies,
            transaction_id=transaction_id,
        )
    else:
        message_id = _send_via_sendgrid(
            html=html,
            text=text,
            subject=subject,
            recipients=recipients,
            copies=copies,
            agent=agent,
            organization=organization,
            transaction_id=transaction_id,
            offer_count=len(draft.offer_ids),
        )

    return {
        'sent': True,
        'skipped': False,
        'recipients': recipients,
        'cc': copies,
        'via': sender['via'],
        'from_email': sender['from_email'],
        'message_id': message_id,
    }


def _gmail_integration(agent):
    """The agent's live Gmail link, or None if we should not send through it."""
    if agent is None:
        return None
    integration = getattr(agent, 'email_integration', None)
    if integration is None:
        return None
    if not getattr(integration, 'sync_enabled', False):
        return None
    if not _clean_email(getattr(integration, 'connected_email', None)):
        return None
    if getattr(integration, 'needs_reauth', False):
        return None
    return integration


def _send_via_gmail(integration, *, html, subject, recipients, copies, transaction_id):
    from services.gmail_service import send_email

    result = send_email(
        integration,
        to_emails=list(recipients),
        subject=subject,
        body_html=html,
        cc_emails=list(copies) or None,
        include_signature=False,
    )
    if result.get('needs_reauth'):
        raise OfferEmailError('Reconnect Gmail in Profile, then send this again.')
    if not result.get('success'):
        logger.warning(
            'Offer summary Gmail send failed transaction=%s error=%s',
            transaction_id, result.get('error'),
        )
        raise OfferEmailError(
            result.get('error') or 'Gmail could not send the email.'
        )
    return result.get('message_id')


def _send_via_sendgrid(
    *, html, text, subject, recipients, copies, agent, organization,
    transaction_id, offer_count,
):
    from config import Config
    from sendgrid import SendGridAPIClient
    from sendgrid.helpers.mail import Cc, CustomArg, Email, Mail, To

    api_key = Config.SENDGRID_API_KEY
    if not api_key:
        raise OfferEmailError(
            'Connect Gmail in Profile, or add SENDGRID_API_KEY, to send this.'
        )

    message = Mail(
        from_email=Email(
            Config.CLIENT_EMAIL_FROM_EMAIL,
            _from_name(agent, organization),
        ),
        to_emails=[To(addr) for addr in recipients],
        subject=subject,
        html_content=html,
        plain_text_content=text,
    )
    reply_to = _clean_email(getattr(agent, 'email', None))
    if reply_to:
        message.reply_to = Email(reply_to, _agent_name(agent))
    for addr in copies:
        message.add_cc(Cc(addr))
    for key, value in (
        ('kind', 'offer_summary'),
        ('transaction_id', transaction_id),
        ('offer_count', offer_count),
    ):
        if value is not None:
            message.add_custom_arg(CustomArg(str(key), str(value)))

    try:
        response = SendGridAPIClient(api_key).send(message)
    except Exception as exc:
        logger.exception(
            'Offer summary send failed transaction=%s from=%s',
            transaction_id, Config.CLIENT_EMAIL_FROM_EMAIL,
        )
        raise OfferEmailError(_sendgrid_reason(exc)) from exc

    if response.status_code not in (200, 201, 202):
        logger.warning(
            'Offer summary send non-2xx status=%s body=%r',
            response.status_code, getattr(response, 'body', None),
        )
        raise OfferEmailError(
            f'The email provider refused the send (status {response.status_code}).'
        )

    headers = getattr(response, 'headers', None) or {}
    return headers.get('X-Message-Id') or headers.get('X-Message-ID') or None


def _sendgrid_reason(exc: Exception) -> str:
    """Turn a provider rejection into something an agent can act on."""
    status = getattr(exc, 'status_code', None)
    body = getattr(exc, 'body', None)
    detail = ''
    if body:
        try:
            detail = body.decode('utf-8') if isinstance(body, bytes) else str(body)
        except Exception:
            detail = ''
    if status == 403 and 'verified Sender Identity' in detail:
        from config import Config
        return (
            f'{Config.CLIENT_EMAIL_FROM_EMAIL} is not a verified sender in '
            'SendGrid yet, so the email was not sent.'
        )
    if status == 401:
        return 'The email provider rejected our credentials.'
    return 'The email could not be sent. Try again in a minute.'


def _from_name(agent, organization) -> str:
    """The client sees their agent, on behalf of the brokerage."""
    name = _agent_name(agent)
    brokerage = _brokerage_name(organization)
    if name and brokerage:
        return f'{name} | {brokerage}'
    return name or brokerage or 'Your agent'


# ---------------------------------------------------------------------------
# Reading offers and people
# ---------------------------------------------------------------------------

def _pick(offer, key: str):
    """Canonical column wins; ``terms_summary`` only fills a gap."""
    column = getattr(offer, key, None)
    if not _blank(column):
        return column
    summary = getattr(offer, 'terms_summary', None)
    if not isinstance(summary, dict):
        return None
    for alias in _TERM_ALIASES.get(key, (key,)):
        if not _blank(summary.get(alias)):
            return summary.get(alias)
    return None


def _offer_label(offer) -> str:
    return (
        _text(getattr(offer, 'buyer_names', None))
        or _text(getattr(offer, 'buyer_agent_name', None))
        or f'Offer {offer.id}'
    )


def _offer_sublabel(offer) -> Optional[str]:
    agent = _text(getattr(offer, 'buyer_agent_name', None))
    brokerage = _text(getattr(offer, 'buyer_agent_brokerage', None))
    if agent and _text(getattr(offer, 'buyer_names', None)):
        return f'{agent} · {brokerage}' if brokerage else agent
    return brokerage


def _property_label(transaction) -> str:
    street = _text(getattr(transaction, 'street_address', None))
    if street:
        return street
    return _text(getattr(transaction, 'full_address', None)) or ''


def _participants(transaction) -> list:
    participants = getattr(transaction, 'participants', None)
    if participants is None:
        return []
    if hasattr(participants, 'all'):
        return list(participants.all())
    return list(participants)


def _participant_email(participant) -> Optional[str]:
    direct = _clean_email(getattr(participant, 'email', None))
    if direct:
        return direct
    contact = getattr(participant, 'contact', None)
    return _clean_email(getattr(contact, 'email', None)) if contact else None


def _participant_name(participant) -> Optional[str]:
    display = getattr(participant, 'display_name', None)
    return _text(display) or _text(getattr(participant, 'name', None))


def _client_first_names(transaction, side: str) -> list[str]:
    roles = _CLIENT_ROLES.get(side, ())
    primary: list[str] = []
    others: list[str] = []
    for participant in _participants(transaction):
        if (getattr(participant, 'role', None) or '') not in roles:
            continue
        name = _participant_name(participant)
        if not name:
            continue
        first = name.split()[0]
        if getattr(participant, 'is_primary', False):
            primary.append(first)
        else:
            others.append(first)
    ordered = primary + others
    deduped: list[str] = []
    for name in ordered:
        if name not in deduped:
            deduped.append(name)
    return deduped[:2]


def _signature(agent, organization) -> dict[str, Optional[str]]:
    return {
        'name': _agent_name(agent),
        'brokerage': _brokerage_name(organization),
        'phone': _text(getattr(agent, 'phone', None)),
        'email': _clean_email(getattr(agent, 'email', None)),
    }


def _brand(organization) -> dict[str, Optional[str]]:
    """Marks for the masthead and footer bands.

    Shared with marketing mail so both use the same slate-band assets.
    """
    from services.email_chrome import brand_assets

    return brand_assets(organization)


def _agent_name(agent) -> Optional[str]:
    if agent is None:
        return None
    parts = [
        _text(getattr(agent, 'first_name', None)),
        _text(getattr(agent, 'last_name', None)),
    ]
    joined = ' '.join(part for part in parts if part)
    return joined or _text(getattr(agent, 'username', None))


def _brokerage_name(organization) -> Optional[str]:
    if organization is None:
        return None
    return (
        _text(getattr(organization, 'broker_name', None))
        or _text(getattr(organization, 'name', None))
    )


def _net_value(net_sheet):
    return getattr(net_sheet, 'estimated_net', None) if net_sheet else None


def _offer_overrides(term_overrides: Any, offer_id: int) -> dict:
    if not isinstance(term_overrides, dict):
        return {}
    for key in (offer_id, str(offer_id)):
        candidate = term_overrides.get(key)
        if isinstance(candidate, dict):
            return candidate
    return {}


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def _option_period(offer) -> Optional[str]:
    days = _int(_pick(offer, 'option_period_days'))
    fee = _money(_pick(offer, 'option_fee'))
    if days and fee:
        return f'{days} {"day" if days == 1 else "days"}, {fee} fee'
    if days:
        return f'{days} {"day" if days == 1 else "days"}'
    if fee:
        return f'{fee} fee'
    return None


def _financing(value) -> Optional[str]:
    text = _text(value)
    if not text:
        return None
    key = re.sub(r'[^a-z]+', '', text.lower())
    return _FINANCING_LABEL.get(key) or text[0].upper() + text[1:]


def _money(value, *, whole: bool = False) -> Optional[str]:
    """Contract figures print exactly. Our own estimates round to the dollar."""
    amount = _decimal(value)
    if amount is None:
        return None
    if whole or amount == amount.to_integral_value():
        return f'${amount:,.0f}'
    return f'${amount:,.2f}'


def _decimal(value) -> Optional[Decimal]:
    if _blank(value):
        return None
    if isinstance(value, str):
        cleaned = value.replace('$', '').replace(',', '').strip()
        if not cleaned:
            return None
        value = cleaned
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _decimal_from_display(value: str) -> Optional[Decimal]:
    if _is_empty_term(value):
        return None
    return _decimal(value)


def _is_empty_term(value) -> bool:
    """True for a blank figure, including the old dash placeholders."""
    if value is None:
        return True
    cleaned = str(value).strip()
    return cleaned.casefold() in _EMPTY_TERM_TOKENS


def _int(value) -> Optional[int]:
    if _blank(value):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _long_date(value) -> Optional[str]:
    parsed = _as_date(value)
    if parsed is None:
        return None
    return f'{parsed.strftime("%B")} {parsed.day}, {parsed.year}'


def _as_date(value) -> Optional[date]:
    if _blank(value):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    for fmt in ('%Y-%m-%d', '%m/%d/%Y', '%B %d, %Y', '%b %d, %Y'):
        try:
            return datetime.strptime(text[:10] if fmt == '%Y-%m-%d' else text, fmt).date()
        except ValueError:
            continue
    return None


def _override(overrides: dict, key: str, generated: str) -> str:
    supplied = _text(overrides.get(key))
    return supplied if supplied is not None else generated


def _flag(value, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ('1', 'true', 'yes', 'on')


def _clean_email(value) -> Optional[str]:
    text = _text(value)
    if not text or '@' not in text:
        return None
    return text.lower()


def _text(value) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _blank(value) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())
