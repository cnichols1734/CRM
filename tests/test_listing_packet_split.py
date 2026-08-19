"""Listing-package split: fingerprints first, then AI, then trimmed parent PDF."""
from pathlib import Path
from unittest.mock import patch

import fitz
import pytest

from services.listing_packet import plan_listing_packet_split
from services.pdf_splitter import get_pdf_page_count, normalize_segments


HERITAGE_PACKET = Path(__file__).resolve().parents[1] / (
    'Listing Agreement and Docs for 6048 Heritage Creek.pdf'
)


def _stored(uploaded: dict, doc) -> bytes:
    """Bytes the app would actually serve for ``doc`` (source path wins)."""
    path = doc.source_file_path or doc.signed_file_path
    by_path = {f'test/{name}': data for name, data in uploaded.items()}
    return by_path[path]


def _pdf_from_pages(pages: list[str]) -> bytes:
    doc = fitz.open()
    try:
        for index, text in enumerate(pages):
            doc.insert_page(index, text=text)
        return doc.tobytes()
    finally:
        doc.close()


def _mixed_listing_packet_pdf() -> bytes:
    """Five docs in a realistic Texas listing packet order."""
    listing = [
        (
            'RESIDENTIAL REAL ESTATE LISTING AGREEMENT\n'
            'EXCLUSIVE RIGHT TO SELL\n'
            'USE OF THIS FORM BY PERSONS WHO ARE NOT MEMBERS OF THE TEXAS '
            'ASSOCIATION OF REALTORS®, INC. IS NOT AUTHORIZED.\n'
            f'Listing body page {page}\n'
            '(TXR-1101) 01-05-26'
        )
        for page in range(1, 4)
    ]
    net = (
        "SELLER'S ESTIMATED NET PROCEEDS\n"
        'USE OF THIS FORM BY PERSONS WHO ARE NOT MEMBERS OF THE TEXAS '
        'ASSOCIATION OF REALTORS®, INC. IS NOT AUTHORIZED.\n'
        'Estimated Proceeds to Seller\n'
        '(TXR-1935) 02-01-18'
    )
    iabs = (
        'Information About Brokerage Services\n'
        'Texas law requires all real estate license holders to give the following '
        'information about brokerage services.\n'
        'IABS 1-0  TXR 2501'
    )
    hoa = (
        'PROMULGATED BY THE TEXAS REAL ESTATE COMMISSION (TREC)\n'
        'ADDENDUM FOR PROPERTY SUBJECT TO MANDATORY MEMBERSHIP IN A PROPERTY '
        'OWNERS ASSOCIATION\n'
        'TREC NO. 36-10  TXR-1922'
    )
    wire = (
        'WIRE FRAUD WARNING\n'
        'USE OF THIS FORM BY PERSONS WHO ARE NOT MEMBERS OF THE TEXAS '
        'ASSOCIATION OF REALTORS®, INC. IS NOT AUTHORIZED.\n'
        'Buyers and Sellers Beware\n'
        '(TXR 2517) 2-1-18'
    )
    return _pdf_from_pages(listing + [net, iabs, hoa, wire])


def _packet_with_unknown_form() -> bytes:
    """Listing pages plus a form our fingerprint table has never heard of."""
    listing = [
        (
            'RESIDENTIAL REAL ESTATE LISTING AGREEMENT\n'
            'EXCLUSIVE RIGHT TO SELL\n'
            'USE OF THIS FORM BY PERSONS WHO ARE NOT MEMBERS OF THE TEXAS '
            'ASSOCIATION OF REALTORS®, INC. IS NOT AUTHORIZED.\n'
            f'Listing body page {page}\n'
            '(TXR-1101) 01-05-26'
        )
        for page in range(1, 4)
    ]
    mystery = (
        'USE OF THIS FORM BY PERSONS WHO ARE NOT MEMBERS OF THE TEXAS '
        'ASSOCIATION OF REALTORS®, INC. IS NOT AUTHORIZED.\n'
        'PIPELINE EASEMENT ACKNOWLEDGEMENT\n'
        'Seller acknowledges the recorded easement affecting the property.'
    )
    return _pdf_from_pages(listing + [mystery])


def test_unknown_form_is_not_absorbed_into_the_listing_agreement():
    """A form nobody can name must never become extra listing pages."""
    from services.listing_packet import build_listing_packet_plan

    plan = build_listing_packet_plan(_packet_with_unknown_form())
    by_type = {seg.document_type: (seg.start_page, seg.end_page) for seg in plan.segments}

    assert by_type['listing_agreement'] == (1, 3)
    assert by_type['unknown'] == (4, 4)
    assert plan.unresolved_pages == [4]
    assert plan.is_confident is False


def test_ai_classifies_a_form_the_fingerprints_do_not_know():
    """AI is the coverage layer for forms outside the fingerprint table."""
    from services.listing_packet import build_listing_packet_plan

    plan = build_listing_packet_plan(
        _packet_with_unknown_form(),
        ai_segments=[
            {
                'document_type': 'other',
                'title': 'Pipeline Easement Acknowledgement',
                'start_page': 4,
                'end_page': 4,
            },
        ],
    )
    by_type = {seg.document_type: seg for seg in plan.segments}

    assert (by_type['listing_agreement'].start_page, by_type['listing_agreement'].end_page) == (1, 3)
    assert by_type['unknown'].title == 'Pipeline Easement Acknowledgement'
    assert plan.is_confident is True
    assert plan.unresolved_pages == []


def test_confident_packet_needs_no_ai():
    from services.listing_packet import build_listing_packet_plan

    plan = build_listing_packet_plan(_mixed_listing_packet_pdf())
    assert plan.is_confident is True
    assert plan.guessed_pages == []


def test_upload_defers_split_until_ai_when_a_page_is_unaccounted_for(app, db, seed):
    from models import Transaction, TransactionDocument
    from services.seller_workflow import split_listing_package_into_children

    pdf_bytes = _packet_with_unknown_form()
    uploaded = {}

    def fake_upload(transaction_id, file_data, original_filename, content_type):
        uploaded[original_filename] = file_data
        return {'path': f'test/{original_filename}'}

    with app.app_context():
        tx = Transaction(
            organization_id=seed['org_a'],
            created_by_id=seed['owner_a'],
            transaction_type_id=seed['tx_type_a'],
            street_address='99 Deferred Split Rd',
            city='Austin',
            state='TX',
            status='active',
        )
        db.session.add(tx)
        db.session.flush()
        parent = TransactionDocument(
            organization_id=seed['org_a'],
            transaction_id=tx.id,
            template_slug='listing-agreement',
            template_name='Listing Agreement',
            status='signed',
            signed_original_filename='mystery.pdf',
            source_file_path='test/mystery.pdf',
            signed_file_path='test/mystery.pdf',
        )
        db.session.add(parent)
        db.session.commit()
        parent_id = parent.id

        with patch(
            'services.supabase_storage.upload_external_document',
            side_effect=fake_upload,
        ):
            # Before AI: hold off rather than guess.
            assert split_listing_package_into_children(parent_id, pdf_bytes) == []
            assert TransactionDocument.query.filter_by(
                parent_document_id=parent_id,
            ).count() == 0

            # After AI has had its say: file it, flagged for classification.
            children = split_listing_package_into_children(
                parent_id, pdf_bytes, require_confident=False,
            )
        db.session.commit()

        assert len(children) == 1
        flagged = children[0]
        # A generic slug renders the row as "Needs classification" in the
        # package UI, named from the heading printed on the page.
        assert flagged.template_slug == 'external'
        assert flagged.template_name == 'PIPELINE EASEMENT ACKNOWLEDGEMENT'
        assert (flagged.page_start, flagged.page_end) == (4, 4)
        assert get_pdf_page_count(uploaded[flagged.signed_original_filename]) == 1

        parent = db.session.get(TransactionDocument, parent_id)
        assert (parent.page_start, parent.page_end) == (1, 3)
        assert get_pdf_page_count(_stored(uploaded, parent)) == 3


def test_normalize_segments_accepts_page_key_aliases():
    segments = normalize_segments(
        [
            {'document_type': 'IABS', 'start_page': 4, 'end_page': 4},
            {'type': 'hoa_addendum', 'page_start': 5, 'page_end': 5},
        ],
        total_pages=5,
    )
    assert [(s.document_type, s.start_page, s.end_page) for s in segments] == [
        ('iabs', 4, 4),
        ('hoa_addendum', 5, 5),
    ]


def test_fingerprints_split_mixed_packet_in_any_detected_order():
    pdf_bytes = _mixed_listing_packet_pdf()
    segments = plan_listing_packet_split(
        pdf_bytes,
        ai_segments=[
            # Deliberately wrong: AI glues net proceeds onto the listing.
            {
                'document_type': 'listing_agreement',
                'start_page': 1,
                'end_page': 7,
            },
        ],
    )
    by_type = {
        seg.document_type: (seg.start_page, seg.end_page) for seg in segments
    }
    assert by_type['listing_agreement'] == (1, 3)
    assert by_type['seller_estimated_net_proceeds'] == (4, 4)
    assert by_type['iabs'] == (5, 5)
    assert by_type['hoa_addendum'] == (6, 6)
    assert by_type['wire_fraud_warning'] == (7, 7)


def test_fingerprints_find_net_proceeds_when_ai_omits_it():
    pdf_bytes = _mixed_listing_packet_pdf()
    segments = plan_listing_packet_split(
        pdf_bytes,
        ai_segments=[
            {'document_type': 'listing_agreement', 'start_page': 1, 'end_page': 3},
            {'document_type': 'iabs', 'start_page': 5, 'end_page': 5},
            {'document_type': 'hoa_addendum', 'start_page': 6, 'end_page': 6},
            {'document_type': 'wire_fraud_warning', 'start_page': 7, 'end_page': 7},
        ],
    )
    net = next(
        seg for seg in segments
        if seg.document_type == 'seller_estimated_net_proceeds'
    )
    assert (net.start_page, net.end_page) == (4, 4)


@pytest.mark.skipif(not HERITAGE_PACKET.exists(), reason='Heritage Creek sample PDF not in repo')
def test_heritage_creek_packet_classifies_every_form():
    pdf_bytes = HERITAGE_PACKET.read_bytes()
    segments = plan_listing_packet_split(pdf_bytes)
    by_type = {
        seg.document_type: (seg.start_page, seg.end_page) for seg in segments
    }
    assert by_type['listing_agreement'] == (1, 11)
    assert by_type['seller_estimated_net_proceeds'] == (12, 12)
    assert by_type['iabs'] == (13, 13)
    assert by_type['hoa_addendum'] == (14, 14)
    assert by_type['wire_fraud_warning'] == (15, 15)


def test_split_listing_package_trims_parent_and_files_net_proceeds(app, db, seed):
    from models import TransactionDocument
    from services.seller_workflow import split_listing_package_into_children

    pdf_bytes = _mixed_listing_packet_pdf()
    uploaded = {}

    def fake_upload(transaction_id, file_data, original_filename, content_type):
        uploaded[original_filename] = file_data
        return {'path': f'test/{original_filename}'}

    with app.app_context():
        from models import Transaction

        tx = Transaction(
            organization_id=seed['org_a'],
            created_by_id=seed['owner_a'],
            transaction_type_id=seed['tx_type_a'],
            street_address='12 Packet Trim Ln',
            city='Austin',
            state='TX',
            status='active',
        )
        db.session.add(tx)
        db.session.flush()
        parent = TransactionDocument(
            organization_id=seed['org_a'],
            transaction_id=tx.id,
            template_slug='listing-agreement',
            template_name='Listing Agreement',
            status='signed',
            signed_original_filename='heritage_packet.pdf',
            signed_file_path='test/heritage_packet.pdf',
            signed_file_size=len(pdf_bytes),
            extraction_status='complete',
            field_data={
                'detected_documents': [
                    {
                        'document_type': 'listing_agreement',
                        'start_page': 1,
                        'end_page': 7,
                    },
                ],
            },
        )
        db.session.add(parent)
        db.session.commit()
        parent_id = parent.id

        with patch(
            'services.supabase_storage.upload_external_document',
            side_effect=fake_upload,
        ):
            children = split_listing_package_into_children(parent_id, pdf_bytes)
        db.session.commit()

        parent = db.session.get(TransactionDocument, parent_id)
        child_slugs = {child.template_slug for child in children}

        assert 'seller-net-proceeds' in child_slugs
        assert 'iabs' in child_slugs
        assert 'hoa-addendum' in child_slugs
        assert 'wire-fraud-warning' in child_slugs
        assert parent.page_start == 1
        assert parent.page_end == 3
        listing_filename = parent.signed_original_filename
        assert listing_filename.endswith('_listing_agreement.pdf')
        trimmed = uploaded[listing_filename]
        assert get_pdf_page_count(trimmed) == 3

        net = next(c for c in children if c.template_slug == 'seller-net-proceeds')
        assert net.page_start == 4 and net.page_end == 4
        net_pdf = uploaded[net.signed_original_filename]
        assert get_pdf_page_count(net_pdf) == 1
        net_text = fitz.open(stream=net_pdf, filetype='pdf')[0].get_text('text')
        assert "SELLER'S ESTIMATED NET PROCEEDS" in net_text
        assert 'LISTING AGREEMENT' not in net_text.upper()


def test_relinked_listing_document_still_splits_packet(app, db, seed):
    """Apply must split even when the bootstrap session already points at the listing row."""
    from models import ContractBootstrapSession, TransactionDocument, User
    from services.contract_bootstrap import _link_document_to_transaction

    pdf_bytes = _mixed_listing_packet_pdf()
    uploaded = {}

    def fake_upload(transaction_id, file_data, original_filename, content_type):
        uploaded[original_filename] = file_data
        return {'path': f'test/{original_filename}'}

    with app.app_context():
        from models import Transaction

        user = User.query.get(seed['owner_a'])
        # Own transaction: the splitter refuses to file a second copy of a form
        # that already has a real PDF on the file.
        tx = Transaction(
            organization_id=seed['org_a'],
            created_by_id=user.id,
            transaction_type_id=seed['tx_type_a'],
            street_address='6048 Heritage Creek',
            city='Austin',
            state='TX',
            status='active',
        )
        db.session.add(tx)
        db.session.flush()
        parent = TransactionDocument(
            organization_id=seed['org_a'],
            transaction_id=tx.id,
            template_slug='listing-agreement',
            template_name='Listing Agreement',
            status='signed',
            signed_original_filename='heritage_packet.pdf',
            signed_file_path='test/heritage_packet.pdf',
            field_data={
                'detected_documents': [
                    {
                        'document_type': 'listing_agreement',
                        'start_page': 1,
                        'end_page': 7,
                    },
                ],
                '_document_identity': {
                    'kind': 'listing_agreement',
                    'template_slug': 'listing-agreement',
                    'confidence': 0.95,
                },
            },
        )
        db.session.add(parent)
        db.session.flush()
        session = ContractBootstrapSession(
            organization_id=seed['org_a'],
            uploader_user_id=user.id,
            document_id=parent.id,
            original_filename='heritage_packet.pdf',
            mime_type='application/pdf',
            page_count=7,
            upload_source='inbox',
            status=ContractBootstrapSession.STATUS_AWAITING_REVIEW,
            match_status=ContractBootstrapSession.MATCH_MATCHED,
            matched_transaction_id=tx.id,
        )
        db.session.add(session)
        db.session.commit()
        parent_id = parent.id

        with patch(
            'services.contract_bootstrap._pdf_bytes_for_listing_split',
            return_value=pdf_bytes,
        ), patch(
            'services.supabase_storage.upload_external_document',
            side_effect=fake_upload,
        ):
            linked = _link_document_to_transaction(session=session, transaction=tx)
        db.session.commit()

        assert linked.id == parent_id
        children = TransactionDocument.query.filter_by(parent_document_id=parent_id).all()
        child_slugs = {child.template_slug for child in children}
        assert 'seller-net-proceeds' in child_slugs
        assert 'iabs' in child_slugs
        assert 'hoa-addendum' in child_slugs
        assert 'wire-fraud-warning' in child_slugs


@pytest.mark.skipif(not HERITAGE_PACKET.exists(), reason='Heritage Creek sample PDF not in repo')
def test_heritage_creek_upload_produces_one_file_per_form(app, db, seed):
    """End to end on the real 15-page packet: 1-11 listing, then one page each."""
    from models import Transaction, TransactionDocument
    from services.intake_service import post_upload_processing

    pdf_bytes = HERITAGE_PACKET.read_bytes()
    uploaded = {}

    def fake_upload(transaction_id, file_data, original_filename, content_type):
        uploaded[original_filename] = file_data
        return {'path': f'test/{original_filename}'}

    with app.app_context():
        tx = Transaction(
            organization_id=seed['org_a'],
            created_by_id=seed['owner_a'],
            transaction_type_id=seed['tx_type_a'],
            street_address='6048 Heritage Creek Dr',
            city='Austin',
            state='TX',
            status='active',
        )
        db.session.add(tx)
        db.session.flush()
        parent = TransactionDocument(
            organization_id=seed['org_a'],
            transaction_id=tx.id,
            template_slug='listing-agreement',
            template_name='Listing Agreement',
            status='signed',
            signed_original_filename='heritage.pdf',
            # Bootstrap files the packet under both paths; the trim has to
            # move both or the viewer keeps serving all 15 pages.
            source_file_path='test/heritage.pdf',
            signed_file_path='test/heritage.pdf',
            signed_file_size=len(pdf_bytes),
        )
        db.session.add(parent)
        db.session.commit()
        parent_id = parent.id

        with patch(
            'services.supabase_storage.upload_external_document',
            side_effect=fake_upload,
        ), patch(
            'services.supabase_storage.download_document',
            return_value=pdf_bytes,
        ), patch('threading.Thread.start'):
            post_upload_processing(parent)
        db.session.commit()

        parent = db.session.get(TransactionDocument, parent_id)
        assert (parent.page_start, parent.page_end) == (1, 11)
        assert get_pdf_page_count(uploaded[parent.signed_original_filename]) == 11
        # Viewers and extraction resolve source_file_path first, so it must
        # point at the trimmed listing, not the original 15-page packet.
        assert parent.source_file_path == parent.signed_file_path
        assert get_pdf_page_count(_stored(uploaded, parent)) == 11

        children = TransactionDocument.query.filter_by(
            parent_document_id=parent_id,
        ).all()
        by_slug = {child.template_slug: child for child in children}
        assert set(by_slug) == {
            'seller-net-proceeds', 'iabs', 'hoa-addendum', 'wire-fraud-warning',
        }
        for slug, page in (
            ('seller-net-proceeds', 12),
            ('iabs', 13),
            ('hoa-addendum', 14),
            ('wire-fraud-warning', 15),
        ):
            child = by_slug[slug]
            assert (child.page_start, child.page_end) == (page, page), slug
            assert get_pdf_page_count(uploaded[child.signed_original_filename]) == 1, slug

        net_text = fitz.open(
            stream=uploaded[by_slug['seller-net-proceeds'].signed_original_filename],
            filetype='pdf',
        )[0].get_text('text').upper()
        assert 'NET PROCEEDS' in net_text
        assert 'LISTING AGREEMENT' not in net_text


def test_upload_splits_packet_and_fulfills_placeholders_without_extraction(app, db, seed):
    """The split must not wait on AI extraction, and must fill questionnaire slots."""
    from models import Transaction, TransactionDocument
    from services.intake_service import post_upload_processing

    pdf_bytes = _mixed_listing_packet_pdf()
    uploaded = {}

    def fake_upload(transaction_id, file_data, original_filename, content_type):
        uploaded[original_filename] = file_data
        return {'path': f'test/{original_filename}'}

    with app.app_context():
        tx = Transaction(
            organization_id=seed['org_a'],
            created_by_id=seed['owner_a'],
            transaction_type_id=seed['tx_type_a'],
            street_address='6048 Heritage Creek Unit B',
            city='Austin',
            state='TX',
            status='active',
        )
        db.session.add(tx)
        db.session.flush()

        placeholders = {}
        for slug, name in (
            ('iabs', 'Information About Brokerage Services'),
            ('seller-net-proceeds', "Seller's Estimated Net Proceeds"),
            ('hoa-addendum', 'HOA Addendum'),
            ('wire-fraud-warning', 'Wire Fraud Warning'),
            ('sellers-disclosure', "Seller's Disclosure Notice"),
        ):
            row = TransactionDocument(
                organization_id=seed['org_a'],
                transaction_id=tx.id,
                template_slug=slug,
                template_name=name,
                status='pending',
                is_placeholder=True,
            )
            db.session.add(row)
            db.session.flush()
            placeholders[slug] = row.id

        parent = TransactionDocument(
            organization_id=seed['org_a'],
            transaction_id=tx.id,
            template_slug='listing-agreement',
            template_name='Listing Agreement',
            status='signed',
            signed_original_filename='heritage_packet.pdf',
            signed_file_path='test/heritage_packet.pdf',
            signed_file_size=len(pdf_bytes),
        )
        db.session.add(parent)
        db.session.commit()
        parent_id = parent.id

        with patch(
            'services.supabase_storage.upload_external_document',
            side_effect=fake_upload,
        ), patch(
            'services.supabase_storage.download_document',
            return_value=pdf_bytes,
        ), patch('threading.Thread.start'):
            post_upload_processing(parent)
        db.session.commit()

        # Each detected form fulfills its questionnaire slot in place.
        for slug, page in (
            ('seller-net-proceeds', 4),
            ('iabs', 5),
            ('hoa-addendum', 6),
            ('wire-fraud-warning', 7),
        ):
            row = db.session.get(TransactionDocument, placeholders[slug])
            assert row.parent_document_id == parent_id, slug
            assert row.is_placeholder is False, slug
            assert row.signed_file_path, slug
            assert (row.page_start, row.page_end) == (page, page), slug
            assert get_pdf_page_count(uploaded[row.signed_original_filename]) == 1, slug

        # A form that is not in the packet stays an open slot.
        disclosure = db.session.get(TransactionDocument, placeholders['sellers-disclosure'])
        assert disclosure.is_placeholder is True
        assert disclosure.signed_file_path is None

        # The listing of record holds listing pages only.
        parent = db.session.get(TransactionDocument, parent_id)
        assert (parent.page_start, parent.page_end) == (1, 3)
        assert get_pdf_page_count(uploaded[parent.signed_original_filename]) == 3
