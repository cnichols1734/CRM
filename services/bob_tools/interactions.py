"""Activity-logging tool handler.

Logging a call or text is the highest-frequency thing an agent does after a
conversation, and it is also what keeps ``last_contact_date`` honest, which the
Daily Briefing's cold-contact logic depends on.
"""
from __future__ import annotations

import logging
from datetime import datetime, time

from models import Interaction, db
from services.bob_tools.common import (
    INTERACTION_TYPES,
    MAX_NOTE_FIELD,
    ToolError,
    get_contact_for_write,
    parse_local_date,
    truncate,
)
from services.bob_tools.context import BobContext, ToolResult

logger = logging.getLogger(__name__)

# Which contact date column each activity type advances.
_DATE_FIELD_BY_TYPE = {
    'email': 'last_email_date',
    'text': 'last_text_date',
    'call': 'last_phone_call_date',
}


def log_interaction(args: dict, ctx: BobContext) -> ToolResult:
    contact = get_contact_for_write(ctx, args.get('contact_id'))

    kind = (args.get('type') or '').strip().lower()
    if kind not in INTERACTION_TYPES:
        raise ToolError(
            f'type must be one of: {", ".join(INTERACTION_TYPES)}.'
        )

    if args.get('date'):
        activity_date = parse_local_date(args['date'], 'date')
    else:
        activity_date = ctx.today()

    if activity_date > ctx.today():
        raise ToolError(
            'Activity cannot be logged in the future. Create a task instead.'
        )

    notes = truncate(args.get('notes'), MAX_NOTE_FIELD)
    user = ctx.load_user()
    if user is None:
        raise ToolError('Could not load your account to log the activity.')

    interaction = Interaction(
        organization_id=ctx.organization_id,
        contact_id=contact.id,
        user_id=ctx.user_id,
        type=kind,
        notes=notes,
        date=datetime.combine(activity_date, time(12, 0)),
    )

    try:
        db.session.add(interaction)

        date_field = _DATE_FIELD_BY_TYPE.get(kind)
        if date_field:
            existing = getattr(contact, date_field, None)
            if existing is None or activity_date > existing:
                setattr(contact, date_field, activity_date)
        elif contact.last_contact_date is None or activity_date > contact.last_contact_date:
            # Meetings and "other" have no dedicated column, so they advance
            # last_contact_date directly, matching the log-activity route.
            contact.last_contact_date = activity_date

        contact.update_last_contact_date()
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception('B.O.B. interaction log failed contact=%s', contact.id)
        raise ToolError('The activity could not be logged. Nothing was changed.')

    _record_meaningful(ctx, user, kind)

    name = f'{contact.first_name} {contact.last_name}'.strip()
    result = ToolResult.success(
        summary=f'Logged {kind} with {name}',
        data={
            'interaction_id': interaction.id,
            'contact_id': contact.id,
            'contact_name': name,
            'type': kind,
            'date': activity_date.isoformat(),
            'last_contact_date': (
                contact.last_contact_date.isoformat()
                if contact.last_contact_date else None
            ),
        },
        undoable=True,
        record_url=f'/contact/{contact.id}',
    )
    result.data['undo_target_id'] = interaction.id
    return result


def undo_log_interaction(action, ctx: BobContext) -> str:
    interaction_id = (action.result or {}).get('undo_target_id')
    interaction = Interaction.query.filter_by(
        id=interaction_id,
        organization_id=ctx.organization_id,
        user_id=ctx.user_id,
    ).first()
    if interaction is None:
        raise ToolError('That activity entry no longer exists.')

    kind = interaction.type
    db.session.delete(interaction)
    db.session.commit()
    return f'Removed the logged {kind}'


def _record_meaningful(ctx: BobContext, user, kind: str) -> None:
    from services.activation_service import record_meaningful_action

    record_meaningful_action(
        user,
        action='interaction_logged',
        surface=ctx.surface,
        data={'interaction_type': kind},
    )
