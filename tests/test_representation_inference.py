"""Representation side inferred from the document, not from a picker."""

from types import SimpleNamespace

from services.document_identity import (
    KIND_LISTING_AGREEMENT,
    KIND_PURCHASE_CONTRACT,
    DocumentIdentity,
)
from services.representation_inference import (
    BASIS_BROKER_BLOCK,
    BASIS_FORM_TYPE,
    AgentProfile,
    agent_profile_for_user,
    infer_representation,
)


def _listing_identity():
    return DocumentIdentity(
        kind=KIND_LISTING_AGREEMENT,
        template_slug='listing-agreement',
        form_number='TXR-1101',
        label='Residential Real Estate Listing Agreement',
        confidence=0.95,
    )


def _contract_identity():
    return DocumentIdentity(
        kind=KIND_PURCHASE_CONTRACT,
        template_slug='one-to-four-family-contract',
        form_number='TREC 20',
        label='One to Four Family Residential Contract',
        confidence=0.93,
    )


def _profile():
    return AgentProfile(
        names=('Chris Nichols',),
        licenses=('0712345',),
        brokerages=('Origen Realty',),
    )


def test_listing_agreement_means_seller_representation():
    result = infer_representation(
        identity=_listing_identity(),
        text='Residential Real Estate Listing Agreement, Exclusive Right to Sell',
        profile=_profile(),
    )
    assert result.side == 'seller'
    assert result.basis == BASIS_FORM_TYPE
    assert result.is_confident


def test_listing_agreement_infers_without_pdf_text():
    """The async pass has identity but no re-extracted text."""
    result = infer_representation(identity=_listing_identity(), text='')
    assert result.side == 'seller'
    assert result.is_confident


def test_buyer_representation_agreement_means_buyer():
    result = infer_representation(
        text='Residential Buyer/Tenant Representation Agreement TXR 1501',
        profile=_profile(),
    )
    assert result.side == 'buyer'
    assert result.basis == BASIS_FORM_TYPE


def test_contract_with_agent_as_other_broker_means_buyer():
    """Other Broker is the buyer's broker column on TREC contracts."""
    result = infer_representation(
        identity=_contract_identity(),
        field_data={
            'listing_broker_firm': 'Bluebonnet Properties',
            'listing_associate_name': 'Dana Ruiz',
            'other_broker_firm': 'Origen Realty',
            'other_broker_associate_name': 'Chris Nichols',
        },
        profile=_profile(),
    )
    assert result.side == 'buyer'
    assert result.basis == BASIS_BROKER_BLOCK
    assert result.is_confident


def test_contract_with_agent_as_listing_broker_means_seller():
    result = infer_representation(
        identity=_contract_identity(),
        field_data={
            'listing_broker_firm': 'Origen Realty',
            'listing_associate_name': 'Chris Nichols',
            'other_broker_firm': 'Bluebonnet Properties',
        },
        profile=_profile(),
    )
    assert result.side == 'seller'
    assert result.basis == BASIS_BROKER_BLOCK


def test_agent_license_matches_broker_column():
    result = infer_representation(
        identity=_contract_identity(),
        field_data={
            'other_broker_associate_license_no': '0712345',
            'listing_broker_firm': 'Bluebonnet Properties',
        },
        profile=AgentProfile(licenses=('0712345',)),
    )
    assert result.side == 'buyer'


def test_same_brokerage_on_both_sides_asks_the_agent():
    result = infer_representation(
        identity=_contract_identity(),
        field_data={
            'listing_broker_firm': 'Origen Realty',
            'other_broker_firm': 'Origen Realty',
        },
        profile=_profile(),
    )
    assert result.side is None
    assert result.extras.get('intermediary_suspected') is True


def test_contract_without_broker_names_asks_the_agent():
    result = infer_representation(
        identity=_contract_identity(),
        text='One to Four Family Residential Contract BROKER INFORMATION',
        field_data={'listing_broker_firm': 'Bluebonnet Properties'},
        profile=_profile(),
    )
    assert result.side is None
    assert not result.is_confident
    assert 'does not name you' in result.summary


def test_raw_text_fallback_attributes_agent_to_a_broker_column():
    text = (
        'BROKER INFORMATION Other Broker Firm Origen Realty License No. 9003104 '
        "Associate's Name Chris Nichols Listing Broker Firm Bluebonnet Properties"
    )
    result = infer_representation(
        identity=_contract_identity(),
        text=text,
        profile=AgentProfile(names=('Chris Nichols',)),
    )
    assert result.side == 'buyer'
    assert result.basis == BASIS_BROKER_BLOCK


def test_both_side_forms_present_is_not_inferred():
    result = infer_representation(
        text=(
            'Residential Real Estate Listing Agreement Exclusive Right to Sell '
            'Residential Buyer/Tenant Representation Agreement'
        ),
        profile=_profile(),
    )
    assert result.side is None


def test_agent_profile_collects_names_license_and_brokerage():
    user = SimpleNamespace(
        first_name='Chris',
        last_name='Nichols',
        license_number='0712345',
        organization=SimpleNamespace(
            name='Origen TechnolOG',
            broker_name='Origen Realty',
            broker_license_number='9003104',
        ),
    )
    profile = agent_profile_for_user(user)
    assert 'Chris Nichols' in profile.names
    assert '0712345' in profile.licenses
    assert 'Origen Realty' in profile.brokerages
    assert profile.is_usable
