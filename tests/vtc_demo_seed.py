#!/usr/bin/env python3
"""Boot a throwaway CRM with a realistic Texas seller file for visual review.

    .venv/bin/python tests/vtc_demo_seed.py            # seed + serve on :5099
    .venv/bin/python tests/vtc_demo_seed.py --seed-only

Login: demo@origen.test / demo12345
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

DEMO_DB = PROJECT_ROOT / "instance" / "vtc_demo.db"
DEMO_EMAIL = "demo@origen.test"
DEMO_PASSWORD = "demo12345"
SAMPLE_PDF = PROJECT_ROOT / "6004 Lakeside Executed.pdf"


def _serve_local_files() -> None:
    """Demo-only: read documents off disk since Supabase is not configured locally."""
    import services.supabase_storage as storage

    def download_document(storage_path: str) -> bytes:
        return Path(storage_path).read_bytes()

    storage.download_document = download_document

os.environ.setdefault("DATABASE_URL", f"sqlite:///{DEMO_DB}")
os.environ.setdefault("SENDGRID_API_KEY", "")
os.environ.setdefault("OPENAI_API_KEY", "")


def seed() -> dict:
    from werkzeug.security import generate_password_hash

    from app import app
    from models import (
        Contact,
        ContactGroup,
        DocumentReviewReport,
        Organization,
        SellerAcceptedContract,
        SellerCommissionTerms,
        SellerContractAmendment,
        SellerContractAmendmentVersion,
        SellerOffer,
        SellerOfferVersion,
        Transaction,
        TransactionChangeProposal,
        TransactionDocument,
        TransactionParticipant,
        TransactionRequirement,
        TransactionType,
        User,
        db,
    )

    with app.app_context():
        db.create_all()

        org = Organization.query.filter_by(name="Origen Realty").first()
        if not org:
            org = Organization(
                name="Origen Realty",
                slug="origen-realty",
                subscription_tier="pro",
                status="active",
            )
            db.session.add(org)
            db.session.flush()

        # Isolated visual demo needs VTC pilot routes (bootstrap inbox, Start from document).
        flags = dict(org.feature_flags or {})
        flags["BOB_VTC_PILOT"] = True
        org.feature_flags = flags

        user = User.query.filter_by(email=DEMO_EMAIL).first()
        if not user:
            user = User(
                organization_id=org.id,
                username="demo",
                email=DEMO_EMAIL,
                first_name="Dana",
                last_name="Reyes",
                role="owner",
                password_hash=generate_password_hash(DEMO_PASSWORD),
            )
            db.session.add(user)
            db.session.flush()

        seller_type = TransactionType.query.filter_by(
            organization_id=org.id, name="seller",
        ).first()
        if not seller_type:
            seller_type = TransactionType(
                organization_id=org.id, name="seller", display_name="Seller",
            )
            db.session.add(seller_type)
            db.session.flush()

        seller = Contact.query.filter_by(
            organization_id=org.id, email="marcus.hall@example.com",
        ).first()
        if not seller:
            seller = Contact(
                organization_id=org.id,
                user_id=user.id,
                first_name="Marcus",
                last_name="Hall",
                email="marcus.hall@example.com",
                phone="512-555-0148",
            )
            db.session.add(seller)
            db.session.flush()

        tx = Transaction.query.filter_by(
            organization_id=org.id, street_address="6004 Lakeside Dr",
        ).first()
        if not tx:
            tx = Transaction(
                organization_id=org.id,
                created_by_id=user.id,
                transaction_type_id=seller_type.id,
                street_address="6004 Lakeside Dr",
                city="Austin",
                state="TX",
                zip_code="78734",
                status="active",
                expected_close_date=date.today() + timedelta(days=38),
            )
            db.session.add(tx)
            db.session.flush()

            db.session.add(TransactionParticipant(
                organization_id=org.id,
                transaction_id=tx.id,
                contact_id=seller.id,
                role="seller",
            ))

        listing_doc = TransactionDocument.query.filter_by(
            transaction_id=tx.id, template_slug="listing-agreement",
        ).first()
        if not listing_doc:
            listing_doc = TransactionDocument(
                organization_id=org.id,
                transaction_id=tx.id,
                template_slug="listing-agreement",
                template_name="Residential Listing Agreement (TXR-1101)",
                status="signed",
                extraction_status="complete",
                field_data={
                    "list_price": "735000",
                    "listing_start_date": "2026-06-01",
                    "listing_end_date": "2026-12-01",
                    "total_commission": "6",
                    "buyer_agent_percent": "3",
                    "protection_period_days": "180",
                    "has_hoa": "yes",
                    "_meta": {
                        "list_price": {"page": 1, "quote": "Sales Price: $735,000", "confidence": 0.97},
                        "listing_start_date": {"page": 1, "quote": "beginning June 1, 2026", "confidence": 0.94},
                        "total_commission": {"page": 3, "quote": "6% of the sales price", "confidence": 0.91},
                    },
                },
            )
            db.session.add(listing_doc)
            db.session.flush()

        contract_doc = TransactionDocument.query.filter_by(
            transaction_id=tx.id, template_slug="one-to-four-family-contract",
        ).first()
        if not contract_doc:
            contract_doc = TransactionDocument(
                organization_id=org.id,
                transaction_id=tx.id,
                template_slug="one-to-four-family-contract",
                template_name="One to Four Family Residential Contract",
                status="signed",
                extraction_status="complete",
                source_file_path=str(SAMPLE_PDF),
                field_data={
                    "sales_price": "721500",
                    "effective_date": "2026-07-14",
                    "closing_date": "2026-08-28",
                    "option_fee": "300",
                    "option_period_days": "7",
                    "earnest_money": "10000",
                    "financing_type": "Conventional",
                    "title_company": "Lone Star Title of Travis County",
                    "escrow_officer": "Priya Raman",
                    "survey_choice": "Seller provides existing survey",
                    "buyer_name": "Elena Castillo",
                    "seller_name": "Marcus Hall",
                    "_meta": {
                        "sales_price": {"page": 1, "quote": "Cash portion of Sales Price", "confidence": 0.96},
                        "closing_date": {"page": 4, "quote": "on or before August 28, 2026", "confidence": 0.93},
                        "option_fee": {"page": 6, "quote": "the amount of $300.00", "confidence": 0.88},
                        "earnest_money": {"page": 2, "quote": "$10,000.00 as earnest money", "confidence": 0.95},
                        "title_company": {"page": 2, "quote": "Lone Star Title of Travis County", "confidence": 0.92},
                    },
                },
            )
            db.session.add(contract_doc)
            db.session.flush()

        if not DocumentReviewReport.query.filter_by(transaction_id=tx.id).first():
            db.session.add(DocumentReviewReport(
                organization_id=org.id,
                transaction_id=tx.id,
                document_id=contract_doc.id,
                severity="critical",
                status="open",
                title="Closing date conflicts with the CRM",
                summary="BOB read a different closing date than the one on this transaction.",
                findings=[
                    {
                        "code": "date_conflict",
                        "severity": "critical",
                        "message": "The closing date on page 4 is 2026-08-28. The CRM currently says "
                                   f"{(date.today() + timedelta(days=38)).isoformat()}.",
                        "field_key": "closing_date",
                        "page": 4,
                        "quote": "on or before August 28, 2026",
                        "crm_value": (date.today() + timedelta(days=38)).isoformat(),
                        "extracted_value": "2026-08-28",
                    },
                    {
                        "code": "low_confidence_critical",
                        "severity": "attention",
                        "message": "The option fee reads $300 but the scan is faint on page 6.",
                        "field_key": "option_fee",
                        "page": 6,
                        "quote": "the amount of $300.00",
                    },
                    {
                        "code": "signature_unconfirmed",
                        "severity": "attention",
                        "message": "I could not confirm a seller signature on page 9.",
                        "page": 9,
                    },
                ],
                field_count=11,
                toast_required=False,
            ))

        if not TransactionChangeProposal.query.filter_by(transaction_id=tx.id).first():
            db.session.add(TransactionChangeProposal(
                organization_id=org.id,
                transaction_id=tx.id,
                source_document_id=contract_doc.id,
                change_type="executed_contract",
                status="pending",
                rationale="Extracted from the executed One to Four Family Residential Contract.",
                proposed_changes={
                    "sales_price": "721500",
                    "closing_date": "2026-08-28",
                    "option_fee": "300",
                    "earnest_money": "10000",
                    "title_company": "Lone Star Title of Travis County",
                },
            ))

        # Checklist demo: pack-aligned keys so expected documents fold correctly.
        now = datetime.utcnow()
        seller_req_specs = [
            ("option_fee", "option_period", "Option Fee Delivered", 2, "Buyer"),
            ("earnest_money", "option_period", "Earnest Money Deposited", 3, "Buyer"),
            ("option_period_end", "option_period", "Option Period Expires", 7, "Buyer"),
            ("inspection", "due_diligence", "Inspection Completed", 5, "Buyer"),
            ("survey", "due_diligence", "Survey Completed", 10, "Seller"),
            ("appraisal", "financing", "Appraisal Completed", 15, "Buyer"),
            ("financing_approval", "financing", "Financing Approval", 21, "Buyer"),
            ("closing", "closing", "Closing", 38, "Agent"),
        ]
        existing_seller_keys = {
            r.requirement_key
            for r in TransactionRequirement.query.filter_by(transaction_id=tx.id).all()
        }
        for key, phase, title, days, party in seller_req_specs:
            if key in existing_seller_keys:
                continue
            db.session.add(TransactionRequirement(
                organization_id=org.id,
                transaction_id=tx.id,
                package_key="seller_ctc",
                phase_key=phase,
                requirement_key=key,
                title=title,
                work_status="pending",
                deadline_rule_version="v1",
                due_at=now + timedelta(days=days),
                responsible_party_label=party,
            ))

        # Folded uploaded doc (inspection) + missing placeholder (survey).
        if not TransactionDocument.query.filter_by(
            transaction_id=tx.id, template_slug="inspection-report",
        ).first():
            db.session.add(TransactionDocument(
                organization_id=org.id,
                transaction_id=tx.id,
                template_slug="inspection-report",
                template_name="Inspection Report",
                status="signed",
                is_placeholder=False,
                document_source="completed",
                source_file_path=str(SAMPLE_PDF),
                signed_file_path=str(SAMPLE_PDF),
                included_reason="Required by: Inspection Completed",
            ))
        if not TransactionDocument.query.filter_by(
            transaction_id=tx.id, template_slug="survey",
        ).first():
            db.session.add(TransactionDocument(
                organization_id=org.id,
                transaction_id=tx.id,
                template_slug="survey",
                template_name="Survey",
                status="pending",
                is_placeholder=True,
                document_source="placeholder",
                included_reason="Required by: Survey Completed",
            ))

        # Buyer file for side-by-side checklist review.
        buyer_type = TransactionType.query.filter_by(
            organization_id=org.id, name="buyer",
        ).first()
        if not buyer_type:
            buyer_type = TransactionType(
                organization_id=org.id, name="buyer", display_name="Buyer",
            )
            db.session.add(buyer_type)
            db.session.flush()

        buyer = Contact.query.filter_by(
            organization_id=org.id, email="elena.castillo@example.com",
        ).first()
        if not buyer:
            buyer = Contact(
                organization_id=org.id,
                user_id=user.id,
                first_name="Elena",
                last_name="Castillo",
                email="elena.castillo@example.com",
                phone="512-555-0199",
            )
            db.session.add(buyer)
            db.session.flush()

        buyer_tx = Transaction.query.filter_by(
            organization_id=org.id, street_address="1412 Barton Springs Rd",
        ).first()
        if not buyer_tx:
            buyer_tx = Transaction(
                organization_id=org.id,
                created_by_id=user.id,
                transaction_type_id=buyer_type.id,
                street_address="1412 Barton Springs Rd",
                city="Austin",
                state="TX",
                zip_code="78704",
                status="under_contract",
                expected_close_date=date.today() + timedelta(days=32),
            )
            db.session.add(buyer_tx)
            db.session.flush()
            db.session.add(TransactionParticipant(
                organization_id=org.id,
                transaction_id=buyer_tx.id,
                contact_id=buyer.id,
                role="buyer",
            ))

        buyer_req_specs = [
            ("earnest_money", "option_period", "Earnest Money Deposited", 2, "Buyer"),
            ("inspection", "due_diligence", "Inspection Completed", 6, "Buyer"),
            ("survey", "due_diligence", "Survey Received", 10, "Seller"),
            ("title_commitment", "due_diligence", "Title Commitment Received", 12, None),
            ("financing_approval", "financing", "Financing Approval", 20, "Buyer"),
            ("closing", "closing", "Closing", 32, "Agent"),
        ]
        existing_buyer_keys = {
            r.requirement_key
            for r in TransactionRequirement.query.filter_by(
                transaction_id=buyer_tx.id,
            ).all()
        }
        for key, phase, title, days, party in buyer_req_specs:
            if key in existing_buyer_keys:
                continue
            db.session.add(TransactionRequirement(
                organization_id=org.id,
                transaction_id=buyer_tx.id,
                package_key="buyer_ctc",
                phase_key=phase,
                requirement_key=key,
                title=title,
                work_status="pending",
                deadline_rule_version="v1",
                due_at=now + timedelta(days=days),
                responsible_party_label=party,
            ))
        if not TransactionDocument.query.filter_by(
            transaction_id=buyer_tx.id, template_slug="title-commitment",
        ).first():
            db.session.add(TransactionDocument(
                organization_id=org.id,
                transaction_id=buyer_tx.id,
                template_slug="title-commitment",
                template_name="Title Commitment",
                status="pending",
                is_placeholder=True,
                document_source="placeholder",
                included_reason="Required by: Title Commitment Received",
            ))
        # survey expected but no placeholder yet — checklist shows "Add upload".
        if not TransactionDocument.query.filter_by(
            transaction_id=buyer_tx.id, template_slug="earnest-option-receipt",
        ).first():
            db.session.add(TransactionDocument(
                organization_id=org.id,
                transaction_id=buyer_tx.id,
                template_slug="earnest-option-receipt",
                template_name="Earnest Money / Option Fee Receipt",
                status="signed",
                is_placeholder=False,
                document_source="completed",
                source_file_path=str(SAMPLE_PDF),
                signed_file_path=str(SAMPLE_PDF),
                included_reason="Required by: Earnest Money Deposited",
            ))

        if not SellerCommissionTerms.query.filter_by(transaction_id=tx.id).first():
            db.session.add(SellerCommissionTerms(
                organization_id=org.id,
                transaction_id=tx.id,
                created_by_id=user.id,
                listing_commission_percent=3.0,
                coop_compensation_percent=2.5,
                admin_transaction_fee=395,
                representation_mode="separate_buyer_agent",
                source="listing_agreement_extraction",
            ))

        if not SellerOffer.query.filter_by(transaction_id=tx.id).first():
            offer_specs = [
                {
                    "buyer_names": "Elena Castillo",
                    "buyer_agent_name": "Ray Whitmore, Compass",
                    "offer_price": 721500,
                    "financing_type": "Conventional",
                    "earnest_money": 10000,
                    "option_fee": 300,
                    "option_period_days": 7,
                    "seller_concessions_amount": 0,
                    "proposed_close_date": date(2026, 8, 28),
                    "appraisal_contingency": True,
                    "financing_contingency": True,
                    "buyer_agent_commission_percent": 2.5,
                    "days_ago": 4,
                },
                {
                    "buyer_names": "Devon & Priya Shah",
                    "buyer_agent_name": "Kim Alvarado, JBGoodwin",
                    "offer_price": 735000,
                    "financing_type": "Cash",
                    "earnest_money": 25000,
                    "option_fee": 500,
                    "option_period_days": 5,
                    "seller_concessions_amount": 6000,
                    "proposed_close_date": date(2026, 9, 4),
                    "appraisal_contingency": False,
                    "financing_contingency": False,
                    "buyer_agent_commission_percent": 3.0,
                    "days_ago": 3,
                },
                {
                    "buyer_names": "The Nakamura Trust",
                    "buyer_agent_name": "Sofia Reyes, Realty Austin",
                    "offer_price": 728000,
                    "financing_type": "VA",
                    "earnest_money": 12000,
                    "option_fee": 250,
                    "option_period_days": 10,
                    "seller_concessions_amount": 9000,
                    "proposed_close_date": date(2026, 8, 21),
                    "appraisal_contingency": True,
                    "financing_contingency": True,
                    "buyer_agent_commission_percent": 2.5,
                    "days_ago": 1,
                },
            ]
            for spec in offer_specs:
                days_ago = spec.pop("days_ago")
                offer = SellerOffer(
                    organization_id=org.id,
                    transaction_id=tx.id,
                    created_by_id=user.id,
                    status="under_review",
                    received_at=datetime.utcnow() - timedelta(days=days_ago),
                    creation_source="uploaded_document",
                    **spec,
                )
                db.session.add(offer)
                db.session.flush()
                version = SellerOfferVersion(
                    organization_id=org.id,
                    transaction_id=tx.id,
                    offer_id=offer.id,
                    created_by_id=user.id,
                    version_number=1,
                    direction="buyer_offer",
                    status="submitted",
                    submitted_at=offer.received_at,
                    terms_data={
                        "offer_price": str(spec["offer_price"]),
                        "financing_type": spec["financing_type"],
                        "proposed_close_date": spec["proposed_close_date"].isoformat(),
                    },
                )
                db.session.add(version)
                db.session.flush()
                offer.current_version_id = version.id

        contract = SellerAcceptedContract.query.filter_by(
            transaction_id=tx.id, position="primary", status="active",
        ).first()
        if not contract:
            contract = SellerAcceptedContract(
                organization_id=org.id,
                transaction_id=tx.id,
                created_by_id=user.id,
                position="primary",
                status="active",
                accepted_price=721500,
                effective_date=date(2026, 7, 14),
                closing_date=date(2026, 8, 28),
                option_period_days=7,
                financing_type="Conventional",
                title_company="Lone Star Title of Travis County",
                escrow_officer="Priya Raman",
                frozen_terms={
                    "sales_price": "721500",
                    "closing_date": "2026-08-28",
                    "option_period_days": 7,
                    "option_fee": "300",
                    "earnest_money": "10000",
                    "financing_type": "Conventional",
                },
            )
            db.session.add(contract)
            db.session.flush()

        amendment = SellerContractAmendment.query.filter_by(
            transaction_id=tx.id,
        ).first()
        if not amendment:
            amendment = SellerContractAmendment(
                organization_id=org.id,
                transaction_id=tx.id,
                accepted_contract_id=contract.id,
                created_by_id=user.id,
                status="received",
                amendment_type="amendment",
                summary=(
                    "Buyer requests a two-week closing extension and a "
                    "$4,000 repair credit after the inspection."
                ),
            )
            db.session.add(amendment)
            db.session.flush()

            version = SellerContractAmendmentVersion(
                organization_id=org.id,
                transaction_id=tx.id,
                amendment_id=amendment.id,
                created_by_id=user.id,
                transaction_document_id=contract_doc.id,
                version_number=1,
                direction="buyer_amendment",
                status="submitted",
                submitted_at=datetime.utcnow(),
                terms_data={
                    "closing_date": "2026-09-11",
                    "sales_price": "717500",
                    "option_period_days": 7,
                    "earnest_money": "10000",
                    "seller_concessions_amount": "4000",
                },
            )
            db.session.add(version)
            db.session.flush()
            amendment.current_version_id = version.id

        # Fresh listed seller with no offers/contract — for stage-aware empty UI.
        fresh_seller = Contact.query.filter_by(
            organization_id=org.id, email="jordan.lee@example.com",
        ).first()
        if not fresh_seller:
            fresh_seller = Contact(
                organization_id=org.id,
                user_id=user.id,
                first_name="Jordan",
                last_name="Lee",
                email="jordan.lee@example.com",
                phone="512-555-0177",
            )
            db.session.add(fresh_seller)
            db.session.flush()

        fresh_tx = Transaction.query.filter_by(
            organization_id=org.id, street_address="908 Willow Creek Ln",
        ).first()
        if not fresh_tx:
            fresh_tx = Transaction(
                organization_id=org.id,
                created_by_id=user.id,
                transaction_type_id=seller_type.id,
                street_address="908 Willow Creek Ln",
                city="Austin",
                state="TX",
                zip_code="78745",
                status="active",
            )
            db.session.add(fresh_tx)
            db.session.flush()
            db.session.add(TransactionParticipant(
                organization_id=org.id,
                transaction_id=fresh_tx.id,
                contact_id=fresh_seller.id,
                role="seller",
            ))

        if not TransactionDocument.query.filter_by(
            transaction_id=fresh_tx.id, template_slug="listing-agreement",
        ).first():
            db.session.add(TransactionDocument(
                organization_id=org.id,
                transaction_id=fresh_tx.id,
                template_slug="listing-agreement",
                template_name="Residential Listing Agreement (TXR-1101)",
                status="signed",
                extraction_status="complete",
                source_file_path=str(SAMPLE_PDF),
                field_data={
                    "list_price": "525000",
                    "listing_start_date": date.today().isoformat(),
                    "total_commission": "6",
                },
            ))

        db.session.commit()
        return {
            "transaction_id": tx.id,
            "buyer_transaction_id": buyer_tx.id,
            "fresh_listing_id": fresh_tx.id,
            "contract_document_id": contract_doc.id,
            "listing_document_id": listing_doc.id,
            "amendment_id": amendment.id,
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed-only", action="store_true")
    parser.add_argument("--port", type=int, default=5099)
    args = parser.parse_args()

    info = seed()
    print(f"Seeded {DEMO_DB}")
    print(f"  seller:       /transactions/{info['transaction_id']}")
    print(f"  buyer:        /transactions/{info['buyer_transaction_id']}")
    print(f"  fresh listing:/transactions/{info['fresh_listing_id']}")
    print(f"  review:       /transactions/{info['transaction_id']}"
          f"/documents/{info['contract_document_id']}/review")
    print(f"  login:        {DEMO_EMAIL} / {DEMO_PASSWORD}")

    if args.seed_only:
        return 0

    _serve_local_files()

    from app import app
    app.run(host="127.0.0.1", port=args.port, debug=False, use_reloader=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
