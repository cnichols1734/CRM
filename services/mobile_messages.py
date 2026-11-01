"""Retry-safe deal messages for both mobile API identities."""
from uuid import UUID

from models import db, PortalMessage, TransactionParticipant


def create_message(*, organization_id, transaction_id, participant_id, sender, body, request_id=None, author_user_id=None):
    if request_id is not None:
        try:
            request_id = str(UUID(request_id))
        except (ValueError, TypeError, AttributeError):
            raise ValueError('Use a valid message request ID.')
    TransactionParticipant.query.filter_by(id=participant_id, organization_id=organization_id,
        transaction_id=transaction_id).with_for_update().one()
    if request_id:
        prior = PortalMessage.query.filter_by(organization_id=organization_id,
            participant_id=participant_id, sender=sender, request_id=request_id).first()
        if prior:
            if prior.body != body or prior.author_user_id != author_user_id:
                raise ValueError('This request ID belongs to another message.')
            return prior, False
    message = PortalMessage(organization_id=organization_id, transaction_id=transaction_id,
        participant_id=participant_id, sender=sender, kind='message', body=body,
        request_id=request_id, author_user_id=author_user_id)
    db.session.add(message)
    db.session.flush()
    return message, True
