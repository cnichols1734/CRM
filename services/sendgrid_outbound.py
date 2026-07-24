"""
Outbound transactional sends for the Magic Inbox feature.

Why a separate module from `services/sendgrid_service.py`? That file is the
SendGrid template-sync admin tool. Magic Inbox sends are dynamic, plain
HTML, content-driven, and triggered from the inbound webhook. Keeping them
separate avoids overloading either module.

All three helpers are best-effort. They never raise into the webhook —
inbound parsing must keep returning 200 to SendGrid even if our reply send
breaks.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Iterable

from flask import current_app, url_for
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Email, Mail, To

from services.inbox_provisioning import get_inbox_domain

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DEFAULT_REPLY_FROM = 'info@origentechnolog.com'
DEFAULT_REPLY_NAME = 'Origen Inbox'
UNDO_TOKEN_MAX_AGE = 24 * 3600  # 24h, per the plan
DEFAULT_ACCOUNT_WELCOME_TEMPLATE_ID = 'd-8ca289d2b7fa4778a8c4b3d10992aab5'
DEFAULT_WELCOME_TEMPLATE_ID = 'd-d89070c074554464a728867471e173e1'
DEFAULT_RECEIPT_TEMPLATE_ID = 'd-f3ef49fcfb80406ab22ec2d0bf87c0e7'
DEFAULT_ACTIVATION_LIFECYCLE_TEMPLATE_ID = 'd-9f6eaf6cb88340e2b6cdfcf6375b68ca'


def _env_value(name: str) -> str | None:
    value = os.getenv(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _reply_from() -> str:
    return _env_value('INBOUND_REPLY_FROM') or DEFAULT_REPLY_FROM


def _welcome_template_id() -> str:
    return (_env_value('SENDGRID_INBOX_WELCOME_TEMPLATE_ID')
            or DEFAULT_WELCOME_TEMPLATE_ID)


def _account_welcome_template_id() -> str:
    return (_env_value('SENDGRID_ACCOUNT_WELCOME_TEMPLATE_ID')
            or DEFAULT_ACCOUNT_WELCOME_TEMPLATE_ID)


def _receipt_template_id() -> str:
    return (_env_value('SENDGRID_INBOX_RECEIPT_TEMPLATE_ID')
            or DEFAULT_RECEIPT_TEMPLATE_ID)


def _activation_lifecycle_template_id() -> str:
    return (_env_value('SENDGRID_ACTIVATION_LIFECYCLE_TEMPLATE_ID')
            or DEFAULT_ACTIVATION_LIFECYCLE_TEMPLATE_ID)


def _sendgrid_api_key() -> str | None:
    return (_env_value('SENDGRID_API_KEY')
            or current_app.config.get('SENDGRID_API_KEY'))


def _sendgrid_error_body(exc: Exception):
    body = getattr(exc, 'body', None)
    if body is None:
        body = getattr(exc, 'response_body', None)
    if isinstance(body, bytes):
        return body.decode('utf-8', 'replace')
    return body


def _sendgrid_error_status(exc: Exception):
    return (getattr(exc, 'status_code', None)
            or getattr(exc, 'code', None))


def _serializer() -> URLSafeTimedSerializer:
    """itsdangerous serializer keyed off the Flask SECRET_KEY."""
    return URLSafeTimedSerializer(
        current_app.config['SECRET_KEY'],
        salt='magic-inbox-undo-v1',
    )


def _vcard_serializer() -> URLSafeTimedSerializer:
    """Signed serializer for public Magic Inbox vCard download links."""
    return URLSafeTimedSerializer(
        current_app.config['SECRET_KEY'],
        salt='magic-inbox-vcard-v1',
    )


# ---------------------------------------------------------------------------
# Undo token
# ---------------------------------------------------------------------------

def make_undo_token(inbound_message_id: int, contact_id: int | None = None) -> str:
    payload = {'inbound_id': int(inbound_message_id)}
    if contact_id is not None:
        payload['contact_id'] = int(contact_id)
    return _serializer().dumps(payload)


def parse_undo_token(token: str) -> dict | None:
    """Return the signed undo payload if valid, else None.

    Payload shape:
      {"inbound_id": 123}                  -> undo whole inbound batch
      {"inbound_id": 123, "contact_id": 7} -> undo one contact from the batch
    """
    try:
        payload = _serializer().loads(token, max_age=UNDO_TOKEN_MAX_AGE)
    except SignatureExpired:
        logger.info('Magic Inbox undo token expired.')
        return None
    except BadSignature:
        logger.info('Magic Inbox undo token failed signature check.')
        return None
    if not isinstance(payload, dict) or 'inbound_id' not in payload:
        return None
    try:
        parsed = {'inbound_id': int(payload['inbound_id'])}
        if payload.get('contact_id') is not None:
            parsed['contact_id'] = int(payload['contact_id'])
        return parsed
    except (TypeError, ValueError):
        return None


def verify_undo_token(token: str) -> int | None:
    """Return the InboundMessage id if the token is valid, else None.

    Kept for existing tests and callers that only care about the batch id.
    Use ``parse_undo_token`` when the caller needs per-contact undo.
    """
    payload = parse_undo_token(token)
    return payload['inbound_id'] if payload else None


def make_vcard_token(user) -> str | None:
    """Return a signed token for saving this user's current inbox contact."""
    if not user or not getattr(user, 'id', None) or not user.inbox_address:
        return None
    return _vcard_serializer().dumps({
        'user_id': int(user.id),
        'inbox_address': user.inbox_address,
    })


def parse_vcard_token(token: str) -> dict | None:
    """Return signed vCard payload if valid, else None."""
    try:
        payload = _vcard_serializer().loads(token)
    except BadSignature:
        logger.info('Magic Inbox vCard token failed signature check.')
        return None
    if not isinstance(payload, dict):
        return None
    try:
        return {
            'user_id': int(payload['user_id']),
            'inbox_address': str(payload['inbox_address']),
        }
    except (KeyError, TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Send helpers
# ---------------------------------------------------------------------------

def _send_html(to_email: str, subject: str, html: str,
               *, reply_to: str | None = None,
               custom_args: dict | None = None) -> bool:
    """Wrap the SendGrid call and swallow errors (logged) so callers stay safe."""
    api_key = _sendgrid_api_key()
    if not api_key:
        logger.warning('SENDGRID_API_KEY missing — magic inbox email skipped.')
        return False

    try:
        message = Mail(
            from_email=Email(_reply_from(), DEFAULT_REPLY_NAME),
            to_emails=To(to_email),
            subject=subject,
            html_content=html,
        )
        if reply_to:
            message.reply_to = Email(reply_to)
        if custom_args:
            # Opaque analytics attribution only — never include email/name.
            try:
                from sendgrid.helpers.mail import CustomArg
                for key, value in custom_args.items():
                    if value is None:
                        continue
                    message.add_custom_arg(CustomArg(str(key), str(value)))
            except Exception:
                logger.exception('Failed to attach SendGrid custom args')
        response = SendGridAPIClient(api_key).send(message)
        ok = response.status_code in (200, 201, 202)
        if not ok:
            logger.warning(
                'Magic Inbox SendGrid non-2xx status=%s body=%r',
                response.status_code, getattr(response, 'body', None),
            )
        return ok
    except Exception as exc:
        logger.exception(
            'Magic Inbox SendGrid send failed to=%s from=%s status=%s body=%r',
            to_email,
            _reply_from(),
            _sendgrid_error_status(exc),
            _sendgrid_error_body(exc),
        )
        return False


def _send_template(to_email: str, template_id: str, data: dict,
                   *, subject: str | None = None,
                   reply_to: str | None = None,
                   custom_args: dict | None = None) -> bool:
    """Send a SendGrid Dynamic Template with dynamic_template_data."""
    api_key = _sendgrid_api_key()
    if not api_key:
        logger.warning('SENDGRID_API_KEY missing — magic inbox email skipped.')
        return False
    if not template_id:
        return False

    try:
        message = Mail(
            from_email=Email(_reply_from(), DEFAULT_REPLY_NAME),
            to_emails=To(to_email),
            subject=subject or '',
        )
        message.template_id = template_id
        message.dynamic_template_data = data
        if reply_to:
            message.reply_to = Email(reply_to)
        if custom_args:
            try:
                from sendgrid.helpers.mail import CustomArg
                for key, value in custom_args.items():
                    if value is None:
                        continue
                    message.add_custom_arg(CustomArg(str(key), str(value)))
            except Exception:
                logger.exception('Failed to attach SendGrid custom args')
        response = SendGridAPIClient(api_key).send(message)
        ok = response.status_code in (200, 201, 202)
        if not ok:
            logger.warning(
                'Magic Inbox SendGrid template non-2xx template_id=%s '
                'status=%s body=%r',
                template_id, response.status_code, getattr(response, 'body', None),
            )
        return ok
    except Exception as exc:
        logger.exception(
            'Magic Inbox SendGrid template send failed template_id=%s '
            'to=%s from=%s status=%s body=%r',
            template_id,
            to_email,
            _reply_from(),
            _sendgrid_error_status(exc),
            _sendgrid_error_body(exc),
        )
        return False


def _safe_url(endpoint: str, **values) -> str:
    """url_for that gracefully degrades when there's no request context."""
    try:
        return url_for(endpoint, _external=True, **values)
    except Exception:
        # Background contexts may not have SERVER_NAME set; fall back to a
        # relative URL so the link is at least useful inside the app.
        try:
            return url_for(endpoint, **values)
        except Exception:
            return '#'


def _public_vcard_url(user) -> str:
    token = make_vcard_token(user)
    if not token:
        return _safe_url('inbound_email.download_vcard')
    return _safe_url('inbound_email.public_vcard', token=token)


# ---------------------------------------------------------------------------
# 1) Receipt — "Saved Sarah Chen to your CRM"
# ---------------------------------------------------------------------------

def send_inbox_receipt(user, contacts: Iterable, *, undo_token: str | None,
                       inbound_recipient: str | None = None,
                       sender_email: str | None = None,
                       source_kind: str | None = None,
                       source_subject: str | None = None,
                       skipped_count: int = 0,
                       sent_at: datetime | None = None) -> bool:
    """Send the per-message receipt with per-contact view + undo links."""
    if not user or not user.email:
        return False
    contacts = list(contacts or [])
    if not contacts:
        return False

    if len(contacts) == 1:
        c = contacts[0]
        name = _display_name(c) or 'a new contact'
        subject = f'Saved {name} to your CRM'
        intro = f'Saved <strong>{_html(name)}</strong> to your CRM.'
    else:
        subject = f'Saved {len(contacts)} contacts to your CRM'
        intro = f'Saved <strong>{len(contacts)} contacts</strong> to your CRM.'

    inbound_id = verify_undo_token(undo_token or '') if undo_token else None

    template_data = _receipt_template_data(
        contacts,
        inbound_id=inbound_id,
        subject=subject,
        inbound_recipient=inbound_recipient or user.inbox_address or '',
        sender_email=sender_email or user.email or '',
        source_kind=source_kind or '',
        source_subject=source_subject or '',
        skipped_count=skipped_count,
        sent_at=sent_at,
    )
    if _send_template(
        user.email,
        _receipt_template_id(),
        template_data,
        subject=subject,
    ):
        return True

    rows_html = '\n'.join(
        _contact_row_html(c, inbound_id=inbound_id) for c in contacts
    )

    view_url = (_safe_url('main.contacts') if len(contacts) > 1
                else _safe_url('contacts.view_contact',
                               contact_id=contacts[0].id))

    actions_html = (
        f'<a class="btn-primary" href="{view_url}">'
        f'{"View contacts" if len(contacts) > 1 else "View contact"}</a>'
    )

    inbox_addr = inbound_recipient or user.inbox_address or ''
    footer = (f'<p style="margin-top:24px;font-size:12px;color:#94a3b8">'
              f'Sent to <a style="color:#94a3b8" href="mailto:{_html(inbox_addr)}">'
              f'{_html(inbox_addr)}</a> — your magic inbox.</p>'
              if inbox_addr else '')

    html = _wrap_html(
        title='Saved to your CRM',
        body=f"""
            <h1 style="margin:0 0 16px;font-size:20px;color:#0f172a">{intro}</h1>
            <table role="presentation" cellpadding="0" cellspacing="0"
                   style="width:100%;border-collapse:collapse;margin:16px 0">
                {rows_html}
            </table>
            <p style="margin:24px 0 0">{actions_html}</p>
            {footer}
        """,
    )
    return _send_html(user.email, subject, html)


def _receipt_template_data(contacts: list, *, inbound_id: int | None,
                           subject: str, inbound_recipient: str,
                           sender_email: str, source_kind: str,
                           source_subject: str, skipped_count: int = 0,
                           sent_at: datetime | None = None) -> dict:
    count = len(contacts)
    return {
        'subject': subject,
        'headline': subject + '.',
        'count': count,
        'count_label': f'{count} new contact' + ('' if count == 1 else 's'),
        'saved_pronoun': 'it' if count == 1 else 'them',
        'sent_at': _format_email_time(sent_at),
        'sender_email': sender_email or '',
        'contacts': [
            _contact_template_data(c, inbound_id=inbound_id)
            for c in contacts
        ],
        'contacts_url': _safe_url('main.contacts'),
        'inbox_url': _safe_url('inbound_email.inbox_home'),
        'source_kind': _source_kind_label(source_kind),
        'source_subject': source_subject or '',
        'has_skipped': skipped_count > 0,
        'skipped_count': skipped_count,
        'skipped_label': (
            f'{skipped_count} entr' + ('y' if skipped_count == 1 else 'ies')
            if skipped_count else ''
        ),
        'inbox_address': inbound_recipient or '',
        'inbound_domain': get_inbox_domain(),
        'year': str(datetime.utcnow().year),
    }


def _contact_template_data(c, *, inbound_id: int | None = None) -> dict:
    name = _display_name(c) or '(no name)'
    group_names = [
        getattr(g, 'name', '') for g in (getattr(c, 'groups', None) or [])
        if getattr(g, 'name', '')
    ]
    view_url = _safe_url('contacts.view_contact', contact_id=c.id)
    undo_url = ''
    if inbound_id:
        undo_url = _safe_url(
            'inbound_email.undo',
            token=make_undo_token(inbound_id, contact_id=c.id),
        )
    return {
        'id': c.id,
        'name': name,
        'initials': _initials(name),
        'email': getattr(c, 'email', None) or '',
        'phone': getattr(c, 'phone', None) or '',
        'title': '',
        'company': '',
        'view_url': view_url,
        'undo_url': undo_url,
        'is_duplicate_merged': False,
        'group_name': group_names[0] if group_names else '',
    }


def _contact_row_html(c, *, inbound_id: int | None = None) -> str:
    name = _display_name(c) or '(no name)'
    bits = [name]
    if getattr(c, 'email', None):
        bits.append(c.email)
    if getattr(c, 'phone', None):
        bits.append(c.phone)
    view_url = _safe_url('contacts.view_contact', contact_id=c.id)
    undo_url = ''
    if inbound_id:
        undo_url = _safe_url(
            'inbound_email.undo',
            token=make_undo_token(inbound_id, contact_id=c.id),
        )
    actions = (
        f'<div style="margin-top:8px">'
        f'<a href="{view_url}" style="color:#ea580c;font-weight:600;'
        f'text-decoration:none">View</a>'
        + (f' <span style="color:#cbd5e1">·</span> '
           f'<a href="{undo_url}" style="color:#64748b;font-weight:600;'
           f'text-decoration:none">Undo</a>' if undo_url else '')
        + '</div>'
    )
    return (
        '<tr><td style="padding:8px 0;border-bottom:1px solid #e2e8f0;'
        'font-size:14px;color:#0f172a">'
        f'<strong>{_html(name)}</strong>'
        f'<div style="color:#64748b;font-size:13px">{_html(" · ".join(bits[1:]) or "—")}</div>'
        f'{actions}'
        '</td></tr>'
    )


def _display_name(c) -> str:
    first = (getattr(c, 'first_name', '') or '').strip()
    last = (getattr(c, 'last_name', '') or '').strip()
    return ' '.join(p for p in (first, last) if p)


def _initials(name: str) -> str:
    parts = [p for p in (name or '').replace('-', ' ').split() if p]
    if not parts:
        return '?'
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def _source_kind_label(source_kind: str | None) -> str:
    value = (source_kind or '').strip().lower()
    labels = {
        'csv': 'CSV',
        'vcard': 'vCard',
        'image': 'Image',
        'text': 'Email',
        'mixed': 'Mixed',
    }
    return labels.get(value, value.title() if value else 'Magic Inbox')


def _format_email_time(value: datetime | None) -> str:
    when = value or datetime.utcnow()
    try:
        return when.strftime('%b %-d at %-I:%M %p')
    except ValueError:
        # Windows does not support %-d / %-I.
        return when.strftime('%b %d at %I:%M %p').replace(' 0', ' ')


# ---------------------------------------------------------------------------
# 2) Welcome emails
# ---------------------------------------------------------------------------

def send_account_welcome(user) -> bool:
    """Send the general new-account welcome email.

    This is used for brand-new registrations and completed invites. The older
    Magic Inbox feature email stays available through ``send_inbox_welcome``
    for backfills and release announcements to existing users.
    """
    from models import ActivationEvent
    from services.activation_service import record_event

    if not user or not user.email:
        return False

    dashboard_url = _safe_url('main.dashboard', email='welcome')
    try:
        from flask import current_app
        from services.retention_tokens import make_email_click_token
        token = make_email_click_token(
            current_app, user_id=user.id, campaign='welcome',
        )
        dashboard_url = _safe_url('main.dashboard', email='welcome', ect=token)
    except Exception:
        pass

    subject = 'Your first follow-up in Origen'
    inbox_note = ''
    if user.inbox_address:
        inbox_note = f"""
            <p style="margin:20px 0 0;color:#64748b;font-size:13px">
                Prefer forwarding leads? Your private inbox is
                <strong>{_html(user.inbox_address)}</strong>.
            </p>
        """

    html = _wrap_html(
        title='Your first follow-up in Origen',
        body=f"""
            <h1 style="margin:0 0 12px;font-size:22px;color:#0f172a">
                Add one person. Pick the next day.
            </h1>
            <p style="margin:0 0 16px;color:#475569;font-size:15px">
                Your workspace is ready. Start with someone you already need to
                call, email, or check in with. Origen will put the follow-up on
                your dashboard before you leave the page.
            </p>
            <p style="margin:24px 0 0">
                <a class="btn-primary" href="{dashboard_url}">Set my first follow-up</a>
            </p>
            {inbox_note}
        """,
    )
    custom_args = {
        'crm_user_id': str(user.id),
        'crm_campaign': 'welcome',
    }
    ok = _send_html(user.email, subject, html, custom_args=custom_args)
    try:
        record_event(
            ActivationEvent.WELCOME_EMAIL_SENT if ok
            else ActivationEvent.WELCOME_EMAIL_FAILED,
            user=user,
            data={'source': 'account_welcome'},
            once=True,
            sync_person=False,
        )
    except Exception:
        logger.exception('Failed to record welcome email analytics')
    return ok


def send_activation_nudge(user, *, stage: str, action_url: str) -> bool:
    """Send one of the capped self-serve activation reminders.

    Uses the branded SendGrid Dynamic Template that matches OGT Welcome /
    Magic Inbox email design. Falls back to the same HTML shell if the
    template send fails.
    """
    if not user or not user.email:
        return False

    # (subject, eyebrow, hero_title, hero_emphasis, hero_body, body, cta, footer)
    messages = {
        'no_contact_2h': (
            'Add one contact to get started',
            'First step',
            'Add one contact.',
            'Pick the follow-up day.',
            'Someone you already need to call or email. Origen puts the next '
            'action on your dashboard.',
            'Your account is ready. Start with one real person. You can import '
            'the rest later.',
            'Add a contact',
            'One contact and a date is enough to get going.',
        ),
        'no_follow_up_24h': (
            'Schedule your next follow-up',
            'Next step',
            'Contact is saved.',
            'Add a follow-up date.',
            'Put a date on it so Origen can bring it back when you need it.',
            'You have a contact in. A dated follow-up is the next step.',
            'Schedule follow-up',
            'Once that date is set, setup is done.',
        ),
        'stalled_3d': (
            'Stuck on setup?',
            'Quick check',
            'Where did',
            'things stall?',
            'Tap a reason below, or open Origen and finish your first follow-up.',
            'If setup got messy, tell us what happened. Or jump back in and add '
            'a dated follow-up.',
            'Open my dashboard',
            'One tap is enough. We read these.',
        ),
    }
    if stage not in messages:
        return False
    (
        subject, eyebrow, hero_title, hero_emphasis, hero_body,
        body, action, footer_note,
    ) = messages[stage]

    reasons = []
    if stage == 'stalled_3d':
        try:
            from flask import current_app
            from services.retention_tokens import (
                CHURN_REASON_LABELS, make_churn_reason_token,
            )
            for reason, label in CHURN_REASON_LABELS.items():
                token = make_churn_reason_token(
                    current_app,
                    user_id=user.id,
                    reason=reason,
                    stage=stage,
                )
                reasons.append({
                    'label': label,
                    'url': _safe_url('main.churn_reason', token=token),
                })
        except Exception:
            logger.exception('Failed to build churn reason links')

    first_name = (
        (getattr(user, 'first_name', None) or '').strip()
        or (user.email or 'there').split('@')[0]
    )
    template_data = {
        'subject': subject,
        'preheader': hero_body,
        'header_label': 'Activation',
        'eyebrow': eyebrow,
        'hero_title': hero_title,
        'hero_emphasis': hero_emphasis,
        'hero_body': hero_body,
        'first_name': first_name,
        'body_text': body,
        'cta_url': action_url,
        'cta_label': action,
        'has_reasons': bool(reasons),
        'reasons': reasons,
        'footer_note': footer_note,
        'year': str(datetime.utcnow().year),
    }
    custom_args = {
        'crm_user_id': str(user.id),
        'crm_campaign': 'lifecycle',
        'crm_stage': stage,
    }
    if _send_template(
        user.email,
        _activation_lifecycle_template_id(),
        template_data,
        subject=subject,
        custom_args=custom_args,
    ):
        return True

    # Branded HTML fallback if the Dynamic Template send fails.
    reason_links = ''
    if reasons:
        items = ''.join(
            f'<p style="margin:0 0 10px 0;font-family:\'DM Sans\',sans-serif;'
            f'font-size:14px;line-height:1.5">'
            f'<a href="{_html(r["url"])}" style="color:#ea580c;'
            f'text-decoration:none;font-weight:600">{_html(r["label"])}</a></p>'
            for r in reasons
        )
        reason_links = f"""
          <tr><td style="padding:36px 48px 8px;background:#ffffff;">
            <p style="margin:0 0 14px 0;font-family:'DM Sans',sans-serif;
               font-size:11px;font-weight:700;color:#627d98;letter-spacing:1.6px;
               text-transform:uppercase;">What blocked you</p>
            <table role="presentation" cellpadding="0" cellspacing="0" width="100%"
                   style="background:#f8fafc;border:1px solid #dbe4ee;border-radius:12px;">
              <tr><td style="padding:18px 20px;">{items}</td></tr>
            </table>
          </td></tr>
        """
    html = _lifecycle_branded_html(
        subject=subject,
        eyebrow=eyebrow,
        hero_title=hero_title,
        hero_emphasis=hero_emphasis,
        hero_body=hero_body,
        first_name=first_name,
        body_text=body,
        cta_url=action_url,
        cta_label=action,
        reason_block=reason_links,
        footer_note=footer_note,
    )
    return _send_html(
        user.email, subject, html, custom_args=custom_args,
    )


def send_inbox_welcome(user) -> bool:
    if not user or not user.email or not user.inbox_address:
        return False

    inbox_url = _safe_url('inbound_email.inbox_home')
    vcard_url = _public_vcard_url(user)

    subject = 'Stop typing contacts by hand'
    template_data = _welcome_template_data(
        inbox_address=user.inbox_address,
        vcard_url=vcard_url,
        inbox_url=inbox_url,
    )
    if _send_template(
        user.email,
        _welcome_template_id(),
        template_data,
        subject=subject,
    ):
        return True

    html = _wrap_html(
        title='Your Magic Inbox is ready',
        body=f"""
            <h1 style="margin:0 0 12px;font-size:22px;color:#0f172a">
                Your Magic Inbox is ready.
            </h1>
            <p style="margin:0 0 16px;color:#475569;font-size:15px">
                This is the fastest way to get contacts into Origen.
                Forward an email, business card photo, vCard, CSV, or messy
                email signature to this private address:
            </p>
            <p style="margin:0 0 24px">
                <code style="display:inline-block;background:#f1f5f9;padding:10px 14px;
                       border-radius:8px;font-size:15px;color:#0f172a;
                       border:1px solid #e2e8f0;font-family:'SF Mono',Menlo,monospace">
                    {_html(user.inbox_address)}
                </code>
            </p>
            <p style="margin:0 0 16px;color:#475569;font-size:15px">
                Origen reads the details, creates the contact, and sends you a
                reply with a View link and an Undo link. No spreadsheet cleanup.
                No copy and paste. No "I'll add it later."
            </p>
            <div style="margin:22px 0;padding:16px;border:1px solid #fed7aa;
                        border-radius:12px;background:#fff7ed;color:#7c2d12;
                        font-size:14px;line-height:1.5">
                <strong>Do this now:</strong> save the address as
                <strong>Origen Inbox</strong> in your phone. Next time someone
                hands you a card, take a picture and share it to that contact.
            </div>
            <p style="margin:0 0 16px;color:#475569;font-size:15px">
                Great things to send here:
            </p>
            <ul style="margin:0 0 22px 20px;padding:0;color:#475569;
                       font-size:15px;line-height:1.6">
                <li>Photos of business cards</li>
                <li>Forwarded intros and lead emails</li>
                <li>Email signatures from buyers, sellers, lenders, and vendors</li>
                <li>Small CSVs or vCards from events</li>
            </ul>
            <p style="margin:24px 0 0">
                <a class="btn-primary"
                   href="{vcard_url}">Save Origen Inbox</a>
                <a class="btn-secondary" href="{inbox_url}">Open Magic Inbox</a>
            </p>
            <p style="margin:24px 0 0;font-size:12px;color:#94a3b8">
                This address is private to your account. If it ever gets shared
                too widely, you can rotate it from your profile.
            </p>
        """,
    )
    return _send_html(user.email, subject, html)


def _account_welcome_template_data(*, first_name: str, inbox_address: str,
                                   vcard_url: str, dashboard_url: str,
                                   contacts_url: str, inbox_url: str) -> dict:
    return {
        'first_name': first_name or 'there',
        'inbox_address': inbox_address,
        'vcard_url': vcard_url,
        'dashboard_url': dashboard_url,
        'contacts_url': contacts_url,
        'inbox_url': inbox_url,
        'year': str(datetime.utcnow().year),
    }


def _welcome_template_data(*, inbox_address: str, vcard_url: str,
                           inbox_url: str) -> dict:
    return {
        'inbox_address': inbox_address,
        'vcard_url': vcard_url,
        'inbox_url': inbox_url,
        'inbound_domain': get_inbox_domain(),
        'year': str(datetime.utcnow().year),
    }


# ---------------------------------------------------------------------------
# 3) Over-limit / rejected
# ---------------------------------------------------------------------------

def send_over_limit_notice(user, *, reason: str) -> bool:
    if not user or not user.email:
        return False
    subject = "Couldn't save the message you forwarded"
    html = _wrap_html(
        title="We couldn't save that one",
        body=f"""
            <h1 style="margin:0 0 12px;font-size:20px;color:#0f172a">
                We couldn't save the contact you forwarded.
            </h1>
            <p style="margin:0 0 16px;color:#475569;font-size:15px">
                {_html(reason)}
            </p>
            <p style="margin:24px 0 0">
                <a class="btn-primary" href="{_safe_url('main.contacts')}">
                    Open Contacts
                </a>
            </p>
        """,
    )
    return _send_html(user.email, subject, html)


# ---------------------------------------------------------------------------
# HTML scaffolding
# ---------------------------------------------------------------------------

def _lifecycle_branded_html(
    *, subject: str, eyebrow: str, hero_title: str, hero_emphasis: str,
    hero_body: str, first_name: str, body_text: str, cta_url: str,
    cta_label: str, reason_block: str, footer_note: str,
) -> str:
    """Fallback shell matching the OGT Welcome / lifecycle Dynamic Template."""
    year = datetime.utcnow().year
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{_html(subject)}</title>
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700;800&family=Fraunces:opsz,wght@9..144,400;9..144,500;9..144,600&display=swap" rel="stylesheet">
</head>
<body style="margin:0;padding:0;background:#f0f4f8;font-family:'DM Sans',-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
<table role="presentation" cellpadding="0" cellspacing="0" width="100%" style="background:#f0f4f8;">
<tr><td align="center" style="padding:40px 16px;">
<table role="presentation" cellpadding="0" cellspacing="0" width="600" style="max-width:600px;background:#ffffff;border-radius:18px;overflow:hidden;box-shadow:0 4px 32px rgba(16,42,67,0.08);">
  <tr><td style="padding:22px 32px;background:#ffffff;border-bottom:1px solid #eef2f7;">
    <table role="presentation" cellpadding="0" cellspacing="0" width="100%"><tr>
      <td style="font-family:'DM Sans',sans-serif;font-size:16px;font-weight:700;">
        <span style="color:#102a43;">Origen Technol</span><span style="color:#f97316;">OG</span>
      </td>
      <td align="right" style="font-family:'DM Sans',sans-serif;font-size:11px;font-weight:600;color:#94a3b8;letter-spacing:1.2px;text-transform:uppercase;">Activation</td>
    </tr></table>
  </td></tr>
  <tr><td style="background:linear-gradient(160deg,#0b1b2b 0%,#102a43 55%,#1a3a5c 100%);padding:56px 48px 52px;">
    <p style="margin:0 0 18px 0;font-family:'DM Sans',sans-serif;font-size:11px;font-weight:700;color:#f97316;letter-spacing:2.4px;text-transform:uppercase;">{_html(eyebrow)}</p>
    <h1 style="margin:0 0 18px 0;font-family:'Fraunces','DM Sans',serif;font-size:42px;line-height:1.08;font-weight:500;color:#f8fafc;letter-spacing:-0.5px;">
      {_html(hero_title)}<br>
      <em style="font-style:italic;color:#f97316;font-weight:500;">{_html(hero_emphasis)}</em>
    </h1>
    <p style="margin:0;font-family:'DM Sans',sans-serif;font-size:16px;line-height:1.6;color:#a8c3df;max-width:448px;">{_html(hero_body)}</p>
  </td></tr>
  <tr><td style="padding:36px 48px 8px;background:#ffffff;">
    <p style="margin:0 0 16px 0;font-family:'DM Sans',sans-serif;font-size:16px;color:#334e68;line-height:1.65;">Hi {_html(first_name)},</p>
    <p style="margin:0;font-family:'DM Sans',sans-serif;font-size:15px;color:#334e68;line-height:1.7;">{_html(body_text)}</p>
  </td></tr>
  <tr><td style="padding:28px 48px 8px;background:#ffffff;">
    <a href="{_html(cta_url)}" style="display:block;text-align:center;font-family:'DM Sans',sans-serif;background:linear-gradient(135deg,#f97316 0%,#ea580c 100%);color:#ffffff;font-size:15px;font-weight:600;text-decoration:none;padding:15px 16px;border-radius:10px;box-shadow:0 8px 22px rgba(249,115,22,0.32);">{_html(cta_label)}</a>
  </td></tr>
  {reason_block}
  <tr><td style="padding:28px 48px 40px;background:#ffffff;">
    <table role="presentation" cellpadding="0" cellspacing="0" width="100%"><tr>
      <td style="border-top:1px dashed #cbd5e1;padding-top:22px;">
        <p style="margin:0;font-family:'DM Sans',sans-serif;font-size:13.5px;color:#627d98;line-height:1.7;">{_html(footer_note)}</p>
      </td>
    </tr></table>
  </td></tr>
  <tr><td style="background:#f8fafc;border-top:1px solid #e8eff5;padding:28px 40px;text-align:center;">
    <p style="margin:0 0 6px 0;font-family:'DM Sans',sans-serif;font-size:14px;font-weight:700;">
      <span style="color:#334e68;">Origen Technol</span><span style="color:#f97316;">OG</span>
    </p>
    <p style="margin:0 0 14px 0;font-family:'DM Sans',sans-serif;font-size:12px;color:#829ab1;">The Real Estate CRM Built for Serious Agents</p>
    <p style="margin:0;font-family:'DM Sans',sans-serif;font-size:11px;color:#9fb3c8;">&copy; {year} Origen TechnolOG. All rights reserved.</p>
  </td></tr>
</table>
</td></tr></table>
</body></html>"""


def _wrap_html(title: str, body: str) -> str:
    """Minimal email shell. Inline styles only; no external CSS."""
    year = datetime.utcnow().year
    domain = get_inbox_domain()
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>{_html(title)}</title>
<style>
  body {{ margin:0; background:#f8fafc; font-family: -apple-system, BlinkMacSystemFont,
          "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; color:#0f172a; }}
  .wrap {{ max-width: 560px; margin: 0 auto; padding: 32px 16px; }}
  .card {{ background:#ffffff; border:1px solid #e2e8f0; border-radius:14px;
           padding:28px; }}
  a.btn-primary {{ display:inline-block; background:#ea580c; color:#ffffff !important;
        text-decoration:none; padding:10px 16px; border-radius:8px;
        font-weight:600; font-size:14px; margin-right:8px; }}
  a.btn-secondary {{ display:inline-block; background:#f1f5f9; color:#0f172a !important;
        text-decoration:none; padding:10px 16px; border-radius:8px;
        font-weight:600; font-size:14px; }}
  code {{ font-family: "SF Mono", Menlo, Consolas, monospace; }}
  .footer {{ text-align:center; color:#94a3b8; font-size:12px; margin-top:18px; }}
</style></head>
<body>
  <div class="wrap">
    <div class="card">{body}</div>
    <div class="footer">Origen TechnolOG · {domain} · &copy; {year}</div>
  </div>
</body></html>"""


def _html(value) -> str:
    """Tiny HTML-escape helper (avoids pulling in jinja in this module)."""
    if value is None:
        return ''
    return (str(value)
            .replace('&', '&amp;')
            .replace('<', '&lt;')
            .replace('>', '&gt;')
            .replace('"', '&quot;')
            .replace("'", '&#39;'))
