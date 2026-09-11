"""Phase 2 offer compare assist (read-only)."""

from decimal import Decimal

from models import SellerOffer, SellerOfferVersion, Transaction, db
from services.offer_compare import OfferCompareService


def _offer(org_id, tx_id, user_id, **kwargs):
    defaults = dict(
        organization_id=org_id,
        transaction_id=tx_id,
        created_by_id=user_id,
        buyer_names=kwargs.pop('buyer_names', 'Buyer A'),
        status='new',
        offer_price=kwargs.pop('offer_price', Decimal('400000')),
        earnest_money=kwargs.pop('earnest_money', Decimal('5000')),
        financing_type=kwargs.pop('financing_type', 'conventional'),
    )
    terms_data = kwargs.pop('terms_data', {})
    defaults.update(kwargs)
    offer = SellerOffer(**defaults)
    db.session.add(offer)
    db.session.flush()
    version = SellerOfferVersion(
        organization_id=org_id,
        transaction_id=tx_id,
        offer_id=offer.id,
        created_by_id=user_id,
        version_number=1,
        direction='buyer_offer',
        status='submitted',
        terms_data=terms_data,
    )
    db.session.add(version)
    db.session.flush()
    offer.current_version_id = version.id
    db.session.flush()
    return offer


def test_compare_offers_highlights_differences(app, seed):
    with app.app_context():
        org_id = seed['org_a']
        tx = Transaction.query.get(seed['tx_a'])
        user_id = seed['owner_a']

        _offer(
            org_id, tx.id, user_id,
            buyer_names='Alpha',
            offer_price=Decimal('410000'),
            option_fee=Decimal('200'),
            financing_type='conventional',
        )
        _offer(
            org_id, tx.id, user_id,
            buyer_names='Bravo',
            offer_price=Decimal('425000'),
            option_fee=Decimal('500'),
            financing_type='cash',
        )
        db.session.commit()

        result = OfferCompareService.compare_offers(tx)
        assert result['read_only'] is True
        assert result['offer_count'] == 2
        assert 'offer_price' in result['differing_fields']
        assert result['highlights']['highest_price']['buyer_label'] == 'Bravo'
        assert 'Comparing 2 offers' in result['summary']


def test_compare_offers_filters_by_ids(app, seed):
    with app.app_context():
        org_id = seed['org_a']
        tx = Transaction.query.get(seed['tx_a'])
        user_id = seed['owner_a']
        a = _offer(org_id, tx.id, user_id, buyer_names='Only A', offer_price=Decimal('300000'))
        _offer(org_id, tx.id, user_id, buyer_names='Skip Me', offer_price=Decimal('350000'))
        db.session.commit()

        result = OfferCompareService.compare_offers(tx, offer_ids=[a.id])
        assert result['offer_count'] == 1
        assert result['offers'][0]['buyer_names'] == 'Only A'


def test_compare_offers_surfaces_contract_terms(app, seed):
    with app.app_context():
        org_id = seed['org_a']
        tx = Transaction.query.get(seed['tx_a'])
        user_id = seed['owner_a']

        alpha = _offer(
            org_id, tx.id, user_id,
            buyer_names='Survey Alpha',
            offer_price=Decimal('410000'),
            survey_furnished_by='Seller shall furnish existing survey and T-47 affidavit',
            buyer_agent_commission_percent=Decimal('2.500'),
            residential_service_contract='650',
            title_policy_payer='Seller',
        )
        bravo = _offer(
            org_id, tx.id, user_id,
            buyer_names='Survey Bravo',
            offer_price=Decimal('425000'),
            survey_furnished_by='Buyer',
            buyer_agent_commission_flat=Decimal('3000'),
            residential_service_contract='900',
            title_policy_payer='Buyer',
        )
        db.session.commit()

        result = OfferCompareService.compare_offers(
            tx, offer_ids=[alpha.id, bravo.id],
        )
        rows = {row['field']: row for row in result['rows']}
        by_id = {col['offer_id']: col['terms'] for col in result['offers']}

        assert rows['survey_responsibility']['label'] == 'Survey provided by'
        assert rows['survey_responsibility']['differs'] is True
        assert by_id[alpha.id]['survey_responsibility'] == (
            'Seller will provide an existing survey'
        )
        assert by_id[bravo.id]['survey_responsibility'] == 'Buyer'

        assert rows['buyer_agent_commission']['label'] == "Commission to buyer's agent"
        assert by_id[alpha.id]['buyer_agent_commission'] == '2.5%'
        assert by_id[bravo.id]['buyer_agent_commission'] == '$3,000'

        assert rows['residential_service_contract']['label'] == 'Home warranty'
        assert by_id[alpha.id]['residential_service_contract'] == '$650'
        assert by_id[bravo.id]['residential_service_contract'] == '$900'

        assert rows['title_policy_payer']['label'] == 'Title policy paid by'
        assert by_id[alpha.id]['title_policy_payer'] == 'Seller'
        assert by_id[bravo.id]['title_policy_payer'] == 'Buyer'

        sources = {col['offer_id']: col['sources'] for col in result['offers']}
        assert sources[alpha.id]['survey_responsibility'] == 'offer'
        assert sources[alpha.id]['buyer_agent_commission'] == 'offer'
        assert sources[alpha.id]['residential_service_contract'] == 'offer'
        assert sources[alpha.id]['title_policy_payer'] == 'offer'


def test_compare_formatters_read_version_terms_data(app, seed):
    """Unreviewed offers keep survey, commission, warranty, and title on
    the version. The matrix still has to format those the same way."""
    with app.app_context():
        org_id = seed['org_a']
        tx = Transaction.query.get(seed['tx_a'])
        user_id = seed['owner_a']

        alpha = _offer(
            org_id, tx.id, user_id,
            buyer_names='Version Alpha',
            offer_price=Decimal('410000'),
            terms_data={
                'survey_furnished_by': (
                    'Seller shall furnish existing survey and T-47 affidavit'
                ),
                'buyer_agent_commission_percent': '2.5',
                'residential_service_contract': '650',
                'title_policy_payer': 'Seller',
            },
        )
        bravo = _offer(
            org_id, tx.id, user_id,
            buyer_names='Version Bravo',
            offer_price=Decimal('425000'),
            terms_data={
                'survey_choice': 'Buyer',
                'buyer_agent_commission_flat': '3000',
                'residential_service_contract': '900',
                'title_policy_payer': 'Buyer',
            },
        )
        db.session.commit()

        result = OfferCompareService.compare_offers(
            tx, offer_ids=[alpha.id, bravo.id],
        )
        by_id = {col['offer_id']: col['terms'] for col in result['offers']}
        sources = {col['offer_id']: col['sources'] for col in result['offers']}

        assert by_id[alpha.id]['survey_responsibility'] == (
            'Seller will provide an existing survey'
        )
        assert by_id[bravo.id]['survey_responsibility'] == 'Buyer'
        assert by_id[alpha.id]['buyer_agent_commission'] == '2.5%'
        assert by_id[bravo.id]['buyer_agent_commission'] == '$3,000'
        assert by_id[alpha.id]['residential_service_contract'] == '$650'
        assert by_id[bravo.id]['residential_service_contract'] == '$900'
        assert by_id[alpha.id]['title_policy_payer'] == 'Seller'
        assert by_id[bravo.id]['title_policy_payer'] == 'Buyer'

        assert sources[alpha.id]['survey_responsibility'] == (
            'version.terms_data.survey_furnished_by'
        )
        assert sources[bravo.id]['survey_responsibility'] == (
            'version.terms_data.survey_choice'
        )
        assert sources[alpha.id]['buyer_agent_commission'] == (
            'version.terms_data.buyer_agent_commission_percent'
        )
        assert sources[bravo.id]['buyer_agent_commission'] == (
            'version.terms_data.buyer_agent_commission_flat'
        )
        assert sources[alpha.id]['residential_service_contract'] == (
            'version.terms_data.residential_service_contract'
        )
        assert sources[bravo.id]['title_policy_payer'] == (
            'version.terms_data.title_policy_payer'
        )
