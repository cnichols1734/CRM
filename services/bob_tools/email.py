"""Gmail draft tool. Saves a draft the user sends themselves — never sends."""
from __future__ import annotations

import html

from models import UserEmailIntegration
from services.bob_tools.common import get_contact_for_read
from services.bob_tools.context import BobContext, ToolResult


def draft_email(args: dict, ctx: BobContext) -> ToolResult:
    subject = (args.get('subject') or '').strip()
    body = (args.get('body') or '').strip()
    if not subject or not body:
        return ToolResult.failure('subject and body are required.')

    to_emails = []
    contact_id = args.get('contact_id')
    if contact_id:
        try:
            contact = get_contact_for_read(ctx, contact_id)
        except Exception as exc:
            return ToolResult.failure(str(exc))
        if not contact.email:
            return ToolResult.failure('That contact does not have an email address.')
        to_emails.append(contact.email)
    for raw in (args.get('to') or '').replace(';', ',').split(','):
        address = raw.strip()
        if address and address not in to_emails:
            to_emails.append(address)
    if not to_emails:
        return ToolResult.failure('Pass contact_id or to with at least one email address.')

    integration = UserEmailIntegration.query.filter_by(user_id=ctx.user_id).first()
    if integration is None or not getattr(integration, 'sync_enabled', False):
        return ToolResult.failure('Connect Gmail in Profile before drafting email.')

    from services.gmail_service import create_draft
    body_html = '<p>' + html.escape(body).replace('\n', '<br>') + '</p>'
    result = create_draft(integration, to_emails, subject, body_html)
    if not result.get('success'):
        return ToolResult.failure(result.get('error') or 'Could not create the Gmail draft.')
    return ToolResult.success(
        'Saved a Gmail draft. It has not been sent.',
        {
            'draft_id': result.get('draft_id'),
            'to': to_emails,
            'subject': subject,
            'sent': False,
        },
    )
