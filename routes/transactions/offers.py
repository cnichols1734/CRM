"""Seller offer routes."""

from datetime import datetime

from flask import abort, jsonify, request
from flask_login import current_user, login_required

from models import (
    SellerAcceptedContract,
    SellerOffer,
    SellerOfferDocument,
    SellerOfferVersion,
    TransactionDocument,
    Transaction,
    db,
)
from services.seller_workflow import (
    apply_offer_terms,
    create_contract_milestones,
    create_offer_activity,
    expire_offer_if_needed,
    get_offer_document_type,
    infer_offer_document_type,
    offer_urgency,
)
from services.intake_service import post_upload_processing
from . import transactions_bp
from .decorators import transactions_required


SUPPORTING_DOCUMENT_TYPES = {
    'sellers_disclosure',
    'hoa_addendum',
    'pre_approval',
    'third_party_financing',
}


def _as_dict(value):
    """Return JSON object values only; extracted document fields may be free-form strings."""
    return value if isinstance(value, dict) else {}


def _can_manage_transaction(transaction):
    return (
        transaction.created_by_id == current_user.id
        or getattr(current_user, 'role', None) == 'admin'
        or getattr(current_user, 'org_role', None) in ('admin', 'owner')
    )


def _get_seller_transaction(id):
    transaction = Transaction.query.filter_by(
        id=id,
        organization_id=current_user.organization_id,
    ).first_or_404()
    if not _can_manage_transaction(transaction):
        abort(403)
    if transaction.transaction_type and transaction.transaction_type.name != 'seller':
        return None
    return transaction


def _parse_datetime(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    if hasattr(value, 'year') and hasattr(value, 'month') and hasattr(value, 'day'):
        return datetime.combine(value, datetime.min.time())
    for fmt in ('%Y-%m-%dT%H:%M', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d'):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def _parse_date(value):
    parsed = _parse_datetime(value)
    return parsed.date() if parsed else None


def _parse_int(value):
    if value in (None, ''):
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _normalize_terms(data):
    terms = dict(data.get('terms_data') or data.get('terms') or {})
    for key in ('response_deadline_at',):
        if isinstance(terms.get(key), str):
            terms[key] = _parse_datetime(terms[key])
    return terms


def _offer_payload(offer):
    urgency = offer_urgency(offer)
    return {
        'id': offer.id,
        'status': offer.status,
        'buyer_names': offer.buyer_names,
        'buyer_agent_name': offer.buyer_agent_name,
        'buyer_agent_brokerage': offer.buyer_agent_brokerage,
        'received_at': offer.received_at.isoformat() if offer.received_at else None,
        'response_deadline_at': offer.response_deadline_at.isoformat() if offer.response_deadline_at else None,
        'urgency': urgency,
        'offer_price': str(offer.offer_price) if offer.offer_price is not None else None,
        'financing_type': offer.financing_type,
        'proposed_close_date': offer.proposed_close_date.isoformat() if offer.proposed_close_date else None,
        'option_period_days': offer.option_period_days,
        'earnest_money': str(offer.earnest_money) if offer.earnest_money is not None else None,
        'seller_concessions_amount': str(offer.seller_concessions_amount) if offer.seller_concessions_amount is not None else None,
        'current_version_id': offer.current_version_id,
        'accepted_version_id': offer.accepted_version_id,
        'source_showing_id': offer.source_showing_id,
        'last_activity_label': offer.last_activity_label,
    }


def _merged_acceptance_terms(offer, version):
    """Build a complete terms snapshot for an accepted contract."""
    terms = dict(version.terms_data or {}) if version and isinstance(version.terms_data, dict) else {}
    offer_terms = dict(offer.terms_summary or {}) if isinstance(offer.terms_summary, dict) else {}

    for key, value in offer_terms.items():
        if key in ('supporting_documents', 'addenda'):
            continue
        if value is not None and key not in terms:
            terms[key] = value

    supporting = dict(_as_dict(offer_terms.get('supporting_documents')))
    supporting.update(_as_dict(terms.get('supporting_documents')))
    if supporting:
        terms['supporting_documents'] = supporting

    addenda = dict(_as_dict(offer_terms.get('addenda')))
    addenda.update(_as_dict(terms.get('addenda')))
    if addenda:
        terms['addenda'] = addenda

    package_docs = []
    for offer_doc in offer.offer_documents.order_by(SellerOfferDocument.created_at.asc()).all():
        doc = offer_doc.document
        package_docs.append({
            'offer_document_id': offer_doc.id,
            'document_id': offer_doc.transaction_document_id,
            'document_type': offer_doc.document_type,
            'display_name': offer_doc.display_name,
            'template_slug': doc.template_slug if doc else None,
            'filename': doc.signed_original_filename if doc else None,
            'extraction_status': doc.extraction_status if doc else None,
            'is_primary_terms_document': offer_doc.is_primary_terms_document,
            'offer_version_id': offer_doc.offer_version_id,
        })
    if package_docs:
        terms['offer_package_documents'] = package_docs

    return terms


def _contract_extra_data_from_offer(offer, terms):
    supporting = _as_dict(terms.get('supporting_documents'))
    package_docs = terms.get('offer_package_documents') or []
    return {
        'source_offer_id': offer.id,
        'source_offer_status': offer.status,
        'buyer_names': offer.buyer_names,
        'buyer_agent_name': offer.buyer_agent_name,
        'buyer_agent_email': offer.buyer_agent_email,
        'buyer_agent_phone': offer.buyer_agent_phone,
        'buyer_agent_brokerage': offer.buyer_agent_brokerage,
        'offer_package_documents': package_docs,
        'supporting_documents': supporting,
        'supporting_document_ids': [
            doc['document_id']
            for doc in package_docs
            if doc.get('document_type') in SUPPORTING_DOCUMENT_TYPES and doc.get('document_id')
        ],
        'primary_document_ids': [
            doc['document_id']
            for doc in package_docs
            if doc.get('is_primary_terms_document') and doc.get('document_id')
        ],
    }


@transactions_bp.route('/<int:id>/offers', methods=['GET'])
@login_required
@transactions_required
def list_seller_offers(id):
    """Return seller offers sorted by deadline urgency."""
    transaction = _get_seller_transaction(id)
    if transaction is None:
        return jsonify({'success': False, 'error': 'Offers are only available for seller transactions'}), 400

    offers = transaction.seller_offers.order_by(SellerOffer.received_at.desc()).all()
    offers.sort(key=lambda offer: (offer_urgency(offer)['rank'], offer.response_deadline_at or datetime.max))
    return jsonify({'success': True, 'offers': [_offer_payload(offer) for offer in offers]})


@transactions_bp.route('/<int:id>/offers', methods=['POST'])
@login_required
@transactions_required
def create_seller_offer(id):
    """Create a manual/verbal offer thread and first version."""
    transaction = _get_seller_transaction(id)
    if transaction is None:
        return jsonify({'success': False, 'error': 'Offers are only available for seller transactions'}), 400

    data = request.get_json(silent=True) or request.form
    terms = _normalize_terms(data)
    response_deadline_at = _parse_datetime(data.get('response_deadline_at')) or terms.get('response_deadline_at')

    offer = SellerOffer(
        organization_id=current_user.organization_id,
        transaction_id=transaction.id,
        created_by_id=current_user.id,
        source_showing_id=data.get('source_showing_id') or None,
        buyer_names=data.get('buyer_names'),
        buyer_agent_name=data.get('buyer_agent_name'),
        buyer_agent_email=data.get('buyer_agent_email'),
        buyer_agent_phone=data.get('buyer_agent_phone'),
        buyer_agent_brokerage=data.get('buyer_agent_brokerage'),
        received_at=_parse_datetime(data.get('received_at')) or datetime.utcnow(),
        creation_source=data.get('creation_source') or 'manual_entry',
        status='new',
        response_deadline_at=response_deadline_at,
        response_deadline_source='manual' if response_deadline_at else None,
    )
    apply_offer_terms(offer, terms)

    version = SellerOfferVersion(
        organization_id=current_user.organization_id,
        transaction_id=transaction.id,
        offer=offer,
        created_by_id=current_user.id,
        version_number=1,
        direction='buyer_offer',
        status='reviewed' if data.get('reviewed') else 'draft',
        submitted_at=offer.received_at,
        terms_data=terms,
        extraction_reviewed_at=datetime.utcnow() if data.get('reviewed') else None,
        extraction_reviewed_by_id=current_user.id if data.get('reviewed') else None,
    )

    try:
        db.session.add(offer)
        db.session.add(version)
        db.session.flush()
        offer.current_version_id = version.id
        create_offer_activity(
            offer,
            'offer_created',
            'Offer logged manually' if offer.creation_source != 'uploaded_document' else 'Offer uploaded',
            actor_id=current_user.id,
            version_id=version.id,
        )
        db.session.commit()
        return jsonify({'success': True, 'offer': _offer_payload(offer)}), 201
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@transactions_bp.route('/<int:id>/offers/upload', methods=['POST'])
@login_required
@transactions_required
def upload_seller_offer_document(id):
    """Upload an offer PDF, creating or attaching to a seller offer thread."""
    transaction = _get_seller_transaction(id)
    if transaction is None:
        return jsonify({'success': False, 'error': 'Offers are only available for seller transactions'}), 400

    files = request.files.getlist('files') or request.files.getlist('file')
    files = [file for file in files if file and file.filename]
    if not files:
        return jsonify({'success': False, 'error': 'No file uploaded'}), 400

    document_types = (
        request.form.getlist('document_type')
        or request.form.getlist('document_types[]')
        or request.form.getlist('direction')
    )

    try:
        from services.supabase_storage import upload_external_document as upload_storage

        offer = None
        offer_id = request.form.get('offer_id')
        if offer_id:
            offer = SellerOffer.query.filter_by(
                id=int(offer_id),
                transaction_id=transaction.id,
                organization_id=current_user.organization_id,
            ).first_or_404()
        else:
            offer = SellerOffer(
                organization_id=current_user.organization_id,
                transaction_id=transaction.id,
                created_by_id=current_user.id,
                buyer_names=request.form.get('buyer_names'),
                buyer_agent_name=request.form.get('buyer_agent_name'),
                buyer_agent_email=request.form.get('buyer_agent_email'),
                buyer_agent_phone=request.form.get('buyer_agent_phone'),
                buyer_agent_brokerage=request.form.get('buyer_agent_brokerage'),
                received_at=_parse_datetime(request.form.get('received_at')) or datetime.utcnow(),
                creation_source='uploaded_document',
                status='needs_review',
                response_deadline_at=_parse_datetime(request.form.get('response_deadline_at')),
                response_deadline_source='manual' if request.form.get('response_deadline_at') else None,
            )
            db.session.add(offer)
            db.session.flush()

        uploaded = []
        next_version = offer.versions.count() + 1
        max_size = 25 * 1024 * 1024
        for index, file in enumerate(files):
            file_ext = file.filename.rsplit('.', 1)[1].lower() if '.' in file.filename else ''
            if file_ext != 'pdf':
                return jsonify({'success': False, 'error': f'{file.filename}: only PDF files are allowed'}), 400

            file_data = file.read()
            if len(file_data) > max_size:
                return jsonify({'success': False, 'error': f'{file.filename}: file too large. Maximum size is 25MB.'}), 400

            explicit_type = document_types[index] if index < len(document_types) else None
            document_type = infer_offer_document_type(
                file.filename,
                explicit_type or request.form.get('document_type') or request.form.get('direction'),
            )
            doc_config = get_offer_document_type(document_type)
            template_slug = doc_config['template_slug']
            display_name = doc_config['label']

            result = upload_storage(
                transaction_id=transaction.id,
                file_data=file_data,
                original_filename=file.filename,
                content_type='application/pdf',
            )

            doc = TransactionDocument(
                organization_id=current_user.organization_id,
                transaction_id=transaction.id,
                template_slug=template_slug,
                template_name=display_name,
                status='signed',
                document_source='completed',
                signed_file_path=result['path'],
                signed_file_size=len(file_data),
                signed_original_filename=file.filename,
                signed_at=datetime.utcnow(),
                extraction_status='pending',
                field_data={},
            )
            db.session.add(doc)
            db.session.flush()

            version = None
            if doc_config['primary_terms']:
                version = SellerOfferVersion(
                    organization_id=current_user.organization_id,
                    transaction_id=transaction.id,
                    offer_id=offer.id,
                    created_by_id=current_user.id,
                    transaction_document_id=doc.id,
                    version_number=next_version,
                    direction=doc_config['direction'] or 'buyer_offer',
                    status='submitted',
                    submitted_at=datetime.utcnow(),
                    terms_data={},
                )
                db.session.add(version)
                db.session.flush()
                offer.current_version_id = version.id
                next_version += 1

            offer_document = SellerOfferDocument(
                organization_id=current_user.organization_id,
                transaction_id=transaction.id,
                offer_id=offer.id,
                transaction_document_id=doc.id,
                offer_version_id=version.id if version else None,
                created_by_id=current_user.id,
                document_type=document_type,
                display_name=display_name,
                is_primary_terms_document=doc_config['primary_terms'],
                extraction_summary={},
            )
            db.session.add(offer_document)
            db.session.flush()

            create_offer_activity(
                offer,
                'document_uploaded',
                f'{display_name} uploaded for extraction',
                actor_id=current_user.id,
                version_id=version.id if version else None,
                document_id=doc.id,
                event_data={
                    'filename': file.filename,
                    'template_slug': template_slug,
                    'document_type': document_type,
                },
            )
            uploaded.append({
                'doc': doc,
                'version': version,
                'offer_document': offer_document,
                'document_type': document_type,
                'display_name': display_name,
            })

        offer.last_activity_at = datetime.utcnow()
        db.session.commit()

        for item in uploaded:
            post_upload_processing(item['doc'])

        documents_payload = [
            {
                'document_id': item['doc'].id,
                'offer_document_id': item['offer_document'].id,
                'version_id': item['version'].id if item['version'] else None,
                'document_type': item['document_type'],
                'display_name': item['display_name'],
                'template_slug': item['doc'].template_slug,
                'extraction_status': item['doc'].extraction_status,
                'filename': item['doc'].signed_original_filename,
            }
            for item in uploaded
        ]
        first = documents_payload[0] if documents_payload else {}
        return jsonify({
            'success': True,
            'message': f'{len(documents_payload)} offer document{"s" if len(documents_payload) != 1 else ""} uploaded. Extraction has started.',
            'offer_id': offer.id,
            'version_id': first.get('version_id'),
            'document_id': first.get('document_id'),
            'documents': documents_payload,
        }), 201
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@transactions_bp.route('/<int:id>/offers/<int:offer_id>', methods=['POST', 'PATCH'])
@login_required
@transactions_required
def update_seller_offer(id, offer_id):
    """Update offer summary fields and the current editable terms."""
    transaction = _get_seller_transaction(id)
    if transaction is None:
        return jsonify({'success': False, 'error': 'Offers are only available for seller transactions'}), 400

    offer = SellerOffer.query.filter_by(
        id=offer_id,
        transaction_id=transaction.id,
        organization_id=current_user.organization_id,
    ).first_or_404()

    data = request.get_json(silent=True) or request.form
    terms = _normalize_terms(data)

    editable_fields = (
        'buyer_names',
        'buyer_agent_name',
        'buyer_agent_email',
        'buyer_agent_phone',
        'buyer_agent_brokerage',
    )
    for field in editable_fields:
        if field in data:
            setattr(offer, field, data.get(field) or None)

    if data.get('received_at'):
        offer.received_at = _parse_datetime(data.get('received_at')) or offer.received_at

    if 'response_deadline_at' in data:
        offer.response_deadline_at = _parse_datetime(data.get('response_deadline_at'))
        offer.response_deadline_source = 'manual' if offer.response_deadline_at else None

    status = data.get('status')
    if status:
        allowed_statuses = {
            'new',
            'reviewing',
            'needs_review',
            'countered',
            'accepted_primary',
            'accepted_backup',
            'declined',
            'withdrawn',
            'expired',
        }
        if status not in allowed_statuses:
            return jsonify({'success': False, 'error': 'Invalid offer status'}), 400
        offer.status = status

    try:
        version = None
        if offer.current_version_id:
            version = SellerOfferVersion.query.filter_by(
                id=offer.current_version_id,
                offer_id=offer.id,
                organization_id=current_user.organization_id,
            ).first()

        if version:
            merged_terms = dict(version.terms_data or {})
            merged_terms.update(terms)
            version.terms_data = merged_terms
            version.status = 'reviewed'
        else:
            merged_terms = terms
            version = SellerOfferVersion(
                organization_id=current_user.organization_id,
                transaction_id=transaction.id,
                offer_id=offer.id,
                created_by_id=current_user.id,
                version_number=offer.versions.count() + 1,
                direction='buyer_offer',
                status='reviewed',
                submitted_at=offer.received_at,
                terms_data=merged_terms,
                extraction_reviewed_at=datetime.utcnow(),
                extraction_reviewed_by_id=current_user.id,
            )
            db.session.add(version)
            db.session.flush()
            offer.current_version_id = version.id

        apply_offer_terms(offer, merged_terms)
        create_offer_activity(
            offer,
            'offer_updated',
            'Offer details updated',
            actor_id=current_user.id,
            version_id=version.id,
        )
        db.session.commit()
        return jsonify({'success': True, 'offer': _offer_payload(offer)})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@transactions_bp.route('/<int:id>/offers/<int:offer_id>/expire', methods=['POST'])
@login_required
@transactions_required
def expire_seller_offer(id, offer_id):
    """Expire an offer if the response deadline has passed."""
    transaction = _get_seller_transaction(id)
    if transaction is None:
        return jsonify({'success': False, 'error': 'Offers are only available for seller transactions'}), 400

    offer = SellerOffer.query.filter_by(
        id=offer_id,
        transaction_id=transaction.id,
        organization_id=current_user.organization_id,
    ).first_or_404()

    try:
        expired = expire_offer_if_needed(offer, actor_id=current_user.id)
        db.session.commit()
        return jsonify({'success': True, 'expired': expired, 'offer': _offer_payload(offer)})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@transactions_bp.route('/<int:id>/offers/<int:offer_id>/accept', methods=['POST'])
@login_required
@transactions_required
def accept_seller_offer(id, offer_id):
    """Accept an offer as primary or backup."""
    transaction = _get_seller_transaction(id)
    if transaction is None:
        return jsonify({'success': False, 'error': 'Offers are only available for seller transactions'}), 400

    offer = SellerOffer.query.filter_by(
        id=offer_id,
        transaction_id=transaction.id,
        organization_id=current_user.organization_id,
    ).first_or_404()
    data = request.get_json(silent=True) or request.form
    position = data.get('position') or 'primary'
    if position not in ('primary', 'backup'):
        return jsonify({'success': False, 'error': 'Invalid acceptance position'}), 400

    if position == 'backup':
        active_primary = SellerAcceptedContract.query.filter_by(
            transaction_id=transaction.id,
            organization_id=current_user.organization_id,
            position='primary',
            status='active',
        ).first()
        if not active_primary:
            return jsonify({'success': False, 'error': 'Accept a primary contract before accepting a backup'}), 400

    version = None
    if offer.current_version_id:
        version = SellerOfferVersion.query.filter_by(
            id=offer.current_version_id,
            offer_id=offer.id,
            organization_id=current_user.organization_id,
        ).first()
    terms = _merged_acceptance_terms(offer, version)
    addenda = _as_dict(terms.get('addenda'))
    supporting_documents = _as_dict(terms.get('supporting_documents'))
    financing_addendum = _as_dict(addenda.get('third_party_financing_addendum'))
    seller_disclosure = _as_dict(supporting_documents.get('sellers_disclosure'))
    hoa_addendum = _as_dict(addenda.get('hoa_addendum'))
    seller_disclosure_delivered = (
        seller_disclosure.get('buyer_received_date')
        or seller_disclosure.get('seller_signed_date')
    )
    financing_deadline = (
        terms.get('financing_approval_deadline')
        or financing_addendum.get('financing_approval_deadline')
        or financing_addendum.get('buyer_approval_deadline')
    )
    if not financing_deadline and financing_addendum.get('buyer_approval_days'):
        effective_date = _parse_date(data.get('effective_date') or terms.get('effective_date'))
        try:
            approval_days = int(str(financing_addendum.get('buyer_approval_days')).strip())
        except (TypeError, ValueError):
            approval_days = None
        if effective_date and approval_days is not None:
            from datetime import timedelta
            financing_deadline = effective_date + timedelta(days=approval_days)

    accepted_contract = SellerAcceptedContract(
        organization_id=current_user.organization_id,
        transaction_id=transaction.id,
        offer_id=offer.id,
        accepted_version_id=version.id if version else None,
        created_by_id=current_user.id,
        position=position,
        backup_position=data.get('backup_position') if position == 'backup' else None,
        backup_addendum_document_id=data.get('backup_addendum_document_id') or None,
        accepted_price=offer.offer_price or terms.get('offer_price') or terms.get('sales_price'),
        effective_date=_parse_date(data.get('effective_date') or terms.get('effective_date')),
        effective_at=_parse_datetime(data.get('effective_at') or terms.get('effective_at')),
        closing_date=offer.proposed_close_date or _parse_date(terms.get('proposed_close_date') or terms.get('closing_date')),
        option_period_days=offer.option_period_days or _parse_int(terms.get('option_period_days')),
        financing_approval_deadline=_parse_date(financing_deadline),
        title_company=terms.get('title_company'),
        escrow_officer=terms.get('escrow_officer'),
        survey_choice=terms.get('survey_choice'),
        hoa_applicable=terms.get('hoa_applicable') if terms.get('hoa_applicable') is not None else bool(hoa_addendum),
        seller_disclosure_required=terms.get('seller_disclosure_required') if terms.get('seller_disclosure_required') is not None else bool(seller_disclosure),
        seller_disclosure_delivered_at=_parse_datetime(seller_disclosure_delivered),
        lead_based_paint_required=terms.get('lead_based_paint_required') if terms.get('lead_based_paint_required') is not None else seller_disclosure.get('built_before_1978'),
        frozen_terms=terms,
        addenda_data=terms.get('addenda') or {},
        extra_data=_contract_extra_data_from_offer(offer, terms),
    )

    try:
        db.session.add(accepted_contract)
        db.session.flush()

        if position == 'primary':
            offer.status = 'accepted_primary'
            transaction.status = 'under_contract'
            create_contract_milestones(accepted_contract)
            event_type = 'accepted_primary'
            label = 'Offer accepted as primary contract'
        else:
            offer.status = 'accepted_backup'
            offer.backup_position = accepted_contract.backup_position
            offer.backup_addendum_document_id = accepted_contract.backup_addendum_document_id
            event_type = 'accepted_backup'
            label = 'Offer accepted as backup contract'

        offer.accepted_version_id = version.id if version else None
        create_offer_activity(
            offer,
            event_type,
            label,
            actor_id=current_user.id,
            version_id=version.id if version else None,
            event_data={'accepted_contract_id': accepted_contract.id, 'position': position},
        )
        db.session.commit()
        return jsonify({
            'success': True,
            'offer': _offer_payload(offer),
            'accepted_contract_id': accepted_contract.id,
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500
