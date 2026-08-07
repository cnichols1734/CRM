"""Offer comparison workspace + highest-and-best request (state only)."""

from __future__ import annotations

import logging
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from flask import abort, jsonify, render_template, request, url_for
from flask_login import current_user, login_required

from models import (
    SellerCommissionTerms,
    SellerListingProfile,
    SellerOffer,
    db,
)
from services import net_sheet as net_sheet_service
from services.offer_compare import OfferCompareService
from services.offer_side import side_for_transaction
from services.seller_workflow import create_offer_activity
from services.transaction_auth import CAP_EDIT, CAP_VIEW, get_transaction_for_user
from . import transactions_bp
from .decorators import transactions_required

logger = logging.getLogger(__name__)

TERMINAL_OFFER_STATUSES = frozenset({
    'accepted', 'declined', 'withdrawn', 'expired', 'replaced',
})

MONEY_TERM_KEYS = frozenset({
    'option_fee', 'sales_price', 'offer_price', 'earnest_money',
    'additional_earnest_money', 'seller_concessions_amount',
    'financing_amount', 'cash_down_payment', 'net_to_seller_estimate',
})
DATE_TERM_KEYS = frozenset({
    'proposed_close_date', 'closing_date', 'effective_date',
})


def _parse_deadline(value):
    """Parse an ISO-8601-ish deadline string. Raises ValueError if unusable."""
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ValueError('deadline_at is required')
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    if text.endswith('Z'):
        text = text[:-1] + '+00:00'
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        pass
    for fmt in (
        '%Y-%m-%dT%H:%M:%S',
        '%Y-%m-%dT%H:%M',
        '%Y-%m-%d %H:%M:%S',
        '%Y-%m-%d %H:%M',
        '%Y-%m-%d',
    ):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise ValueError('deadline_at is not a valid date')


def _status_badge(status):
    tone_map = {
        'new': 'info',
        'reviewing': 'info',
        'countered': 'warning',
        'accepted': 'success',
        'declined': 'danger',
        'withdrawn': 'neutral',
        'expired': 'danger',
        'replaced': 'neutral',
        'backup': 'warning',
    }
    key = (status or 'new').lower()
    label = key.replace('_', ' ').title()
    return label, tone_map.get(key, 'neutral')


def _format_money(value):
    """Format money for display without float coercion."""
    if value is None or value == '':
        return '—'
    if isinstance(value, Decimal):
        amount = value
    else:
        text = str(value).strip().replace('$', '').replace(',', '')
        if not text:
            return '—'
        try:
            amount = Decimal(text)
        except (InvalidOperation, ValueError):
            return str(value)
    quantized = amount.quantize(Decimal('0.01'))
    if quantized == quantized.to_integral_value():
        return f'${quantized:,.0f}'
    return f'${quantized:,.2f}'


def _format_date(value):
    if value is None or value == '':
        return '—'
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return value.strftime('%b %-d, %Y') if hasattr(value, 'strftime') else value.isoformat()
    text = str(value).strip()
    parts = text[:10].split('-')
    if len(parts) == 3 and len(parts[0]) == 4:
        try:
            parsed = date(int(parts[0]), int(parts[1]), int(parts[2]))
            # %-d is POSIX-only; fall back for portability.
            try:
                return parsed.strftime('%b %-d, %Y')
            except ValueError:
                return parsed.strftime('%b %d, %Y').replace(' 0', ' ')
        except ValueError:
            return text[:10]
    return text


def _format_term_value(key, value):
    if value is None or value == '':
        return '—'
    if value is True:
        return 'Yes'
    if value is False:
        return 'No'
    if key in DATE_TERM_KEYS:
        return _format_date(value)
    if key in MONEY_TERM_KEYS:
        return _format_money(value)
    return str(value)


@transactions_bp.route('/<int:id>/offers/compare', methods=['GET'])
@login_required
@transactions_required
def compare_offers_view(id):
    """Full-page side-by-side offer comparison with net sheets."""
    tx, decision = get_transaction_for_user(id, capability=CAP_VIEW)
    if not tx:
        abort(403 if decision.reason != 'not_found' else 404)

    # The net sheet computes seller proceeds, which is meaningless for a buyer
    # file's submitted offers. Buyer-side comparison needs its own math.
    if side_for_transaction(tx) != 'seller':
        abort(404)

    raw_ids = request.args.getlist('offer_id')
    offer_ids = None
    if raw_ids:
        parsed = []
        for raw in raw_ids:
            try:
                parsed.append(int(raw))
            except (TypeError, ValueError):
                continue
        offer_ids = parsed or None

    result = OfferCompareService.compare_offers(tx, offer_ids=offer_ids)

    offer_id_order = [col['offer_id'] for col in result['offers']]
    offers_by_id = {}
    if offer_id_order:
        loaded = SellerOffer.query.filter(
            SellerOffer.id.in_(offer_id_order),
            SellerOffer.transaction_id == tx.id,
            SellerOffer.organization_id == current_user.organization_id,
        ).all()
        offers_by_id = {o.id: o for o in loaded}

    ordered_offers = [
        offers_by_id[oid] for oid in offer_id_order if oid in offers_by_id
    ]

    commission_terms = SellerCommissionTerms.query.filter_by(
        transaction_id=tx.id,
        organization_id=current_user.organization_id,
    ).first()

    sheets = net_sheet_service.build_for_offers(
        ordered_offers,
        commission_terms=commission_terms,
    )
    net_sheets_by_offer = {sheet.offer_id: sheet for sheet in sheets}
    net_lines_by_offer = {
        sheet.offer_id: {line.key: line for line in sheet.lines}
        for sheet in sheets
    }

    net_line_specs = []
    unknown_label_map = {}
    if sheets:
        for line in sheets[0].lines:
            if line.key != 'estimated_net':
                net_line_specs.append({
                    'key': line.key,
                    'label': line.label,
                    'kind': line.kind,
                })
        for sheet in sheets:
            for line in sheet.lines:
                if not line.known and line.key != 'estimated_net':
                    unknown_label_map[line.key] = line.label
    unknown_labels = [unknown_label_map[k] for k in unknown_label_map]

    # The net figure is the one an agent actually decides on, so mark its winner
    # the same way the matrix marks highest price and soonest close.
    best_net_offer_id = None
    best_net = None
    for sheet in sheets:
        if sheet.estimated_net is None:
            continue
        if best_net is None or sheet.estimated_net > best_net:
            best_net = sheet.estimated_net
            best_net_offer_id = sheet.offer_id

    listing_profile = SellerListingProfile.query.filter_by(
        transaction_id=tx.id,
        organization_id=current_user.organization_id,
    ).first()

    return_url = url_for('transactions.view_transaction', id=tx.id)
    highest_best_url = url_for('transactions.request_highest_and_best', id=tx.id)
    accept_urls = {
        col['offer_id']: url_for(
            'transactions.accept_seller_offer',
            id=tx.id,
            offer_id=col['offer_id'],
        )
        for col in result['offers']
    }

    hb_candidates = [
        col for col in result['offers']
        if (col.get('status') or '').lower() not in TERMINAL_OFFER_STATUSES
    ]

    return render_template(
        'transactions/offer_compare.html',
        transaction=tx,
        result=result,
        net_sheets_by_offer=net_sheets_by_offer,
        net_lines_by_offer=net_lines_by_offer,
        net_line_specs=net_line_specs,
        best_net_offer_id=best_net_offer_id,
        unknown_labels=unknown_labels,
        listing_profile=listing_profile,
        return_url=return_url,
        highest_best_url=highest_best_url,
        accept_urls=accept_urls,
        hb_candidates=hb_candidates,
        status_badge=_status_badge,
        format_money=_format_money,
        format_date=_format_date,
        format_term_value=_format_term_value,
    )


@transactions_bp.route('/<int:id>/offers/highest-and-best', methods=['POST'])
@login_required
@transactions_required
def request_highest_and_best(id):
    """Record a highest-and-best request. Does not send any outbound message."""
    tx, decision = get_transaction_for_user(id, capability=CAP_EDIT)
    if not tx:
        abort(403 if decision.reason != 'not_found' else 404)

    # Calling for highest and best is a listing-side move.
    if side_for_transaction(tx) != 'seller':
        abort(404)

    data = request.get_json(silent=True) or {}
    try:
        deadline_at = _parse_deadline(data.get('deadline_at'))
        message = data.get('message')
        if message is not None:
            message = str(message).strip() or None

        raw_ids = data.get('offer_ids') or []
        if not isinstance(raw_ids, list):
            raise ValueError('offer_ids must be a list')
        offer_ids = []
        for raw in raw_ids:
            try:
                offer_ids.append(int(raw))
            except (TypeError, ValueError):
                raise ValueError('offer_ids must contain integers') from None

        profile = SellerListingProfile.query.filter_by(
            transaction_id=tx.id,
            organization_id=current_user.organization_id,
        ).first()
        if not profile:
            profile = SellerListingProfile(
                organization_id=current_user.organization_id,
                transaction_id=tx.id,
                created_by_id=current_user.id,
            )
            db.session.add(profile)

        now = datetime.utcnow()
        profile.highest_best_enabled = True
        profile.highest_best_deadline_at = deadline_at
        profile.highest_best_message = message
        profile.highest_best_sent_at = now
        profile.highest_best_sent_by_id = current_user.id

        affected = []
        if offer_ids:
            offers = SellerOffer.query.filter(
                SellerOffer.id.in_(offer_ids),
                SellerOffer.transaction_id == tx.id,
                SellerOffer.organization_id == current_user.organization_id,
            ).all()
            for offer in offers:
                offer.included_in_highest_best = True
                offer.highest_best_requested_at = now
                create_offer_activity(
                    offer,
                    'highest_best_requested',
                    'Included in highest and best request',
                    actor_id=current_user.id,
                )
                affected.append(offer.id)

        db.session.commit()
        return jsonify({
            'success': True,
            'deadline_at': deadline_at.isoformat(),
            'offer_ids': affected,
            'message': message,
            'sent': False,
        })
    except ValueError as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception:
        logger.exception(
            'request_highest_and_best failed tx=%s', id,
        )
        db.session.rollback()
        return jsonify({
            'success': False,
            'error': 'Could not record highest and best. Nothing was saved.',
        }), 500
