"""Telegram webhook + profile connect/disconnect for B.O.B.

Public surface:
- ``POST /webhooks/telegram/<secret_path>`` — signed webhook from Telegram.
- ``GET  /integrations/telegram`` — connect page with deep-link QR.
- ``POST /integrations/telegram/connect`` — issue a fresh link token.
- ``POST /integrations/telegram/disconnect`` — disable the channel.
- ``POST /integrations/telegram/register-webhook`` — admin helper to call setWebhook.
"""
from __future__ import annotations

import logging
from io import BytesIO

from flask import (
    Blueprint, current_app, flash, redirect,
    render_template, request, url_for,
)
from flask_login import current_user, login_required
from markupsafe import Markup
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from feature_flags import feature_required, org_has_feature
from models import (
    AgentMessagingChannel,
    MessagingInboundUpdate,
    db,
)
from services.messaging.binding import (
    BindingError,
    constant_time_secret_match,
    deep_link_url,
    disconnect_channel,
    find_channel_by_external_id,
    get_active_channel,
    issue_link_token,
    redeem_link_token,
)
from services.messaging.queue import (
    enqueue_telegram_callback,
    enqueue_telegram_message,
)
from services.messaging.telegram import TelegramTransport, get_transport

logger = logging.getLogger(__name__)

bob_telegram_bp = Blueprint('bob_telegram', __name__)

PROVIDER = AgentMessagingChannel.PROVIDER_TELEGRAM


# ---------------------------------------------------------------------------
# Webhook
# ---------------------------------------------------------------------------

@bob_telegram_bp.route('/webhooks/telegram/<secret_path>', methods=['POST'])
def telegram_webhook(secret_path: str):
    """Telegram update handler. Always returns 200 once authenticated.

    Telegram retries non-2XX responses and does not publish a schedule, so the
    handler validates, dedupes, enqueues, and returns immediately.
    """
    expected_path = current_app.config.get('TELEGRAM_WEBHOOK_PATH') or ''
    if not expected_path or secret_path != expected_path:
        logger.warning('Telegram webhook: bad path')
        return ('', 404)

    expected_secret = current_app.config.get('TELEGRAM_WEBHOOK_SECRET') or ''
    provided = request.headers.get('X-Telegram-Bot-Api-Secret-Token')
    if not constant_time_secret_match(expected_secret, provided):
        logger.warning('Telegram webhook: bad secret')
        return ('', 403)

    payload = request.get_json(silent=True) or {}
    update_id = payload.get('update_id')
    if update_id is None:
        return ('ok', 200)

    if not _claim_update(str(update_id), kind=_update_kind(payload)):
        # Already processed (or racing a retry). Silent success.
        return ('ok', 200)

    try:
        if 'callback_query' in payload:
            _handle_callback_update(payload['callback_query'], str(update_id))
        elif 'message' in payload:
            _handle_message_update(payload['message'], str(update_id))
    except Exception:
        logger.exception('Telegram webhook handler crashed update_id=%s',
                         update_id)
    return ('ok', 200)


def _update_kind(payload: dict) -> str:
    if 'callback_query' in payload:
        return 'callback_query'
    if 'message' in payload:
        return 'message'
    return 'other'


def _claim_update(external_update_id: str, *, kind: str,
                  organization_id: int | None = None,
                  user_id: int | None = None) -> bool:
    """Insert the idempotency row. Returns False if it already existed."""
    row = MessagingInboundUpdate(
        provider=PROVIDER,
        external_update_id=external_update_id,
        kind=kind,
        organization_id=organization_id,
        user_id=user_id,
    )
    try:
        db.session.add(row)
        db.session.commit()
        return True
    except IntegrityError:
        db.session.rollback()
        return False


def _set_webhook_org_context(org_id: int) -> None:
    """Connection-scoped RLS, safe across the webhook's internal commits."""
    try:
        db.session.execute(
            text("SELECT set_config('app.current_org_id', :org_id, false)"),
            {'org_id': str(org_id)},
        )
    except Exception:
        db.session.rollback()


def _handle_message_update(message: dict, update_id: str) -> None:
    from_user = message.get('from') or {}
    chat = message.get('chat') or {}
    external_id = from_user.get('id')
    chat_id = chat.get('id')
    text_body = (message.get('text') or message.get('caption') or '').strip()
    voice = message.get('voice') or message.get('audio') or {}
    voice_file_id = voice.get('file_id') if isinstance(voice, dict) else None
    voice_duration = voice.get('duration') if isinstance(voice, dict) else None
    if external_id is None or chat_id is None:
        return

    # Binding path: /start <token> works even when unbound.
    if text_body.startswith('/start'):
        _handle_start(text_body, external_id=str(external_id),
                      chat_id=str(chat_id), update_id=update_id)
        return

    channel = find_channel_by_external_id(str(external_id))
    if channel is None:
        logger.info('Telegram: unbound sender %s — silence', external_id)
        return

    _set_webhook_org_context(channel.organization_id)
    _annotate_update(update_id, channel)

    if not _org_allows_telegram(channel.organization_id):
        get_transport().send_text(
            channel.chat_id,
            "Telegram for B.O.B. is not enabled for your organization.\n\n--BOB",
        )
        return

    if not text_body and not voice_file_id:
        get_transport().send_text(
            channel.chat_id,
            "I can take a text message or a voice note. "
            "Photos, stickers, and files aren't supported yet.\n\n--BOB",
        )
        return

    enqueue_telegram_message(
        org_id=channel.organization_id,
        channel_id=channel.id,
        text=text_body,
        telegram_message_id=str(message.get('message_id') or ''),
        voice_file_id=voice_file_id,
        voice_duration_seconds=(
            int(voice_duration) if voice_duration is not None else None
        ),
    )


def _handle_callback_update(callback: dict, update_id: str) -> None:
    from_user = callback.get('from') or {}
    message = callback.get('message') or {}
    chat = message.get('chat') or {}
    external_id = from_user.get('id')
    if external_id is None:
        return

    channel = find_channel_by_external_id(str(external_id))
    if channel is None:
        # Still need to stop the spinner.
        try:
            get_transport().answer_callback(
                callback.get('id'), text='Not linked',
            )
        except Exception:
            pass
        return

    _set_webhook_org_context(channel.organization_id)
    _annotate_update(update_id, channel)

    enqueue_telegram_callback(
        org_id=channel.organization_id,
        channel_id=channel.id,
        callback_query_id=str(callback.get('id') or ''),
        data=str(callback.get('data') or ''),
        message_id=str(message.get('message_id') or '') or None,
    )


def _handle_start(text_body: str, *, external_id: str, chat_id: str,
                  update_id: str) -> None:
    parts = text_body.split(maxsplit=1)
    token = parts[1].strip() if len(parts) > 1 else ''

    transport = get_transport()

    if not token:
        channel = find_channel_by_external_id(external_id)
        if channel is not None:
            _set_webhook_org_context(channel.organization_id)
            enqueue_telegram_message(
                org_id=channel.organization_id,
                channel_id=channel.id,
                text='/start',
            )
            return
        transport.send_text(
            chat_id,
            "Open your CRM profile, click Connect Telegram, and scan the "
            "QR code to link this account.\n\n--BOB",
        )
        return

    try:
        channel = redeem_link_token(
            token, external_id=external_id, chat_id=chat_id,
        )
    except BindingError as exc:
        transport.send_text(chat_id, f"{exc}\n\n--BOB")
        return

    _set_webhook_org_context(channel.organization_id)
    _annotate_update(update_id, channel)
    from services.messaging.base import ChoiceOption
    transport.send_choice(
        chat_id,
        "Linked. Ask me about your contacts or tasks anytime, "
        "or send a voice note from the field.\n\n"
        "Try one of these, or just type.\n\n--BOB",
        [
            ChoiceOption(label="Today's plate", callback_data='cmd:today'),
            ChoiceOption(label='Overdue', callback_data='cmd:overdue'),
            ChoiceOption(label='Help', callback_data='cmd:help'),
        ],
    )


def _annotate_update(update_id: str, channel: AgentMessagingChannel) -> None:
    """Backfill org/user on the idempotency row once we know them."""
    row = MessagingInboundUpdate.query.filter_by(
        provider=PROVIDER,
        external_update_id=update_id,
    ).first()
    if row is None:
        return
    row.organization_id = channel.organization_id
    row.user_id = channel.user_id
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()


def _org_allows_telegram(organization_id: int) -> bool:
    from models import Organization
    org = db.session.get(Organization, organization_id)
    if org is None:
        return False
    return org_has_feature('BOB_TELEGRAM', org)


# ---------------------------------------------------------------------------
# Profile connect / disconnect
# ---------------------------------------------------------------------------

@bob_telegram_bp.route('/integrations/telegram', methods=['GET'])
@login_required
@feature_required('BOB_TELEGRAM')
def telegram_connect_page():
    channel = get_active_channel(current_user.id)
    link_url = None
    qr_svg = Markup('')
    if channel is None:
        raw = issue_link_token(
            current_user.id, current_user.organization_id,
        )
        bot_username = current_app.config.get('TELEGRAM_BOT_USERNAME') or ''
        try:
            link_url = deep_link_url(bot_username, raw)
            qr_svg = _qr_svg_markup(link_url)
        except BindingError as exc:
            flash(str(exc), 'error')
    return render_template(
        'integrations/telegram.html',
        channel=channel,
        link_url=link_url,
        qr_svg=qr_svg,
        bot_username=current_app.config.get('TELEGRAM_BOT_USERNAME'),
    )


@bob_telegram_bp.route('/integrations/telegram/connect', methods=['POST'])
@login_required
@feature_required('BOB_TELEGRAM')
def telegram_connect():
    return redirect(url_for('bob_telegram.telegram_connect_page'))


@bob_telegram_bp.route('/integrations/telegram/disconnect', methods=['POST'])
@login_required
@feature_required('BOB_TELEGRAM')
def telegram_disconnect():
    if disconnect_channel(current_user.id, reason='user_disconnected'):
        flash('Telegram disconnected from B.O.B.', 'success')
    else:
        flash('No active Telegram link to disconnect.', 'info')
    return redirect(url_for('auth.view_user_profile'))


@bob_telegram_bp.route('/integrations/telegram/register-webhook', methods=['POST'])
@login_required
def register_webhook():
    """Owner/admin helper: point Telegram at this deployment's webhook URL."""
    if getattr(current_user, 'org_role', None) not in ('owner', 'admin'):
        if not getattr(current_user, 'is_platform_admin', False):
            flash('Admin access required.', 'error')
            return redirect(url_for('auth.view_user_profile'))

    token = current_app.config.get('TELEGRAM_BOT_TOKEN')
    secret = current_app.config.get('TELEGRAM_WEBHOOK_SECRET')
    path = current_app.config.get('TELEGRAM_WEBHOOK_PATH')
    base = current_app.config.get('APP_BASE_URL') or ''
    if not all([token, secret, path, base]):
        flash('Telegram is not fully configured on this server.', 'error')
        return redirect(url_for('auth.view_user_profile'))

    url = f'{base.rstrip("/")}/webhooks/telegram/{path}'
    try:
        transport = TelegramTransport(token)
        transport.set_webhook(url, secret_token=secret)
        from services.messaging.telegram import BOT_COMMANDS
        transport.set_my_commands(BOT_COMMANDS)
    except Exception as exc:
        logger.exception('setWebhook failed')
        flash(f'Webhook registration failed: {exc}', 'error')
        return redirect(url_for('auth.view_user_profile'))

    flash(f'Telegram webhook registered: {url}', 'success')
    return redirect(url_for('auth.view_user_profile'))


def _qr_svg_markup(payload: str) -> Markup:
    if not payload:
        return Markup('')
    try:
        import segno
        qr = segno.make(payload, error='m')
        buf = BytesIO()
        qr.save(
            buf, kind='svg', scale=4, border=2,
            dark='#0f172a', light='#ffffff',
            xmldecl=False, svgns=False, omitsize=False,
        )
        return Markup(buf.getvalue().decode('utf-8'))
    except Exception:
        logger.exception('Failed to render Telegram QR')
        return Markup('')
