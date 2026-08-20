"""The unsubscribe link inside a marketing email.

Reached with no session by someone who is not our user, so every assumption the
rest of the app makes is off:

  * RLS context has to be set from the token, since ``set_tenant_context``
    returns early for anonymous requests and marketing_sends forces RLS.
  * GET never unsubscribes. Mail clients and security appliances prefetch links,
    and a scanner should not be able to opt a recipient out. GET asks; POST acts.
  * POST also has to work unattended, because ``List-Unsubscribe-Post`` promises
    Gmail and Outlook that it will.
"""
from flask import (
    abort, current_app, render_template, request,
)

from jobs.base import set_job_org_context
from models import Organization, db
from routes.marketing import marketing_public
from services.marketing import suppression

# The form field Gmail and Outlook post when the recipient uses their client's
# own unsubscribe control. No page is rendered for those; nobody is looking.
_ONE_CLICK = 'List-Unsubscribe=One-Click'


def _load(token: str):
    """Resolve a token to (send, organization) with org context established."""
    org_id = suppression.org_id_from_token(token)
    if org_id is None:
        abort(404)

    set_job_org_context(org_id)

    send = suppression.find_send_by_token(token)
    if send is None:
        abort(404)
    return send, db.session.get(Organization, send.organization_id)


def _sender_name(org: Organization | None) -> str:
    if org is None:
        return 'this sender'
    return org.broker_name or org.name or 'this sender'


def _is_one_click() -> bool:
    return request.form.get('List-Unsubscribe') == 'One-Click' or _ONE_CLICK in (
        request.get_data(as_text=True) or ''
    )


@marketing_public.route('/email/unsubscribe/<token>', methods=['GET'])
def confirm(token: str):
    """Ask. A prefetching scanner lands here and changes nothing."""
    send, org = _load(token)
    already = suppression.is_suppressed(send.to_email, send.organization_id)
    return render_template(
        'marketing/unsubscribe.html',
        token=token,
        email=send.to_email,
        sender_name=_sender_name(org),
        state='already' if already else 'confirm',
    )


@marketing_public.route('/email/unsubscribe/<token>', methods=['POST'])
def unsubscribe(token: str):
    """Act. Idempotent, so a second click or a retried one-click POST is fine."""
    send, org = _load(token)

    suppression.record_unsubscribe(send)
    db.session.commit()
    current_app.logger.info(
        'Marketing unsubscribe: org=%s send=%s one_click=%s',
        send.organization_id, send.id, _is_one_click(),
    )

    if _is_one_click():
        return 'Unsubscribed', 200, {'Content-Type': 'text/plain; charset=utf-8'}

    return render_template(
        'marketing/unsubscribe.html',
        token=token,
        email=send.to_email,
        sender_name=_sender_name(org),
        state='done',
    )


@marketing_public.route('/email/unsubscribe/<token>/undo', methods=['POST'])
def undo(token: str):
    """For the person who clicked the wrong button and immediately knew it."""
    send, org = _load(token)

    restored = suppression.resubscribe(send)
    db.session.commit()

    return render_template(
        'marketing/unsubscribe.html',
        token=token,
        email=send.to_email,
        sender_name=_sender_name(org),
        state='restored' if restored else 'done',
    )
