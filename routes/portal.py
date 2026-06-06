"""Client portal — passwordless, per-seller transaction portal.

Every route is authenticated by a long, unguessable token in the URL that maps
to a single ClientPortalAccess row. There is no User session. The token's org
is resolved first (the access table carries no RLS, like the Magic Inbox user
lookup), then a connection-scoped RLS context is set for all tenant queries and
reset in teardown.
"""
from __future__ import annotations

import logging
from functools import wraps

from flask import (
    Blueprint, render_template, abort, request, redirect, url_for,
    flash, g, jsonify,
)
from sqlalchemy import text

from models import db, ClientPortalAccess, PortalMessage
from services.portal_service import build_portal_context, SELLER_ROLES

logger = logging.getLogger(__name__)

portal_bp = Blueprint('portal', __name__, url_prefix='/portal')


# --------------------------------------------------------------------------
# RLS org context (connection-scoped; reset in teardown). Mirrors the
# Magic Inbox webhook pattern since there is no authenticated user here.
# --------------------------------------------------------------------------

def _set_portal_org_context(org_id: int) -> None:
    try:
        db.session.execute(
            text("SELECT set_config('app.current_org_id', :org_id, false)"),
            {'org_id': str(org_id)},
        )
        db.session.commit()
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass


@portal_bp.teardown_request
def _reset_portal_org_context(exc=None):
    try:
        db.session.execute(text('RESET app.current_org_id'))
        db.session.commit()
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass


# --------------------------------------------------------------------------
# access decorator
# --------------------------------------------------------------------------

def portal_access(view):
    """Resolve and authorize the portal token, then set RLS context.

    Stashes the ClientPortalAccess row on ``g.portal`` and passes it to the
    view as the first argument.
    """
    @wraps(view)
    def wrapper(token, *args, **kwargs):
        access = ClientPortalAccess.query.filter_by(
            token=token, is_active=True).first()
        if not access:
            return render_template('portal/invalid.html'), 404

        _set_portal_org_context(access.organization_id)

        tx = access.transaction
        participant = access.participant
        if (tx is None or participant is None
                or participant.transaction_id != tx.id
                or (participant.role or '') not in SELLER_ROLES):
            return render_template('portal/invalid.html'), 404

        g.portal = access
        return view(access, *args, **kwargs)

    return wrapper


# --------------------------------------------------------------------------
# routes
# --------------------------------------------------------------------------

@portal_bp.route('/<token>')
@portal_access
def home(access):
    """The seller's portal: status tracker + everything they care about."""
    try:
        access.record_view()
        db.session.commit()
    except Exception:
        db.session.rollback()

    ctx = build_portal_context(access)
    return render_template('portal/show.html', token=access.token, **ctx)


@portal_bp.route('/<token>/document/<int:doc_id>/view')
@portal_access
def view_document(access, doc_id):
    """Redirect to a short-lived signed URL for a completed document the
    seller was a party to."""
    from models import TransactionDocument
    from services.supabase_storage import get_transaction_document_url

    doc = TransactionDocument.query.filter_by(
        id=doc_id, transaction_id=access.transaction_id).first()
    email = (access.participant.display_email or '').strip().lower()
    if not doc or doc.status != 'signed' or not doc.signed_file_path:
        abort(404)

    # Must be a signer on the doc (or an unsigner-tracked completed doc).
    sigs = doc.signatures.all()
    if sigs and not any((s.signer_email or '').strip().lower() == email for s in sigs):
        abort(403)

    try:
        signed_url = get_transaction_document_url(doc.signed_file_path, expires_in=3600)
    except Exception:
        logger.exception('Portal: failed to sign document URL for doc %s', doc_id)
        signed_url = None
    if not signed_url:
        abort(404)
    return redirect(signed_url)


@portal_bp.route('/<token>/sign/<int:doc_id>')
@portal_access
def sign_document(access, doc_id):
    """Embedded DocuSeal signing page for a document awaiting the seller."""
    from models import TransactionDocument

    doc = TransactionDocument.query.filter_by(
        id=doc_id, transaction_id=access.transaction_id).first()
    email = (access.participant.display_email or '').strip().lower()
    if not doc:
        abort(404)

    sigs = doc.signatures.all()
    mine = [s for s in sigs
            if (s.signer_email or '').strip().lower() == email
            and s.status in ('sent', 'viewed')]
    if not mine or not mine[0].docuseal_submitter_slug:
        abort(404)

    embed_src = f'https://docuseal.com/s/{mine[0].docuseal_submitter_slug}'
    ctx = build_portal_context(access)
    doc_name = doc.template_name or (doc.template_slug or 'Document').replace('-', ' ').title()
    return render_template(
        'portal/sign.html',
        token=access.token,
        embed_src=embed_src,
        doc_name=doc_name,
        agent=ctx['agent'],
        property=ctx['property'],
    )


@portal_bp.route('/<token>/message', methods=['POST'])
@portal_access
def post_message(access):
    """Light action: the seller sends a note to their agent."""
    body = (request.form.get('body') or '').strip()
    if not body:
        flash('Please write a message before sending.', 'error')
        return redirect(url_for('portal.home', token=access.token) + '#talk')
    if len(body) > 4000:
        body = body[:4000]

    msg = PortalMessage(
        organization_id=access.organization_id,
        transaction_id=access.transaction_id,
        participant_id=access.participant_id,
        sender='client',
        kind='message',
        body=body,
    )
    db.session.add(msg)
    db.session.commit()

    # Notify the agent in-app (best-effort; never block the seller's action).
    try:
        _notify_agent_of_message(access, body)
    except Exception:
        logger.exception('Portal: failed to notify agent of client message.')

    flash('Message sent to your agent.', 'success')
    return redirect(url_for('portal.home', token=access.token) + '#talk')


def _notify_agent_of_message(access, body):
    from services.notification_service import create_notification

    tx = access.transaction
    agent = getattr(tx, 'created_by', None)
    if not agent:
        return
    sender = access.participant.name or 'Your client'
    preview = (body[:120] + '…') if len(body) > 120 else body
    create_notification(
        user_id=agent.id,
        organization_id=access.organization_id,
        category='portal',
        title=f'Portal message from {sender}',
        body=preview,
        icon='fa-comment',
        action_url=url_for('transactions.view_transaction', id=tx.id),
        respect_preference=False,
    )
