"""Unit tests for pure document routing policy."""

from services.document_identity import (
    EXEC_DRAFT,
    EXEC_EXECUTED,
    EXEC_UNKNOWN,
    KIND_ADDENDUM,
    KIND_AMENDMENT,
    KIND_LISTING_AGREEMENT,
    KIND_PURCHASE_CONTRACT,
    DocumentIdentity,
)
from services.document_routing import (
    ACTION_ATTACH_CONTROLLING_CONTRACT,
    ACTION_ATTACH_SUPPORTING,
    ACTION_CREATE_AMENDMENT,
    ACTION_CREATE_BUYER_OFFER,
    ACTION_CREATE_INBOUND_OFFER,
    ACTION_CREATE_LISTING,
    ACTION_INVALID,
    ACTION_NEEDS_CONFIRMATION,
    TransactionContext,
    decide_route,
)


def _listing():
    return DocumentIdentity(
        kind=KIND_LISTING_AGREEMENT,
        template_slug='listing-agreement',
        form_number='TXR-1101',
        label='Listing Agreement',
        confidence=0.95,
        possible_scopes=('listing',),
    )


def _purchase(*, execution=EXEC_UNKNOWN):
    return DocumentIdentity(
        kind=KIND_PURCHASE_CONTRACT,
        template_slug='one-to-four-family-contract',
        form_number='TREC 20',
        label='One to Four Family Residential Contract',
        confidence=0.93,
        execution_state=execution,
        possible_scopes=('offer', 'contract'),
    )


def _tpf():
    return DocumentIdentity(
        kind=KIND_ADDENDUM,
        template_slug='third-party-financing-addendum',
        form_number='TREC 40',
        label='Third Party Financing Addendum',
        confidence=0.92,
        possible_scopes=('offer', 'contract'),
        addendum_key='third_party_financing',
    )


def test_scenario_a_seller_listing_agreement():
    decision = decide_route(
        identity=_listing(),
        representation_side='seller',
        side_confirmed=True,
    )
    assert decision.action == ACTION_CREATE_LISTING
    assert decision.destination_scope == 'listing'
    assert decision.transaction_status == 'preparing_to_list'
    assert decision.template_slug == 'listing-agreement'
    assert decision.seed_pack_key == 'listing'


def test_listing_agreement_infers_seller_side_without_picker():
    """A high-confidence listing agreement is seller representation by form type."""
    decision = decide_route(
        identity=_listing(),
        representation_side=None,
        side_confirmed=False,
    )
    assert decision.action == ACTION_CREATE_LISTING
    assert decision.ok
    assert decision.error_code is None


def test_scenario_a_buyer_listing_mismatch():
    decision = decide_route(
        identity=_listing(),
        representation_side='buyer',
        side_confirmed=True,
    )
    assert decision.action == ACTION_INVALID
    assert decision.error_code == 'side_mismatch_listing'


def test_scenario_b_buyer_executed_controlling():
    decision = decide_route(
        identity=_purchase(execution=EXEC_EXECUTED),
        representation_side='buyer',
        side_confirmed=True,
    )
    assert decision.action == ACTION_ATTACH_CONTROLLING_CONTRACT
    assert decision.transaction_status == 'under_contract'
    assert decision.seed_pack_key == 'buyer_ctc'


def test_scenario_b_buyer_unknown_needs_destination_choice():
    decision = decide_route(
        identity=_purchase(execution=EXEC_UNKNOWN),
        representation_side='buyer',
        side_confirmed=True,
    )
    assert decision.action == ACTION_NEEDS_CONFIRMATION
    assert 'offer_thread' in decision.confirmation_options
    assert 'controlling_contract' in decision.confirmation_options


def test_scenario_b_buyer_draft_offer_thread():
    decision = decide_route(
        identity=_purchase(execution=EXEC_DRAFT),
        representation_side='buyer',
        side_confirmed=True,
        destination_choice='offer_thread',
    )
    assert decision.action == ACTION_CREATE_BUYER_OFFER
    assert decision.destination_scope == 'offer'


def test_scenario_c_seller_purchase_is_inbound_offer():
    decision = decide_route(
        identity=_purchase(execution=EXEC_EXECUTED),
        representation_side='seller',
        side_confirmed=True,
        transaction=TransactionContext(status='active', has_listing_agreement=True),
    )
    assert decision.action == ACTION_CREATE_INBOUND_OFFER
    assert decision.destination_scope == 'offer'
    assert decision.transaction_status is None
    assert decision.template_slug == 'seller-offer-contract'


def test_amendment_without_transaction_needs_confirmation():
    decision = decide_route(
        identity=DocumentIdentity(
            kind=KIND_AMENDMENT,
            template_slug='amendment',
            confidence=0.93,
            label='Amendment',
            possible_scopes=('amendment', 'contract'),
        ),
        representation_side='seller',
        side_confirmed=True,
        transaction=TransactionContext(),  # no tx, no primary
    )
    assert decision.action == ACTION_NEEDS_CONFIRMATION
    assert decision.error_code == 'amendment_requires_controlling_contract'


def test_amendment_with_tx_but_no_primary_needs_confirmation():
    decision = decide_route(
        identity=DocumentIdentity(
            kind=KIND_AMENDMENT,
            template_slug='amendment',
            confidence=0.93,
            label='Amendment',
            possible_scopes=('amendment', 'contract'),
        ),
        representation_side='seller',
        side_confirmed=True,
        transaction=TransactionContext(
            transaction_id=9,
            side='seller',
            has_primary_contract=False,
        ),
    )
    assert decision.action == ACTION_NEEDS_CONFIRMATION
    assert decision.error_code == 'amendment_requires_controlling_contract'


def test_scenario_e_amendment_with_primary_contract():
    decision = decide_route(
        identity=DocumentIdentity(
            kind=KIND_AMENDMENT,
            template_slug='amendment',
            confidence=0.93,
            label='Amendment',
            possible_scopes=('amendment', 'contract'),
        ),
        representation_side='seller',
        side_confirmed=True,
        transaction=TransactionContext(
            transaction_id=1,
            side='seller',
            has_primary_contract=True,
        ),
    )
    assert decision.action == ACTION_CREATE_AMENDMENT


def test_tpf_on_listing_without_offer_needs_confirmation():
    decision = decide_route(
        identity=_tpf(),
        representation_side='seller',
        side_confirmed=True,
        transaction=TransactionContext(
            transaction_id=5,
            side='seller',
            status='active',
            has_listing_agreement=True,
            active_offer_ids=(11, 12),
        ),
    )
    assert decision.action == ACTION_NEEDS_CONFIRMATION
    assert decision.error_code == 'supporting_offer_unconfirmed'
    assert 'offer:11' in decision.confirmation_options
    assert 'new_offer' in decision.confirmation_options


def test_tpf_on_offer_scoped_attaches_to_offer():
    decision = decide_route(
        identity=_tpf(),
        representation_side='seller',
        side_confirmed=True,
        transaction=TransactionContext(
            transaction_id=5,
            offer_id=11,
            is_offer_scoped_upload=True,
        ),
    )
    assert decision.action == ACTION_ATTACH_SUPPORTING
    assert decision.destination_scope == 'offer'


def test_representation_must_be_confirmed():
    decision = decide_route(
        identity=_purchase(),
        representation_side='seller',
        side_confirmed=False,
    )
    assert decision.action == ACTION_NEEDS_CONFIRMATION
    assert decision.error_code == 'side_unconfirmed'
