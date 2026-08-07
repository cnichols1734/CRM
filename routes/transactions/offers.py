"""Seller offer routes."""

import logging
from datetime import datetime

from flask import abort, jsonify, render_template, request, url_for
from flask_login import current_user, login_required

from models import (
    SellerOffer,
    SellerOfferDocument,
    SellerOfferVersion,
    TransactionDocument,
    Transaction,
    db,
)
from services.controlling_contracts import (
    ControllingContractConflict,
    ControllingContractSeedError,
    create_baseline_from_accepted_offer,
)
from services.offer_package_review import (
    build_offer_package_review,
    confirm_offer_package,
    offer_package_review_url,
)
from services.offer_side import (
    opening_direction_for_side,
    side_for_transaction,
    supports_offers,
)
from services.seller_workflow import (
    apply_offer_terms,
    create_offer_activity,
    expire_offer_if_needed,
    get_offer_document_type,
    infer_offer_document_type,
    infer_offer_document_type_from_pdf,
    offer_urgency,
)
from services.intake_service import post_upload_processing
from services.transaction_auth import CAP_EDIT, CAP_VIEW, get_transaction_for_user
from . import transactions_bp
from .decorators import transactions_required

logger = logging.getLogger(__name__)

_OFFERS_UNSUPPORTED_ERROR = 'Offers are only available for buyer and seller transactions'


def _can_manage_transaction(transaction):
    return (
        transaction.created_by_id == current_user.id
        or getattr(current_user, 'role', None) == 'admin'
        or getattr(current_user, 'org_role', None) in ('admin', 'owner')
    )


def _get_offer_transaction(id):
    """Load an org-scoped transaction that supports offer threads (buyer or seller)."""
    transaction = Transaction.query.filter_by(
        id=id,
        organization_id=current_user.organization_id,
    ).first_or_404()
    if not _can_manage_transaction(transaction):
        abort(403)
    if not supports_offers(transaction):
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


def _merge_extraction_status(current_status, candidate_status):
    priority = {
        'failed': 4,
        'processing': 3,
        'pending': 2,
        'complete': 1,
    }
    if not candidate_status:
        return current_status
    if not current_status:
        return candidate_status
    return (
        candidate_status
        if priority.get(candidate_status, 0) > priority.get(current_status, 0)
        else current_status
    )


def _offer_extraction_status(offer):
    status = None
    for offer_document in offer.offer_documents.all():
        document = offer_document.document
        if document:
            status = _merge_extraction_status(status, document.extraction_status)

    if offer.current_version_id:
        version = SellerOfferVersion.query.filter_by(
            id=offer.current_version_id,
            offer_id=offer.id,
            organization_id=offer.organization_id,
        ).first()
        if version and version.document:
            status = _merge_extraction_status(status, version.document.extraction_status)

    return status


def _pending_offer_extraction_documents(offer):
    pending = []
    for offer_document in offer.offer_documents.all():
        document = offer_document.document
        if document and document.extraction_status in ('pending', 'processing'):
            pending.append(offer_document.display_name or document.template_name or document.signed_original_filename)
    return pending


def _normalize_terms(data):
    terms = dict(data.get('terms_data') or data.get('terms') or {})
    for key in ('response_deadline_at',):
        if isinstance(terms.get(key), str):
            terms[key] = _parse_datetime(terms[key])
    return terms


def _offer_payload(offer):
    urgency = offer_urgency(offer)
    document_count = offer.offer_documents.count()
    version_count = offer.versions.count()
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
        'cash_down_payment': str(offer.cash_down_payment) if offer.cash_down_payment is not None else None,
        'financing_amount': str(offer.financing_amount) if offer.financing_amount is not None else None,
        'proposed_close_date': offer.proposed_close_date.isoformat() if offer.proposed_close_date else None,
        'option_period_days': offer.option_period_days,
        'earnest_money': str(offer.earnest_money) if offer.earnest_money is not None else None,
        'seller_concessions_amount': str(offer.seller_concessions_amount) if offer.seller_concessions_amount is not None else None,
        'survey_furnished_by': offer.survey_furnished_by,
        'residential_service_contract': offer.residential_service_contract,
        'buyer_agent_commission_percent': str(offer.buyer_agent_commission_percent) if offer.buyer_agent_commission_percent is not None else None,
        'buyer_agent_commission_flat': str(offer.buyer_agent_commission_flat) if offer.buyer_agent_commission_flat is not None else None,
        'current_version_id': offer.current_version_id,
        'accepted_version_id': offer.accepted_version_id,
        'source_showing_id': offer.source_showing_id,
        'last_activity_label': offer.last_activity_label,
        'document_count': document_count,
        'version_count': version_count,
        'extraction_status': _offer_extraction_status(offer),
    }


@transactions_bp.route('/<int:id>/offers', methods=['GET'])
@login_required
@transactions_required
def list_seller_offers(id):
    """Return offer threads sorted by deadline urgency."""
    transaction = _get_offer_transaction(id)
    if transaction is None:
        return jsonify({'success': False, 'error': _OFFERS_UNSUPPORTED_ERROR}), 400

    offers = transaction.seller_offers.order_by(SellerOffer.received_at.desc()).all()
    offers.sort(key=lambda offer: (offer_urgency(offer)['rank'], offer.response_deadline_at or datetime.max))
    return jsonify({'success': True, 'offers': [_offer_payload(offer) for offer in offers]})


@transactions_bp.route('/<int:id>/offers', methods=['POST'])
@login_required
@transactions_required
def create_seller_offer(id):
    """Create a manual/verbal offer thread and first version."""
    transaction = _get_offer_transaction(id)
    if transaction is None:
        return jsonify({'success': False, 'error': _OFFERS_UNSUPPORTED_ERROR}), 400

    data = request.get_json(silent=True) or request.form
    terms = _normalize_terms(data)
    response_deadline_at = _parse_datetime(data.get('response_deadline_at')) or terms.get('response_deadline_at')
    side = side_for_transaction(transaction)
    opening_direction = opening_direction_for_side(side)

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
        direction=opening_direction,
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
    """Upload an offer PDF, creating or attaching to an offer thread."""
    transaction = _get_offer_transaction(id)
    if transaction is None:
        return jsonify({'success': False, 'error': _OFFERS_UNSUPPORTED_ERROR}), 400

    files = request.files.getlist('files') or request.files.getlist('file')
    files = [file for file in files if file and file.filename]
    if not files:
        return jsonify({'success': False, 'error': 'No file uploaded'}), 400

    document_types = (
        request.form.getlist('document_type')
        or request.form.getlist('document_types[]')
        or request.form.getlist('direction')
    )
    side = side_for_transaction(transaction)
    default_direction = opening_direction_for_side(side)

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
            document_type = infer_offer_document_type_from_pdf(
                file_data,
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
                    direction=doc_config['direction'] or default_direction,
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
        if uploaded and offer.status == 'new':
            offer.status = 'needs_review'
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
            'offer_review_url': offer_package_review_url(transaction.id, offer.id),
            'version_id': first.get('version_id'),
            'document_id': first.get('document_id'),
            'documents': documents_payload,
        }), 201
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@transactions_bp.route('/<int:id>/offers/<int:offer_id>/review', methods=['GET'])
@login_required
@transactions_required
def offer_package_review(id, offer_id):
    """One-page package review: terms + docs + findings, no per-doc filing."""
    tx, decision = get_transaction_for_user(id, capability=CAP_VIEW)
    if not tx:
        abort(403 if decision.reason != 'not_found' else 404)
    if not supports_offers(tx):
        abort(404)

    offer = SellerOffer.query.filter_by(
        id=offer_id,
        transaction_id=tx.id,
        organization_id=current_user.organization_id,
    ).first_or_404()

    payload = build_offer_package_review(transaction=tx, offer=offer)
    return render_template(
        'transactions/offer_package_review.html',
        transaction=tx,
        offer=offer,
        review=payload,
    )


@transactions_bp.route('/<int:id>/offers/<int:offer_id>/review/live', methods=['GET'])
@login_required
@transactions_required
def offer_package_review_live(id, offer_id):
    """JSON poll while BOB finishes extracting linked package docs."""
    tx, decision = get_transaction_for_user(id, capability=CAP_VIEW)
    if not tx:
        return jsonify({'success': False, 'error': 'Not found'}), 404
    offer = SellerOffer.query.filter_by(
        id=offer_id,
        transaction_id=tx.id,
        organization_id=current_user.organization_id,
    ).first_or_404()
    return jsonify({
        'success': True,
        'review': build_offer_package_review(transaction=tx, offer=offer),
    })


@transactions_bp.route('/<int:id>/offers/<int:offer_id>/review/confirm', methods=['POST'])
@login_required
@transactions_required
def confirm_offer_package_review(id, offer_id):
    """Confirm (or draft-save) the whole offer package in one shot."""
    tx, decision = get_transaction_for_user(id, capability=CAP_EDIT)
    if not tx:
        return jsonify({'success': False, 'error': 'Not found'}), 404
    if not supports_offers(tx):
        return jsonify({'success': False, 'error': _OFFERS_UNSUPPORTED_ERROR}), 400

    offer = SellerOffer.query.filter_by(
        id=offer_id,
        transaction_id=tx.id,
        organization_id=current_user.organization_id,
    ).first_or_404()

    data = request.get_json(silent=True) or request.form or {}
    draft = str(data.get('draft') or '').lower() in ('1', 'true', 'yes')
    try:
        confirm_offer_package(
            offer=offer,
            actor_id=current_user.id,
            terms_dict=dict(data),
            draft=draft,
        )
        db.session.commit()
        return jsonify({
            'success': True,
            'draft': draft,
            'offer_id': offer.id,
            'status': offer.status,
            'redirect_url': url_for('transactions.view_transaction', id=tx.id) + '#offers',
            'review': build_offer_package_review(transaction=tx, offer=offer),
        })
    except Exception as exc:
        db.session.rollback()
        logger.exception('confirm_offer_package failed offer=%s', offer_id)
        return jsonify({'success': False, 'error': str(exc)}), 500


@transactions_bp.route('/<int:id>/offers/<int:offer_id>', methods=['POST', 'PATCH'])
@login_required
@transactions_required
def update_seller_offer(id, offer_id):
    """Update offer summary fields and the current editable terms."""
    transaction = _get_offer_transaction(id)
    if transaction is None:
        return jsonify({'success': False, 'error': _OFFERS_UNSUPPORTED_ERROR}), 400

    offer = SellerOffer.query.filter_by(
        id=offer_id,
        transaction_id=transaction.id,
        organization_id=current_user.organization_id,
    ).first_or_404()

    data = request.get_json(silent=True) or request.form
    terms = _normalize_terms(data)
    side = side_for_transaction(transaction)
    opening_direction = opening_direction_for_side(side)

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
                direction=opening_direction,
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
    transaction = _get_offer_transaction(id)
    if transaction is None:
        return jsonify({'success': False, 'error': _OFFERS_UNSUPPORTED_ERROR}), 400

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
    """Accept an offer as primary or backup.

    Seller-only: activates a controlling baseline via the side-neutral
    controlling-contract service. Buyer acceptance into a contract record is a
    separate workflow and is not supported here.
    """
    transaction = _get_offer_transaction(id)
    if transaction is None:
        return jsonify({'success': False, 'error': _OFFERS_UNSUPPORTED_ERROR}), 400
    if side_for_transaction(transaction) != 'seller':
        return jsonify({
            'success': False,
            'error': 'Accepting an offer as a contract is only available for seller transactions',
        }), 400

    offer = SellerOffer.query.filter_by(
        id=offer_id,
        transaction_id=transaction.id,
        organization_id=current_user.organization_id,
    ).first_or_404()
    data = request.get_json(silent=True) or request.form
    position = data.get('position') or 'primary'
    if position not in ('primary', 'backup'):
        return jsonify({'success': False, 'error': 'Invalid acceptance position'}), 400

    pending_extractions = _pending_offer_extraction_documents(offer)
    if pending_extractions:
        return jsonify({
            'success': False,
            'error': 'AI extraction is still running for this offer. Wait for extraction to finish before accepting it as a contract.',
            'pending_documents': pending_extractions,
        }), 409

    version = None
    if offer.current_version_id:
        version = SellerOfferVersion.query.filter_by(
            id=offer.current_version_id,
            offer_id=offer.id,
            organization_id=current_user.organization_id,
        ).first()

    try:
        accepted_contract = create_baseline_from_accepted_offer(
            transaction=transaction,
            offer=offer,
            actor_id=current_user.id,
            position=position,
            version=version,
            effective_date=_parse_date(data.get('effective_date')),
            effective_at=_parse_datetime(data.get('effective_at')),
            backup_position=data.get('backup_position') if position == 'backup' else None,
            backup_addendum_document_id=(
                data.get('backup_addendum_document_id') or None
                if position == 'backup'
                else None
            ),
        )
        db.session.commit()
        return jsonify({
            'success': True,
            'offer': _offer_payload(offer),
            'accepted_contract_id': accepted_contract.id,
        })
    except ControllingContractConflict as e:
        db.session.rollback()
        return jsonify({
            'success': False,
            'error': str(e),
            'code': e.code,
            'existing_contract_id': e.existing_contract_id,
        }), e.status
    except ControllingContractSeedError as e:
        db.session.rollback()
        return jsonify({
            'success': False,
            'error': str(e),
            'code': e.code,
        }), 500
    except ValueError as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception:
        db.session.rollback()
        logger.exception(
            'Failed to accept offer %s on transaction %s',
            offer_id, id,
        )
        return jsonify({
            'success': False,
            'error': 'Could not accept this offer. Try again or contact support.',
        }), 500
