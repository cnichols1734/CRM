# routes/transactions/decorators.py
"""
Shared decorators for transaction routes.
"""

from functools import wraps
from flask import flash, redirect, url_for, jsonify, request, abort
from flask_login import current_user
from feature_flags import can_access_transactions, org_has_feature


GENERATION_FROZEN_MESSAGE = (
    'Document generation and e-signature sending are paused. '
    'Upload externally signed PDFs to fulfill placeholders.'
)


def transactions_required(f):
    """Decorator to check if user can access transactions module."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not can_access_transactions(current_user):
            flash('You do not have access to this feature.', 'error')
            return redirect(url_for('main.dashboard'))
        return f(*args, **kwargs)
    return decorated_function


def bob_vtc_pilot_required(f):
    """Hide the contract-first BOB workflow unless the organization is piloting it."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not org_has_feature('BOB_VTC_PILOT', current_user.organization):
            abort(404)
        return f(*args, **kwargs)
    return decorated_function


def document_generation_required(f):
    """Block new DocuSeal/generation operations while DOCUMENT_GENERATION is frozen.

    Legacy webhook completion is intentionally not wrapped by this decorator.
    Placeholder upload/fulfill routes should also remain unwrapped.

    Never redirect to ``view_transaction`` using an unverified id from the URL —
    that runs before org ownership checks and can leak cross-org route behavior.

    Cross-org transaction ids must 404 (not 403) so freeze denials do not
    disclose that a foreign transaction id exists.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not org_has_feature('DOCUMENT_GENERATION'):
            tx_id = kwargs.get('id')
            if (
                tx_id is not None
                and getattr(current_user, 'is_authenticated', False)
                and getattr(current_user, 'organization_id', None) is not None
            ):
                from models import Transaction
                visible = Transaction.query.filter_by(
                    id=tx_id,
                    organization_id=current_user.organization_id,
                ).first()
                if not visible:
                    abort(404)
            wants_json = (
                request.is_json
                or request.headers.get('X-Requested-With') == 'XMLHttpRequest'
                or (request.accept_mimetypes.best == 'application/json')
                or request.path.startswith('/api/')
                or (
                    request.method in ('POST', 'PUT', 'PATCH', 'DELETE')
                    and 'application/json' in (request.headers.get('Accept') or '')
                )
            )
            if wants_json or request.is_json:
                return jsonify({
                    'error': GENERATION_FROZEN_MESSAGE,
                    'code': 'document_generation_frozen',
                }), 403
            flash(GENERATION_FROZEN_MESSAGE, 'error')
            # Same-org freeze deny — do not redirect to an unverified id.
            abort(403)
        return f(*args, **kwargs)
    return decorated_function
