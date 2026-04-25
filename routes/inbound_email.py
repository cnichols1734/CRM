"""
Magic Inbox — SendGrid Inbound Parse webhook + lightweight UI routes.

Public surface:
- ``POST /webhooks/sendgrid/inbound-parse`` — signed webhook from SendGrid.
- ``GET  /inbox/undo/<token>`` — signed-token soft-delete for the receipt
  email's "Undo" link.
- ``GET  /inbox`` — the user's Magic Inbox home page.
- ``GET  /inbox/vcard`` — downloadable .vcf so the user can save the
  address as "Origen Inbox" on their phone.
- ``POST /inbox/dismiss-onboarding`` — hide the dashboard onboarding card.
- ``POST /inbox/rotate`` — re-issue the user's token suffix.

The webhook always returns 200. SendGrid retries 4xx forever, and we
never want a parsing bug to fill our logs with delivery failures.
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timedelta
from io import BytesIO

from flask import (
    Blueprint, Response, abort, current_app, flash, redirect, render_template,
    request, send_file, url_for,
)
from flask_login import current_user, login_required
from markupsafe import Markup
from sqlalchemy import text
from werkzeug.exceptions import RequestEntityTooLarge

from models import Contact, InboundMessage, User, db
from services.contact_extraction import process_inbound
from services.inbound_payload import normalize_sendgrid_payload
from services.inbox_provisioning import (
    ensure_inbox_for, get_inbox_domain, parse_recipient, rotate_inbox_address,
)
from services.sendgrid_outbound import (
    parse_undo_token, send_over_limit_notice,
)

logger = logging.getLogger(__name__)

inbound_bp = Blueprint('inbound_email', __name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PER_USER_DAILY_LIMIT = 200
PER_ORG_DAILY_LIMIT = 1000
RAW_RETENTION_BUCKET = os.getenv('INBOUND_RAW_BUCKET', 'inbound-email-raw')
INBOUND_MAX_CONTENT_LENGTH = int(
    os.getenv('INBOUND_MAX_CONTENT_MB', '25')
) * 1024 * 1024
INBOUND_MAX_FORM_MEMORY_SIZE = int(
    os.getenv('INBOUND_MAX_FORM_MEMORY_MB', '8')
) * 1024 * 1024


# ---------------------------------------------------------------------------
# Webhook
# ---------------------------------------------------------------------------

@inbound_bp.route('/webhooks/sendgrid/inbound-parse', methods=['POST'])
def sendgrid_inbound_parse():
    """SendGrid Inbound Parse handler. Always returns 200."""
    org_context_set = False
    try:
        _raise_inbound_parse_limits()

        if not _verify_signature(request):
            logger.warning('Magic Inbox: webhook signature invalid — dropping.')
            return ('ok', 200)

        recipient = _resolve_recipient(request.form)
        slug, token, plus_alias = parse_recipient(recipient or '')
        if not token:
            logger.info('Magic Inbox: unknown recipient %r — dropping.', recipient)
            return ('ok', 200)

        user = User.query.filter_by(inbox_token=token).first()
        if user is None or user.organization_id is None:
            logger.info('Magic Inbox: no user for token %r — dropping.', token)
            return ('ok', 200)

        # Keep RLS context across the webhook's internal commits. SET LOCAL
        # resets on commit, but this endpoint intentionally commits several
        # times so SendGrid retries never duplicate contacts.
        org_context_set = _set_webhook_org_context(user.organization_id)

        # Spam scoring — drop anything SendGrid flagged hard.
        try:
            spam_score = float(request.form.get('spam_score') or 0)
        except ValueError:
            spam_score = 0.0
        if spam_score >= 7.0:
            logger.info(
                'Magic Inbox: spam_score=%.2f over threshold — dropping for user_id=%s',
                spam_score, user.id,
            )
            return ('ok', 200)

        sender_email = _sender_email(request.form)

        # Persist the row first so even crashes downstream leave a trace.
        message = InboundMessage(
            organization_id=user.organization_id,
            user_id=user.id,
            recipient_address=recipient,
            sender_email=sender_email,
            subject=(request.form.get('subject') or '')[:500] or None,
            plus_alias=(plus_alias or None),
            source_kind='text',
            status='received',
        )
        db.session.add(message)
        db.session.commit()

        # Rate limits — written as friendly replies, not 4xx.
        over_user, over_org = _over_rate_limits(user)
        if over_user or over_org:
            reason = ('Daily limit reached (200 inbox messages/user). '
                      'Try again tomorrow.' if over_user else
                      'Your team has hit today\'s 1,000 inbox messages limit.')
            message.status = 'rejected'
            message.error_message = reason
            message.processed_at = datetime.utcnow()
            db.session.commit()
            try:
                send_over_limit_notice(user, reason=reason)
            except Exception:
                logger.exception('Failed to send over-limit notice user_id=%s',
                                 user.id)
            return ('ok', 200)

        # Stash the raw payload to Supabase Storage (best-effort).
        try:
            message.raw_storage_path = _archive_raw_payload(user, request)
        except Exception:
            logger.exception('Magic Inbox: raw archive upload failed inbound_id=%s',
                             message.id)
        # Persist the path even if no body change downstream.
        db.session.commit()

        # Normalize → AI → contacts. Orchestrator owns all DB writes from here.
        bundle = normalize_sendgrid_payload(
            request.form, request.files, plus_alias=plus_alias,
        )
        message.source_kind = bundle.source_kind
        db.session.commit()

        process_inbound(user, message, bundle)
        return ('ok', 200)

    except RequestEntityTooLarge:
        logger.warning(
            'Magic Inbox: inbound payload exceeded parser limit '
            '(content_length=%s max_content_length=%s '
            'max_form_memory_size=%s).',
            request.content_length,
            INBOUND_MAX_CONTENT_LENGTH,
            INBOUND_MAX_FORM_MEMORY_SIZE,
        )
        return ('ok', 200)
    except Exception:
        logger.exception('Magic Inbox: unhandled error in inbound webhook.')
        try:
            db.session.rollback()
        except Exception:
            pass
        return ('ok', 200)
    finally:
        if org_context_set:
            _reset_webhook_org_context()


# ---------------------------------------------------------------------------
# Undo
# ---------------------------------------------------------------------------

@inbound_bp.route('/inbox/undo/<token>')
def undo(token):
    """Soft-delete one contact, or the full batch for older receipt links."""
    payload = parse_undo_token(token or '')
    if payload is None:
        flash('That undo link is invalid or has expired.', 'error')
        return redirect(url_for('inbound_email.inbox_home')
                        if current_user.is_authenticated
                        else url_for('auth.login'))

    message = InboundMessage.query.get(payload['inbound_id'])
    if message is None:
        flash('Nothing to undo — the message could not be found.', 'error')
        return redirect(url_for('inbound_email.inbox_home')
                        if current_user.is_authenticated
                        else url_for('auth.login'))

    # If logged in, require the same user. If not logged in, the signed
    # token alone is the auth (24h, single-purpose, low-risk soft delete).
    if current_user.is_authenticated and current_user.id != message.user_id:
        abort(403)

    original_ids = list(message.created_contact_ids or [])
    contact_id = payload.get('contact_id')
    target_ids = [contact_id] if contact_id else original_ids

    deleted = 0
    deleted_ids = set()
    for cid in target_ids:
        if cid not in original_ids:
            continue
        contact = Contact.query.filter_by(id=cid,
                                          user_id=message.user_id).first()
        if contact is None:
            continue
        try:
            db.session.delete(contact)
            deleted += 1
            deleted_ids.add(cid)
        except Exception:
            logger.exception('Magic Inbox undo: failed deleting contact_id=%s', cid)

    remaining_ids = [cid for cid in original_ids if cid not in deleted_ids]
    message.created_contact_ids = remaining_ids
    if remaining_ids:
        message.error_message = (
            f'Partially undone via signed link at '
            f'{datetime.utcnow().isoformat()}Z'
        )
    else:
        message.status = 'rejected'
        message.error_message = (
            f'Undone via signed link at {datetime.utcnow().isoformat()}Z'
        )
    db.session.commit()

    flash(
        f"Removed {deleted} contact{'s' if deleted != 1 else ''} from your CRM.",
        'success',
    )
    return redirect(url_for('inbound_email.inbox_home')
                    if current_user.is_authenticated
                    else url_for('auth.login'))


# ---------------------------------------------------------------------------
# Magic Inbox home page
# ---------------------------------------------------------------------------

@inbound_bp.route('/inbox')
@login_required
def inbox_home():
    ensure_inbox_for(current_user)

    recent = (InboundMessage.query
              .filter_by(user_id=current_user.id)
              .order_by(InboundMessage.created_at.desc())
              .limit(20)
              .all())

    # Resolve created Contact rows for inline display.
    activity = []
    for m in recent:
        ids = m.created_contact_ids or []
        contacts = []
        if ids:
            contacts = (Contact.query
                        .filter(Contact.id.in_(ids),
                                Contact.user_id == current_user.id)
                        .all())
        activity.append({'message': m, 'contacts': contacts})

    qr_svg = _qr_svg_markup(f'mailto:{current_user.inbox_address}'
                            if current_user.inbox_address else '')

    return render_template(
        'inbox/home.html',
        inbox_address=current_user.inbox_address or '',
        inbox_domain=get_inbox_domain(),
        activity=activity,
        total_inbound=InboundMessage.query.filter_by(
            user_id=current_user.id).count(),
        qr_svg=qr_svg,
    )


def _qr_svg_markup(payload: str) -> Markup:
    """Render an inline SVG QR pointing at ``payload`` (typically ``mailto:``).

    Falls back to an empty string if ``segno`` is unavailable so the page
    still renders without the QR.
    """
    if not payload:
        return Markup('')
    try:
        import segno
        qr = segno.make(payload, error='m')
        buf = BytesIO()
        qr.save(buf, kind='svg', scale=4, border=2, dark='#0f172a',
                light='#ffffff', xmldecl=False, svgns=False, omitsize=False)
        return Markup(buf.getvalue().decode('utf-8'))
    except Exception:
        logger.exception('Magic Inbox: QR generation failed.')
        return Markup('')


# ---------------------------------------------------------------------------
# vCard download — "Save Origen Inbox to your phone contacts"
# ---------------------------------------------------------------------------

@inbound_bp.route('/inbox/vcard')
@login_required
def download_vcard():
    """Return a .vcf file with the user's inbox address as 'Origen Inbox'."""
    ensure_inbox_for(current_user)
    address = current_user.inbox_address
    if not address:
        flash('Your magic inbox is still being set up. Try again in a moment.',
              'info')
        return redirect(url_for('inbound_email.inbox_home'))

    vcf = (
        'BEGIN:VCARD\r\n'
        'VERSION:3.0\r\n'
        'PRODID:-//OrigenTechnolOG//Magic Inbox//EN\r\n'
        'N:Inbox;Origen;;;\r\n'
        'FN:Origen Inbox\r\n'
        'ORG:Origen TechnolOG\r\n'
        f'EMAIL;TYPE=INTERNET;TYPE=PREF:{address}\r\n'
        'NOTE:Forward emails or share photos to this contact and they '
        'land in your CRM automatically.\r\n'
        'END:VCARD\r\n'
    )
    return send_file(
        BytesIO(vcf.encode('utf-8')),
        mimetype='text/vcard',
        as_attachment=True,
        download_name='origen-inbox.vcf',
    )


# ---------------------------------------------------------------------------
# Onboarding dismiss + rotate token
# ---------------------------------------------------------------------------

@inbound_bp.route('/inbox/dismiss-onboarding', methods=['POST'])
@login_required
def dismiss_onboarding():
    current_user.has_seen_inbox_onboarding = True
    db.session.commit()
    if request.headers.get('Accept', '').startswith('application/json'):
        return ('', 204)
    return redirect(request.referrer or url_for('main.dashboard'))


@inbound_bp.route('/inbox/rotate', methods=['POST'])
@login_required
def rotate():
    rotate_inbox_address(current_user)
    flash('Your magic inbox address has been rotated. Save the new one.',
          'success')
    return redirect(request.referrer or url_for('inbound_email.inbox_home'))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _raise_inbound_parse_limits() -> None:
    """Allow realistic SendGrid Parse payloads without lifting app-wide caps."""
    request.max_content_length = INBOUND_MAX_CONTENT_LENGTH
    request.max_form_memory_size = INBOUND_MAX_FORM_MEMORY_SIZE


def _set_webhook_org_context(org_id: int) -> bool:
    """Set RLS org context for the whole DB connection during this webhook.

    The normal request hook uses SET LOCAL, which is transaction-scoped. The
    inbound webhook commits multiple times by design, so it needs a
    connection-scoped setting that we explicitly reset in a finally block.
    """
    try:
        db.session.execute(
            text("SELECT set_config('app.current_org_id', :org_id, false)"),
            {'org_id': str(org_id)},
        )
        db.session.commit()
        return True
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass
        logger.exception('Magic Inbox: failed setting webhook RLS context.')
        return False


def _reset_webhook_org_context() -> None:
    try:
        db.session.execute(text('RESET app.current_org_id'))
        db.session.commit()
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass
        logger.exception('Magic Inbox: failed resetting webhook RLS context.')


def _verify_signature(req) -> bool:
    """Authorize an inbound webhook hit.

    SendGrid Inbound Parse does NOT natively sign the request the way the
    Event Webhook does. The conventional way to authenticate it is to put a
    shared secret in the destination URL configured in SendGrid:

        https://app.example.com/webhooks/sendgrid/inbound-parse?secret=XXXXXXXX

    We accept the secret from either:
      * ``?secret=...``    (easiest, what SendGrid supports natively)
      * ``X-Inbound-Secret: ...`` header (set by a reverse proxy if you
        prefer not to expose the value in URLs)

    For full end-to-end signing (e.g. when using SendGrid's Signed Event
    Webhook in front of a proxy), ``SENDGRID_INBOUND_PUBLIC_KEY`` is also
    honoured for backwards compatibility.

    Auth precedence::

        SENDGRID_INBOUND_WEBHOOK_SECRET   →  shared-secret check
        SENDGRID_INBOUND_PUBLIC_KEY       →  signed event webhook check
        (neither set)                     →  accept with a warning
    """
    secret = (os.getenv('SENDGRID_INBOUND_WEBHOOK_SECRET') or '').strip()
    if secret:
        provided_query = req.args.get('secret') or ''
        provided_header = req.headers.get('X-Inbound-Secret') or ''
        provided = (provided_query or provided_header).strip()
        if not _consteq(provided, secret):
            source = ('query' if provided_query else
                      'header' if provided_header else
                      'missing')
            logger.warning(
                'Magic Inbox: shared-secret check failed '
                '(source=%s provided_len=%d expected_len=%d).',
                source, len(provided), len(secret),
            )
            return False
        return True

    pub_key = (os.getenv('SENDGRID_INBOUND_PUBLIC_KEY') or '').strip()
    if pub_key:
        sig = (req.headers.get('X-Twilio-Email-Event-Webhook-Signature')
               or req.headers.get('X-Sendgrid-Signature'))
        ts = (req.headers.get('X-Twilio-Email-Event-Webhook-Timestamp')
              or req.headers.get('X-Sendgrid-Timestamp'))
        if not sig or not ts:
            return False
        try:
            from sendgrid.helpers.eventwebhook import EventWebhook
            verifier = EventWebhook(public_key=pub_key)
            return verifier.verify_signature(
                req.get_data(as_text=True), sig, ts)
        except Exception:
            logger.exception(
                'Magic Inbox signature verification raised — rejecting.')
            return False

    logger.warning(
        'Magic Inbox: no SENDGRID_INBOUND_WEBHOOK_SECRET / '
        'SENDGRID_INBOUND_PUBLIC_KEY configured — accepting unverified.'
    )
    return True


def _consteq(a: str, b: str) -> bool:
    """Constant-time string compare (avoid hmac dep). Python's `==` would
    short-circuit on the first mismatch and leak length info via timing.
    """
    if len(a) != len(b):
        return False
    result = 0
    for x, y in zip(a.encode(), b.encode()):
        result |= x ^ y
    return result == 0


def _resolve_recipient(form) -> str | None:
    """Pull the recipient address out of a SendGrid Inbound Parse payload.

    The SMTP envelope is the most reliable source (the real RCPT TO).
    Falls back to the literal ``to`` header field for resilience.
    """
    envelope_raw = form.get('envelope') or ''
    if envelope_raw:
        try:
            env = json.loads(envelope_raw)
            tos = env.get('to') or []
            if tos:
                return _strip_address(tos[0])
        except Exception:
            pass

    raw_to = form.get('to') or ''
    if raw_to:
        first = raw_to.split(',', 1)[0].strip()
        return _strip_address(first)

    return None


def _strip_address(value: str) -> str:
    """Return just the bare email from a "Name <addr@x>" string."""
    value = (value or '').strip()
    if '<' in value and '>' in value:
        value = value[value.find('<') + 1:value.find('>')]
    return value.strip()


def _sender_email(form) -> str | None:
    raw = form.get('from') or ''
    bare = _strip_address(raw)
    return bare.lower() if bare else None


def _over_rate_limits(user: User) -> tuple[bool, bool]:
    """Return ``(over_user_limit, over_org_limit)`` for today (UTC)."""
    cutoff = datetime.utcnow() - timedelta(hours=24)
    base = InboundMessage.query.filter(InboundMessage.created_at >= cutoff)
    user_count = base.filter(InboundMessage.user_id == user.id).count()
    org_count = base.filter(
        InboundMessage.organization_id == user.organization_id).count()
    return (user_count > PER_USER_DAILY_LIMIT,
            org_count > PER_ORG_DAILY_LIMIT)


def _archive_raw_payload(user: User, req) -> str | None:
    """Stash the raw multipart payload in Supabase Storage. Best-effort.

    Returns the storage path on success, ``None`` if unconfigured.
    """
    if not (os.getenv('SUPABASE_URL') and os.getenv('SUPABASE_KEY')):
        return None

    from services.supabase_storage import upload_file

    today = datetime.utcnow().strftime('%Y/%m/%d')
    storage_path = (f'{user.organization_id}/{user.id}/{today}/'
                    f'{uuid.uuid4().hex}.eml')

    raw = req.get_data() or b''
    if not raw:
        # Fall back to whatever the form has if there's no body.
        try:
            raw = json.dumps({k: req.form.get(k)
                              for k in req.form.keys()}).encode('utf-8')
        except Exception:
            raw = b''

    upload_file(RAW_RETENTION_BUCKET, storage_path, raw,
                'inbound.eml', content_type='message/rfc822')
    return storage_path
