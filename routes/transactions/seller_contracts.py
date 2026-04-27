"""Seller accepted contract routes."""

from datetime import datetime
from decimal import Decimal, InvalidOperation

from flask import abort, jsonify, request
from flask_login import current_user, login_required

from models import SellerAcceptedContract, SellerContractMilestone, Transaction, db
from services.seller_workflow import (
    close_contract,
    create_contract_milestones,
    derive_financing_approval_deadline,
    promote_backup_contract,
    terminate_contract,
)
from . import transactions_bp
from .decorators import transactions_required


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
    for fmt in ('%Y-%m-%dT%H:%M', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d'):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def _parse_date(value):
    parsed = _parse_datetime(value)
    return parsed.date() if parsed else None


def _decimal(value):
    if value in (None, ''):
        return None
    try:
        return Decimal(str(value).replace('$', '').replace(',', '').strip())
    except (InvalidOperation, AttributeError):
        return None


MILESTONE_STATUSES = {
    'not_started',
    'waiting',
    'due_soon',
    'overdue',
    'completed',
    'not_applicable',
}


def _milestone_payload(milestone):
    return {
        'id': milestone.id,
        'title': milestone.title,
        'milestone_key': milestone.milestone_key,
        'due_at': milestone.due_at.isoformat() if milestone.due_at else None,
        'status': milestone.status,
        'responsible_party': milestone.responsible_party,
        'source': milestone.source,
        'notes': milestone.notes,
        'completed_at': milestone.completed_at.isoformat() if milestone.completed_at else None,
    }


def _get_contract_for_update(transaction, contract_id):
    return SellerAcceptedContract.query.filter_by(
        id=contract_id,
        transaction_id=transaction.id,
        organization_id=current_user.organization_id,
    ).first_or_404()


def _json_safe_value(value):
    if value is None:
        return None
    if hasattr(value, 'isoformat'):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return value


def _sync_contract_frozen_terms(contract):
    from sqlalchemy.orm.attributes import flag_modified

    terms = dict(contract.frozen_terms or {})
    for field in (
        'accepted_price',
        'effective_date',
        'closing_date',
        'option_period_days',
        'financing_approval_deadline',
        'financing_type',
        'cash_down_payment',
        'financing_amount',
        'seller_concessions_amount',
        'survey_choice',
        'survey_furnished_by',
        'residential_service_contract',
        'buyer_agent_commission_percent',
        'buyer_agent_commission_flat',
    ):
        terms[field] = _json_safe_value(getattr(contract, field))
    contract.frozen_terms = terms
    flag_modified(contract, 'frozen_terms')


def _apply_milestone_data(milestone, data):
    title = (data.get('title') or '').strip()
    if not title:
        raise ValueError('Milestone title is required')

    status = data.get('status') or milestone.status or 'not_started'
    if status not in MILESTONE_STATUSES:
        raise ValueError('Invalid milestone status')

    milestone.title = title
    milestone.due_at = _parse_datetime(data.get('due_at'))
    milestone.status = status
    milestone.responsible_party = data.get('responsible_party') or None
    milestone.notes = data.get('notes') or None
    milestone.source = 'manual'
    if status == 'completed':
        milestone.completed_at = milestone.completed_at or datetime.utcnow()
    else:
        milestone.completed_at = None
    return milestone


@transactions_bp.route('/<int:id>/seller/contracts/<int:contract_id>/details', methods=['POST', 'PATCH'])
@login_required
@transactions_required
def update_seller_contract_details(id, contract_id):
    """Update accepted contract terms that drive seller milestones."""
    transaction = _get_seller_transaction(id)
    if transaction is None:
        return jsonify({'success': False, 'error': 'Contracts are only available for seller transactions'}), 400

    contract = _get_contract_for_update(transaction, contract_id)
    data = request.get_json(silent=True) or request.form

    try:
        if 'accepted_price' in data:
            contract.accepted_price = _decimal(data.get('accepted_price'))
        if 'effective_date' in data:
            contract.effective_date = _parse_date(data.get('effective_date'))
            contract.effective_at = _parse_datetime(data.get('effective_at')) if data.get('effective_at') else None
        if 'closing_date' in data:
            contract.closing_date = _parse_date(data.get('closing_date'))
        if 'option_period_days' in data:
            contract.option_period_days = int(data.get('option_period_days')) if data.get('option_period_days') else None

        for field in (
            'financing_type',
            'survey_choice',
            'survey_furnished_by',
            'residential_service_contract',
        ):
            if field in data:
                setattr(contract, field, data.get(field) or None)

        for field in (
            'cash_down_payment',
            'financing_amount',
            'seller_concessions_amount',
            'buyer_agent_commission_percent',
            'buyer_agent_commission_flat',
        ):
            if field in data:
                setattr(contract, field, _decimal(data.get(field)))

        if 'financing_approval_deadline' in data and data.get('financing_approval_deadline'):
            contract.financing_approval_deadline = _parse_date(data.get('financing_approval_deadline'))
        else:
            contract.financing_approval_deadline = derive_financing_approval_deadline(
                contract.frozen_terms or {},
                contract.effective_date,
            )

        _sync_contract_frozen_terms(contract)
        create_contract_milestones(contract, replace=True)
        db.session.commit()
        return jsonify({'success': True, 'accepted_contract_id': contract.id})
    except ValueError as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@transactions_bp.route('/<int:id>/seller/contracts/<int:contract_id>/milestones', methods=['POST'])
@login_required
@transactions_required
def create_seller_contract_milestone(id, contract_id):
    """Create a manual milestone for a seller contract."""
    transaction = _get_seller_transaction(id)
    if transaction is None:
        return jsonify({'success': False, 'error': 'Contracts are only available for seller transactions'}), 400

    contract = _get_contract_for_update(transaction, contract_id)
    data = request.get_json(silent=True) or request.form
    milestone = SellerContractMilestone(
        organization_id=current_user.organization_id,
        transaction_id=transaction.id,
        accepted_contract_id=contract.id,
        created_by_id=current_user.id,
        milestone_key='manual',
        title='Manual milestone',
        source='manual',
    )

    try:
        _apply_milestone_data(milestone, data)
        db.session.add(milestone)
        db.session.commit()
        return jsonify({'success': True, 'milestone': _milestone_payload(milestone)}), 201
    except ValueError as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@transactions_bp.route('/<int:id>/seller/contracts/<int:contract_id>/milestones/<int:milestone_id>', methods=['POST', 'PATCH'])
@login_required
@transactions_required
def update_seller_contract_milestone(id, contract_id, milestone_id):
    """Manually update a seller contract milestone."""
    transaction = _get_seller_transaction(id)
    if transaction is None:
        return jsonify({'success': False, 'error': 'Contracts are only available for seller transactions'}), 400

    contract = _get_contract_for_update(transaction, contract_id)
    milestone = SellerContractMilestone.query.filter_by(
        id=milestone_id,
        accepted_contract_id=contract.id,
        transaction_id=transaction.id,
        organization_id=current_user.organization_id,
    ).first_or_404()
    data = request.get_json(silent=True) or request.form

    try:
        _apply_milestone_data(milestone, data)
        db.session.commit()
        return jsonify({'success': True, 'milestone': _milestone_payload(milestone)})
    except ValueError as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@transactions_bp.route('/<int:id>/seller/contracts/<int:contract_id>/terminate', methods=['POST'])
@login_required
@transactions_required
def terminate_seller_contract(id, contract_id):
    """Terminate a primary contract and promote a backup if requested."""
    transaction = _get_seller_transaction(id)
    if transaction is None:
        return jsonify({'success': False, 'error': 'Contracts are only available for seller transactions'}), 400

    contract = SellerAcceptedContract.query.filter_by(
        id=contract_id,
        transaction_id=transaction.id,
        organization_id=current_user.organization_id,
    ).first_or_404()
    data = request.get_json(silent=True) or request.form
    reason = data.get('termination_reason') or 'other'

    try:
        termination = terminate_contract(
            contract,
            reason=reason,
            actor_id=current_user.id,
            terminated_at=_parse_datetime(data.get('terminated_at')) or datetime.utcnow(),
            document_id=data.get('termination_document_id') or None,
            notes=data.get('notes'),
        )

        promote_backup_id = data.get('promote_backup_contract_id')
        if promote_backup_id:
            backup_contract = SellerAcceptedContract.query.filter_by(
                id=int(promote_backup_id),
                transaction_id=transaction.id,
                organization_id=current_user.organization_id,
                position='backup',
                status='active',
            ).first_or_404()
            promoted = promote_backup_contract(
                contract,
                backup_contract,
                _parse_datetime(data.get('backup_notice_received_at')) or datetime.utcnow(),
                actor_id=current_user.id,
            )
            termination.promoted_backup_contract_id = promoted.id
            termination.backup_promoted = True
            transaction.status = 'under_contract'
        else:
            termination.returned_to_active = True
            transaction.status = 'active'

        db.session.commit()
        return jsonify({'success': True, 'backup_promoted': termination.backup_promoted})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@transactions_bp.route('/<int:id>/seller/contracts/<int:contract_id>/close', methods=['POST'])
@login_required
@transactions_required
def close_seller_contract(id, contract_id):
    """Close a seller transaction from an active primary contract."""
    transaction = _get_seller_transaction(id)
    if transaction is None:
        return jsonify({'success': False, 'error': 'Contracts are only available for seller transactions'}), 400

    contract = SellerAcceptedContract.query.filter_by(
        id=contract_id,
        transaction_id=transaction.id,
        organization_id=current_user.organization_id,
        position='primary',
    ).first_or_404()
    data = request.get_json(silent=True) or request.form

    try:
        closing = close_contract(
            contract,
            current_user.id,
            actual_closing_date=_parse_date(data.get('actual_closing_date')),
            funded_recorded_at=_parse_datetime(data.get('funded_recorded_at')),
            final_sales_price=_decimal(data.get('final_sales_price')),
            final_seller_concessions=_decimal(data.get('final_seller_concessions')),
            final_listing_commission=_decimal(data.get('final_listing_commission')),
            final_coop_compensation=_decimal(data.get('final_coop_compensation')),
            final_referral_fee=_decimal(data.get('final_referral_fee')),
            final_net_proceeds=_decimal(data.get('final_net_proceeds')),
            deed_recording_reference=data.get('deed_recording_reference'),
            final_walkthrough_complete=str(data.get('final_walkthrough_complete', '')).lower() in ('1', 'true', 'yes', 'on'),
            key_access_handoff_complete=str(data.get('key_access_handoff_complete', '')).lower() in ('1', 'true', 'yes', 'on'),
            possession_status=data.get('possession_status'),
            notes=data.get('notes'),
        )
        db.session.commit()
        return jsonify({'success': True, 'closing_summary_id': closing.id})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500
