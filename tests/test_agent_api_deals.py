"""AgentDesk deal-file APIs: listing, offers, contract, checklist, notes, parties."""
from __future__ import annotations

from models import (
    SellerAcceptedContract,
    SellerListingProfile,
    Transaction,
    TransactionParticipant,
    TransactionRequirement,
    db,
)


def _auth(token):
    return {'Authorization': f'Bearer {token}'}


def _owner_token(client):
    resp = client.post(
        '/api/agent/v1/session',
        json={'email': 'owner_a@test.com', 'password': 'password123'},
        content_type='application/json',
    )
    assert resp.status_code == 200
    return resp.get_json()['token']


def _as_money(value):
    if value in (None, ''):
        return None
    return float(str(value).replace(',', '').replace('$', '').strip())


def _fresh_seller(app, seed, address):
    with app.app_context():
        tx = Transaction(
            organization_id=seed['org_a'],
            created_by_id=seed['owner_a'],
            transaction_type_id=seed['tx_type_a'],
            street_address=address,
            city='Austin',
            state='TX',
            status='active',
        )
        db.session.add(tx)
        db.session.commit()
        return tx.id


def test_get_offers_on_seller_tx(app, seed, client):
    headers = _auth(_owner_token(client))
    resp = client.get(
        f'/api/agent/v1/transactions/{seed["tx_a"]}/offers',
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.content_type.startswith('application/json')
    body = resp.get_json()
    assert isinstance(body.get('offers'), list)


def test_post_offer_then_get(app, seed, client):
    headers = _auth(_owner_token(client))
    tx_id = _fresh_seller(app, seed, '510 Offer Create Ln')

    created = client.post(
        f'/api/agent/v1/transactions/{tx_id}/offers',
        headers=headers,
        json={
            'offer_price': 425000,
            'financing_type': 'conventional',
            'earnest_money': 4000,
            'option_fee': 200,
            'option_days': 7,
            'concessions': 2500,
            'proposed_close_date': '2026-09-15',
            'buyer_name': 'Casey Buyer',
            'notes': 'Verbal, waiting on PDF',
        },
    )
    assert created.status_code == 201, created.get_json()
    offer = created.get_json()['offer']
    assert offer['id']
    assert offer['status'] == 'new'
    assert _as_money(offer['offer_price']) == 425000
    assert offer['financing_type']
    assert offer['option_days'] == 7
    assert _as_money(offer['concessions']) == 2500
    assert offer['proposed_close_date'] == '2026-09-15'
    assert offer['buyer_name'] == 'Casey Buyer'
    assert offer['notes'] == 'Verbal, waiting on PDF'
    offer_id = offer['id']

    listed = client.get(
        f'/api/agent/v1/transactions/{tx_id}/offers',
        headers=headers,
    )
    assert listed.status_code == 200
    ids = [row['id'] for row in listed.get_json()['offers']]
    assert offer_id in ids


def test_expire_offer(app, seed, client):
    headers = _auth(_owner_token(client))
    tx_id = _fresh_seller(app, seed, '511 Offer Expire Ln')
    created = client.post(
        f'/api/agent/v1/transactions/{tx_id}/offers',
        headers=headers,
        json={'offer_price': 300000, 'buyer_name': 'Expiring Buyer'},
    )
    assert created.status_code == 201
    offer_id = created.get_json()['offer']['id']

    expired = client.post(
        f'/api/agent/v1/transactions/{tx_id}/offers/{offer_id}/expire',
        headers=headers,
        json={},
    )
    assert expired.status_code == 200, expired.get_json()
    assert expired.get_json()['offer']['status'] == 'expired'


def test_accept_creates_seller_accepted_contract(app, seed, client):
    headers = _auth(_owner_token(client))
    tx_id = seed['tx_a']

    created = client.post(
        f'/api/agent/v1/transactions/{tx_id}/offers',
        headers=headers,
        json={
            'offer_price': 500000,
            'financing_type': 'conventional',
            'earnest_money': 5000,
            'option_days': 10,
            'proposed_close_date': '2026-10-20',
            'buyer_name': 'Jordan Buyer',
        },
    )
    assert created.status_code == 201, created.get_json()
    offer_id = created.get_json()['offer']['id']

    accepted = client.post(
        f'/api/agent/v1/transactions/{tx_id}/offers/{offer_id}/accept',
        headers=headers,
        json={'as_backup': False, 'effective_date': '2026-08-05'},
    )
    assert accepted.status_code == 200, accepted.get_json()
    body = accepted.get_json()
    assert body['offer']['status'] == 'accepted_primary'
    assert body['contract']['id']
    assert body['contract']['position'] == 'primary'
    assert _as_money(body['contract']['accepted_price']) == 500000
    assert body['transaction']['id'] == tx_id

    with app.app_context():
        row = SellerAcceptedContract.query.filter_by(
            transaction_id=tx_id,
            offer_id=offer_id,
            status='active',
        ).one()
        assert row.position == 'primary'
        assert (row.extra_data or {}).get('created_via') == (
            'controlling_contracts.create_baseline_from_accepted_offer'
        )


def test_patch_listing_persists_and_enrich_shows_price(app, seed, client):
    headers = _auth(_owner_token(client))
    tx_id = _fresh_seller(app, seed, '512 Listing Price Ln')

    patched = client.patch(
        f'/api/agent/v1/transactions/{tx_id}/listing',
        headers=headers,
        json={'list_price': 425000, 'lockbox_combo': '4321'},
    )
    assert patched.status_code == 200, patched.get_json()
    listing = patched.get_json()['transaction']['listing']
    assert _as_money(listing['list_price']) == 425000
    assert listing['lockbox_combo'] == '4321'

    detail = client.get(
        f'/api/agent/v1/transactions/{tx_id}',
        headers=headers,
    )
    assert detail.status_code == 200
    shown = detail.get_json()['transaction'].get('listing') or {}
    assert _as_money(shown.get('list_price')) == 425000

    with app.app_context():
        tx = Transaction.query.get(tx_id)
        overrides = (tx.extra_data or {}).get('listing_info_overrides') or {}
        assert _as_money(overrides.get('list_price')) == 425000
        assert (tx.extra_data or {}).get('lockbox_combo') == '4321'
        profile = SellerListingProfile.query.filter_by(transaction_id=tx_id).first()
        assert profile is not None
        assert _as_money(profile.current_list_price) == 425000


def test_requirement_due_date(app, seed, client):
    headers = _auth(_owner_token(client))
    tx_id = _fresh_seller(app, seed, '513 Requirement Due Ln')
    with app.app_context():
        req = TransactionRequirement(
            organization_id=seed['org_a'],
            transaction_id=tx_id,
            package_key='listing_prep',
            phase_key='property_prep',
            requirement_key='agent_desk_due_date_test',
            title='Confirm lockbox',
            work_status='pending',
        )
        db.session.add(req)
        db.session.commit()
        req_id = req.id

    updated = client.post(
        f'/api/agent/v1/transactions/{tx_id}/requirements/{req_id}/due-date',
        headers=headers,
        json={'due_at': '2026-09-01'},
    )
    assert updated.status_code == 200, updated.get_json()
    row = updated.get_json()['requirement']
    assert row['due_at'].startswith('2026-09-01')
    assert row['due_at_manual_override'] is True

    listed = client.get(
        f'/api/agent/v1/transactions/{tx_id}/requirements',
        headers=headers,
    )
    assert listed.status_code == 200
    keys = [item['requirement_key'] for item in listed.get_json()['requirements']]
    assert 'agent_desk_due_date_test' in keys


def test_delete_participant(app, seed, client):
    headers = _auth(_owner_token(client))
    tx_id = seed['tx_a']
    with app.app_context():
        party = TransactionParticipant(
            organization_id=seed['org_a'],
            transaction_id=tx_id,
            role='title_company',
            name='Desk Title Co',
            is_primary=False,
        )
        db.session.add(party)
        db.session.commit()
        party_id = party.id

    removed = client.delete(
        f'/api/agent/v1/transactions/{tx_id}/participants/{party_id}',
        headers=headers,
    )
    assert removed.status_code == 200
    assert removed.get_json() == {'ok': True}

    missing = client.delete(
        f'/api/agent/v1/transactions/{tx_id}/participants/{party_id}',
        headers=headers,
    )
    assert missing.status_code == 404

    with app.app_context():
        assert TransactionParticipant.query.get(party_id) is None


def test_post_note_then_get(app, seed, client):
    headers = _auth(_owner_token(client))
    tx_id = _fresh_seller(app, seed, '514 Notes Ln')

    created = client.post(
        f'/api/agent/v1/transactions/{tx_id}/notes',
        headers=headers,
        json={'text': 'Called seller about photos.'},
    )
    assert created.status_code == 201, created.get_json()
    notes = created.get_json()['notes']
    assert notes
    assert notes[-1]['text'] == 'Called seller about photos.'
    assert notes[-1]['user_id'] == seed['owner_a']
    assert notes[-1]['at']

    listed = client.get(
        f'/api/agent/v1/transactions/{tx_id}/notes',
        headers=headers,
    )
    assert listed.status_code == 200
    texts = [row['text'] for row in listed.get_json()['notes']]
    assert 'Called seller about photos.' in texts


def test_deal_routes_require_jwt(app, seed, client):
    tx_id = seed['tx_a']
    resp = client.get(f'/api/agent/v1/transactions/{tx_id}/offers')
    assert resp.status_code == 401
    assert resp.content_type.startswith('application/json')
    assert 'token' not in (resp.get_json() or {})


def test_org_b_cannot_see_org_a_tx(app, seed, client):
    other = client.post(
        '/api/agent/v1/session',
        json={'email': 'owner_b@test.com', 'password': 'password123'},
        content_type='application/json',
    )
    assert other.status_code == 200
    resp = client.get(
        f'/api/agent/v1/transactions/{seed["tx_a"]}/offers',
        headers=_auth(other.get_json()['token']),
    )
    assert resp.status_code in (403, 404)
    assert resp.content_type.startswith('application/json')
