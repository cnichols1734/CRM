"""AgentDesk deal-file APIs: listing, offers, contract, checklist, notes, parties.

Attaches to the agent_api blueprint. Live checklist is TransactionRequirement
(deadline packs). SellerContractMilestone stays on agent_api /milestones.
"""
from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal, InvalidOperation

from flask import jsonify
from sqlalchemy.orm.attributes import flag_modified

from models import (
    SellerListingProfile,
    SellerOffer,
    SellerOfferVersion,
    Task,
    TransactionRequirement,
    TransactionParticipant,
    db,
)
from routes.agent_api import (
    _json_body,
    _json_error,
    _load_tx,
    _parse_date,
    _parse_datetime,
    _serialize_transaction,
    agent_api_bp,
    agent_jwt_required,
    transactions_flag_required,
)
from services.controlling_contracts import (
    ControllingContractConflict,
    ControllingContractSeedError,
    create_baseline_from_accepted_offer,
    get_active_primary_contract,
)
from services.deadline_recompute import recompute_from_changes
from services.listing_prep_checklist import AUTO_KEYS
from services.offer_compare import OfferCompareService
from services.offer_side import opening_direction_for_side, side_for_transaction, supports_offers
from services.requirements_service import RequirementsService
from services.seller_workflow import (
    ACTIVE_OFFER_STATUSES,
    apply_offer_terms,
    create_offer_activity,
)
from services.transaction_auth import CAP_EDIT, CAP_VIEW

logger = logging.getLogger(__name__)

_registered = False

_LISTING_OVERRIDE_KEYS = frozenset({
    'list_price',
    'go_live_date',
    'listing_start_date',
    'listing_end_date',
    'total_commission',
    'listing_side_commission',
    'buyer_commission',
})
_PROFILE_LISTING_KEYS = frozenset({'list_price', 'go_live_date', 'mls_number', 'occupancy'})
_OFFER_TERM_KEYS = frozenset({
    'offer_price',
    'sales_price',
    'financing_type',
    'earnest_money',
    'option_fee',
    'option_period_days',
    'option_days',
    'seller_concessions_amount',
    'concessions',
    'proposed_close_date',
    'closing_date',
    'response_deadline_at',
    'cash_down_payment',
    'financing_amount',
    'additional_earnest_money',
    'possession_type',
    'leaseback_days',
    'appraisal_contingency',
    'financing_contingency',
    'sale_of_other_property_contingency',
    'buyer_agent_commission_percent',
    'buyer_agent_commission_flat',
    'survey_furnished_by',
    'residential_service_contract',
    'title_policy_payer',
    'survey_payer',
})
_OFFER_PARTY_KEYS = (
    'buyer_names',
    'buyer_agent_name',
    'buyer_agent_email',
    'buyer_agent_phone',
    'buyer_agent_brokerage',
)
_OFFER_STATUSES = {
    'new',
    'reviewing',
    'needs_review',
    'countered',
    'declined',
    'withdrawn',
    'expired',
}
_ACCEPT_STATUSES = frozenset({'accepted_primary', 'accepted_backup'})
_OFFERS_UNSUPPORTED = 'Offers are only available for buyer and seller transactions.'
_ACCEPT_SELLER_ONLY = (
    'Accepting an offer as a contract is only available for seller transactions.'
)
_LISTING_ONLY = 'Listing edits are only available for seller listings.'
_COMPARE_SELLER_ONLY = 'Offer compare is only available for seller transactions.'
_NO_CONTRACT = 'Accept an offer or set dates first.'


def register(bp):
    """Idempotent attach."""
    global _registered
    if _registered:
        return
    _registered = True
    rules = (
        (
            '/transactions/<int:transaction_id>/listing',
            'deal_desk_patch_listing',
            patch_listing,
            ['PATCH'],
        ),
        (
            '/transactions/<int:transaction_id>/offers',
            'deal_desk_list_offers',
            list_offers,
            ['GET'],
        ),
        (
            '/transactions/<int:transaction_id>/offers',
            'deal_desk_create_offer',
            create_offer,
            ['POST'],
        ),
        (
            '/transactions/<int:transaction_id>/offers/compare',
            'deal_desk_compare_offers',
            compare_offers,
            ['GET'],
        ),
        (
            '/transactions/<int:transaction_id>/offers/<int:offer_id>',
            'deal_desk_patch_offer',
            patch_offer,
            ['PATCH'],
        ),
        (
            '/transactions/<int:transaction_id>/offers/<int:offer_id>/expire',
            'deal_desk_expire_offer',
            expire_offer,
            ['POST'],
        ),
        (
            '/transactions/<int:transaction_id>/offers/<int:offer_id>/accept',
            'deal_desk_accept_offer',
            accept_offer,
            ['POST'],
        ),
        (
            '/transactions/<int:transaction_id>/contract',
            'deal_desk_get_contract',
            get_contract,
            ['GET'],
        ),
        (
            '/transactions/<int:transaction_id>/contract',
            'deal_desk_patch_contract',
            patch_contract,
            ['PATCH'],
        ),
        (
            '/transactions/<int:transaction_id>/requirements',
            'deal_desk_list_requirements',
            list_requirements,
            ['GET'],
        ),
        (
            '/transactions/<int:transaction_id>/requirements/<int:requirement_id>/due-date',
            'deal_desk_requirement_due_date',
            set_requirement_due_date,
            ['POST'],
        ),
        (
            '/transactions/<int:transaction_id>/requirements/<int:requirement_id>/toggle',
            'deal_desk_toggle_requirement',
            toggle_requirement,
            ['POST'],
        ),
        (
            '/transactions/<int:transaction_id>/participants/<int:participant_id>',
            'deal_desk_delete_participant',
            delete_participant,
            ['DELETE'],
        ),
        (
            '/transactions/<int:transaction_id>/notes',
            'deal_desk_list_notes',
            list_notes,
            ['GET'],
        ),
        (
            '/transactions/<int:transaction_id>/notes',
            'deal_desk_add_note',
            add_note,
            ['POST'],
        ),
    )
    for rule, endpoint, view, methods in rules:
        bp.add_url_rule(rule, endpoint=endpoint, view_func=view, methods=methods)


def enrich_transaction_detail(tx, payload: dict) -> None:
    """Mutate payload in place. Called from _serialize_transaction(detail=True)."""
    payload['listing'] = serialize_listing(tx)
    payload['contract'] = serialize_contract(
        get_active_primary_contract(tx.id, tx.organization_id),
    )
    offers = (
        SellerOffer.query.filter_by(
            transaction_id=tx.id,
            organization_id=tx.organization_id,
        )
        .order_by(SellerOffer.received_at.desc())
        .all()
    )
    payload['offers'] = [serialize_offer(offer) for offer in offers]
    requirements = (
        TransactionRequirement.query.filter_by(
            transaction_id=tx.id,
            organization_id=tx.organization_id,
        )
        .order_by(TransactionRequirement.due_at.asc().nullslast())
        .all()
    )
    payload['requirements'] = [serialize_requirement(row) for row in requirements]
    tasks = (
        Task.query.filter_by(
            transaction_id=tx.id,
            organization_id=tx.organization_id,
        )
        .order_by(Task.due_date.asc())
        .all()
    )
    payload['tasks'] = [serialize_task(task) for task in tasks]
    payload['notes'] = list((tx.extra_data or {}).get('bob_notes') or [])


def _money_json(value):
    if value in (None, ''):
        return None
    if isinstance(value, Decimal):
        amount = value
    else:
        try:
            amount = Decimal(str(value).replace(',', '').replace('$', '').strip())
        except (InvalidOperation, AttributeError, ValueError):
            return None
    if amount == amount.to_integral_value():
        return int(amount)
    return float(amount)


def _as_date_str(value):
    if value in (None, ''):
        return None
    if hasattr(value, 'isoformat'):
        return value.isoformat()[:10]
    return str(value)[:10]


def _decimal(value):
    if value in (None, ''):
        return None
    try:
        return Decimal(str(value).replace(',', '').replace('$', '').strip())
    except (InvalidOperation, AttributeError, ValueError) as exc:
        raise ValueError('Enter a valid amount.') from exc


def _truthy(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in ('1', 'true', 'yes', 'on')
    return False


def _tx_type_name(tx):
    tx_type = getattr(tx, 'transaction_type', None)
    return getattr(tx_type, 'name', None)


def _listing_editable(tx):
    name = _tx_type_name(tx)
    if name == 'seller':
        return True
    if name == 'landlord' and tx.seller_listing_profile is not None:
        return True
    return False


def _ensure_listing_profile(tx, user):
    profile = tx.seller_listing_profile
    if profile is not None:
        return profile
    profile = SellerListingProfile(
        organization_id=user.organization_id,
        transaction_id=tx.id,
        created_by_id=user.id,
    )
    db.session.add(profile)
    db.session.flush()
    return profile


def _extra(tx):
    return dict(tx.extra_data or {})


def serialize_listing(tx):
    extra = tx.extra_data or {}
    overrides = extra.get('listing_info_overrides') or {}
    profile = getattr(tx, 'seller_listing_profile', None)
    list_price = None
    if profile is not None and profile.current_list_price is not None:
        list_price = _money_json(profile.current_list_price)
    elif overrides.get('list_price') not in (None, ''):
        list_price = _money_json(overrides.get('list_price'))
    go_live = None
    if profile is not None and profile.go_live_date:
        go_live = profile.go_live_date.isoformat()
    elif overrides.get('go_live_date'):
        go_live = _as_date_str(overrides.get('go_live_date'))
    listing = {
        'list_price': list_price,
        'go_live_date': go_live,
        'listing_start_date': _as_date_str(overrides.get('listing_start_date')),
        'listing_end_date': _as_date_str(overrides.get('listing_end_date')),
        'mls_listing_url': tx.mls_listing_url,
        'mls_number': profile.mls_number if profile is not None else None,
        'lockbox_combo': extra.get('lockbox_combo'),
    }
    for key in ('total_commission', 'listing_side_commission', 'buyer_commission'):
        if overrides.get(key) not in (None, ''):
            listing[key] = overrides[key]
    occupancy = profile.occupancy_status if profile is not None else None
    if occupancy:
        listing['occupancy'] = occupancy
    return listing


def serialize_offer(offer):
    extra = offer.extra_data or {}
    return {
        'id': offer.id,
        'status': offer.status,
        'offer_price': _money_json(offer.offer_price),
        'financing_type': offer.financing_type,
        'earnest_money': _money_json(offer.earnest_money),
        'option_fee': _money_json(offer.option_fee),
        'option_days': offer.option_period_days,
        'concessions': _money_json(offer.seller_concessions_amount),
        'proposed_close_date': (
            offer.proposed_close_date.isoformat() if offer.proposed_close_date else None
        ),
        'response_deadline_at': (
            offer.response_deadline_at.isoformat() if offer.response_deadline_at else None
        ),
        'buyer_name': offer.buyer_names,
        'notes': extra.get('notes'),
        'created_at': offer.created_at.isoformat() if offer.created_at else None,
    }


def serialize_contract(contract):
    if contract is None:
        return None
    return {
        'id': contract.id,
        'status': contract.status,
        'position': contract.position,
        'accepted_price': _money_json(contract.accepted_price),
        'effective_date': (
            contract.effective_date.isoformat() if contract.effective_date else None
        ),
        'closing_date': (
            contract.closing_date.isoformat() if contract.closing_date else None
        ),
        'option_period_days': contract.option_period_days,
        'financing_type': contract.financing_type,
        'financing_approval_deadline': (
            contract.financing_approval_deadline.isoformat()
            if contract.financing_approval_deadline else None
        ),
    }


def serialize_requirement(req):
    return {
        'id': req.id,
        'title': req.title,
        'name': req.title,
        'requirement_key': req.requirement_key,
        'group': req.phase_key,
        'due_at': req.due_at.isoformat() if req.due_at else None,
        'work_status': req.work_status,
        'timing_state': req.timing_state,
        'risk_level': req.risk_level,
        'due_at_manual_override': bool(req.due_at_manual_override),
        'is_auto': req.requirement_key in AUTO_KEYS,
    }


def serialize_task(task):
    return {
        'id': task.id,
        'subject': task.subject,
        'status': task.status,
        'priority': task.priority,
        'due_date': task.due_date.isoformat() if task.due_date else None,
        'contact_id': task.contact_id,
    }


def _detail_transaction(tx):
    return _serialize_transaction(tx, detail=True)


def _load_offer(tx, offer_id):
    offer = SellerOffer.query.filter_by(
        id=offer_id,
        transaction_id=tx.id,
        organization_id=tx.organization_id,
    ).first()
    if offer is None:
        return None, _json_error('Offer not found.', 404)
    return offer, None


def _load_requirement(tx, requirement_id):
    req = TransactionRequirement.query.filter_by(
        id=requirement_id,
        transaction_id=tx.id,
        organization_id=tx.organization_id,
    ).first()
    if req is None:
        return None, _json_error('Requirement not found.', 404)
    return req, None


def _incoming_offer_terms(data):
    terms = {}
    nested = data.get('terms_data') or data.get('terms')
    if isinstance(nested, dict):
        terms.update(nested)
    for key in _OFFER_TERM_KEYS:
        if key in data:
            terms[key] = data[key]
    if 'option_days' in terms and terms.get('option_period_days') in (None, ''):
        terms['option_period_days'] = terms.get('option_days')
    if 'concessions' in terms and terms.get('seller_concessions_amount') in (None, ''):
        terms['seller_concessions_amount'] = terms.get('concessions')
    if isinstance(terms.get('response_deadline_at'), str):
        try:
            terms['response_deadline_at'] = _parse_datetime(terms['response_deadline_at'])
        except ValueError:
            terms['response_deadline_at'] = None
    return terms


def _apply_offer_party_fields(offer, data):
    if 'buyer_name' in data or 'buyer_names' in data:
        offer.buyer_names = (
            data.get('buyer_names') or data.get('buyer_name') or None
        )
        if isinstance(offer.buyer_names, str):
            offer.buyer_names = offer.buyer_names.strip() or None
    for field in _OFFER_PARTY_KEYS:
        if field == 'buyer_names':
            continue
        if field in data:
            value = data.get(field)
            setattr(offer, field, (value or '').strip() or None if isinstance(value, str) else value)
    note = data.get('notes')
    if note is not None:
        extra = dict(offer.extra_data or {})
        extra['notes'] = (str(note).strip() or None)
        offer.extra_data = extra
        flag_modified(offer, 'extra_data')


def _current_offer_version(offer):
    if not offer.current_version_id:
        return None
    return SellerOfferVersion.query.filter_by(
        id=offer.current_version_id,
        offer_id=offer.id,
        organization_id=offer.organization_id,
    ).first()


def _merged_offer_terms(offer, data):
    merged = {}
    version = _current_offer_version(offer)
    if version is not None:
        merged.update(version.terms_data or {})
    if isinstance(offer.terms_summary, dict):
        merged.update(offer.terms_summary)
    merged.update(_incoming_offer_terms(data))
    return merged, version


@agent_jwt_required
@transactions_flag_required
def patch_listing(user, transaction_id):
    tx, error = _load_tx(user, transaction_id, CAP_EDIT)
    if error:
        return error
    if not _listing_editable(tx):
        return _json_error(_LISTING_ONLY, 400)

    data = _json_body()
    try:
        extra = _extra(tx)
        overrides = dict(extra.get('listing_info_overrides') or {})
        needs_profile = any(key in data for key in _PROFILE_LISTING_KEYS)
        profile = tx.seller_listing_profile
        if needs_profile:
            profile = _ensure_listing_profile(tx, user)

        for field in _LISTING_OVERRIDE_KEYS:
            if field not in data:
                continue
            value = data.get(field)
            if value in (None, ''):
                overrides.pop(field, None)
                continue
            if field in ('go_live_date', 'listing_start_date', 'listing_end_date'):
                parsed = _parse_date(value)
                overrides[field] = parsed.isoformat() if parsed else None
                if overrides[field] is None:
                    overrides.pop(field, None)
            else:
                overrides[field] = str(value).strip()

        if overrides:
            extra['listing_info_overrides'] = overrides
        else:
            extra.pop('listing_info_overrides', None)

        if 'list_price' in data and profile is not None:
            profile.current_list_price = _decimal(data.get('list_price'))
        if 'go_live_date' in data and profile is not None:
            profile.go_live_date = _parse_date(data.get('go_live_date'))
        if 'mls_number' in data and profile is not None:
            profile.mls_number = (str(data.get('mls_number') or '').strip() or None)
        if 'occupancy' in data and profile is not None:
            profile.occupancy_status = (str(data.get('occupancy') or '').strip() or None)
        if 'mls_listing_url' in data:
            tx.mls_listing_url = (str(data.get('mls_listing_url') or '').strip() or None)
        if 'lockbox_combo' in data:
            combo = str(data.get('lockbox_combo') or '').strip()
            if combo:
                extra['lockbox_combo'] = combo
            else:
                extra.pop('lockbox_combo', None)

        tx.extra_data = extra
        flag_modified(tx, 'extra_data')
        db.session.commit()
    except ValueError as exc:
        db.session.rollback()
        return _json_error(str(exc), 400)

    return jsonify({'transaction': _detail_transaction(tx)})


@agent_jwt_required
@transactions_flag_required
def list_offers(user, transaction_id):
    tx, error = _load_tx(user, transaction_id, CAP_VIEW)
    if error:
        return error
    if not supports_offers(tx):
        return _json_error(_OFFERS_UNSUPPORTED, 400)
    offers = (
        tx.seller_offers.order_by(SellerOffer.received_at.desc()).all()
    )
    return jsonify({'offers': [serialize_offer(offer) for offer in offers]})


@agent_jwt_required
@transactions_flag_required
def create_offer(user, transaction_id):
    tx, error = _load_tx(user, transaction_id, CAP_EDIT)
    if error:
        return error
    if not supports_offers(tx):
        return _json_error(_OFFERS_UNSUPPORTED, 400)

    data = _json_body()
    terms = _incoming_offer_terms(data)
    try:
        response_deadline_at = data.get('response_deadline_at')
        if response_deadline_at:
            response_deadline_at = _parse_datetime(response_deadline_at)
        else:
            response_deadline_at = terms.get('response_deadline_at')
    except ValueError as exc:
        return _json_error(str(exc), 400)

    side = side_for_transaction(tx)
    offer = SellerOffer(
        organization_id=user.organization_id,
        transaction_id=tx.id,
        created_by_id=user.id,
        source_showing_id=data.get('source_showing_id') or None,
        received_at=datetime.utcnow(),
        creation_source=data.get('creation_source') or 'manual_entry',
        status='new',
        response_deadline_at=response_deadline_at,
        response_deadline_source='manual' if response_deadline_at else None,
    )
    _apply_offer_party_fields(offer, data)
    apply_offer_terms(offer, terms)
    version = SellerOfferVersion(
        organization_id=user.organization_id,
        transaction_id=tx.id,
        offer=offer,
        created_by_id=user.id,
        version_number=1,
        direction=opening_direction_for_side(side),
        status='reviewed',
        submitted_at=offer.received_at,
        terms_data=terms,
        extraction_reviewed_at=datetime.utcnow(),
        extraction_reviewed_by_id=user.id,
    )
    try:
        db.session.add(offer)
        db.session.add(version)
        db.session.flush()
        offer.current_version_id = version.id
        create_offer_activity(
            offer,
            'offer_created',
            'Offer logged manually',
            actor_id=user.id,
            version_id=version.id,
        )
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception('AgentDesk create offer failed tx=%s', transaction_id)
        return _json_error('Could not log this offer.', 500)
    return jsonify({'offer': serialize_offer(offer)}), 201


@agent_jwt_required
@transactions_flag_required
def patch_offer(user, transaction_id, offer_id):
    tx, error = _load_tx(user, transaction_id, CAP_EDIT)
    if error:
        return error
    if not supports_offers(tx):
        return _json_error(_OFFERS_UNSUPPORTED, 400)
    offer, error = _load_offer(tx, offer_id)
    if error:
        return error

    data = _json_body()
    status = data.get('status')
    if status in _ACCEPT_STATUSES:
        return _json_error(
            'Accept the offer through POST /transactions/<id>/offers/<offer_id>/accept.',
            400,
        )
    if status and status not in _OFFER_STATUSES:
        return _json_error('Invalid offer status.', 400)

    try:
        if 'response_deadline_at' in data:
            offer.response_deadline_at = _parse_datetime(data.get('response_deadline_at'))
            offer.response_deadline_source = (
                'manual' if offer.response_deadline_at else None
            )
        if data.get('received_at'):
            offer.received_at = _parse_datetime(data.get('received_at')) or offer.received_at
    except ValueError as exc:
        return _json_error(str(exc), 400)

    _apply_offer_party_fields(offer, data)
    if status:
        offer.status = status

    merged_terms, version = _merged_offer_terms(offer, data)
    side = side_for_transaction(tx)
    try:
        if version is not None:
            version.terms_data = merged_terms
            version.status = 'reviewed'
        else:
            version = SellerOfferVersion(
                organization_id=user.organization_id,
                transaction_id=tx.id,
                offer_id=offer.id,
                created_by_id=user.id,
                version_number=offer.versions.count() + 1,
                direction=opening_direction_for_side(side),
                status='reviewed',
                submitted_at=offer.received_at,
                terms_data=merged_terms,
                extraction_reviewed_at=datetime.utcnow(),
                extraction_reviewed_by_id=user.id,
            )
            db.session.add(version)
            db.session.flush()
            offer.current_version_id = version.id
        apply_offer_terms(offer, merged_terms)
        create_offer_activity(
            offer,
            'offer_updated',
            'Offer details updated',
            actor_id=user.id,
            version_id=version.id,
        )
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception('AgentDesk patch offer failed offer=%s', offer_id)
        return _json_error('Could not update this offer.', 500)
    return jsonify({'offer': serialize_offer(offer)})


@agent_jwt_required
@transactions_flag_required
def expire_offer(user, transaction_id, offer_id):
    tx, error = _load_tx(user, transaction_id, CAP_EDIT)
    if error:
        return error
    if not supports_offers(tx):
        return _json_error(_OFFERS_UNSUPPORTED, 400)
    offer, error = _load_offer(tx, offer_id)
    if error:
        return error
    if offer.status not in ACTIVE_OFFER_STATUSES and offer.status != 'expired':
        return _json_error(
            f'Offer {offer.id} is {offer.status} and cannot be expired.',
            400,
        )
    if offer.status != 'expired':
        now = datetime.utcnow()
        offer.status = 'expired'
        offer.expired_at = now
        offer.next_action = None
        offer.next_deadline_at = None
        create_offer_activity(offer, 'expired', 'Offer expired', actor_id=user.id)
        db.session.commit()
    return jsonify({'offer': serialize_offer(offer)})


@agent_jwt_required
@transactions_flag_required
def accept_offer(user, transaction_id, offer_id):
    """Accept as controlling contract via create_baseline_from_accepted_offer."""
    tx, error = _load_tx(user, transaction_id, CAP_EDIT)
    if error:
        return error
    if not supports_offers(tx):
        return _json_error(_OFFERS_UNSUPPORTED, 400)
    if side_for_transaction(tx) != 'seller':
        return _json_error(_ACCEPT_SELLER_ONLY, 400)
    offer, error = _load_offer(tx, offer_id)
    if error:
        return error

    data = _json_body()
    position = 'backup' if _truthy(data.get('as_backup')) else 'primary'
    version = _current_offer_version(offer)
    try:
        effective_date = (
            _parse_date(data.get('effective_date')) if data.get('effective_date') else None
        )
        effective_at = (
            _parse_datetime(data.get('effective_at')) if data.get('effective_at') else None
        )
    except ValueError as exc:
        return _json_error(str(exc), 400)

    try:
        accepted_contract = create_baseline_from_accepted_offer(
            transaction=tx,
            offer=offer,
            actor_id=user.id,
            position=position,
            version=version,
            effective_date=effective_date,
            effective_at=effective_at,
            backup_position=data.get('backup_position') if position == 'backup' else None,
            backup_addendum_document_id=(
                data.get('backup_addendum_document_id') or None
                if position == 'backup' else None
            ),
            seed_requirements=True,
        )
        db.session.commit()
    except ControllingContractConflict as exc:
        db.session.rollback()
        payload = {'error': str(exc), 'code': exc.code}
        if exc.existing_contract_id:
            payload['existing_contract_id'] = exc.existing_contract_id
        return jsonify(payload), exc.status
    except ControllingContractSeedError as exc:
        db.session.rollback()
        return jsonify({'error': str(exc), 'code': exc.code}), 500
    except ValueError as exc:
        db.session.rollback()
        return _json_error(str(exc), 400)
    except Exception:
        db.session.rollback()
        logger.exception(
            'AgentDesk accept offer failed offer=%s tx=%s', offer_id, transaction_id,
        )
        return _json_error('Could not accept this offer.', 500)

    return jsonify({
        'transaction': _detail_transaction(tx),
        'offer': serialize_offer(offer),
        'contract': serialize_contract(accepted_contract),
    })


@agent_jwt_required
@transactions_flag_required
def compare_offers(user, transaction_id):
    tx, error = _load_tx(user, transaction_id, CAP_VIEW)
    if error:
        return error
    if side_for_transaction(tx) != 'seller':
        return _json_error(_COMPARE_SELLER_ONLY, 400)
    result = OfferCompareService.compare_offers(tx, include_terminal=True)
    rows = []
    for column in result.get('offers') or []:
        offer = SellerOffer.query.filter_by(
            id=column.get('offer_id'),
            transaction_id=tx.id,
            organization_id=tx.organization_id,
        ).first()
        if offer is None:
            continue
        row = serialize_offer(offer)
        terms = column.get('terms') or {}
        row['net'] = _money_json(
            offer.net_to_seller_estimate or terms.get('net_to_seller_estimate'),
        )
        if not row['financing_type']:
            row['financing_type'] = terms.get('financing_type')
        if row['offer_price'] is None:
            row['offer_price'] = _money_json(terms.get('offer_price'))
        rows.append(row)
    return jsonify({'offers': rows})


@agent_jwt_required
@transactions_flag_required
def get_contract(user, transaction_id):
    tx, error = _load_tx(user, transaction_id, CAP_VIEW)
    if error:
        return error
    contract = get_active_primary_contract(tx.id, tx.organization_id)
    return jsonify({'contract': serialize_contract(contract)})


@agent_jwt_required
@transactions_flag_required
def patch_contract(user, transaction_id):
    tx, error = _load_tx(user, transaction_id, CAP_EDIT)
    if error:
        return error
    contract = get_active_primary_contract(tx.id, tx.organization_id)
    if contract is None:
        return _json_error(_NO_CONTRACT, 400)

    data = _json_body()
    changes = {}
    try:
        if 'accepted_price' in data:
            contract.accepted_price = _decimal(data.get('accepted_price'))
        if 'effective_date' in data:
            contract.effective_date = _parse_date(data.get('effective_date'))
            changes['effective_date'] = contract.effective_date
        if 'closing_date' in data:
            contract.closing_date = _parse_date(data.get('closing_date'))
            changes['closing_date'] = contract.closing_date
        option_raw = (
            data['option_period_days'] if 'option_period_days' in data
            else data.get('option_days') if 'option_days' in data
            else None
        )
        if 'option_period_days' in data or 'option_days' in data:
            if option_raw in (None, ''):
                contract.option_period_days = None
            else:
                contract.option_period_days = int(option_raw)
            changes['option_period_days'] = contract.option_period_days
        if 'financing_type' in data:
            contract.financing_type = (str(data.get('financing_type') or '').strip() or None)
        if 'financing_approval_deadline' in data:
            contract.financing_approval_deadline = _parse_date(
                data.get('financing_approval_deadline'),
            )
        if 'concessions' in data or 'seller_concessions_amount' in data:
            contract.seller_concessions_amount = _decimal(
                data.get('seller_concessions_amount', data.get('concessions')),
            )
        if 'buyer_agent_commission_percent' in data and hasattr(
            contract, 'buyer_agent_commission_percent',
        ):
            contract.buyer_agent_commission_percent = _decimal(
                data.get('buyer_agent_commission_percent'),
            )
        if 'buyer_agent_commission_flat' in data and hasattr(
            contract, 'buyer_agent_commission_flat',
        ):
            contract.buyer_agent_commission_flat = _decimal(
                data.get('buyer_agent_commission_flat'),
            )
    except (TypeError, ValueError) as exc:
        return _json_error(str(exc), 400)

    if changes:
        recompute_from_changes(
            tx, changes, actor_id=user.id, source='agent_desk',
        )
    db.session.commit()
    return jsonify({
        'contract': serialize_contract(contract),
        'transaction': _detail_transaction(tx),
    })


@agent_jwt_required
@transactions_flag_required
def list_requirements(user, transaction_id):
    tx, error = _load_tx(user, transaction_id, CAP_VIEW)
    if error:
        return error
    rows = (
        TransactionRequirement.query.filter_by(
            transaction_id=tx.id,
            organization_id=user.organization_id,
        )
        .order_by(TransactionRequirement.due_at.asc().nullslast())
        .all()
    )
    return jsonify({'requirements': [serialize_requirement(row) for row in rows]})


@agent_jwt_required
@transactions_flag_required
def set_requirement_due_date(user, transaction_id, requirement_id):
    tx, error = _load_tx(user, transaction_id, CAP_EDIT)
    if error:
        return error
    req, error = _load_requirement(tx, requirement_id)
    if error:
        return error

    data = _json_body()
    if 'due_at' not in data and 'due_date' not in data:
        return _json_error('due_at is required.', 400)
    raw = data['due_at'] if 'due_at' in data else data.get('due_date')
    due_at = None
    if raw not in (None, ''):
        try:
            due_at = datetime.strptime(str(raw)[:10], '%Y-%m-%d')
        except ValueError:
            return _json_error('Use YYYY-MM-DD for dates.', 400)
    try:
        updated = RequirementsService.update_due_at(
            req.id, due_at, actor_id=user.id, manual=True,
        )
        db.session.commit()
    except ValueError as exc:
        db.session.rollback()
        return _json_error(str(exc), 400)
    except Exception:
        db.session.rollback()
        logger.exception('AgentDesk requirement due-date failed req=%s', requirement_id)
        return _json_error('Could not update the due date.', 500)
    return jsonify({'requirement': serialize_requirement(updated)})


@agent_jwt_required
@transactions_flag_required
def toggle_requirement(user, transaction_id, requirement_id):
    tx, error = _load_tx(user, transaction_id, CAP_EDIT)
    if error:
        return error
    req, error = _load_requirement(tx, requirement_id)
    if error:
        return error
    if req.requirement_key in AUTO_KEYS:
        return _json_error('This item checks itself when the work is on file.', 400)

    current = (req.work_status or 'pending').lower()
    new_status = 'pending' if current == 'completed' else 'completed'
    try:
        updated = RequirementsService.update_work_status(
            req.id, new_status, actor_id=user.id,
        )
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception('AgentDesk requirement toggle failed req=%s', requirement_id)
        return _json_error('Could not update this item.', 500)
    return jsonify({
        'requirement': serialize_requirement(updated),
        'work_status': updated.work_status,
        'done': updated.work_status == 'completed',
    })


@agent_jwt_required
@transactions_flag_required
def delete_participant(user, transaction_id, participant_id):
    tx, error = _load_tx(user, transaction_id, CAP_EDIT)
    if error:
        return error
    participant = TransactionParticipant.query.filter_by(
        id=participant_id,
        transaction_id=tx.id,
        organization_id=user.organization_id,
    ).first()
    if participant is None:
        return _json_error('Participant not found.', 404)
    db.session.delete(participant)
    db.session.commit()
    return jsonify({'ok': True})


def _bob_notes(tx):
    return list((tx.extra_data or {}).get('bob_notes') or [])


@agent_jwt_required
@transactions_flag_required
def list_notes(user, transaction_id):
    tx, error = _load_tx(user, transaction_id, CAP_VIEW)
    if error:
        return error
    return jsonify({'notes': _bob_notes(tx)})


@agent_jwt_required
@transactions_flag_required
def add_note(user, transaction_id):
    tx, error = _load_tx(user, transaction_id, CAP_EDIT)
    if error:
        return error
    data = _json_body()
    text = (data.get('text') or data.get('note') or data.get('body') or '').strip()
    if not text:
        return _json_error('Note text is required.', 400)
    text = text[:4000]
    extra = _extra(tx)
    notes = list(extra.get('bob_notes') or [])
    stamp = datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')
    notes.append({'at': stamp, 'user_id': user.id, 'text': text})
    extra['bob_notes'] = notes[-50:]
    tx.extra_data = extra
    flag_modified(tx, 'extra_data')
    db.session.commit()
    return jsonify({'notes': extra['bob_notes']}), 201


register(agent_api_bp)
