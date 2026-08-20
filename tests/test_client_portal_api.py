"""Client iPhone app API: invite codes, JWT sessions, client-safe deal."""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from models import (
    ClientPortalAccess,
    DocumentSignature,
    Organization,
    PortalMessage,
    SellerCommissionTerms,
    SellerListingProfile,
    SellerShowing,
    Transaction,
    TransactionDocument,
    TransactionParticipant,
    TransactionRequirement,
    TransactionType,
    db,
)


LOCKBOX_SECRET = 'LOCK-SECRET-99'
RENTCAST_SECRET = 'RENTCAST-HIDE-ME'
OTHER_PARTY_EMAIL = 'other-party-secret@example.com'
COMMISSION_TITLE = 'Listing commission CDA'


def _seller_tx(seed, address='900 Client App Ln'):
    tx_type = TransactionType.query.filter_by(
        organization_id=seed['org_a'], name='seller',
    ).first()
    tx = Transaction(
        organization_id=seed['org_a'],
        created_by_id=seed['owner_a'],
        transaction_type_id=tx_type.id,
        street_address=address,
        city='Austin',
        state='TX',
        zip_code='78701',
        status='active',
        rentcast_data={'secret': RENTCAST_SECRET},
    )
    db.session.add(tx)
    db.session.flush()
    return tx


def _participant(seed, tx, *, role='seller', contact_id=None, name=None, email=None):
    p = TransactionParticipant(
        organization_id=seed['org_a'],
        transaction_id=tx.id,
        contact_id=contact_id,
        role=role,
        name=name,
        email=email,
        is_primary=True,
    )
    db.session.add(p)
    db.session.flush()
    return p


def _grant(seed, tx, participant, *, invite_expires_at=None):
    access = ClientPortalAccess(
        organization_id=seed['org_a'],
        transaction_id=tx.id,
        participant_id=participant.id,
        token=ClientPortalAccess.generate_token(),
        invite_code=ClientPortalAccess.generate_invite_code(),
        session_version=1,
        is_active=True,
        invite_expires_at=invite_expires_at,
    )
    db.session.add(access)
    db.session.flush()
    return access


def _auth_headers(token):
    return {'Authorization': f'Bearer {token}'}


def _open_session(client, code):
    return client.post(
        '/api/client/v1/session',
        json={'code': code},
        content_type='application/json',
    )


def test_invite_code_create_rotate_revoke_binds_one_participant(
    app, seed, owner_a_client,
):
    with app.app_context():
        tx = _seller_tx(seed, '901 Invite Bind Ln')
        seller = _participant(seed, tx, contact_id=seed['contact_a'])
        other = _participant(
            seed, tx, role='co_seller', name='Pat Seller',
            email='pat-seller@example.com',
        )
        db.session.commit()
        tx_id, seller_id, other_id = tx.id, seller.id, other.id

    created = owner_a_client.post(
        f'/transactions/{tx_id}/portal/create',
        json={'participant_id': seller_id},
    )
    assert created.status_code == 200
    seller_link = created.get_json()['link']
    assert seller_link['participant_id'] == seller_id
    assert seller_link['invite_code']
    seller_code = seller_link['invite_code']

    other_created = owner_a_client.post(
        f'/transactions/{tx_id}/portal/create',
        json={'participant_id': other_id},
    )
    other_code = other_created.get_json()['link']['invite_code']
    assert other_code != seller_code

    seller_session = _open_session(owner_a_client, seller_code)
    other_session = _open_session(owner_a_client, other_code)
    assert seller_session.status_code == 200
    assert other_session.status_code == 200

    seller_deal = owner_a_client.get(
        '/api/client/v1/deal',
        headers=_auth_headers(seller_session.get_json()['token']),
    ).get_json()
    other_deal = owner_a_client.get(
        '/api/client/v1/deal',
        headers=_auth_headers(other_session.get_json()['token']),
    ).get_json()
    assert seller_deal['client_first_name'] == 'Jane'
    assert other_deal['client_first_name'] == 'Pat'

    rotated = owner_a_client.post(
        f"/transactions/{tx_id}/portal/{seller_link['access_id']}/rotate",
    )
    assert rotated.status_code == 200
    new_code = rotated.get_json()['link']['invite_code']
    assert new_code != seller_code
    assert _open_session(owner_a_client, seller_code).status_code == 401
    assert _open_session(owner_a_client, new_code).status_code == 200
    assert owner_a_client.get(
        '/api/client/v1/deal',
        headers=_auth_headers(seller_session.get_json()['token']),
    ).status_code == 401

    revoked = owner_a_client.post(
        f"/transactions/{tx_id}/portal/{seller_link['access_id']}/revoke",
    )
    assert revoked.status_code == 200
    assert _open_session(owner_a_client, new_code).status_code == 401


def test_session_exchange_valid_invalid_revoked_expired(app, seed, client):
    with app.app_context():
        tx = _seller_tx(seed, '902 Session Ln')
        seller = _participant(seed, tx, contact_id=seed['contact_a'])
        access = _grant(seed, tx, seller)
        expired = _grant(
            seed, tx,
            _participant(seed, tx, role='co_seller', name='Expired Person',
                         email='expired-person@example.com'),
            invite_expires_at=datetime.utcnow() - timedelta(days=1),
        )
        revoked = _grant(
            seed, tx,
            _participant(seed, tx, role='co_seller', name='Revoked Person',
                         email='revoked-person@example.com'),
        )
        revoked.revoke()
        org = db.session.get(Organization, seed['org_a'])
        org.logo_url = 'https://example.com/origen-logo.png'
        org.brand_accent = '#123456'
        db.session.commit()
        valid_code = access.invite_code_display
        expired_code = expired.invite_code
        revoked_code = revoked.invite_code

    try:
        ok = _open_session(client, valid_code)
        assert ok.status_code == 200
        body = ok.get_json()
        assert body['token']
        assert body['token_type'] == 'Bearer'
        assert body['expires_in'] > 0
        branding = body['branding']
        assert branding['name'] == 'Test Realty A'
        assert branding['logo_url'] == 'https://example.com/origen-logo.png'
        assert branding['accent'] == '#123456'

        assert _open_session(client, 'NOTA-CODE').status_code == 401
        assert _open_session(client, revoked_code).status_code == 401
        assert _open_session(client, expired_code).status_code == 401
    finally:
        with app.app_context():
            org = db.session.get(Organization, seed['org_a'])
            org.logo_url = None
            org.brand_accent = None
            db.session.commit()


def test_jwt_required_cookie_login_is_not_enough(app, seed, owner_a_client, client):
    with app.app_context():
        tx = _seller_tx(seed, '903 Jwt Gate Ln')
        seller = _participant(seed, tx, contact_id=seed['contact_a'])
        access = _grant(seed, tx, seller)
        db.session.commit()
        code = access.invite_code

    token = _open_session(client, code).get_json()['token']

    for path in (
        '/api/client/v1/deal',
        '/api/client/v1/messages',
        '/api/client/v1/documents',
    ):
        assert owner_a_client.get(path).status_code == 401
        assert client.get(path).status_code == 401
        assert client.get(path, headers=_auth_headers(token)).status_code == 200

    assert owner_a_client.post(
        '/api/client/v1/showings/1/approve',
    ).status_code == 401
    assert owner_a_client.post(
        '/api/client/v1/showings/1/decline',
    ).status_code == 401
    assert owner_a_client.post('/api/client/v1/session/leave').status_code == 401


def test_deal_payload_is_client_safe(app, seed, client):
    with app.app_context():
        tx = _seller_tx(seed, '904 Safe Deal Ln')
        seller = _participant(seed, tx, contact_id=seed['contact_a'])
        _participant(
            seed, tx, role='buyer', name='Hidden Buyer',
            email=OTHER_PARTY_EMAIL,
        )
        access = _grant(seed, tx, seller)
        db.session.add(SellerListingProfile(
            organization_id=seed['org_a'],
            transaction_id=tx.id,
            created_by_id=seed['owner_a'],
            current_list_price=450000,
            access_type='lockbox',
            lockbox_type='supra',
            gate_code=LOCKBOX_SECRET,
            private_showing_notes='Do not tell the client the combo.',
        ))
        db.session.add(SellerCommissionTerms(
            organization_id=seed['org_a'],
            transaction_id=tx.id,
            created_by_id=seed['owner_a'],
            listing_commission_percent=3,
            listing_commission_flat=15000,
        ))
        db.session.add(SellerShowing(
            organization_id=seed['org_a'],
            transaction_id=tx.id,
            created_by_id=seed['owner_a'],
            showing_agent_name='Rival Agent',
            showing_agent_email='rival@other-broker.com',
            showing_agent_phone='5559990000',
            showing_agent_brokerage='Other Brokerage',
            buyer_name='Secret Buyer',
            scheduled_start_at=datetime.utcnow() + timedelta(days=1),
            status=SellerShowing.STATUS_PENDING_APPROVAL,
            access_instructions_snapshot=LOCKBOX_SECRET,
            private_notes='Lockbox code inside',
        ))
        db.session.add(TransactionRequirement(
            organization_id=seed['org_a'],
            transaction_id=tx.id,
            package_key='test',
            phase_key='option',
            requirement_key='earnest_money',
            title='Deliver earnest money',
            work_status='pending',
            responsible_party_label='Seller',
        ))
        db.session.add(TransactionRequirement(
            organization_id=seed['org_a'],
            transaction_id=tx.id,
            package_key='test',
            phase_key='internal',
            requirement_key='listing_commission_cda',
            title=COMMISSION_TITLE,
            work_status='pending',
        ))
        db.session.commit()
        code = access.invite_code

    token = _open_session(client, code).get_json()['token']
    resp = client.get('/api/client/v1/deal', headers=_auth_headers(token))
    assert resp.status_code == 200
    deal = resp.get_json()
    blob = json.dumps(deal)

    assert deal['property']['street'] == '904 Safe Deal Ln'
    titles = [row['title'] for row in deal['requirements']]
    assert 'Deliver earnest money' in titles
    assert COMMISSION_TITLE not in titles
    assert 'assignee_user_id' not in blob
    assert 'risk_level' not in blob

    assert 'commission' not in blob.lower()
    assert LOCKBOX_SECRET not in blob
    assert RENTCAST_SECRET not in blob
    assert OTHER_PARTY_EMAIL not in blob
    assert 'rival@other-broker.com' not in blob
    assert '5559990000' not in blob
    assert 'Secret Buyer' not in blob
    assert 'lockbox' not in blob.lower()
    assert 'rentcast' not in blob.lower()
    assert deal['showings']['items'][0]['id']
    assert deal['showings']['items'][0]['can_decide'] is True
    assert 'showing_agent_name' not in blob


def test_documents_only_that_participant(app, seed, client, monkeypatch):
    monkeypatch.setattr(
        'services.supabase_storage.get_transaction_document_url',
        lambda path, expires_in=3600: f'https://signed.example/{path}',
    )

    with app.app_context():
        tx = _seller_tx(seed, '905 Docs Ln')
        seller = _participant(seed, tx, contact_id=seed['contact_a'])
        access = _grant(seed, tx, seller)
        mine = TransactionDocument(
            organization_id=seed['org_a'],
            transaction_id=tx.id,
            template_slug='listing-agreement',
            template_name='Listing Agreement',
            status='signed',
            signed_file_path='org/tx/listing-signed.pdf',
        )
        theirs = TransactionDocument(
            organization_id=seed['org_a'],
            transaction_id=tx.id,
            template_slug='buyer-rep',
            template_name='Buyer Rep',
            status='signed',
            signed_file_path='org/tx/buyer-rep-signed.pdf',
        )
        db.session.add_all([mine, theirs])
        db.session.flush()
        db.session.add(DocumentSignature(
            organization_id=seed['org_a'],
            document_id=mine.id,
            participant_id=seller.id,
            signer_email='jane@test.com',
            signer_name='Jane Doe',
            signer_role='seller',
            status='signed',
        ))
        db.session.add(DocumentSignature(
            organization_id=seed['org_a'],
            document_id=theirs.id,
            signer_email=OTHER_PARTY_EMAIL,
            signer_name='Hidden Buyer',
            signer_role='buyer',
            status='signed',
        ))
        db.session.commit()
        code = access.invite_code

    token = _open_session(client, code).get_json()['token']
    docs = client.get(
        '/api/client/v1/documents', headers=_auth_headers(token),
    ).get_json()
    names = [row['name'] for row in docs['completed']]
    assert 'Listing Agreement' in names
    assert 'Buyer Rep' not in names
    listing = next(row for row in docs['completed'] if row['name'] == 'Listing Agreement')
    assert listing['view_url'].startswith('https://signed.example/')


def test_showing_approve_decline_only_when_pending(app, seed, client):
    with app.app_context():
        tx = _seller_tx(seed, '906 Showing Ln')
        seller = _participant(seed, tx, contact_id=seed['contact_a'])
        access = _grant(seed, tx, seller)
        pending = SellerShowing(
            organization_id=seed['org_a'],
            transaction_id=tx.id,
            created_by_id=seed['owner_a'],
            showing_agent_name='Tour Agent',
            showing_agent_brokerage='Visitor Realty',
            scheduled_start_at=datetime.utcnow() + timedelta(days=2),
            status=SellerShowing.STATUS_PENDING_APPROVAL,
        )
        scheduled = SellerShowing(
            organization_id=seed['org_a'],
            transaction_id=tx.id,
            created_by_id=seed['owner_a'],
            showing_agent_name='Already Booked',
            showing_agent_brokerage='Visitor Realty',
            scheduled_start_at=datetime.utcnow() + timedelta(days=3),
            status=SellerShowing.STATUS_SCHEDULED,
        )
        to_decline = SellerShowing(
            organization_id=seed['org_a'],
            transaction_id=tx.id,
            created_by_id=seed['owner_a'],
            showing_agent_name='Maybe Not',
            showing_agent_brokerage='Visitor Realty',
            scheduled_start_at=datetime.utcnow() + timedelta(days=4),
            status=SellerShowing.STATUS_PENDING_APPROVAL,
        )
        db.session.add_all([pending, scheduled, to_decline])
        db.session.commit()
        code = access.invite_code
        pending_id, scheduled_id, decline_id = pending.id, scheduled.id, to_decline.id

    token = _open_session(client, code).get_json()['token']
    headers = _auth_headers(token)

    denied = client.post(
        f'/api/client/v1/showings/{scheduled_id}/approve', headers=headers,
    )
    assert denied.status_code == 409

    approved = client.post(
        f'/api/client/v1/showings/{pending_id}/approve', headers=headers,
    )
    assert approved.status_code == 200
    assert approved.get_json()['status'] == 'approved'
    assert client.post(
        f'/api/client/v1/showings/{pending_id}/approve', headers=headers,
    ).status_code == 409

    declined = client.post(
        f'/api/client/v1/showings/{decline_id}/decline', headers=headers,
    )
    assert declined.status_code == 200
    assert declined.get_json()['status'] == 'declined'


def test_session_leave_invalidates_jwt_not_invite(app, seed, client):
    with app.app_context():
        tx = _seller_tx(seed, '907 Leave Ln')
        seller = _participant(seed, tx, contact_id=seed['contact_a'])
        access = _grant(seed, tx, seller)
        db.session.commit()
        code = access.invite_code

    first = _open_session(client, code)
    token = first.get_json()['token']
    assert client.get(
        '/api/client/v1/deal', headers=_auth_headers(token),
    ).status_code == 200

    left = client.post(
        '/api/client/v1/session/leave', headers=_auth_headers(token),
    )
    assert left.status_code == 200
    assert client.get(
        '/api/client/v1/deal', headers=_auth_headers(token),
    ).status_code == 401

    again = _open_session(client, code)
    assert again.status_code == 200
    assert client.get(
        '/api/client/v1/deal',
        headers=_auth_headers(again.get_json()['token']),
    ).status_code == 200


def test_messages_round_trip(app, seed, client):
    with app.app_context():
        tx = _seller_tx(seed, '908 Notes Ln')
        seller = _participant(seed, tx, contact_id=seed['contact_a'])
        access = _grant(seed, tx, seller)
        db.session.add(PortalMessage(
            organization_id=seed['org_a'],
            transaction_id=tx.id,
            participant_id=seller.id,
            sender='agent',
            kind='update',
            body='Inspection is booked for Thursday.',
            author_user_id=seed['owner_a'],
        ))
        db.session.commit()
        code = access.invite_code

    token = _open_session(client, code).get_json()['token']
    headers = _auth_headers(token)
    listed = client.get('/api/client/v1/messages', headers=headers).get_json()
    assert listed['messages'][0]['body'] == 'Inspection is booked for Thursday.'

    posted = client.post(
        '/api/client/v1/messages',
        headers=headers,
        json={'body': 'Thanks, I can be there.'},
    )
    assert posted.status_code == 201
    again = client.get('/api/client/v1/messages', headers=headers).get_json()
    bodies = [m['body'] for m in again['messages']]
    assert 'Thanks, I can be there.' in bodies


def test_feature_flag_hides_invite_and_branding(app, seed, owner_b_client):
    settings = owner_b_client.get('/org/settings')
    assert settings.status_code == 200
    html = settings.get_data(as_text=True)
    assert 'Client app accent' not in html
    assert 'iPhone app' not in html

    owner_b_client.post('/org/settings/update', data={
        'name': 'Test Realty B',
        'brand_accent': '#112233',
    }, follow_redirects=True)
    with app.app_context():
        org_b = db.session.get(Organization, seed['org_b'])
        assert org_b.brand_accent is None

    invite = owner_b_client.post(
        f'/transactions/{seed["tx_b"]}/portal/create',
        json={'participant_id': 1},
    )
    assert invite.status_code in (302, 403, 404)


def test_branding_fields_persist(app, seed, owner_a_client, client):
    try:
        resp = owner_a_client.post('/org/settings/update', data={
            'name': 'Origen Realty',
            'logo_url': 'https://example.com/origen.png',
            'brand_accent': '#ea580c',
        }, follow_redirects=True)
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert 'Client app accent' in html
        assert 'https://example.com/origen.png' in html
        assert '#ea580c' in html

        with app.app_context():
            org = db.session.get(Organization, seed['org_a'])
            assert org.name == 'Origen Realty'
            assert org.logo_url == 'https://example.com/origen.png'
            assert org.brand_accent == '#ea580c'
            tx = _seller_tx(seed, '909 Brand Ln')
            seller = _participant(seed, tx, contact_id=seed['contact_a'])
            access = _grant(seed, tx, seller)
            db.session.commit()
            code = access.invite_code

        session = _open_session(client, code).get_json()
        branding = session['branding']
        assert branding['name'] == 'Origen Realty'
        assert branding['logo_url'] == 'https://example.com/origen.png'
        assert branding['accent'] == '#ea580c'
    finally:
        with app.app_context():
            org = db.session.get(Organization, seed['org_a'])
            org.name = 'Test Realty A'
            org.logo_url = None
            org.brand_accent = None
            db.session.commit()


def test_invite_ui_on_transaction_not_old_portal_chrome(app, seed, owner_a_client):
    with app.app_context():
        tx = _seller_tx(seed, '910 Invite Ui Ln')
        _participant(seed, tx, contact_id=seed['contact_a'])
        db.session.commit()
        tx_id = tx.id

    page = owner_a_client.get(f'/transactions/{tx_id}')
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert 'Client app invite' in html
    assert 'Create invite' in html
    assert 'Client portal' not in html
    assert 'showing approval board' not in html.lower()


def test_expired_jwt_is_rejected(app, seed, client):
    import time
    from services.client_portal_auth import issue_client_jwt

    with app.app_context():
        tx = _seller_tx(seed, '912 Expired Jwt Ln')
        seller = _participant(seed, tx, contact_id=seed['contact_a'])
        access = _grant(seed, tx, seller)
        db.session.commit()
        token = issue_client_jwt(access, now=int(time.time()) - 120, ttl_seconds=30)

    assert client.get(
        '/api/client/v1/deal', headers=_auth_headers(token),
    ).status_code == 401


def test_default_branding_uses_product_orange(app, seed, client):
    with app.app_context():
        tx = _seller_tx(seed, '911 Default Brand Ln')
        seller = _participant(seed, tx, contact_id=seed['contact_a'])
        access = _grant(seed, tx, seller)
        db.session.commit()
        code = access.invite_code

    branding = _open_session(client, code).get_json()['branding']
    assert branding['accent'] == '#f97316'
    assert branding['accent_ink'] == '#ea580c'
    assert branding['name'] == 'Test Realty A'
