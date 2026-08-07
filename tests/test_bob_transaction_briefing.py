"""Deal-scoped Bob briefing + sticky conversation APIs."""

from decimal import Decimal

from models import (
    ChatConversation,
    ChatMessage,
    ContractBootstrapSession,
    SellerCommissionTerms,
    TransactionDocument,
    db,
)
from services.bob_transaction_briefing import (
    build_transaction_setup_facts,
    ensure_transaction_conversation,
    format_setup_briefing,
    seed_setup_briefing,
)
from services.document_intake_ui import append_bob_setup_query, resolve_bootstrap_next_url


def test_append_bob_setup_query_preserves_hash():
    assert append_bob_setup_query(
        '/transactions/9#offers',
        bootstrap_session_id=12,
    ) == '/transactions/9?bob=setup&bootstrap_session_id=12#offers'


def test_resolve_bootstrap_next_url_adds_bob_setup(app):
    with app.app_context():
        url = resolve_bootstrap_next_url(
            transaction_id=42,
            route_action='create_or_match_listing',
            bob_setup=True,
            bootstrap_session_id=7,
        )
    assert 'bob=setup' in url
    assert 'bootstrap_session_id=7' in url
    assert '/transactions/42' in url


def test_format_setup_briefing_mentions_needed_and_commission():
    text = format_setup_briefing({
        'address': '14046 Wolftrap Lane, Conroe, TX 77384',
        'city': 'Conroe',
        'documents_filed': [
            {
                'name': 'Residential Real Estate Listing Agreement',
                'filename': 'Residential Real Estate Listing Agreement - Exclusive Right to Sell - 126.pdf',
                'slug': 'listing-agreement',
            },
        ],
        'documents_needed': [
            {'name': "Seller's Disclosure Notice", 'slug': 'sellers-disclosure'},
        ],
        'questionnaire': {
            'has_hoa': 'yes',
            'special_districts': 'yes',
            'built_before_1978': 'no',
            'has_survey': 'not_sure',
        },
        'commission': {
            'listing_side_flat': 8000,
            'buyer_side_percent': 2,
            'notes': "$8,000 + 2% to a Buyer's Broker",
        },
        'issues': [],
    })
    assert '14046 Wolftrap Lane, Conroe, TX 77384' in text
    assert text.count('Conroe') == 1  # no duplicated city
    assert 'Listing Agreement' in text
    assert 'Exclusive Right to Sell' not in text  # filename noise omitted
    assert "Seller's Disclosure" in text
    assert '$8,000 listing + 2% buyer broker' in text
    assert "$8,000 + 2% to a Buyer's Broker" not in text  # notes not echoed when structured
    assert 'HOA' in text
    assert 'special tax district' in text
    assert 'survey unclear' in text
    assert 'not_sure' not in text
    assert 'Still need' in text


def test_build_facts_and_seed_briefing_idempotent(app, seed):
    with app.app_context():
        tx_id = seed['tx_a']
        org_id = seed['org_a']
        user_id = seed['owner_a']

        db.session.add(TransactionDocument(
            organization_id=org_id,
            transaction_id=tx_id,
            template_slug='listing-agreement',
            template_name='Listing Agreement',
            status='signed',
            document_source='external',
            signed_original_filename='listing.pdf',
            signed_file_path='/tmp/listing.pdf',
            is_placeholder=False,
        ))
        db.session.add(TransactionDocument(
            organization_id=org_id,
            transaction_id=tx_id,
            template_slug='sellers-disclosure',
            template_name="Seller's Disclosure Notice",
            status='pending',
            document_source='placeholder',
            is_placeholder=True,
        ))
        db.session.add(SellerCommissionTerms(
            organization_id=org_id,
            transaction_id=tx_id,
            created_by_id=user_id,
            listing_commission_flat=Decimal('8000'),
            coop_compensation_percent=Decimal('2'),
            source='listing_agreement_extraction',
        ))
        from models import Transaction
        tx = Transaction.query.get(tx_id)
        tx.intake_data = {
            'has_hoa': True,
            'special_districts': True,
            'built_before_1978': False,
        }
        db.session.commit()

        facts = build_transaction_setup_facts(tx, organization_id=org_id)
        assert any(d['slug'] == 'listing-agreement' for d in facts['documents_filed'])
        assert any(d['slug'] == 'sellers-disclosure' for d in facts['documents_needed'])
        assert facts['commission']['listing_side_flat'] == 8000.0

        conversation = ensure_transaction_conversation(
            user_id=user_id,
            org_id=org_id,
            transaction=tx,
        )
        conversation, msg, created = seed_setup_briefing(
            conversation=conversation,
            transaction=tx,
        )
        db.session.commit()
        assert created is True
        assert msg is not None
        assert conversation.setup_briefing_sent_at is not None
        assert "Seller's Disclosure" in msg.content

        conversation, msg2, created2 = seed_setup_briefing(
            conversation=conversation,
            transaction=tx,
        )
        assert created2 is False
        assert msg2.id == msg.id
        assert ChatMessage.query.filter_by(conversation_id=conversation.id).count() == 1


def test_ensure_conversation_api_binds_transaction(app, seed, owner_a_client):
    tx_id = seed['tx_a']
    with app.app_context():
        from models import Transaction
        # Isolate from other tests that may have seeded a briefing on tx_a.
        ChatMessage.query.filter(
            ChatMessage.conversation_id.in_(
                db.session.query(ChatConversation.id).filter_by(
                    transaction_id=tx_id,
                    user_id=seed['owner_a'],
                )
            )
        ).delete(synchronize_session=False)
        ChatConversation.query.filter_by(
            transaction_id=tx_id,
            user_id=seed['owner_a'],
        ).delete(synchronize_session=False)
        tx = Transaction.query.get(tx_id)
        tx.intake_data = {'has_hoa': True}
        db.session.add(TransactionDocument(
            organization_id=seed['org_a'],
            transaction_id=tx_id,
            template_slug='iabs',
            template_name='IABS',
            status='signed',
            signed_file_path='/tmp/iabs.pdf',
            signed_original_filename='iabs.pdf',
            is_placeholder=False,
        ))
        db.session.commit()

    resp = owner_a_client.post(
        f'/api/ai-chat/transactions/{tx_id}/ensure-conversation',
        json={'seed_briefing': True},
        content_type='application/json',
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    data = resp.get_json()
    assert data['transaction_id'] == tx_id
    assert data['setup_briefing_sent'] is True
    assert data['briefing_created'] is True
    assert len(data['messages']) >= 1

    again = owner_a_client.post(
        f'/api/ai-chat/transactions/{tx_id}/setup-briefing',
        json={},
        content_type='application/json',
    )
    assert again.status_code == 200
    again_data = again.get_json()
    assert again_data['briefing_created'] is False
    assert again_data['id'] == data['id']


def test_ensure_conversation_cross_org_blocked(app, seed, owner_a_client):
    resp = owner_a_client.post(
        f'/api/ai-chat/transactions/{seed["tx_b"]}/ensure-conversation',
        json={'seed_briefing': False},
        content_type='application/json',
    )
    assert resp.status_code in (403, 404)


def test_hydrate_page_entity_includes_needed_docs(app, seed):
    from routes.ai_chat import hydrate_page_entity
    from flask_login import login_user
    from models import Transaction, User

    with app.app_context():
        user = User.query.get(seed['owner_a'])
        tx = Transaction.query.get(seed['tx_a'])
        tx.intake_data = {'special_districts': True}
        db.session.add(TransactionDocument(
            organization_id=seed['org_a'],
            transaction_id=tx.id,
            template_slug='special-tax-district-notice',
            template_name='Special Tax District Notice',
            status='pending',
            is_placeholder=True,
            document_source='placeholder',
        ))
        db.session.commit()

        with app.test_request_context('/'):
            login_user(user)
            entity = hydrate_page_entity('transaction', tx.id)
            assert entity is not None
            assert entity.entity_id == tx.id
            needed = entity.summary.get('documents_needed') or []
            assert any(
                d.get('slug') == 'special-tax-district-notice' for d in needed
            )
