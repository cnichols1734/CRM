"""Offer summary email: copy, matrix, overrides, render, and send guard."""

from datetime import date, datetime
from decimal import Decimal

import pytest

from services import offer_summary_email as ose


class FakeType:
    def __init__(self, name):
        self.name = name


class FakeContact:
    def __init__(self, first_name, email=None):
        self.first_name = first_name
        self.last_name = 'Nichols'
        self.email = email


class FakeParticipant:
    def __init__(self, role, name, email=None, is_primary=True, contact=None):
        self.role = role
        self.name = name
        self.email = email
        self.is_primary = is_primary
        self.contact = contact
        self.user = None

    @property
    def display_name(self):
        if self.contact:
            return f'{self.contact.first_name} {self.contact.last_name}'
        return self.name


class FakeTransaction:
    def __init__(self, side='seller', participants=None):
        self.id = 7
        self.transaction_type = FakeType(side)
        self.street_address = '6048 Heritage Creek Dr'
        self.city = 'Katy'
        self.state = 'TX'
        self.zip_code = '77494'
        self.participants = participants or [
            FakeParticipant('seller', 'Cassie Nichols', 'cassie@origenrealty.com'),
        ]

    @property
    def full_address(self):
        return f'{self.street_address}, {self.city}, {self.state} {self.zip_code}'


class FakeOffer:
    _next_id = 1

    def __init__(self, **kwargs):
        self.id = kwargs.pop('id', None) or FakeOffer._next_id
        FakeOffer._next_id = self.id + 1
        self.status = kwargs.pop('status', 'new')
        self.buyer_names = kwargs.pop('buyer_names', None)
        self.buyer_agent_name = kwargs.pop('buyer_agent_name', None)
        self.buyer_agent_brokerage = kwargs.pop('buyer_agent_brokerage', None)
        self.offer_price = kwargs.pop('offer_price', None)
        self.financing_type = kwargs.pop('financing_type', None)
        self.earnest_money = kwargs.pop('earnest_money', None)
        self.option_fee = kwargs.pop('option_fee', None)
        self.option_period_days = kwargs.pop('option_period_days', None)
        self.seller_concessions_amount = kwargs.pop('seller_concessions_amount', None)
        self.proposed_close_date = kwargs.pop('proposed_close_date', None)
        self.buyer_agent_commission_percent = kwargs.pop('buyer_agent_commission_percent', None)
        self.buyer_agent_commission_flat = kwargs.pop('buyer_agent_commission_flat', None)
        self.survey_furnished_by = kwargs.pop('survey_furnished_by', None)
        self.survey_payer = kwargs.pop('survey_payer', None)
        self.residential_service_contract = kwargs.pop('residential_service_contract', None)
        self.sale_of_other_property_contingency = kwargs.pop(
            'sale_of_other_property_contingency', None,
        )
        self.non_realty_items = kwargs.pop('non_realty_items', None)
        self.offer_documents = kwargs.pop('offer_documents', [])
        self.terms_summary = kwargs.pop('terms_summary', {})
        for key, value in kwargs.items():
            setattr(self, key, value)


class FakeOfferDocument:
    def __init__(self, document_type):
        self.document_type = document_type


class FakeVersion:
    def __init__(self, terms_data):
        self.terms_data = terms_data


class FakeGmail:
    def __init__(self, email='cassie@gmail.com', sync_enabled=True, needs_reauth=False):
        self.connected_email = email
        self.sync_enabled = sync_enabled
        self.needs_reauth = needs_reauth


class FakeAgent:
    first_name = 'Cassie'
    last_name = 'Nichols'
    email = 'cassie@origenrealty.com'
    phone = '(832) 414-0353'
    email_integration = None


class FakeOrg:
    name = 'Origen Realty'
    broker_name = 'Origen Realty'
    broker_license_number = '9003104'
    broker_address = '123 Main St, Katy, TX 77494'
    logo_url = None


def full_offer(**overrides):
    defaults = dict(
        buyer_names='Jordan and Riley Vance',
        buyer_agent_name='Dana Reed',
        buyer_agent_brokerage='Keller Williams',
        offer_price=Decimal('425000'),
        financing_type='conventional',
        earnest_money=Decimal('5000'),
        option_fee=Decimal('300'),
        option_period_days=7,
        seller_concessions_amount=Decimal('4000'),
        proposed_close_date=date(2026, 3, 15),
        buyer_agent_commission_percent=Decimal('2.500'),
        survey_furnished_by='Seller shall furnish existing survey and T-47 affidavit',
        residential_service_contract='650',
    )
    defaults.update(overrides)
    return FakeOffer(**defaults)


def build(offers, *, side='seller', overrides=None, organization=None):
    if not isinstance(offers, (list, tuple)):
        offers = [offers]
    return ose.build_draft(
        FakeTransaction(side=side),
        offers,
        agent=FakeAgent(),
        organization=organization or FakeOrg(),
        side=side,
        overrides=overrides,
    )


def row_keys(draft):
    return {spec['key'] for spec in draft.row_specs}


# ---------------------------------------------------------------------------
# Single offer
# ---------------------------------------------------------------------------

def test_subject_leads_with_the_address_and_price():
    draft = build(full_offer())
    assert draft.subject == 'New offer on 6048 Heritage Creek Dr: $425,000'


def test_price_and_financing_carry_the_headline_not_a_table_row():
    draft = build(full_offer())
    assert draft.headline['value'] == '$425,000'
    assert draft.headline['caption'] == 'Conventional loan'
    keys = [spec['key'] for spec in draft.row_specs]
    assert 'offer_price' not in keys
    assert 'financing_type' not in keys


def test_remaining_terms_read_in_plain_language():
    draft = build(full_offer())
    block = draft.offers[0]
    assert block.value('earnest_money') == '$5,000'
    assert block.value('option_period') == '7 days, $300 fee'
    assert block.value('proposed_close_date') == 'March 15, 2026'
    assert block.value('seller_concessions_amount') == '$4,000'
    assert block.value('buyer_agent_commission') == '2.5%'
    assert block.value('survey_responsibility') == ose.SURVEY_EXISTING
    assert block.value('residential_service_contract') == '$650'
    assert block.value('sale_of_other_property') == 'No'


def test_the_row_labels_are_the_ones_a_client_reads():
    draft = build(full_offer())
    labels = {spec['key']: spec['label'] for spec in draft.row_specs}
    assert labels['seller_concessions_amount'] == 'Seller contributions'
    assert labels['buyer_agent_commission'] == "Commission to buyer's agent"
    assert labels['survey_responsibility'] == 'Who pays for the survey'
    assert labels['residential_service_contract'] == 'Home warranty'
    assert labels['sale_of_other_property'] == 'Contingent on buyer selling another property'


# ---------------------------------------------------------------------------
# The net sheet is gone from the client email
# ---------------------------------------------------------------------------

def test_the_draft_carries_no_estimated_net():
    draft = build(full_offer())
    assert 'estimated_net' not in row_keys(draft)
    assert not hasattr(draft, 'include_net')
    assert not hasattr(draft, 'net_available')
    payload = draft.as_payload()
    assert 'include_net' not in payload
    assert 'net_available' not in payload


def test_an_include_net_override_from_an_old_client_is_ignored():
    draft = build(full_offer(), overrides={'include_net': True})
    assert 'estimated_net' not in row_keys(draft)
    assert 'estimated_net' not in draft.offers[0].cells


def test_a_stray_estimated_net_figure_override_is_dropped():
    offer = full_offer()
    draft = build(offer, overrides={
        'terms': {str(offer.id): {'estimated_net': '$401,000'}},
    })
    assert 'estimated_net' not in draft.offers[0].cells


def test_rendered_html_has_no_net_language(app):
    low, high = compare_set()
    draft = build([low, high])
    with app.app_context():
        html = ose.render_html(draft)
    assert 'Estimated net' not in html
    assert 'settlement statement' not in html


# ---------------------------------------------------------------------------
# Commission to the buyer's agent
# ---------------------------------------------------------------------------

def test_commission_shows_the_percent_when_that_is_what_was_written():
    draft = build(full_offer(buyer_agent_commission_percent=Decimal('3.000')))
    assert draft.offers[0].value('buyer_agent_commission') == '3%'


def test_commission_shows_the_flat_fee_when_that_is_what_was_written():
    draft = build(full_offer(
        buyer_agent_commission_percent=None,
        buyer_agent_commission_flat=Decimal('9000'),
    ))
    assert draft.offers[0].value('buyer_agent_commission') == '$9,000'


def test_commission_shows_both_when_the_contract_has_both():
    draft = build(full_offer(
        buyer_agent_commission_percent=Decimal('2.5'),
        buyer_agent_commission_flat=Decimal('500'),
    ))
    assert draft.offers[0].value('buyer_agent_commission') == '2.5% + $500'


def test_commission_row_is_dropped_when_neither_is_set():
    draft = build(full_offer(buyer_agent_commission_percent=None))
    assert 'buyer_agent_commission' not in row_keys(draft)


def test_commission_falls_back_to_terms_summary():
    offer = full_offer(
        buyer_agent_commission_percent=None,
        terms_summary={'buyer_agent_commission_percent': '2.75'},
    )
    assert build(offer).offers[0].value('buyer_agent_commission') == '2.75%'


# ---------------------------------------------------------------------------
# Who pays for the survey
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('written, expected', [
    ('Seller shall furnish existing survey and T-47 affidavit', ose.SURVEY_EXISTING),
    ('seller existing survey', ose.SURVEY_EXISTING),
    ('Buyer shall obtain a new survey at Buyer\'s expense', ose.SURVEY_BUYER),
    ('buyer new survey', ose.SURVEY_BUYER),
    ('Seller, at Seller\'s expense, shall furnish a new survey', ose.SURVEY_SELLER),
    ('seller new survey', ose.SURVEY_SELLER),
])
def test_survey_prose_collapses_to_the_ticked_box(written, expected):
    draft = build(full_offer(survey_furnished_by=written))
    assert draft.offers[0].value('survey_responsibility') == expected


def test_survey_falls_back_to_the_payer_column():
    draft = build(full_offer(survey_furnished_by=None, survey_payer='Buyer'))
    assert draft.offers[0].value('survey_responsibility') == ose.SURVEY_BUYER


def test_survey_row_is_dropped_when_nothing_was_read():
    draft = build(full_offer(survey_furnished_by=None))
    assert 'survey_responsibility' not in row_keys(draft)


# ---------------------------------------------------------------------------
# Home warranty
# ---------------------------------------------------------------------------

def test_home_warranty_is_a_dollar_amount():
    draft = build(full_offer(residential_service_contract='900'))
    assert draft.offers[0].value('residential_service_contract') == '$900'


def test_home_warranty_keeps_cents_the_contract_wrote():
    draft = build(full_offer(residential_service_contract='549.99'))
    assert draft.offers[0].value('residential_service_contract') == '$549.99'


def test_a_zero_home_warranty_is_not_a_term():
    draft = build(full_offer(residential_service_contract='0'))
    assert 'residential_service_contract' not in row_keys(draft)


def test_legacy_home_warranty_prose_passes_through():
    draft = build(full_offer(residential_service_contract='Seller to pay'))
    assert draft.offers[0].value('residential_service_contract') == 'Seller to pay'


# ---------------------------------------------------------------------------
# Title policy paid by
# ---------------------------------------------------------------------------

def test_title_policy_payer_appears_in_row_specs_when_present():
    draft = build(full_offer(title_policy_payer='Seller'))
    labels = {spec['key']: spec['label'] for spec in draft.row_specs}
    assert 'title_policy_payer' in labels
    assert labels['title_policy_payer'] == 'Title policy paid by'
    assert draft.offers[0].value('title_policy_payer') == 'Seller'


def test_title_policy_payer_row_is_dropped_when_nothing_was_read():
    draft = build(full_offer())
    assert 'title_policy_payer' not in row_keys(draft)


def test_title_policy_payer_falls_back_to_version_terms_data():
    offer = full_offer(
        title_policy_payer=None,
        terms_summary={},
        current_version=FakeVersion({'title_policy_payer': 'Buyer'}),
    )
    draft = build(offer)
    assert 'title_policy_payer' in row_keys(draft)
    assert draft.offers[0].value('title_policy_payer') == 'Buyer'


# ---------------------------------------------------------------------------
# Contingent on the buyer selling another property
# ---------------------------------------------------------------------------

def test_no_addendum_means_no():
    draft = build(full_offer())
    assert draft.offers[0].value('sale_of_other_property') == 'No'
    assert 'sale_of_other_property' in row_keys(draft)


def test_contingency_column_means_yes():
    draft = build(full_offer(sale_of_other_property_contingency=True))
    assert draft.offers[0].value('sale_of_other_property') == 'Yes'


def test_addenda_entry_means_yes():
    offer = full_offer(terms_summary={
        'addenda': {'sale_of_other_property_addendum': {'deadline_days': 30}},
    })
    assert build(offer).offers[0].value('sale_of_other_property') == 'Yes'


def test_a_null_addenda_entry_still_means_no():
    offer = full_offer(terms_summary={
        'addenda': {'sale_of_other_property_addendum': None},
    })
    assert build(offer).offers[0].value('sale_of_other_property') == 'No'


def test_detected_document_segment_means_yes():
    offer = full_offer(terms_summary={
        'detected_documents': [
            {'document_type': 'buyer_offer', 'start_page': 1, 'end_page': 9},
            {'document_type': 'sale_of_other_property', 'start_page': 10, 'end_page': 10},
        ],
    })
    assert build(offer).offers[0].value('sale_of_other_property') == 'Yes'


def test_a_typed_offer_document_means_yes():
    offer = full_offer(offer_documents=[FakeOfferDocument('sale_of_other_property')])
    assert build(offer).offers[0].value('sale_of_other_property') == 'Yes'


def test_the_contingency_is_never_marked_a_winner():
    low, high = compare_set()
    high.sale_of_other_property_contingency = True
    draft = build([low, high])
    assert all(not block.cells['sale_of_other_property'].wins for block in draft.offers)


# ---------------------------------------------------------------------------
# Non-realty items
# ---------------------------------------------------------------------------

def test_non_realty_items_are_a_paragraph_not_a_row():
    draft = build(full_offer(non_realty_items='Refrigerator\nWasher and dryer'))
    assert 'non_realty_items' not in row_keys(draft)
    assert [spec['key'] for spec in draft.long_rows] == ['non_realty_items']
    assert draft.offers[0].value('non_realty_items') == 'Refrigerator\nWasher and dryer'


def test_no_non_realty_addendum_means_no_block():
    draft = build(full_offer())
    assert draft.long_rows == []
    assert 'non_realty_items' not in draft.offers[0].cells


def test_non_realty_items_read_from_the_addenda_bag_for_older_offers():
    offer = full_offer(terms_summary={
        'addenda': {
            'non_realty_items_addendum': {
                'items': ['Refrigerator', 'Pool equipment'],
                'price': '0',
            },
        },
    })
    assert build(offer).offers[0].value('non_realty_items') == 'Refrigerator\nPool equipment'


def test_non_realty_items_travel_in_the_payload():
    draft = build(full_offer(non_realty_items='Refrigerator'))
    payload = draft.as_payload()
    assert payload['long_rows'] == [{
        'key': 'non_realty_items',
        'label': 'Non-realty items addendum, the buyer is asking for',
    }]
    assert payload['offers'][0]['terms']['non_realty_items']['value'] == 'Refrigerator'


def test_non_realty_items_render_one_line_per_item(app):
    draft = build(full_offer(non_realty_items='Refrigerator\nWasher and dryer'))
    with app.app_context():
        html = ose.render_html(draft)
    assert 'Non-realty items addendum, the buyer is asking for' in html
    assert 'Refrigerator' in html
    assert 'Washer and dryer' in html


def test_non_realty_items_sit_under_the_matrix_per_offer(app):
    low, high = compare_set()
    high.non_realty_items = 'Refrigerator'
    draft = build([low, high])
    with app.app_context():
        html = ose.render_html(draft)
    assert html.count('Refrigerator') == 1
    # Alpha has no addendum, so only Bravo gets a block under the table.
    block_section = html.split('Non-realty items addendum, the buyer is asking for', 1)[1]
    assert 'Bravo Buyer' in block_section
    assert 'Alpha Buyer' not in block_section.split('Refrigerator', 1)[0]


def test_non_realty_items_are_in_the_text_alternative():
    text = ose.render_text(build(full_offer(non_realty_items='Refrigerator\nPatio set')))
    assert 'Non-realty items addendum, the buyer is asking for:' in text
    assert '  Refrigerator' in text
    assert '  Patio set' in text


def test_one_option_day_is_singular():
    draft = build(full_offer(option_period_days=1, option_fee=None))
    assert draft.offers[0].value('option_period') == '1 day'


def test_greeting_uses_the_client_first_name():
    draft = build(full_offer())
    assert draft.greeting == 'Hi Cassie,'


def test_greeting_names_both_clients():
    transaction = FakeTransaction(participants=[
        FakeParticipant('seller', 'Cassie Nichols', 'cassie@origenrealty.com'),
        FakeParticipant('co_seller', 'Chris Nichols', 'chris@origenrealty.com',
                        is_primary=False),
    ])
    draft = ose.build_draft(
        transaction, [full_offer()], agent=FakeAgent(), organization=FakeOrg(),
    )
    assert draft.greeting == 'Hi Cassie and Chris,'


def test_a_summary_built_after_a_terms_edit_shows_the_new_number():
    offer = full_offer()
    first = build(offer)
    offer.offer_price = Decimal('432500')
    offer.proposed_close_date = date(2026, 4, 1)
    second = build(offer)

    assert first.headline['value'] == '$425,000'
    assert second.headline['value'] == '$432,500'
    assert second.offers[0].value('proposed_close_date') == 'April 1, 2026'


def test_terms_summary_fills_a_gap_the_column_left_empty():
    offer = full_offer(offer_price=None, terms_summary={'sales_price': '418000'})
    draft = build(offer)
    assert draft.headline['value'] == '$418,000'


def test_unreviewed_version_terms_data_fills_draft_fields():
    """A scoped offer still sitting on the version, not the denormalized
    columns, has to show those terms in the client email."""
    offer = full_offer(
        buyer_agent_commission_percent=None,
        buyer_agent_commission_flat=None,
        survey_furnished_by=None,
        survey_payer=None,
        residential_service_contract=None,
        terms_summary={},
        current_version=FakeVersion({
            'survey_furnished_by': (
                'Seller shall furnish existing survey and T-47 affidavit'
            ),
            'buyer_agent_commission_percent': '2.5',
            'residential_service_contract': '650',
        }),
    )
    draft = build(offer)
    block = draft.offers[0]
    assert block.value('buyer_agent_commission') == '2.5%'
    assert block.value('survey_responsibility') == ose.SURVEY_EXISTING
    assert block.value('residential_service_contract') == '$650'


def test_reviewed_terms_summary_wins_over_version_terms_data():
    """A reviewed or manual terms_summary keeps its values. Version
    terms_data only fills keys the summary left blank or omitted."""
    offer = full_offer(
        title_policy_payer=None,
        buyer_agent_commission_percent=None,
        buyer_agent_commission_flat=None,
        residential_service_contract=None,
        terms_summary={
            'title_policy_payer': 'Seller',
            'buyer_agent_commission_percent': '3',
        },
        current_version=FakeVersion({
            'title_policy_payer': 'Buyer',
            'buyer_agent_commission_percent': '2.5',
            'residential_service_contract': '650',
        }),
    )
    draft = build(offer)
    block = draft.offers[0]
    assert block.value('title_policy_payer') == 'Seller'
    assert block.value('buyer_agent_commission') == '3%'
    assert block.value('residential_service_contract') == '$650'


def test_version_terms_data_fills_only_blank_summary_keys():
    offer = FakeOffer(terms_summary={
        'title_policy_payer': 'Seller',
        'buyer_agent_commission_percent': '',
    })
    backed = ose._VersionBackedOffer(offer, {
        'title_policy_payer': 'Buyer',
        'buyer_agent_commission_percent': '2.5',
        'residential_service_contract': '650',
    })
    assert backed.terms_summary['title_policy_payer'] == 'Seller'
    assert backed.terms_summary['buyer_agent_commission_percent'] == '2.5'
    assert backed.terms_summary['residential_service_contract'] == '650'


def test_reviewed_survey_payer_wins_over_version_furnished_by():
    """survey_payer, survey_furnished_by, and survey_choice are one family.
    A reviewed payer must block version furnished_by from joining the merge."""
    offer = FakeOffer(terms_summary={'survey_payer': 'Buyer'})
    backed = ose._VersionBackedOffer(offer, {'survey_furnished_by': 'Seller'})
    assert backed.terms_summary['survey_payer'] == 'Buyer'
    assert 'survey_furnished_by' not in backed.terms_summary
    assert ose._pick(backed, 'survey_furnished_by') == 'Buyer'
    assert ose._alias_family('survey_furnished_by') == ose._alias_family('survey_payer')
    assert 'survey_choice' in ose._alias_family('survey_payer')

    draft_offer = full_offer(
        survey_furnished_by=None,
        survey_payer=None,
        terms_summary={'survey_payer': 'Buyer'},
        current_version=FakeVersion({'survey_furnished_by': 'Seller'}),
    )
    assert build(draft_offer).offers[0].value('survey_responsibility') == ose.SURVEY_BUYER


def test_canonical_survey_payer_blocks_version_furnished_by():
    """SellerOffer.survey_payer occupies the survey family even when
    terms_summary is empty. Version survey_furnished_by must stay out."""
    offer = FakeOffer(survey_payer='Buyer', terms_summary={})
    backed = ose._VersionBackedOffer(offer, {'survey_furnished_by': 'Seller'})
    assert 'survey_furnished_by' not in backed.terms_summary
    assert ose._survey_responsibility(backed) == ose.SURVEY_BUYER

    draft_offer = full_offer(
        survey_furnished_by=None,
        survey_payer='Buyer',
        terms_summary={},
        current_version=FakeVersion({'survey_furnished_by': 'Seller'}),
    )
    assert build(draft_offer).offers[0].value('survey_responsibility') == ose.SURVEY_BUYER


def test_version_merge_picks_one_alias_in_pick_order():
    """terms_data insertion order must not beat _TERM_ALIASES order.
    survey_payer first in the dict still loses to survey_furnished_by.
    sales_price first still loses to offer_price. Extra keys still copy."""
    offer = FakeOffer(terms_summary={})
    backed = ose._VersionBackedOffer(offer, {
        'survey_payer': 'Buyer',
        'survey_furnished_by': (
            'Seller shall furnish existing survey and T-47 affidavit'
        ),
        'sales_price': '400000',
        'offer_price': '450000',
        'non_realty_items': 'patio furniture',
    })
    assert backed.terms_summary['survey_furnished_by'].startswith('Seller shall')
    assert 'survey_payer' not in backed.terms_summary
    assert ose._survey_responsibility(backed) == ose.SURVEY_EXISTING
    assert backed.terms_summary['offer_price'] == '450000'
    assert 'sales_price' not in backed.terms_summary
    assert ose._pick(backed, 'offer_price') == '450000'
    assert backed.terms_summary['non_realty_items'] == 'patio furniture'


def test_reviewed_sales_price_wins_over_version_offer_price():
    """Exact-key blank-fill used to let version offer_price sit next to
    reviewed sales_price. _pick then walked offer_price first."""
    offer = FakeOffer(terms_summary={'sales_price': '418000'})
    backed = ose._VersionBackedOffer(offer, {'offer_price': '450000'})
    assert backed.terms_summary['sales_price'] == '418000'
    assert 'offer_price' not in backed.terms_summary
    assert ose._pick(backed, 'offer_price') == '418000'

    draft_offer = full_offer(
        offer_price=None,
        terms_summary={'sales_price': '418000'},
        current_version=FakeVersion({'offer_price': '450000'}),
    )
    assert build(draft_offer).headline['value'] == '$418,000'


def test_build_draft_reads_current_version_like_compare(app, seed):
    """Same current_version_id lookup Compare uses, through the composer."""
    from models import SellerOffer, SellerOfferVersion, Transaction, db

    with app.app_context():
        org_id = seed['org_a']
        tx = Transaction.query.get(seed['tx_a'])
        offer = SellerOffer(
            organization_id=org_id,
            transaction_id=tx.id,
            created_by_id=seed['owner_a'],
            buyer_names='Version Alpha',
            status='new',
            offer_price=Decimal('410000'),
            financing_type='conventional',
        )
        db.session.add(offer)
        db.session.flush()
        version = SellerOfferVersion(
            organization_id=org_id,
            transaction_id=tx.id,
            offer_id=offer.id,
            created_by_id=seed['owner_a'],
            version_number=1,
            direction='buyer_offer',
            status='submitted',
            terms_data={
                'survey_furnished_by': (
                    'Seller shall furnish existing survey and T-47 affidavit'
                ),
                'buyer_agent_commission_percent': '2.5',
                'residential_service_contract': '650',
            },
        )
        db.session.add(version)
        db.session.flush()
        offer.current_version_id = version.id
        db.session.commit()
        offer_id = offer.id
        version_id = version.id
        try:
            draft = ose.build_draft(tx, [offer], side='seller')
            block = draft.offers[0]
            assert block.value('buyer_agent_commission') == '2.5%'
            assert block.value('survey_responsibility') == ose.SURVEY_EXISTING
            assert block.value('residential_service_contract') == '$650'
        finally:
            SellerOfferVersion.query.filter_by(id=version_id).delete()
            SellerOffer.query.filter_by(id=offer_id).delete()
            db.session.commit()


def test_a_thin_offer_says_what_is_missing_instead_of_implying_zero():
    draft = build(FakeOffer(buyer_names='Jordan Vance', offer_price=Decimal('400000')))
    assert draft.footnote == ose.MISSING_TERMS_NOTE
    assert draft.headline['value'] == '$400,000'


def test_a_complete_offer_has_no_footnote():
    assert build(full_offer()).footnote is None


def test_no_price_still_produces_a_usable_subject():
    draft = build(full_offer(offer_price=None))
    assert draft.subject == 'New offer on 6048 Heritage Creek Dr'
    assert draft.headline['value'] == 'Price not set yet'


def test_a_missing_cell_reads_not_set():
    block = ose.OfferBlock(offer_id=1, label='A', sublabel=None, status='new')
    assert block.value('earnest_money') == 'Not set'


def test_legacy_dash_override_does_not_reach_the_client():
    offer = full_offer()
    draft = build(offer, overrides={
        'terms': {str(offer.id): {'earnest_money': '\u2014'}},
    })
    assert draft.offers[0].value('earnest_money') == '$5,000'


def test_decimal_from_display_treats_placeholders_as_empty():
    assert ose._decimal_from_display('Not set') is None
    assert ose._decimal_from_display('-') is None
    assert ose._decimal_from_display('') is None
    assert ose._decimal_from_display('   ') is None
    assert ose._decimal_from_display('\u2014') is None
    assert ose._decimal_from_display('\u2013') is None
    assert ose._decimal_from_display('$425,000') == Decimal('425000')


# ---------------------------------------------------------------------------
# Buyer side
# ---------------------------------------------------------------------------

def test_buyer_side_speaks_for_the_buyer():
    draft = build(full_offer(), side='buyer')
    assert draft.subject.startswith('Your offer on')
    assert draft.intro == "Here's the offer we submitted on 6048 Heritage Creek Dr."
    assert 'hear back' in draft.closing


def test_buyer_side_shows_the_same_contract_terms():
    draft = build(full_offer(), side='buyer')
    assert {'buyer_agent_commission', 'survey_responsibility', 'sale_of_other_property'} <= row_keys(draft)


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------

def compare_set():
    low = full_offer(
        id=101, buyer_names='Alpha Buyer', offer_price=Decimal('410000'),
        proposed_close_date=date(2026, 3, 1), financing_type='fha',
    )
    high = full_offer(
        id=102, buyer_names='Bravo Buyer', offer_price=Decimal('440000'),
        proposed_close_date=date(2026, 4, 10), financing_type='cash',
    )
    return low, high


def test_comparison_orders_the_strongest_price_first():
    low, high = compare_set()
    draft = build([low, high])
    assert draft.mode == 'compare'
    assert [block.label for block in draft.offers] == ['Bravo Buyer', 'Alpha Buyer']


def test_comparison_subject_shows_the_spread():
    low, high = compare_set()
    draft = build([low, high])
    assert draft.subject == (
        '2 offers on 6048 Heritage Creek Dr: $410,000 to $440,000'
    )


def test_comparison_marks_the_best_price_and_the_soonest_close():
    low, high = compare_set()
    draft = build([low, high])
    by_label = {block.label: block for block in draft.offers}

    assert by_label['Bravo Buyer'].cells['offer_price'].wins is True
    assert by_label['Alpha Buyer'].cells['offer_price'].wins is False
    assert by_label['Alpha Buyer'].cells['proposed_close_date'].wins is True
    assert by_label['Bravo Buyer'].cells['proposed_close_date'].wins is False
    assert draft.has_winners is True


def test_identical_figures_are_not_marked_as_a_winner():
    low, high = compare_set()
    high.offer_price = low.offer_price
    draft = build([low, high])
    assert all(
        not block.cells['offer_price'].wins for block in draft.offers
    )


def test_the_matrix_carries_the_contract_terms_for_every_offer():
    low, high = compare_set()
    high.buyer_agent_commission_percent = None
    high.buyer_agent_commission_flat = Decimal('10000')
    draft = build([low, high])
    by_label = {block.label: block for block in draft.offers}
    assert 'buyer_agent_commission' in row_keys(draft)
    assert by_label['Alpha Buyer'].value('buyer_agent_commission') == '2.5%'
    assert by_label['Bravo Buyer'].value('buyer_agent_commission') == '$10,000'


def test_an_empty_row_is_dropped_from_the_matrix():
    low, high = compare_set()
    low.seller_concessions_amount = None
    high.seller_concessions_amount = None
    draft = build([low, high])
    assert 'seller_concessions_amount' not in {
        spec['key'] for spec in draft.row_specs
    }


def test_withdrawn_offers_are_not_offered_for_a_client_email():
    live = full_offer(id=201)
    dead = full_offer(id=202, status='withdrawn')
    assert [o.id for o in ose.selectable_offers([live, dead])] == [201]


# ---------------------------------------------------------------------------
# Agent overrides
# ---------------------------------------------------------------------------

def test_agent_wording_replaces_ours():
    draft = build(full_offer(), overrides={
        'subject': 'Great news on Heritage Creek',
        'intro': 'Read this one twice.',
        'closing': 'Call me tonight.',
        'note': 'I think we counter at 435.',
    })
    assert draft.subject == 'Great news on Heritage Creek'
    assert draft.intro == 'Read this one twice.'
    assert draft.closing == 'Call me tonight.'
    assert draft.note == 'I think we counter at 435.'


def test_blank_overrides_fall_back_to_generated_copy():
    draft = build(full_offer(), overrides={'subject': '   ', 'intro': ''})
    assert draft.subject == 'New offer on 6048 Heritage Creek Dr: $425,000'
    assert draft.intro.startswith('We received an offer')


def test_an_edited_figure_is_kept_and_flagged():
    offer = full_offer()
    draft = build(offer, overrides={
        'terms': {str(offer.id): {'earnest_money': '$7,500'}},
    })
    cell = draft.offers[0].cells['earnest_money']
    assert cell.value == '$7,500'
    assert cell.edited is True


def test_every_new_term_round_trips_an_agent_edit():
    offer = full_offer(non_realty_items='Refrigerator')
    edits = {
        'seller_concessions_amount': '$6,000',
        'buyer_agent_commission': '3%',
        'survey_responsibility': ose.SURVEY_BUYER,
        'residential_service_contract': '$800',
        'title_policy_payer': 'Buyer',
        'sale_of_other_property': 'Yes',
        'non_realty_items': 'Refrigerator\nRiding mower',
    }
    draft = build(offer, overrides={'terms': {str(offer.id): edits}})
    block = draft.offers[0]
    for key, value in edits.items():
        assert block.cells[key].value == value, key
        assert block.cells[key].edited is True, key


def test_an_agent_can_type_a_term_the_contract_left_blank():
    offer = full_offer(survey_furnished_by=None)
    draft = build(offer, overrides={
        'terms': {str(offer.id): {'survey_responsibility': ose.SURVEY_SELLER}},
    })
    assert draft.offers[0].value('survey_responsibility') == ose.SURVEY_SELLER
    assert 'survey_responsibility' in row_keys(draft)


def test_an_agent_can_add_non_realty_items_by_hand():
    offer = full_offer()
    draft = build(offer, overrides={
        'terms': {str(offer.id): {'non_realty_items': 'Refrigerator'}},
    })
    assert [spec['key'] for spec in draft.long_rows] == ['non_realty_items']
    assert draft.offers[0].cells['non_realty_items'].edited is True


def test_a_blank_non_realty_override_clears_the_generated_list():
    offer = full_offer(non_realty_items='Refrigerator')
    draft = build(offer, overrides={
        'terms': {str(offer.id): {'non_realty_items': ''}},
    })
    assert 'non_realty_items' not in draft.offers[0].cells
    assert [spec['key'] for spec in draft.long_rows] == []


def test_an_edited_figure_never_gets_the_winner_highlight():
    low, high = compare_set()
    draft = build([low, high], overrides={
        'terms': {'102': {'offer_price': '$999,000'}},
    })
    by_label = {block.label: block for block in draft.offers}
    assert by_label['Bravo Buyer'].cells['offer_price'].edited is True
    assert by_label['Bravo Buyer'].cells['offer_price'].wins is False


def test_retyping_the_same_value_is_not_an_edit():
    offer = full_offer()
    draft = build(offer, overrides={
        'terms': {str(offer.id): {'earnest_money': '$5,000'}},
    })
    assert draft.offers[0].cells['earnest_money'].edited is False


def test_at_least_one_offer_is_required():
    with pytest.raises(ValueError):
        build([])


# ---------------------------------------------------------------------------
# Recipients
# ---------------------------------------------------------------------------

def test_recipients_come_from_the_client_side_participants():
    transaction = FakeTransaction(participants=[
        FakeParticipant('seller', 'Cassie Nichols', 'cassie@origenrealty.com'),
        FakeParticipant('buyers_agent', 'Dana Reed', 'dana@kw.com'),
        FakeParticipant('title_company', 'Katy Title', 'closing@katytitle.com'),
    ])
    recipients = ose.resolve_recipients(transaction, 'seller')
    assert [r.email for r in recipients] == ['cassie@origenrealty.com']


def test_recipient_email_falls_back_to_the_linked_contact():
    transaction = FakeTransaction(participants=[
        FakeParticipant('seller', None, None,
                        contact=FakeContact('Cassie', 'cassie@origenrealty.com')),
    ])
    recipients = ose.resolve_recipients(transaction, 'seller')
    assert recipients[0].email == 'cassie@origenrealty.com'
    assert recipients[0].name == 'Cassie Nichols'


def test_a_participant_without_an_email_is_skipped():
    transaction = FakeTransaction(participants=[
        FakeParticipant('seller', 'Cassie Nichols', None),
    ])
    assert ose.resolve_recipients(transaction, 'seller') == []


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _assert_no_dash_placeholder(text: str) -> None:
    assert '&mdash;' not in text
    assert '&#8212;' not in text
    assert '\u2014' not in text
    assert '\u2013' not in text


def test_single_offer_html_renders(app):
    draft = build(full_offer())
    with app.app_context():
        html = ose.render_html(draft)
    assert '$425,000' in html
    assert 'Conventional loan' in html
    assert '7 days, $300 fee' in html
    assert 'March 15, 2026' in html
    assert 'Seller contributions' in html
    assert "Commission to buyer&#39;s agent" in html or "Commission to buyer's agent" in html
    assert '2.5%' in html
    assert ose.SURVEY_EXISTING in html
    assert 'Home warranty' in html
    assert '$650' in html
    assert 'Contingent on buyer selling another property' in html
    assert 'Origen Realty' in html
    assert 'Brokerage license #9003104' in html
    # Transactional mail must not carry a marketing opt-out.
    assert 'Unsubscribe' not in html
    _assert_no_dash_placeholder(html)


def test_the_brand_marks_are_absolute_urls(app):
    """A relative src is a broken image once the mail leaves us."""
    draft = build(full_offer())
    with app.app_context():
        html = ose.render_html(draft)
    assert draft.brand['mark_url'].startswith('https://')
    assert draft.brand['mark_url'] in html
    assert draft.brand['wordmark_url'] in html


def test_an_organization_logo_replaces_our_mark(app):
    org = FakeOrg()
    org.logo_url = 'https://cdn.example.com/brokerage.png'
    draft = build(full_offer(), organization=org)
    assert draft.brand['mark_url'] == 'https://cdn.example.com/brokerage.png'
    # We have no idea whether their art reads on the slate footer band.
    assert draft.brand['wordmark_url'] is None
    with app.app_context():
        html = ose.render_html(draft)
    assert 'origen-wordmark' not in html


def test_comparison_html_renders_a_column_per_offer(app):
    low, high = compare_set()
    draft = build([low, high])
    with app.app_context():
        html = ose.render_html(draft)
    assert 'Alpha Buyer' in html
    assert 'Bravo Buyer' in html
    assert '$440,000' in html
    assert 'Who pays for the survey' in html
    assert 'Offer comparison' in html
    _assert_no_dash_placeholder(html)


def test_comparison_html_writes_not_set_for_a_gap(app):
    low, high = compare_set()
    low.earnest_money = None
    draft = build([low, high])
    assert draft.offers[1].value('earnest_money') == 'Not set'
    with app.app_context():
        html = ose.render_html(draft)
    assert 'Not set' in html
    _assert_no_dash_placeholder(html)


def test_the_agent_note_survives_into_the_html(app):
    draft = build(full_offer(), overrides={
        'note': 'First line.\n\nSecond line.',
    })
    with app.app_context():
        html = ose.render_html(draft)
    assert 'First line.' in html
    assert 'Second line.' in html


def test_html_escapes_a_hostile_buyer_name(app):
    draft = build(full_offer(buyer_names='<script>alert(1)</script>'))
    with app.app_context():
        html = ose.render_html(draft)
    assert '<script>alert(1)</script>' not in html
    assert '&lt;script&gt;' in html


def test_text_alternative_lists_every_row():
    draft = build(full_offer())
    text = ose.render_text(draft)
    assert 'Hi Cassie,' in text
    assert 'Earnest money: $5,000' in text
    assert 'Closing date: March 15, 2026' in text
    assert 'Seller contributions: $4,000' in text
    assert "Commission to buyer's agent: 2.5%" in text
    assert f'Who pays for the survey: {ose.SURVEY_EXISTING}' in text
    assert 'Home warranty: $650' in text
    assert 'Contingent on buyer selling another property: No' in text
    assert 'Estimated net' not in text
    assert 'Cassie Nichols' in text
    assert 'Origen Realty' in text


def test_text_alternative_covers_each_offer_in_a_comparison():
    low, high = compare_set()
    text = ose.render_text(build([low, high]))
    assert 'Alpha Buyer' in text
    assert 'Bravo Buyer' in text
    assert text.count('Price:') == 2
    _assert_no_dash_placeholder(text)


# ---------------------------------------------------------------------------
# Delivery
# ---------------------------------------------------------------------------

def test_send_is_blocked_under_test_so_no_client_is_ever_mailed(app):
    draft = build(full_offer())
    with app.app_context():
        result = ose.send_draft(
            draft,
            to_emails=['seller@example.com'],
            agent=FakeAgent(),
            organization=FakeOrg(),
            transaction_id=7,
        )
    assert result['sent'] is False
    assert result['skipped'] is True


def test_send_refuses_an_empty_recipient_list(app):
    draft = build(full_offer())
    with app.app_context():
        with pytest.raises(ose.OfferEmailError):
            ose.send_draft(draft, to_emails=['   '])


def test_send_refuses_a_blank_subject(app):
    draft = build(full_offer(), overrides={'subject': 'x'})
    draft.subject = '  '
    with app.app_context():
        with pytest.raises(ose.OfferEmailError):
            ose.send_draft(draft, to_emails=['seller@example.com'])


def test_from_name_puts_the_agent_in_front_of_the_brokerage():
    assert ose._from_name(FakeAgent(), FakeOrg()) == 'Cassie Nichols | Origen Realty'


def test_sender_is_the_linked_gmail_when_one_is_connected(app):
    agent = FakeAgent()
    agent.email_integration = FakeGmail()
    with app.app_context():
        sender = ose.resolve_sender(agent)
    assert sender['via'] == 'gmail'
    assert sender['from_email'] == 'cassie@gmail.com'


def test_sender_falls_back_to_the_brokerage_when_gmail_is_off(app):
    with app.app_context():
        sender = ose.resolve_sender(FakeAgent())
    assert sender['via'] == 'sendgrid'
    assert sender['from_email'] == 'info@origenrealty.com'


def test_send_uses_gmail_when_the_agent_has_it_linked(app, monkeypatch):
    draft = build(full_offer())
    agent = FakeAgent()
    agent.email_integration = FakeGmail()
    called = {}

    def fake_gmail(integration, **kwargs):
        called.update(kwargs)
        called['integration'] = integration
        return {'success': True, 'message_id': 'gmail-1'}

    monkeypatch.setattr(ose, 'skip_outbound_send', lambda _to: False)
    monkeypatch.setattr('services.gmail_service.send_email', fake_gmail)

    with app.app_context():
        result = ose.send_draft(
            draft,
            to_emails=['seller@origenrealty.com'],
            agent=agent,
            organization=FakeOrg(),
        )
    assert result['sent'] is True
    assert result['via'] == 'gmail'
    assert result['from_email'] == 'cassie@gmail.com'
    assert called['include_signature'] is False
    assert called['to_emails'] == ['seller@origenrealty.com']
    assert called['body_html'].startswith('<!DOCTYPE html')
