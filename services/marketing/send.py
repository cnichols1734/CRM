"""Deliver one marketing send through SendGrid.

The layout is rendered here, per recipient, so the signature belongs to the
sending agent and merge fields are escaped. Batching via SendGrid personalizations
would force the same HTML for everyone; we already substitute before the wire.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Optional

from config import Config
from models import (
    Contact, MarketingCampaign, MarketingSend, MarketingTemplate, Organization,
    User, db,
)
from services.marketing import merge_fields as mf
from services.marketing import sending_config
from services.marketing import suppression as supp
from services.marketing.context import shell_for
from services.marketing.links import unsubscribe_url
from services.marketing.render import personalize, render

logger = logging.getLogger(__name__)

MAX_TEST_RECIPIENTS = 5
_TEST_SUBJECT_PREFIX = '[Test] '
_EMAIL_RE = re.compile(r'^[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}$', re.I)


class SendError(Exception):
    def __init__(self, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


def render_for_send(send: MarketingSend, campaign: MarketingCampaign) -> tuple[str, str, str]:
    """Return subject, html, text with merge fields filled.

    Raises SendError if a required merge field is missing.
    """
    contact = send.contact or db.session.get(Contact, send.contact_id)
    template = send.template or db.session.get(MarketingTemplate, send.template_id)
    org = db.session.get(Organization, send.organization_id)
    agent = None
    if send.user_id:
        agent = db.session.get(User, send.user_id)
    if agent is None:
        agent = db.session.get(User, campaign.user_id)

    if contact is None or template is None or org is None:
        raise SendError('Send is missing its contact, template, or organization.')

    ctx = shell_for(
        org,
        agent,
        unsubscribe_token=send.unsubscribe_token,
        preheader=template.preheader,
        eyebrow=(template.category or '').replace('_', ' ') or None,
    )
    rendered = render(template.blocks or [], ctx, validate=False)
    values = mf.resolve_values(contact, agent, org)
    subject, html, text, missing = personalize(rendered, template.subject, values)
    if missing:
        raise SendError(
            'missing_merge_field:' + ','.join(sorted(missing)),
            retryable=False,
        )
    return subject, html, text


def _provider_send(
    *,
    to_email: str,
    subject: str,
    html: str,
    text: str,
    sender,
    headers: dict[str, str],
    custom_args: dict,
) -> str:
    """Talk to SendGrid. Returns the provider message id when present."""
    api_key = Config.SENDGRID_API_KEY
    if not api_key:
        raise SendError('SENDGRID_API_KEY is not configured.', retryable=False)

    try:
        from sendgrid import SendGridAPIClient
        from sendgrid.helpers.mail import (
            CustomArg, Email, Header, Mail, To,
        )
    except ImportError as exc:
        raise SendError('SendGrid library is not installed.', retryable=False) from exc

    message = Mail(
        from_email=Email(sender.from_email, sender.from_name),
        to_emails=To(to_email),
        subject=subject,
        html_content=html,
        plain_text_content=text,
    )
    if sender.reply_to:
        message.reply_to = Email(sender.reply_to)
    for name, value in headers.items():
        message.add_header(Header(name, value))
    for key, value in custom_args.items():
        if value is None:
            continue
        message.add_custom_arg(CustomArg(str(key), str(value)))

    try:
        response = SendGridAPIClient(api_key).send(message)
    except Exception as exc:
        status = getattr(exc, 'status_code', None) or getattr(
            getattr(exc, 'http_error', None), 'status_code', None,
        )
        retryable = status in (429, 500, 502, 503, 504) or status is None
        raise SendError(str(exc)[:400], retryable=retryable) from exc

    if response.status_code not in (200, 201, 202):
        retryable = response.status_code in (429, 500, 502, 503, 504)
        raise SendError(
            f'SendGrid returned {response.status_code}',
            retryable=retryable,
        )

    headers_out = getattr(response, 'headers', None) or {}
    return (
        headers_out.get('X-Message-Id')
        or headers_out.get('X-Message-ID')
        or ''
    )


def parse_test_recipients(raw: str) -> list[str]:
    """Comma-separated addresses for a studio test send."""
    parts = re.split(r'[,;\n]+', raw or '')
    emails: list[str] = []
    seen: set[str] = set()
    for part in parts:
        address = supp.normalize(part)
        if not address:
            continue
        if not _EMAIL_RE.fullmatch(address):
            raise SendError(f'{part.strip()} is not a valid email address.')
        if address in seen:
            continue
        seen.add(address)
        emails.append(address)
    if not emails:
        raise SendError('Add at least one email address.')
    if len(emails) > MAX_TEST_RECIPIENTS:
        raise SendError(
            f'Send a test to at most {MAX_TEST_RECIPIENTS} addresses at a time.'
        )
    return emails


def send_test(
    *,
    org,
    agent,
    subject: str,
    preheader: str,
    blocks: list,
    to_emails: list[str],
    sample_values: Optional[dict] = None,
    category: str = '',
) -> dict:
    """Send the current studio draft to listed addresses.

    Uses sample merge values, not a real contact. Does not create a campaign
    send or count against the monthly quota.
    """
    from services.marketing.templates import TemplateError, prepare

    if not to_emails:
        raise SendError('Add at least one email address.')
    try:
        prepared = prepare(
            subject, preheader, blocks, acknowledge_warnings=True,
        )
    except TemplateError as exc:
        raise SendError(str(exc), retryable=False) from exc

    sender = sending_config.sender_for(agent, org)
    token = supp.issue_token(getattr(org, 'id', 0) or 1)
    ctx = shell_for(
        org,
        agent,
        unsubscribe_token=token,
        preheader=prepared['preheader'],
        eyebrow=(category or '').replace('_', ' ') or None,
    )
    rendered = render(prepared['blocks'], ctx, validate=False)
    values = sample_values if sample_values is not None else mf.sample_values()
    filled_subject, html, text, _missing = personalize(
        rendered, prepared['subject'], values,
    )
    if not filled_subject.lower().startswith('[test]'):
        filled_subject = f'{_TEST_SUBJECT_PREFIX}{filled_subject}'

    sent: list[str] = []
    errors: list[str] = []
    for to_email in to_emails:
        try:
            _provider_send(
                to_email=to_email,
                subject=filled_subject,
                html=html,
                text=text,
                sender=sender,
                headers={},
                custom_args={
                    'organization_id': getattr(org, 'id', None),
                    'kind': 'marketing_test',
                },
            )
            sent.append(to_email)
        except SendError as exc:
            errors.append(f'{to_email}: {exc}')
            logger.warning('Marketing test send failed to %s: %s', to_email, exc)

    if not sent:
        raise SendError(errors[0] if errors else 'Could not send the test email.')
    if errors:
        raise SendError(
            f'Sent to {len(sent)}, but {len(errors)} failed. {errors[0]}'
        )
    return {'sent': sent, 'subject': filled_subject}


def deliver(send: MarketingSend, *, now: Optional[datetime] = None) -> MarketingSend:
    """Attempt one queued row. Caller owns the surrounding transaction."""
    now = now or datetime.utcnow()
    campaign = send.campaign or db.session.get(MarketingCampaign, send.campaign_id)
    if campaign is None:
        send.status = 'failed'
        send.error = 'missing_campaign'
        return send

    if campaign.status in ('paused', 'cancelled'):
        if campaign.status == 'cancelled':
            send.status = 'skipped'
            send.skip_reason = 'campaign_cancelled'
        return send

    org = db.session.get(Organization, send.organization_id)
    agent = db.session.get(User, send.user_id) if send.user_id else None
    sender = sending_config.sender_for(
        agent, org, reply_to=campaign.reply_to if campaign else None,
    )
    unsub = unsubscribe_url(send.unsubscribe_token)
    headers = supp.unsubscribe_headers(
        unsub, mailto=Config.MARKETING_UNSUBSCRIBE_MAILTO,
    )

    send.attempt_count = (send.attempt_count or 0) + 1
    send.last_attempt_at = now
    send.status = 'sending'

    try:
        subject, html, text = render_for_send(send, campaign)
    except SendError as exc:
        if str(exc).startswith('missing_merge_field'):
            send.status = 'skipped'
            send.skip_reason = 'missing_merge_field'
            send.error = str(exc)[:500]
            campaign.queued_count = max((campaign.queued_count or 1) - 1, 0)
            campaign.skipped_count = (campaign.skipped_count or 0) + 1
            return send
        raise

    send.subject_rendered = subject[:300]
    try:
        message_id = _provider_send(
            to_email=send.to_email,
            subject=subject,
            html=html,
            text=text,
            sender=sender,
            headers=headers,
            custom_args={
                'send_id': send.id,
                'campaign_id': campaign.id,
                'step_id': send.step_id,
                'organization_id': send.organization_id,
                'kind': 'marketing',
            },
        )
    except SendError as exc:
        send.error = str(exc)[:500]
        if exc.retryable and send.attempt_count < MarketingSend.MAX_ATTEMPTS:
            send.status = 'queued'
            send.scheduled_for = now
            return send
        send.status = 'failed'
        campaign.queued_count = max((campaign.queued_count or 1) - 1, 0)
        campaign.failed_count = (campaign.failed_count or 0) + 1
        return send

    send.status = 'sent'
    send.sent_at = now
    send.provider_message_id = message_id or None
    send.error = None
    campaign.queued_count = max((campaign.queued_count or 1) - 1, 0)
    campaign.sent_count = (campaign.sent_count or 0) + 1
    return send
