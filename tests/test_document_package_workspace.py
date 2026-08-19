"""Expected document package workspace builder."""

from datetime import date, datetime

from models import (
    SellerOffer,
    SellerOfferDocument,
    SellerOfferVersion,
    Transaction,
    TransactionDocument,
    TransactionType,
    db,
)
from services.controlling_contracts import create_baseline_from_document
from services.document_package_workspace import build_document_packages


def _listing_doc(seed, tx):
    doc = TransactionDocument(
        organization_id=seed['org_a'],
        transaction_id=tx.id,
        template_slug='listing-agreement',
        template_name='Listing Agreement',
        status='signed',
        document_source='completed',
        field_data={'has_hoa': True, 'list_price': '500000'},
    )
    db.session.add(doc)
    db.session.flush()
    return doc


def _offer_with_contract_doc(seed, tx, *, buyer_names, price):
    offer = SellerOffer(
        organization_id=seed['org_a'],
        transaction_id=tx.id,
        created_by_id=seed['owner_a'],
        buyer_names=buyer_names,
        received_at=datetime.utcnow(),
        status='new',
        offer_price=price,
        financing_type='Conventional',
    )
    db.session.add(offer)
    db.session.flush()
    doc = TransactionDocument(
        organization_id=seed['org_a'],
        transaction_id=tx.id,
        template_slug='seller-offer-contract',
        template_name='Offer Contract',
        status='signed',
        document_source='completed',
        field_data={'offer_price': str(price), 'financing_type': 'Conventional'},
    )
    db.session.add(doc)
    db.session.flush()
    version = SellerOfferVersion(
        organization_id=seed['org_a'],
        transaction_id=tx.id,
        offer_id=offer.id,
        created_by_id=seed['owner_a'],
        transaction_document_id=doc.id,
        version_number=1,
        direction='buyer_offer',
        status='submitted',
        submitted_at=datetime.utcnow(),
        terms_data={
            'offer_price': price,
            'financing_type': 'Conventional',
            'proposed_close_date': '2026-10-01',
        },
    )
    db.session.add(version)
    db.session.flush()
    offer.current_version_id = version.id
    link = SellerOfferDocument(
        organization_id=seed['org_a'],
        transaction_id=tx.id,
        offer_id=offer.id,
        transaction_document_id=doc.id,
        offer_version_id=version.id,
        created_by_id=seed['owner_a'],
        document_type='buyer_offer',
        display_name='Offer Contract',
        is_primary_terms_document=True,
    )
    db.session.add(link)
    db.session.flush()
    return offer


def test_seller_listing_two_offers_and_accepted_contract_packages(app, seed):
    with app.app_context():
        tx = Transaction.query.get(seed['tx_a'])
        tx.status = 'active'
        _listing_doc(seed, tx)
        _offer_with_contract_doc(seed, tx, buyer_names='Buyer One', price=480000)
        _offer_with_contract_doc(seed, tx, buyer_names='Buyer Two', price=495000)

        # Accept-style controlling baseline via service (offer_id nullable OK).
        contract_doc = TransactionDocument(
            organization_id=seed['org_a'],
            transaction_id=tx.id,
            template_slug='seller-accepted-contract',
            template_name='Executed Contract',
            status='signed',
            document_source='completed',
            field_data={},
        )
        db.session.add(contract_doc)
        db.session.flush()
        create_baseline_from_document(
            transaction=tx,
            document=contract_doc,
            approved_terms={
                'sales_price': '495000',
                'effective_date': '2026-08-01',
                'closing_date': '2026-09-20',
                'financing_type': 'Conventional',
            },
            actor_id=seed['owner_a'],
        )

        packages = build_document_packages(tx)
        assert packages['side'] == 'seller'
        assert packages['listing_package'] is not None
        assert packages['listing_package']['label'] == 'Listing package'
        assert packages['buyer_transaction_package'] is None
        listing_slugs = {
            row['canonical_slug'] for row in packages['listing_package']['documents']
        }
        assert 'listing-agreement' in listing_slugs
        assert len(packages['offer_packages']) == 2
        assert packages['controlling_contract_package'] is not None
        assert packages['controlling_contract_package']['scope'] == 'contract'
        contract_rows = packages['controlling_contract_package']['documents']
        assert any(r['canonical_slug'] and 'contract' in (r['canonical_slug'] or '') for r in contract_rows)
        assert packages['unfiled_documents']['key'] == 'unfiled_documents'
        # Never claim legally required.
        for row in packages['listing_package']['documents']:
            assert 'legally required' not in (row.get('reason') or '').lower()
            assert row['applicability_label'] in (
                'Expected', 'May apply', 'Unknown', 'Not applicable', 'After execution',
            )
        db.session.rollback()


def test_buyer_controlling_contract_package(app, seed):
    with app.app_context():
        tx_type = TransactionType.query.filter_by(
            organization_id=seed['org_a'], name='buyer',
        ).first()
        tx = Transaction(
            organization_id=seed['org_a'],
            created_by_id=seed['owner_a'],
            transaction_type_id=tx_type.id,
            street_address='Workspace Buyer',
            status='showing',
        )
        db.session.add(tx)
        db.session.flush()
        doc = TransactionDocument(
            organization_id=seed['org_a'],
            transaction_id=tx.id,
            template_slug='one-to-four-family-contract',
            template_name='Purchase Contract',
            status='signed',
            document_source='completed',
            field_data={},
        )
        db.session.add(doc)
        db.session.flush()
        create_baseline_from_document(
            transaction=tx,
            document=doc,
            approved_terms={
                'sales_price': '410000',
                'effective_date': '2026-08-02',
                'closing_date': '2026-09-10',
                'financing_type': 'Cash',
            },
            actor_id=seed['owner_a'],
        )
        packages = build_document_packages(tx)
        assert packages['side'] == 'buyer'
        assert packages['listing_package'] is None
        assert packages['buyer_transaction_package'] is not None
        assert packages['buyer_transaction_package']['label'] == 'Buyer transaction package'
        assert 'listing' not in (
            packages['buyer_transaction_package']['label'] or ''
        ).lower()
        assert packages['controlling_contract_package'] is not None
        assert packages['controlling_contract_package']['closing_date'] == '2026-09-10'
        # Cash: POF is never presented as something the file is waiting on.
        # No stronger terms means no row at all — agents add it by hand.
        rows = packages['controlling_contract_package']['documents']
        assert not [
            r for r in rows
            if r.get('canonical_slug') == 'pre-approval-or-proof-of-funds'
        ]
        db.session.rollback()


def test_buyer_unknown_doc_appears_once_in_unfiled(app, seed):
    with app.app_context():
        tx_type = TransactionType.query.filter_by(
            organization_id=seed['org_a'], name='buyer',
        ).first()
        tx = Transaction(
            organization_id=seed['org_a'],
            created_by_id=seed['owner_a'],
            transaction_type_id=tx_type.id,
            street_address='Unfiled Buyer Doc',
            status='showing',
        )
        db.session.add(tx)
        db.session.flush()
        unknown = TransactionDocument(
            organization_id=seed['org_a'],
            transaction_id=tx.id,
            template_slug='completed',
            template_name='Mystery Upload',
            status='uploaded',
            document_source='completed',
            signed_file_path='transactions/1/external/mystery.pdf',
            field_data={},
        )
        known = TransactionDocument(
            organization_id=seed['org_a'],
            transaction_id=tx.id,
            template_slug='buyer-rep-agreement',
            template_name='Buyer Rep',
            status='signed',
            document_source='completed',
            signed_file_path='transactions/1/external/buyer-rep.pdf',
            field_data={},
        )
        db.session.add_all([unknown, known])
        db.session.flush()

        packages = build_document_packages(tx)
        assert packages['listing_package'] is None
        labels = []
        for key in (
            'listing_package',
            'buyer_transaction_package',
            'transaction_documents_package',
            'unfiled_documents',
        ):
            pkg = packages.get(key)
            if pkg:
                labels.append(pkg.get('label') or '')
        assert not any('listing package' in (label or '').lower() for label in labels)

        unfiled_ids = [
            r['document_id']
            for r in packages['unfiled_documents']['documents']
            if r.get('document_id')
        ]
        assert unfiled_ids.count(unknown.id) == 1
        assert known.id not in unfiled_ids
        buyer_ids = [
            r['document_id']
            for r in packages['buyer_transaction_package']['documents']
            if r.get('document_id')
        ]
        assert buyer_ids.count(known.id) == 1
        assert unknown.id not in buyer_ids
        db.session.rollback()


def test_not_applicable_lead_paint_placeholder_is_hidden(app, seed):
    """Post-1978 questionnaire answer must not leave a lead-paint Upload slot."""
    with app.app_context():
        tx = Transaction.query.get(seed['tx_a'])
        assert tx is not None
        tx.ownership_status = 'conventional'
        tx.intake_data = {
            'built_before_1978': False,
            'has_hoa': True,
            'flood_hazard': False,
            'has_survey': 'no',
        }
        listing = _listing_doc(seed, tx)
        listing.signed_file_path = 'org/tx/listing.pdf'
        lead = TransactionDocument(
            organization_id=seed['org_a'],
            transaction_id=tx.id,
            template_slug='lead-paint',
            template_name='Lead-Based Paint Disclosure',
            status='pending',
            is_placeholder=True,
            document_source='placeholder',
            included_reason='Property built before 1978',
        )
        db.session.add(lead)
        db.session.commit()
        tx_id = tx.id
        lead_id = lead.id

        packages = build_document_packages(tx)
        rows = packages['listing_package']['documents']
        slugs = {r.get('canonical_slug') or r.get('template_slug') for r in rows}
        assert 'lead-paint' not in slugs
        assert all(r.get('document_id') != lead_id for r in rows)
        assert all(r.get('state') != 'not_applicable' for r in rows)

        # Only remove rows this test created — seed is session-scoped.
        TransactionDocument.query.filter(
            TransactionDocument.id.in_([listing.id, lead_id]),
        ).delete(synchronize_session=False)
        db.session.commit()


def test_placeholder_without_file_is_needed_not_uploaded(app, seed):
    """Questionnaire slots are not uploads until a PDF is actually on file."""
    with app.app_context():
        tx = Transaction(
            organization_id=seed['org_a'],
            created_by_id=seed['owner_a'],
            transaction_type_id=seed['tx_type_a'],
            street_address='Placeholder Listing Ln',
            status='preparing_to_list',
            intake_data={'has_hoa': True},
        )
        db.session.add(tx)
        db.session.flush()
        listing = TransactionDocument(
            organization_id=seed['org_a'],
            transaction_id=tx.id,
            template_slug='listing-agreement',
            template_name='Listing Agreement',
            status='signed',
            document_source='completed',
            signed_file_path='transactions/1/external/listing.pdf',
            is_placeholder=False,
        )
        iabs = TransactionDocument(
            organization_id=seed['org_a'],
            transaction_id=tx.id,
            template_slug='iabs',
            template_name='Information About Brokerage Services',
            status='pending',
            document_source='placeholder',
            is_placeholder=True,
            signed_file_path=None,
        )
        db.session.add_all([listing, iabs])
        db.session.flush()

        packages = build_document_packages(tx)
        all_rows = []
        for key in (
            'listing_package',
            'buyer_transaction_package',
            'transaction_documents_package',
            'unfiled_documents',
        ):
            pkg = packages.get(key) or {}
            all_rows.extend(pkg.get('documents') or [])

        listing_row = next(
            r for r in all_rows
            if (r.get('canonical_slug') or r.get('template_slug')) == 'listing-agreement'
        )
        iabs_row = next(r for r in all_rows if r.get('document_id') == iabs.id)

        assert listing_row['state'] == 'uploaded'
        assert listing_row['doc_url']
        assert iabs_row['state'] == 'missing'
        assert iabs_row['doc_url'] is None
        assert iabs_row['is_placeholder'] is True
        db.session.rollback()


def test_listing_packet_embeds_surface_over_empty_placeholders(app, seed):
    """AI/fingerprint packet members must not look Needed behind empty slots."""
    from services.document_identity import (
        KIND_LISTING_AGREEMENT,
        DocumentIdentity,
        persist_identity_on_field_data,
    )

    with app.app_context():
        tx = Transaction(
            organization_id=seed['org_a'],
            created_by_id=seed['owner_a'],
            transaction_type_id=seed['tx_type_a'],
            street_address='6048 Heritage Creek Lane',
            status='preparing_to_list',
            intake_data={'has_hoa': True},
        )
        db.session.add(tx)
        db.session.flush()
        identity = DocumentIdentity(
            kind=KIND_LISTING_AGREEMENT,
            template_slug='listing-agreement',
            form_number='TXR-1101',
            confidence=0.95,
        )
        field_data = persist_identity_on_field_data(
            {
                'has_hoa': True,
                'detected_documents': [
                    {
                        'document_type': 'listing_agreement',
                        'start_page': 1,
                        'end_page': 11,
                    },
                    {
                        'document_type': 'iabs',
                        'start_page': 13,
                        'end_page': 13,
                    },
                    {
                        'document_type': 'hoa_addendum',
                        'start_page': 14,
                        'end_page': 14,
                    },
                ],
            },
            identity,
        )
        listing = TransactionDocument(
            organization_id=seed['org_a'],
            transaction_id=tx.id,
            template_slug='listing-agreement',
            template_name='Listing Agreement',
            status='signed',
            document_source='completed',
            signed_file_path='transactions/1/external/listing.pdf',
            is_placeholder=False,
            field_data=field_data,
        )
        iabs = TransactionDocument(
            organization_id=seed['org_a'],
            transaction_id=tx.id,
            template_slug='iabs',
            template_name='Information About Brokerage Services',
            status='pending',
            document_source='placeholder',
            is_placeholder=True,
        )
        hoa = TransactionDocument(
            organization_id=seed['org_a'],
            transaction_id=tx.id,
            template_slug='hoa-addendum',
            template_name='HOA Addendum',
            status='pending',
            document_source='placeholder',
            is_placeholder=True,
        )
        db.session.add_all([listing, iabs, hoa])
        db.session.flush()

        packages = build_document_packages(tx)
        rows = (packages.get('listing_package') or {}).get('documents') or []
        by_slug = {
            (r.get('canonical_slug') or r.get('template_slug')): r for r in rows
        }
        assert by_slug['listing-agreement']['state'] == 'uploaded'
        assert by_slug['iabs']['state'] == 'detected_in_package'
        assert by_slug['iabs']['detected_in_package'] is True
        assert by_slug['iabs']['parent_document_id'] == listing.id
        assert by_slug['iabs']['document_id'] == iabs.id
        assert by_slug['hoa-addendum']['state'] == 'detected_in_package'
        assert by_slug['hoa-addendum']['parent_document_id'] == listing.id
        db.session.rollback()
