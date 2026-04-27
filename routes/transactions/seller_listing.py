"""Seller listing operations routes."""
from decimal import Decimal, InvalidOperation

from flask import abort, jsonify, request
from flask_login import current_user, login_required

from models import (
    SellerCommissionTerms,
    SellerListingPriceChange,
    SellerListingProfile,
    Transaction,
    db,
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


def _decimal(value):
    if value in (None, ''):
        return None
    try:
        return Decimal(str(value).replace('$', '').replace(',', '').strip())
    except (InvalidOperation, AttributeError):
        return None


def _profile_for(transaction):
    profile = SellerListingProfile.query.filter_by(
        transaction_id=transaction.id,
        organization_id=current_user.organization_id,
    ).first()
    if not profile:
        profile = SellerListingProfile(
            organization_id=current_user.organization_id,
            transaction_id=transaction.id,
            created_by_id=current_user.id,
        )
        db.session.add(profile)
    return profile


@transactions_bp.route('/<int:id>/seller/listing-profile', methods=['POST'])
@login_required
@transactions_required
def update_seller_listing_profile(id):
    """Update showing access instructions and listing operations fields."""
    transaction = _get_seller_transaction(id)
    if transaction is None:
        return jsonify({'success': False, 'error': 'Seller listing profile is only available for seller transactions'}), 400

    data = request.get_json(silent=True) or request.form
    profile = _profile_for(transaction)

    profile.showing_approval_policy = data.get('showing_approval_policy') or profile.showing_approval_policy
    profile.access_type = data.get('access_type')
    profile.lockbox_type = data.get('lockbox_type')
    profile.gate_code = data.get('gate_code')
    profile.alarm_notes = data.get('alarm_notes')
    profile.pet_notes = data.get('pet_notes')
    profile.occupancy_status = data.get('occupancy_status')
    profile.public_showing_instructions = data.get('public_showing_instructions')
    profile.private_showing_notes = data.get('private_showing_notes')
    profile.showing_service_url = data.get('showing_service_url')
    profile.mls_number = data.get('mls_number')

    try:
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@transactions_bp.route('/<int:id>/seller/price-change', methods=['POST'])
@login_required
@transactions_required
def add_listing_price_change(id):
    """Record a list price change and update the seller listing profile."""
    transaction = _get_seller_transaction(id)
    if transaction is None:
        return jsonify({'success': False, 'error': 'Price changes are only available for seller transactions'}), 400

    data = request.get_json(silent=True) or request.form
    new_price = _decimal(data.get('new_price'))
    if new_price is None:
        return jsonify({'success': False, 'error': 'New price is required'}), 400

    profile = _profile_for(transaction)
    old_price = profile.current_list_price
    if profile.original_list_price is None:
        profile.original_list_price = old_price or new_price
    profile.current_list_price = new_price

    price_change = SellerListingPriceChange(
        organization_id=current_user.organization_id,
        transaction_id=transaction.id,
        created_by_id=current_user.id,
        old_price=old_price,
        new_price=new_price,
        reason=data.get('reason'),
        notes=data.get('notes'),
    )

    try:
        db.session.add(price_change)
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@transactions_bp.route('/<int:id>/seller/commission', methods=['POST'])
@login_required
@transactions_required
def update_seller_commission(id):
    """Update seller commission and representation terms."""
    transaction = _get_seller_transaction(id)
    if transaction is None:
        return jsonify({'success': False, 'error': 'Commission terms are only available for seller transactions'}), 400

    data = request.get_json(silent=True) or request.form
    terms = SellerCommissionTerms.query.filter_by(
        transaction_id=transaction.id,
        organization_id=current_user.organization_id,
    ).first()
    if not terms:
        terms = SellerCommissionTerms(
            organization_id=current_user.organization_id,
            transaction_id=transaction.id,
            created_by_id=current_user.id,
        )
        db.session.add(terms)

    terms.listing_commission_percent = _decimal(data.get('listing_commission_percent'))
    terms.listing_commission_flat = _decimal(data.get('listing_commission_flat'))
    terms.coop_compensation_percent = _decimal(data.get('coop_compensation_percent'))
    terms.coop_compensation_flat = _decimal(data.get('coop_compensation_flat'))
    terms.bonus_amount = _decimal(data.get('bonus_amount'))
    terms.referral_fee_percent = _decimal(data.get('referral_fee_percent'))
    terms.referral_fee_flat = _decimal(data.get('referral_fee_flat'))
    terms.representation_mode = data.get('representation_mode') or 'unknown'
    terms.notes = data.get('notes')

    try:
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500
