"""E0B-4: stale BobAction confirm is rejected when the record version drifts."""
from __future__ import annotations

from datetime import datetime, timedelta

from models import BobAction, Contact, db
from services.bob_tools.context import BobContext
from services.bob_tools.registry import confirm_action


def test_confirm_rejects_stale_contact_version(app, seed):
    with app.app_context():
        contact = Contact.query.get(seed['contact_a'])
        action = BobAction(
            organization_id=seed['org_a'],
            user_id=seed['owner_a'],
            tool_name='update_contact',
            arguments={'contact_id': contact.id, 'notes': 'new'},
            preview={'contact_name': 'Jane Doe'},
            record_version={
                'contact': {
                    'id': contact.id,
                    'updated_at': '2000-01-01T00:00:00',
                }
            },
            status=BobAction.STATUS_PENDING,
            summary='Update contact',
            surface='web',
            expires_at=datetime.utcnow() + timedelta(minutes=15),
        )
        db.session.add(action)
        db.session.commit()
        action_id = action.id

        ctx = BobContext(
            organization_id=seed['org_a'],
            user_id=seed['owner_a'],
            surface='web',
            is_org_admin=True,
            org_role='owner',
        )
        result = confirm_action(action_id, ctx)
        assert result.ok is False
        assert 'stale' in result.error.lower()
        refreshed = BobAction.query.get(action_id)
        assert refreshed.status == BobAction.STATUS_EXPIRED
