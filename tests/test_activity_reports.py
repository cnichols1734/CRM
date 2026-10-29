"""Reporting counts, permission boundaries, and independent rollout gate."""
from datetime import date, datetime

import pytest

from flask_login import login_user

from feature_flags import FEATURE_FLAGS
from models import Contact, Interaction, Transaction, TransactionAssignment, TransactionParticipant, User
from services.activity_report_service import get_activity_overview


def test_activity_window_distinct_contacts_and_scope(app, db, seed):
    with app.test_request_context():
        login_user(db.session.get(User, seed['agent_a']))
        entries = [
            ('contact_a2', 'agent_a', 'org_a', '2026-10-22T00:00:00', 'call'),
            ('contact_a2', 'agent_a', 'org_a', '2026-10-28T23:59:59', 'email'),
            ('contact_a2', 'agent_a', 'org_a', '2026-10-21T23:59:59', 'text'),
            ('contact_a2', 'agent_a', 'org_a', '2026-10-29T00:00:00', 'text'),
            ('contact_a2', 'owner_a', 'org_a', '2026-10-27T12:00:00', 'meeting'),
            ('contact_a', 'agent_a', 'org_a', '2026-10-27T12:00:00', 'call'),
            ('contact_b', 'agent_a', 'org_a', '2026-10-27T12:00:00', 'call'),
            ('contact_a2', 'agent_a', 'org_b', '2026-10-27T12:00:00', 'call'),
        ]
        for contact, user, org, when, kind in entries:
            db.session.add(Interaction(contact_id=seed[contact], user_id=seed[user],
                organization_id=seed[org], date=datetime.fromisoformat(when), type=kind))
        # A last-contact value without an interaction is not an activity record.
        db.session.get(Contact, seed['contact_a']).last_contact_date = date(2026, 10, 28)
        db.session.flush()
        report = get_activity_overview(team=True, today=date(2026, 10, 28))
        assert report['activities'] == 2
        assert report['contacts_with_activity'] == 1
        assert report['total_contacts'] == 1
        assert get_activity_overview(days=30, today=date(2026, 10, 28))['activities'] == 3
        login_user(db.session.get(User, seed['owner_a']))
        team = get_activity_overview(team=True, today=date(2026, 10, 28))
        assert team['activities'] == 4
        assert team['contacts_with_activity'] == 2
        assert team['total_contacts'] == 2
        db.session.rollback()


def test_source_counts_deduplicate_and_respect_transaction_access(app, db, seed):
    with app.test_request_context():
        login_user(db.session.get(User, seed['agent_a']))
        contact = db.session.get(Contact, seed['contact_a2'])
        contact.lead_source = ' Referral '
        tx = db.session.get(Transaction, seed['tx_a'])
        tx.status = 'closed'
        db.session.add_all([
            TransactionParticipant(organization_id=seed['org_a'], transaction_id=tx.id,
                                   contact_id=contact.id, role=role)
            for role in ('buyer', 'co_buyer')
        ])
        db.session.flush()
        # Another agent's inaccessible transaction cannot affect the report.
        row = get_activity_overview()['sources'][0]
        assert (row['source'], row['contacts'], row['closed_contacts']) == ('Referral', 1, 0)
        assignment = TransactionAssignment(organization_id=seed['org_a'], transaction_id=tx.id,
                                           user_id=seed['agent_a'], role='collaborator')
        db.session.add(assignment)
        db.session.flush()
        row = get_activity_overview()['sources'][0]
        assert (row['closed_contacts'], row['closed_share']) == (1, 100.0)
        tx.status = 'cancelled'
        db.session.flush()
        assert get_activity_overview()['sources'][0]['closed_contacts'] == 0
        tx.status = 'closed'
        tx.organization_id = seed['org_b']
        db.session.flush()
        assert get_activity_overview()['sources'][0]['closed_contacts'] == 0
        tx.organization_id = seed['org_a']
        TransactionParticipant.query.filter_by(transaction_id=tx.id).update({'organization_id': seed['org_b']})
        db.session.flush()
        assert get_activity_overview()['sources'][0]['closed_contacts'] == 0
        db.session.rollback()


def test_source_denominator_and_non_client_roles(app, db, seed):
    with app.test_request_context():
        login_user(db.session.get(User, seed['owner_a']))
        for key in ('contact_a', 'contact_a2'):
            db.session.get(Contact, seed[key]).lead_source = 'Other'
        tx = db.session.get(Transaction, seed['tx_a'])
        tx.status = 'closed'
        for contact, role in [('contact_a', 'seller'), ('contact_a2', 'lender')]:
            db.session.add(TransactionParticipant(organization_id=seed['org_a'],
                transaction_id=tx.id, contact_id=seed[contact], role=role))
        db.session.flush()
        row = get_activity_overview(team=True)['sources'][0]
        assert (row['contacts'], row['closed_contacts'], row['closed_share']) == (2, 1, 50.0)
        db.session.rollback()


def test_overview_free_access_and_legacy_stays_parked(owner_b_client, monkeypatch):
    monkeypatch.setitem(FEATURE_FLAGS, 'REPORTS_ENABLED', False)
    monkeypatch.setitem(FEATURE_FLAGS, 'REPORTS_OVERVIEW_ENABLED', True)
    response = owner_b_client.get('/reports/overview?days=bad&view=bad')
    assert response.status_code == 200
    assert b'Not recorded' in response.data
    assert b'No activity logged' in response.data
    assert b'/reports/overview' in response.data
    for path in ('/reports/', '/reports/view/pipeline_overview', '/reports/api/data/pipeline_overview', '/reports/api/export/pipeline_overview'):
        assert owner_b_client.get(path).status_code == 403
    monkeypatch.setitem(FEATURE_FLAGS, 'REPORTS_OVERVIEW_ENABLED', False)
    assert owner_b_client.get('/reports/overview').status_code == 403


def test_overview_requires_login_and_hides_team_control(client, agent_a_client):
    assert client.get('/reports/overview').status_code == 302
    response = agent_a_client.get('/reports/overview?view=all&days=30')
    assert response.status_code == 200
    assert b'id="report-view"' not in response.data
    assert b'Activities you recorded' in response.data
    assert b'1 owned contacts' in response.data


@pytest.mark.parametrize('role', ['co_landlord', 'co_tenant'])
def test_closed_rental_co_clients_count_once(app, db, seed, role):
    with app.test_request_context():
        login_user(db.session.get(User, seed['owner_a']))
        contact = db.session.get(Contact, seed['contact_a'])
        contact.lead_source = 'Referral'
        tx = db.session.get(Transaction, seed['tx_a'])
        tx.status = 'closed'
        for _ in range(2):
            db.session.add(TransactionParticipant(
                organization_id=seed['org_a'], transaction_id=tx.id,
                contact_id=contact.id, role=role,
            ))
        db.session.flush()
        row = get_activity_overview()['sources'][0]
        assert (row['contacts'], row['closed_contacts'], row['closed_share']) == (1, 1, 100.0)
        db.session.rollback()
