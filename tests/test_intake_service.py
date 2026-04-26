"""
Tests for intake schema loading and document rule evaluation.

Run with: python -m pytest tests/test_intake_service.py -v
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from services.intake_service import evaluate_document_rules, get_intake_schema


def slugs_for(schema, intake_data):
    """Return the generated document slugs for the provided intake answers."""
    return {doc["slug"] for doc in evaluate_document_rules(schema, intake_data)}


def test_buyer_schema_loads_placeholder_workflow():
    schema = get_intake_schema("buyer")

    assert schema is not None
    assert schema["transaction_type"] == "buyer"
    assert schema["document_workflow"] == "placeholder_upload_only"


def test_buyer_schema_falls_back_when_ownership_status_is_present():
    schema = get_intake_schema("buyer", "conventional")

    assert schema is not None
    assert schema["transaction_type"] == "buyer"


def test_buyer_representation_stage_generates_core_documents_only():
    schema = get_intake_schema("buyer")

    slugs = slugs_for(schema, {
        "buyer_stage": "representation_only",
        "purchase_type": "resale_one_to_four",
        "financing": "cash",
        "built_before_1978": "no",
        "has_association": "no",
        "special_district": "no",
        "buyer_sale_contingency": False,
        "temporary_lease": "none",
        "referral_fee": False,
    })

    assert slugs == {
        "iabs",
        "buyer-tenant-representation-agreement",
        "wire-fraud-warning",
    }


def test_buyer_offer_answers_generate_conditional_placeholders():
    schema = get_intake_schema("buyer")

    slugs = slugs_for(schema, {
        "buyer_stage": "preparing_offer",
        "purchase_type": "condominium",
        "financing": "third_party",
        "built_before_1978": "yes",
        "has_association": "yes",
        "special_district": "yes",
        "buyer_sale_contingency": True,
        "temporary_lease": "not_sure",
        "referral_fee": True,
    })

    expected = {
        "iabs",
        "buyer-tenant-representation-agreement",
        "wire-fraud-warning",
        "condominium-contract",
        "sellers-disclosure",
        "pre-approval-or-proof-of-funds",
        "earnest-option-receipt",
        "inspection-report",
        "repair-amendment",
        "survey",
        "title-commitment",
        "closing-disclosure",
        "third-party-financing-addendum",
        "lead-paint",
        "hoa-addendum",
        "subdivision-resale-certificate",
        "special-tax-district-notice",
        "sale-of-other-property-addendum",
        "buyers-temporary-residential-lease",
        "sellers-temporary-residential-lease",
        "referral-agreement",
    }

    assert expected <= slugs
    assert "one-to-four-family-contract" not in slugs


def test_landlord_schema_generates_core_and_conditional_placeholders():
    schema = get_intake_schema("landlord")

    assert schema["document_workflow"] == "placeholder_upload_only"

    slugs = slugs_for(schema, {
        "built_before_1978": "yes",
        "pets_allowed": True,
        "has_association": "yes",
        "floodplain_notice": "not_sure",
    })

    expected = {
        "iabs",
        "residential-lease-listing-agreement",
        "residential-lease",
        "residential-lease-application",
        "tenant-selection-criteria",
        "security-deposit-receipt",
        "lease-inventory-condition-form",
        "wire-fraud-warning",
        "lead-paint",
        "pet-agreement",
        "association-rules-addendum",
        "landlord-floodplain-flood-notice",
    }

    assert expected <= slugs


def test_tenant_schema_generates_core_and_conditional_placeholders():
    schema = get_intake_schema("tenant")

    assert schema["document_workflow"] == "placeholder_upload_only"

    slugs = slugs_for(schema, {
        "has_pets": True,
        "built_before_1978": "not_sure",
        "has_association": "yes",
    })

    expected = {
        "iabs",
        "buyer-tenant-representation-agreement",
        "residential-lease-application",
        "tenant-selection-criteria",
        "application-fee-receipt",
        "income-id-verification",
        "residential-lease",
        "lease-inventory-condition-form",
        "wire-fraud-warning",
        "pet-agreement",
        "lead-paint",
        "association-rules-addendum",
    }

    assert expected <= slugs


def test_referral_schema_generates_core_and_relocation_placeholders():
    schema = get_intake_schema("referral")

    assert schema["document_workflow"] == "placeholder_upload_only"

    slugs = slugs_for(schema, {
        "referral_side": "buyer",
        "receiving_broker_known": True,
        "relocation_partner": True,
    })

    assert {
        "referral-agreement",
        "client-referral-authorization",
        "receiving-broker-confirmation",
        "commission-disbursement-instructions",
        "closing-lease-confirmation",
        "relocation-referral-agreement",
    } <= slugs


def test_seller_builder_schema_generates_new_construction_placeholders():
    schema = get_intake_schema("seller", "builder")

    assert schema["ownership_status"] == "builder"
    assert schema["document_workflow"] == "placeholder_upload_only"

    slugs = slugs_for(schema, {
        "construction_status": "incomplete",
        "builder_contract": "no",
        "has_hoa": True,
        "special_districts": True,
        "flood_hazard": True,
        "referral_fee": True,
    })

    assert {
        "listing-agreement",
        "iabs",
        "wire-fraud-warning",
        "seller-net-proceeds",
        "builder-disclosure-package",
        "new-home-incomplete-construction-contract",
        "plans-specifications-addendum",
        "builder-warranty-documents",
        "hoa-addendum",
        "special-tax-district-notice",
        "flood-hazard",
        "referral-agreement",
    } <= slugs
    assert "new-home-completed-construction-contract" not in slugs


def test_seller_reo_schema_generates_bank_and_exemption_placeholders():
    schema = get_intake_schema("seller", "reo")

    assert schema["ownership_status"] == "reo"
    assert schema["document_workflow"] == "placeholder_upload_only"

    slugs = slugs_for(schema, {
        "built_before_1978": "not_sure",
        "has_hoa": "yes",
        "special_districts": "yes",
        "flood_hazard": "yes",
        "bank_addendum_required": "not_sure",
        "has_survey": "yes",
    })

    assert {
        "listing-agreement",
        "iabs",
        "wire-fraud-warning",
        "seller-net-proceeds",
        "seller-disclosure-exemption",
        "reo-bank-addendum",
        "lead-paint",
        "hoa-addendum",
        "special-tax-district-notice",
        "flood-hazard",
        "t47-affidavit",
    } <= slugs
    assert "sellers-disclosure" not in slugs


def test_seller_short_sale_schema_generates_lender_approval_placeholders():
    schema = get_intake_schema("seller", "short_sale")

    assert schema["ownership_status"] == "short_sale"
    assert schema["document_workflow"] == "placeholder_upload_only"

    slugs = slugs_for(schema, {
        "built_before_1978": True,
        "has_hoa": True,
        "special_districts": True,
        "flood_hazard": True,
        "has_septic": True,
        "multiple_lienholders": True,
        "referral_fee": True,
        "has_survey": "not_sure",
    })

    assert {
        "listing-agreement",
        "iabs",
        "sellers-disclosure",
        "wire-fraud-warning",
        "seller-net-proceeds",
        "short-sale-addendum",
        "short-sale-lender-authorization",
        "short-sale-hardship-package",
        "payoff-or-lienholder-statement",
        "multiple-lienholder-approval",
        "lead-paint",
        "hoa-addendum",
        "special-tax-district-notice",
        "flood-hazard",
        "sewer-facility",
        "referral-agreement",
        "t47-affidavit",
    } <= slugs
