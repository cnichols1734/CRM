"""Anonymous pixel and link endpoints. Tokens grant no access to CRM data."""
import base64

from flask import abort, current_app, make_response, redirect, request
from flask_login import current_user
from sqlalchemy.exc import SQLAlchemyError

from jobs.base import set_job_org_context
from models import MarketingTracking, MarketingTrackingLink, db
from routes.marketing import marketing_public
from services.marketing import tracking

_PIXEL = base64.b64decode('R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7')


def _context(token):
    org_id = tracking.token_org(token)
    if org_id is None:
        abort(404)
    # Resolve the logged-in actor before switching to the token's tenant.
    actor_id = current_user.id if current_user.is_authenticated else None
    set_job_org_context(org_id)
    return org_id, actor_id


def _headers(response):
    response.headers['Cache-Control'] = 'no-store, max-age=0'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['X-Robots-Tag'] = 'noindex, nofollow'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response


def _record(row, kind, actor_id, link=None):
    if request.method == 'HEAD':
        return
    try:
        tracking.record(row, kind, actor_id=actor_id, link=link)
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.warning('Could not record marketing %s event', kind)


@marketing_public.route('/email/track/open/<token>.gif', methods=['GET', 'HEAD'])
def track_open(token):
    org_id, actor_id = _context(token)
    row = MarketingTracking.query.filter_by(token=token, organization_id=org_id).first_or_404()
    _record(row, 'open', actor_id)
    response = make_response(_PIXEL)
    response.headers['Content-Type'] = 'image/gif'
    return _headers(response)


@marketing_public.route('/email/track/click/<token>', methods=['GET', 'HEAD'])
def track_click(token):
    org_id, actor_id = _context(token)
    link = MarketingTrackingLink.query.filter_by(token=token, organization_id=org_id).first_or_404()
    destination = link.destination
    if not tracking.safe_destination(destination):
        abort(404)
    # Capture destination first so a failed event write cannot break the redirect.
    _record(link.tracking, 'click', actor_id, link)
    return _headers(redirect(destination, code=302))
