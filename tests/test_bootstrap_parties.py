"""Bootstrap party split / link / create / side confirmation tests."""

import pytest

from models import (
    Contact,
    ContractBootstrapSession,
    Transaction,
    TransactionParticipant,
    db,
)
from services.contract_bootstrap import (
    approve_selected,
    build_party_proposals,
    classify_and_extract,
    default_party_resolutions,
    record_upload_metadata,
    resolve_match,
    split_party_names,
)


def _user(seed):
    from models import User
    return User.query.get(seed['owner_a'])


def _bootstrap_session(user, org_id, filename='parties.pdf'):
    return record_upload_metadata(
        file_bytes=b'%PDF-1.4 parties-test',
        filename=filename,
        mime_type='application/pdf',
        source='inbox',
        user=user,
        org_id=org_id,
    )


def test_split_party_names_billy_and_kimberly():
    names = split_party_names('Billy Copaus and Kimberly Copaus')
    assert names == ['Billy Copaus', 'Kimberly Copaus']


def test_split_party_names_ampersand_and_slash():
    assert split_party_names('Clark Smith & Rachel Smith') == [
        'Clark Smith', 'Rachel Smith',
    ]
    assert split_party_names('A Buyer / B Buyer') == ['A Buyer', 'B Buyer']


def test_approve_create_new_without_side_raises(app, seed):
    with app.app_context():
        user = _user(seed)
        session = _bootstrap_session(user, seed['org_a'])
        classify_and_extract(
            session=session,
            field_data={
                'property_address': '6004 Lakeside Drive',
                'document_type': 'residential_contract',
                'side': 'unknown',
                'seller_name': 'Billy Copaus and Kimberly Copaus',
                'buyer_name': 'Clark Smith and Rachel Smith',
            },
        )
        resolve_match(session=session, decision='create_new', side='seller')
        # Clear side to simulate approve without confirmation
        classification = dict(session.classification or {})
        classification['side'] = 'unknown'
        session.classification = classification
        db.session.flush()

        with pytest.raises(ValueError, match='side|representing'):
            approve_selected(
                session=session,
                user_id=user.id,
                selected_fields={},
                corrections={},
                confirmed_side=None,
                party_resolutions=[],
            )


def test_party_create_and_link_separate_participants(app, seed):
    with app.app_context():
        user = _user(seed)
        org_id = seed['org_a']

        existing = Contact(
            organization_id=org_id,
            user_id=user.id,
            created_by_id=user.id,
            first_name='Billy',
            last_name='Copaus',
            email='billy@test.com',
        )
        db.session.add(existing)
        db.session.flush()

        session = _bootstrap_session(user, org_id)
        classify_and_extract(
            session=session,
            field_data={
                'property_address': '6004 Lakeside Drive',
                'document_type': 'residential_contract',
                'side': 'seller',
                'seller_name': 'Billy Copaus and Kimberly Copaus',
                'buyer_name': 'Clark Smith',
                'sales_price': '500000',
            },
        )
        parties = build_party_proposals(session, user.id)
        assert len(parties) == 3
        assert parties[0]['party_key'] == 'seller_0'
        assert parties[0]['role'] == 'seller'
        assert parties[1]['party_key'] == 'seller_1'
        assert parties[1]['role'] == 'co_seller'
        assert parties[2]['role'] == 'buyer'

        resolve_match(session=session, decision='create_new', side='seller')

        resolutions = [
            {
                'party_key': 'seller_0',
                'role': 'seller',
                'full_name': 'Billy Copaus',
                'action': 'link',
                'contact_id': existing.id,
            },
            {
                'party_key': 'seller_1',
                'role': 'co_seller',
                'full_name': 'Kimberly Copaus',
                'action': 'create',
                'first_name': 'Kimberly',
                'last_name': 'Copaus',
            },
            {
                'party_key': 'buyer_0',
                'role': 'buyer',
                'full_name': 'Clark Smith',
                'action': 'create',
                'first_name': 'Clark',
                'last_name': 'Smith',
            },
        ]

        transaction, _proposal = approve_selected(
            session=session,
            user_id=user.id,
            selected_fields={'sales_price': True, 'property_address': True},
            corrections={},
            confirmed_side='seller',
            party_resolutions=resolutions,
        )
        db.session.flush()

        participants = TransactionParticipant.query.filter_by(
            transaction_id=transaction.id,
        ).order_by(TransactionParticipant.id).all()

        assert len(participants) == 3
        by_role = {p.role: p for p in participants}
        assert by_role['seller'].contact_id == existing.id
        assert by_role['co_seller'].contact_id is not None
        assert by_role['buyer'].contact_id is not None

        kim = Contact.query.get(by_role['co_seller'].contact_id)
        clark = Contact.query.get(by_role['buyer'].contact_id)
        assert kim.first_name == 'Kimberly' and kim.last_name == 'Copaus'
        assert clark.first_name == 'Clark' and clark.last_name == 'Smith'
        assert session.status == ContractBootstrapSession.STATUS_APPLIED
        assert Transaction.query.get(transaction.id) is not None


def test_unmatched_parties_default_to_create(app, seed):
    with app.app_context():
        user = _user(seed)
        session = _bootstrap_session(user, seed['org_a'])
        classify_and_extract(
            session=session,
            field_data={
                'property_address': '88 New Contact Way',
                'document_type': 'listing_agreement',
                'side': 'seller',
                'seller_name': 'Pat Seller',
                'seller_email': 'pat.seller@example.com',
                'seller_phone': '5125550199',
                'street_address': '88 New Contact Way',
                'city': 'Austin',
                'state': 'TX',
                'zip_code': '78701',
            },
        )
        parties = build_party_proposals(session, user.id)
        assert len(parties) == 1
        assert parties[0]['default_action'] == 'create'
        assert parties[0]['recommended_contact_id'] is None
        assert parties[0]['email'] == 'pat.seller@example.com'
        assert parties[0]['phone'] == '(512) 555-0199'

        resolutions = default_party_resolutions(session, user.id)
        assert resolutions[0]['action'] == 'create'
        assert resolutions[0]['email'] == 'pat.seller@example.com'


def test_approve_without_parties_creates_contact_from_document(app, seed):
    with app.app_context():
        user = _user(seed)
        session = _bootstrap_session(user, seed['org_a'])
        classify_and_extract(
            session=session,
            field_data={
                'property_address': '88 New Contact Way',
                'document_type': 'listing_agreement',
                'side': 'seller',
                'seller_name': 'Pat Seller',
                'seller_email': 'pat.seller@example.com',
                'seller_phone': '(512) 555-0199',
                'city': 'Austin',
                'state': 'TX',
                'zip_code': '78701',
            },
        )
        resolve_match(session=session, decision='create_new', side='seller')
        before = Contact.query.filter_by(
            organization_id=seed['org_a'],
            email='pat.seller@example.com',
        ).count()

        transaction, _proposal = approve_selected(
            session=session,
            user_id=user.id,
            selected_fields={'property_address': True},
            corrections={},
            confirmed_side='seller',
        )
        db.session.flush()

        contact = Contact.query.filter_by(
            organization_id=seed['org_a'],
            email='pat.seller@example.com',
        ).one()
        assert before == 0
        assert contact.first_name == 'Pat'
        assert contact.last_name == 'Seller'
        assert contact.phone == '(512) 555-0199'
        assert contact.city == 'Austin'
        participant = TransactionParticipant.query.filter_by(
            transaction_id=transaction.id,
            role='seller',
        ).one()
        assert participant.contact_id == contact.id


def test_high_confidence_name_match_still_links(app, seed):
    with app.app_context():
        user = _user(seed)
        existing = Contact(
            organization_id=seed['org_a'],
            user_id=user.id,
            created_by_id=user.id,
            first_name='Pat',
            last_name='Seller',
            email='already@example.com',
        )
        db.session.add(existing)
        db.session.flush()

        session = _bootstrap_session(user, seed['org_a'])
        classify_and_extract(
            session=session,
            field_data={
                'property_address': '90 Existing Contact Way',
                'document_type': 'listing_agreement',
                'side': 'seller',
                'seller_name': 'Pat Seller',
            },
        )
        parties = build_party_proposals(session, user.id)
        assert parties[0]['default_action'] == 'link'
        assert parties[0]['recommended_contact_id'] == existing.id

        resolve_match(session=session, decision='create_new', side='seller')
        transaction, _proposal = approve_selected(
            session=session,
            user_id=user.id,
            selected_fields={'property_address': True},
            corrections={},
            confirmed_side='seller',
        )
        db.session.flush()
        participant = TransactionParticipant.query.filter_by(
            transaction_id=transaction.id,
            role='seller',
        ).one()
        assert participant.contact_id == existing.id
        assert Contact.query.filter_by(
            organization_id=seed['org_a'],
            first_name='Pat',
            last_name='Seller',
        ).count() == 1
