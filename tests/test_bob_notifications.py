"""Tests for the bell record of what B.O.B. changed.

Covers grouping per request, B.O.B. attribution, surface wording, undo
retraction, preference opt-out, and that reads never notify.

Run with: python -m pytest tests/test_bob_notifications.py -v
"""
from __future__ import annotations

import os
import sys
from datetime import timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models import BobAction, Contact, Interaction, Notification, Task, db
from services.bob_tools import BobContext, dispatch, undo_action
from services.bob_tools.notifications import (
    CATEGORY,
    ActionCollector,
    flush,
)
from services.notification_service import set_preference


@pytest.fixture()
def ctx_owner_a(seed):
    return BobContext(
        user_id=seed['owner_a'],
        organization_id=seed['org_a'],
        org_role='owner',
        is_org_admin=True,
    )


@pytest.fixture()
def ctx_telegram(seed):
    return BobContext(
        user_id=seed['owner_a'],
        organization_id=seed['org_a'],
        org_role='owner',
        is_org_admin=True,
        surface='bob_telegram',
    )


# Every contact these tests create uses this first name, so cleanup can find
# them without threading ids through each test.
SCRATCH_FIRST_NAME = 'Nora'


@pytest.fixture(autouse=True)
def _clean_notifications(app, seed):
    """Isolate counts from any other test file that drove B.O.B."""
    _wipe(app)
    yield
    _wipe(app)


def _wipe(app):
    with app.app_context():
        # Actions go too. SQLite recycles row ids, so a leftover action still
        # pointing at a deleted notification would collide with the next one.
        BobAction.query.delete()
        Notification.query.filter_by(category=CATEGORY).delete()

        # The contacts these tests create own interactions and tasks. Left
        # behind, they break unrelated files later in the session, because the
        # SQLite test database is shared for the whole run.
        scratch = Contact.query.filter_by(
            first_name=SCRATCH_FIRST_NAME,
        ).all()
        if scratch:
            ids = [c.id for c in scratch]
            Interaction.query.filter(
                Interaction.contact_id.in_(ids),
            ).delete(synchronize_session=False)
            Task.query.filter(
                Task.contact_id.in_(ids),
            ).delete(synchronize_session=False)
            for contact in scratch:
                contact.groups = []
            db.session.flush()
            for contact in scratch:
                db.session.delete(contact)
        db.session.commit()


def _bob_notifications(user_id):
    return (
        Notification.query
        .filter_by(user_id=user_id, category=CATEGORY)
        .order_by(Notification.created_at.desc())
        .all()
    )


def _new_contact(ctx, collector, last_name='Notify'):
    return dispatch('create_contact', {
        'first_name': SCRATCH_FIRST_NAME, 'last_name': last_name,
        'email': f'nora.{last_name.lower()}@test.com',
        'group_names': ['Buyers'],
    }, ctx, collector=collector)


class TestGrouping:
    def test_one_request_with_one_write_makes_one_notification(
            self, app, seed, ctx_owner_a):
        with app.app_context():
            collector = ActionCollector()
            _new_contact(ctx_owner_a, collector)
            flush(collector, ctx_owner_a)

            notifs = _bob_notifications(seed['owner_a'])
            assert len(notifs) == 1
            assert notifs[0].title.startswith('B.O.B.')

    def test_several_writes_in_one_request_collapse_into_one(
            self, app, seed, ctx_owner_a):
        """Three bells for one sentence would read as noise."""
        with app.app_context():
            collector = ActionCollector()
            created = _new_contact(ctx_owner_a, collector, last_name='Grouped')
            contact_id = created.data['contact']['contact_id']
            dispatch('append_contact_note', {
                'contact_id': contact_id, 'note': 'Met at open house',
            }, ctx_owner_a, collector=collector)
            dispatch('log_interaction', {
                'contact_id': contact_id, 'type': 'call',
            }, ctx_owner_a, collector=collector)
            flush(collector, ctx_owner_a)

            notifs = _bob_notifications(seed['owner_a'])
            assert len(notifs) == 1
            assert '3 changes' in notifs[0].title

    def test_reads_never_notify(self, app, seed, ctx_owner_a):
        with app.app_context():
            collector = ActionCollector()
            dispatch('get_agenda', {}, ctx_owner_a, collector=collector)
            dispatch('search_contacts', {'query': 'Jane'}, ctx_owner_a,
                     collector=collector)
            flush(collector, ctx_owner_a)

            assert _bob_notifications(seed['owner_a']) == []

    def test_a_failed_write_does_not_notify(self, app, seed, ctx_owner_a):
        with app.app_context():
            collector = ActionCollector()
            result = dispatch('append_contact_note', {
                'contact_id': seed['contact_b'], 'note': 'nope',
            }, ctx_owner_a, collector=collector)
            flush(collector, ctx_owner_a)

            assert result.ok is False
            assert _bob_notifications(seed['owner_a']) == []


class TestAttribution:
    def test_names_bob_and_the_surface(self, app, seed, ctx_telegram):
        with app.app_context():
            collector = ActionCollector()
            _new_contact(ctx_telegram, collector, last_name='Telegram')
            flush(collector, ctx_telegram)

            notif = _bob_notifications(seed['owner_a'])[0]
            assert 'B.O.B.' in notif.title
            assert 'Telegram' in notif.body

    def test_chat_surface_is_named_too(self, app, seed, ctx_owner_a):
        with app.app_context():
            collector = ActionCollector()
            _new_contact(ctx_owner_a, collector, last_name='Chat')
            flush(collector, ctx_owner_a)

            notif = _bob_notifications(seed['owner_a'])[0]
            assert 'chat' in notif.body

    def test_links_to_the_record_when_there_is_one(self, app, seed,
                                                   ctx_owner_a):
        with app.app_context():
            collector = ActionCollector()
            result = _new_contact(ctx_owner_a, collector, last_name='Linked')
            flush(collector, ctx_owner_a)

            notif = _bob_notifications(seed['owner_a'])[0]
            if result.record_url:
                assert notif.action_url == result.record_url


class TestUndo:
    def test_undo_retracts_the_notification(self, app, seed, ctx_owner_a):
        """A bell saying B.O.B. created something it just removed is a lie."""
        with app.app_context():
            collector = ActionCollector()
            result = _new_contact(ctx_owner_a, collector, last_name='Undone')
            flush(collector, ctx_owner_a)
            assert len(_bob_notifications(seed['owner_a'])) == 1

            undo = undo_action(result.action_id, ctx_owner_a)

            assert undo.ok is True
            assert _bob_notifications(seed['owner_a']) == []

    def test_grouped_notification_survives_a_partial_undo(self, app, seed,
                                                          ctx_owner_a):
        with app.app_context():
            collector = ActionCollector()
            created = _new_contact(ctx_owner_a, collector, last_name='Partial')
            contact_id = created.data['contact']['contact_id']
            logged = dispatch('log_interaction', {
                'contact_id': contact_id, 'type': 'call',
            }, ctx_owner_a, collector=collector)
            flush(collector, ctx_owner_a)
            assert len(_bob_notifications(seed['owner_a'])) == 1

            undo_action(logged.action_id, ctx_owner_a)

            # The contact still exists, so the record must stay.
            assert len(_bob_notifications(seed['owner_a'])) == 1


class TestPreferences:
    def test_opting_out_silences_the_category(self, app, seed, ctx_owner_a):
        with app.app_context():
            set_preference(seed['owner_a'], seed['org_a'], CATEGORY,
                           in_app=False)

            collector = ActionCollector()
            _new_contact(ctx_owner_a, collector, last_name='Muted')
            flush(collector, ctx_owner_a)

            assert _bob_notifications(seed['owner_a']) == []

            set_preference(seed['owner_a'], seed['org_a'], CATEGORY,
                           in_app=True)

    def test_write_still_succeeds_when_muted(self, app, seed, ctx_owner_a):
        """The bell is a side effect and must never gate real CRM work."""
        with app.app_context():
            set_preference(seed['owner_a'], seed['org_a'], CATEGORY,
                           in_app=False)

            collector = ActionCollector()
            result = _new_contact(ctx_owner_a, collector, last_name='Silent')
            flush(collector, ctx_owner_a)

            assert result.ok is True

            set_preference(seed['owner_a'], seed['org_a'], CATEGORY,
                           in_app=True)


class TestConfirmedActions:
    def test_a_confirmed_change_notifies_on_its_own(self, app, seed,
                                                    ctx_owner_a):
        """Confirming is its own moment, after the turn already ended."""
        from services.bob_tools import confirm_action

        with app.app_context():
            created = _new_contact(ctx_owner_a, None, last_name='Confirmed')
            assert _bob_notifications(seed['owner_a']) == []

            pending = dispatch('update_contact', {
                'contact_id': created.data['contact']['contact_id'],
                'fields': {'city': 'Katy'},
            }, ctx_owner_a)
            assert pending.requires_confirmation is True
            assert _bob_notifications(seed['owner_a']) == []

            confirmed = confirm_action(pending.action_id, ctx_owner_a)

            assert confirmed.ok is True
            notifs = _bob_notifications(seed['owner_a'])
            assert len(notifs) == 1
            assert 'B.O.B.' in notifs[0].title
