"""Human labels and post-approve destinations for document-first intake UI."""
from __future__ import annotations

import re
from typing import Any, Optional

from flask import url_for

from services.document_identity import identity_for_slug
from services.document_routing import (
    ACTION_ATTACH_CONTROLLING_CONTRACT,
    ACTION_ATTACH_LISTING_DOC,
    ACTION_ATTACH_SUPPORTING,
    ACTION_CREATE_AMENDMENT,
    ACTION_CREATE_BUYER_OFFER,
    ACTION_CREATE_INBOUND_OFFER,
    ACTION_CREATE_LISTING,
    ACTION_INVALID,
    ACTION_NEEDS_CONFIRMATION,
)

# Confidence bands — no fake precision.
CONFIDENCE_HIGH = 'High confidence'
CONFIDENCE_NEEDS = 'Needs confirmation'

_DESTINATION_LABELS = {
    'offer_thread': 'Offer still being negotiated',
    'controlling_contract': 'Fully executed controlling contract',
    'new_offer': 'Start a new offer package',
    'listing': 'Listing package',
    'contract': 'Controlling contract package',
    'amendment': 'Amendment under the controlling contract',
    'other': 'Other / supporting document',
}

_FILING_PLAN_BY_ACTION = {
    ACTION_CREATE_LISTING: 'Create seller listing',
    ACTION_ATTACH_LISTING_DOC: 'Add to listing package',
    ACTION_CREATE_INBOUND_OFFER: 'Add incoming offer to seller listing',
    ACTION_CREATE_BUYER_OFFER: 'Add buyer offer',
    ACTION_ATTACH_CONTROLLING_CONTRACT: 'Establish controlling contract',
    ACTION_CREATE_AMENDMENT: 'Open amendment review',
    ACTION_ATTACH_SUPPORTING: 'File as supporting document',
    ACTION_NEEDS_CONFIRMATION: 'Choose where this document belongs',
    ACTION_INVALID: 'Cannot file this document as submitted',
}

_APPROVE_CTA_BY_ACTION = {
    ACTION_CREATE_LISTING: 'Create seller listing',
    ACTION_ATTACH_LISTING_DOC: 'Add to listing',
    ACTION_CREATE_INBOUND_OFFER: 'Add incoming offer',
    ACTION_CREATE_BUYER_OFFER: 'Create buyer transaction',
    ACTION_ATTACH_CONTROLLING_CONTRACT: 'Start contract coordination',
    ACTION_CREATE_AMENDMENT: 'Open amendment review',
    ACTION_ATTACH_SUPPORTING: 'File document',
}

_STATE_BADGES = {
    'expected': 'Expected',
    'missing': 'Expected',
    'uploaded': 'Uploaded',
    'needs_classification': 'Needs filing',
    'detected_in_package': 'Detected in package',
    'not_applicable': 'Not applicable',
    'optional': 'May apply',
}


def identity_display_label(identity: dict[str, Any] | None) -> str:
    """Human form label from identity payload or slug lookup."""
    data = identity if isinstance(identity, dict) else {}
    label = (data.get('label') or '').strip()
    if label:
        return label
    slug = (data.get('template_slug') or '').strip()
    if slug:
        found = identity_for_slug(slug)
        if found and found.label and found.label != 'Unknown document':
            return found.label
    kind = (data.get('kind') or '').replace('_', ' ').strip()
    return kind.title() if kind else 'Unrecognized document'


def confidence_wording(identity: dict[str, Any] | None) -> str:
    data = identity if isinstance(identity, dict) else {}
    try:
        conf = float(data.get('confidence')) if data.get('confidence') is not None else None
    except (TypeError, ValueError):
        conf = None
    kind = (data.get('kind') or '').strip().lower()
    if kind in ('', 'unknown', 'other') or conf is None or conf < 0.75:
        return CONFIDENCE_NEEDS
    return CONFIDENCE_HIGH


def filing_plan_label(
    *,
    route_action: str | None,
    side: str | None = None,
    destination_choice: str | None = None,
) -> str:
    action = (route_action or '').strip()
    choice = (destination_choice or '').strip().lower()
    if choice == 'offer_thread':
        if (side or '').lower() == 'buyer':
            return 'Add buyer offer'
        return 'Add incoming offer to seller listing'
    if choice == 'controlling_contract':
        return 'Establish controlling contract'
    if choice == 'new_offer':
        if (side or '').lower() == 'buyer':
            return 'Add buyer offer'
        return 'Add incoming offer to seller listing'
    return _FILING_PLAN_BY_ACTION.get(action, 'Review and file this document')


def destination_option_label(
    option: str,
    *,
    offer_meta: dict[str, Any] | None = None,
) -> str:
    text = (option or '').strip()
    if text.startswith('offer:') or text.startswith('existing_offer:'):
        offer_id = text.split(':', 1)[-1]
        meta = offer_meta or {}
        party = meta.get('buyer_names') or meta.get('party_names') or f'Offer #{offer_id}'
        price = meta.get('offer_price')
        if price is not None:
            try:
                return f'Existing offer: {party} · ${float(price):,.0f}'
            except (TypeError, ValueError):
                return f'Existing offer: {party}'
        return f'Existing offer: {party}'
    if text in _DESTINATION_LABELS:
        return _DESTINATION_LABELS[text]
    return text.replace('_', ' ').title()


def approve_cta_label(
    *,
    route_action: str | None,
    side: str | None,
    match_status: str | None = None,
    destination_choice: str | None = None,
) -> str:
    if match_status == 'matched':
        return 'Update transaction'
    plan = filing_plan_label(
        route_action=route_action,
        side=side,
        destination_choice=destination_choice,
    )
    action = (route_action or '').strip()
    if action in _APPROVE_CTA_BY_ACTION:
        return _APPROVE_CTA_BY_ACTION[action]
    return plan


def package_state_badge(state: str | None, applicability: str | None = None) -> str:
    key = (state or '').strip().lower()
    if key == 'expected' and (applicability or '') == 'optional':
        return 'May apply'
    if key == 'expected' and (applicability or '') == 'unknown':
        return 'Unknown'
    if key in _STATE_BADGES:
        return _STATE_BADGES[key]
    if (applicability or '') == 'optional':
        return 'May apply'
    if (applicability or '') == 'unknown':
        return 'Unknown'
    return (state or 'Unknown').replace('_', ' ').title()


def append_bob_setup_query(
    url: str,
    *,
    bootstrap_session_id: int | None = None,
) -> str:
    """Attach one-shot Bob setup briefing query params to a redirect URL."""
    if not url:
        return url
    query = 'bob=setup'
    if bootstrap_session_id:
        query = f'{query}&bootstrap_session_id={int(bootstrap_session_id)}'
    if '#' in url:
        base, frag = url.split('#', 1)
        sep = '&' if '?' in base else '?'
        return f'{base}{sep}{query}#{frag}'
    sep = '&' if '?' in url else '?'
    return f'{url}{sep}{query}'


def resolve_bootstrap_next_url(
    *,
    transaction_id: int,
    route_action: str | None,
    offer_id: int | None = None,
    amendment_id: int | None = None,
    side: str | None = None,
    bob_setup: bool = False,
    bootstrap_session_id: int | None = None,
) -> str:
    """Where to send the agent after bootstrap / questionnaire filing.

    Listing and seller workspace landings open at the top of the transaction
    page (header, deadlines, next steps). Specific offer / amendment IDs still
    deep-link so the agent lands on the thing they just filed.
    """
    action = (route_action or '').strip()
    detail = url_for('transactions.view_transaction', id=transaction_id)

    if action in (ACTION_CREATE_LISTING, ACTION_ATTACH_LISTING_DOC):
        next_url = detail
    elif action == ACTION_CREATE_INBOUND_OFFER:
        if offer_id:
            next_url = f'{detail}#offer-{offer_id}'
        else:
            next_url = detail
    elif action == ACTION_CREATE_BUYER_OFFER:
        if offer_id:
            next_url = f'{detail}#offer-{offer_id}'
        else:
            next_url = f'{detail}#transaction-offers'
    elif action == ACTION_ATTACH_CONTROLLING_CONTRACT:
        if (side or '').lower() == 'seller':
            next_url = detail
        else:
            next_url = f'{detail}#control-tower'
    elif action == ACTION_CREATE_AMENDMENT and amendment_id:
        next_url = url_for(
            'transactions.amendment_review',
            id=transaction_id,
            amendment_id=amendment_id,
        )
    else:
        next_url = detail

    if bob_setup and '/transactions/' in next_url and '/amendment' not in next_url:
        # Auto-open Bob on the transaction workspace, not amendment review.
        return append_bob_setup_query(
            next_url,
            bootstrap_session_id=bootstrap_session_id,
        )
    return next_url


def build_identification_summary(review: dict[str, Any], *, side: str | None) -> dict[str, Any]:
    """Compact identity + filing plan block for bootstrap review."""
    identity = review.get('document_identity') or {}
    route = review.get('route_decision') or {}
    action = route.get('action')
    choice = review.get('destination_choice')
    kind = (identity.get('kind') or '').strip().lower()
    is_listing = (
        kind == 'listing_agreement'
        or (identity.get('template_slug') or '') == 'listing-agreement'
        or action in (ACTION_CREATE_LISTING, ACTION_ATTACH_LISTING_DOC)
    )
    return {
        'label': identity_display_label(identity),
        'form_number': identity.get('form_number'),
        'confidence_wording': confidence_wording(identity),
        'filing_plan': filing_plan_label(
            route_action=action,
            side=side,
            destination_choice=choice,
        ),
        'side': side,
        'route_reason': route.get('reason') or review.get('destination_prompt'),
        'route_action': action,
        'needs_confirmation': bool(
            route.get('needs_confirmation') or review.get('destination_options')
        ),
        'destination_options': list(review.get('destination_options') or []),
        'destination_choice': choice,
        'is_invalid': action == ACTION_INVALID,
        'is_listing_intake': is_listing,
        'source_phrase': 'Found in uploaded document',
        'summary_heading': 'Listing summary' if is_listing else 'Deal summary',
        'dates_heading': 'Listing dates and terms' if is_listing else 'Contract dates and money',
        'dates_help': (
            'These set the listing timeline. Check them before continuing.'
            if is_listing
            else 'These set deadlines. Check them before continuing.'
        ),
        'dates_empty': (
            'No listing dates or terms were read confidently. Add them before the listing package can be prepared.'
            if is_listing
            else 'No dates or amounts were read confidently. Add them before deadlines can be prepared.'
        ),
        'summary_empty': (
            'Property or list price was not read. Add the missing values below before creating the listing.'
            if is_listing
            else 'Property or price was not read. Add the missing values below before creating the transaction.'
        ),
    }


_SLUG_CHOICES = (
    ('listing-agreement', 'Listing agreement (TXR-1101)'),
    ('seller-offer-contract', 'Purchase contract (offer)'),
    ('one-to-four-family-contract', 'Purchase contract (TREC 20)'),
    ('amendment', 'Amendment (TREC 39)'),
    ('hoa-addendum', 'HOA addendum (TREC 36)'),
    ('third-party-financing-addendum', 'Financing addendum (TREC 40)'),
    ('appraisal-termination-addendum', 'Appraisal termination (TREC 49)'),
    ('broker-compensation-agreement', 'Broker compensation (TXR 2402)'),
    ('sellers-disclosure', "Seller's disclosure"),
    ('pre-approval-or-proof-of-funds', 'Pre-approval / POF'),
)

_SCOPE_LABELS = {
    'listing': 'Listing package',
    'offer': 'Offer package',
    'contract': 'Controlling contract',
    'amendment': 'Amendment',
    'other': 'Other / needs filing',
}


def _match_suggested_offer_id(
    *,
    routing_context: dict[str, Any],
    identity: dict[str, Any],
    field_data: dict[str, Any] | None = None,
    linked_offer_id: int | None = None,
) -> int | None:
    """Prefer an already-linked offer, else match extracted buyers to active offers."""
    if linked_offer_id:
        try:
            return int(linked_offer_id)
        except (TypeError, ValueError):
            pass
    active = list(routing_context.get('active_offers') or [])
    if not active:
        return None
    data = field_data if isinstance(field_data, dict) else {}
    buyers = (
        data.get('buyer_names')
        or data.get('buyer_name')
        or identity.get('buyer_names')
    )
    if isinstance(buyers, (list, tuple)):
        buyer_text = ' '.join(str(b) for b in buyers if b)
    else:
        buyer_text = str(buyers or '')
    buyer_key = re.sub(r'[^a-z0-9]+', ' ', buyer_text.lower()).strip()
    if not buyer_key:
        return int(active[0]['id']) if len(active) == 1 else None
    for offer in active:
        offer_key = re.sub(
            r'[^a-z0-9]+',
            ' ',
            str(offer.get('buyer_names') or '').lower(),
        ).strip()
        if not offer_key:
            continue
        if offer_key in buyer_key or buyer_key in offer_key:
            return int(offer['id'])
        if set(offer_key.split()) & set(buyer_key.split()):
            return int(offer['id'])
    return int(active[0]['id']) if len(active) == 1 else None


def build_classification_form_options(
    *,
    identity: dict[str, Any] | None,
    routing_context: dict[str, Any] | None,
    document_template_slug: str | None = None,
    field_data: dict[str, Any] | None = None,
    linked_offer_id: int | None = None,
) -> dict[str, Any]:
    """Server-built filing form defaults filtered by side/kind/baseline."""
    from services.document_classification_policy import (
        KIND_ADDENDUM,
        KIND_AMENDMENT,
        KIND_DISCLOSURE,
        KIND_LISTING,
        KIND_POF,
        KIND_PURCHASE,
        KIND_ALLOWED_SCOPES,
        kind_for_slug,
    )
    from services.document_identity import EXEC_EXECUTED

    identity = identity if isinstance(identity, dict) else {}
    rc = routing_context if isinstance(routing_context, dict) else {}
    side = (rc.get('side') or '').strip().lower()
    has_baseline = bool(rc.get('has_primary_contract'))
    slug = (
        (identity.get('template_slug') or document_template_slug or '')
        .strip()
        .lower()
    )
    kind = (identity.get('kind') or kind_for_slug(slug) or 'unknown').strip().lower()
    possible = {
        str(s).strip().lower()
        for s in (identity.get('possible_scopes') or [])
        if str(s).strip()
    }
    allowed = set(KIND_ALLOWED_SCOPES.get(kind, {'other'}))
    if possible:
        allowed &= possible

    if side == 'buyer':
        allowed.discard('listing')
    if kind == KIND_LISTING:
        allowed = {'listing'} & allowed or {'listing'}
    if kind == KIND_AMENDMENT:
        if has_baseline:
            allowed = {'amendment'} & (allowed | {'amendment'})
        else:
            allowed = set()
    if kind == KIND_PURCHASE:
        allowed.discard('listing')
    if not has_baseline:
        allowed.discard('amendment')

    # Always allow "other" as escape hatch except pure listing kind.
    if kind != KIND_LISTING:
        allowed.add('other')

    scope_options = []
    for value in ('listing', 'offer', 'contract', 'amendment', 'other'):
        if value not in allowed:
            continue
        label = _SCOPE_LABELS[value]
        if value == 'offer' and side == 'seller':
            label = 'Incoming offer'
        elif value == 'offer' and side == 'buyer':
            label = 'Submitted offer'
        scope_options.append({'value': value, 'label': label})

    suggested = None
    execution = (identity.get('execution_state') or '').strip().lower()
    if kind == KIND_LISTING and 'listing' in allowed:
        suggested = 'listing'
    elif kind == KIND_AMENDMENT and has_baseline and 'amendment' in allowed:
        suggested = 'amendment'
    elif kind == KIND_PURCHASE and side == 'seller' and 'offer' in allowed:
        suggested = 'offer'
    elif (
        kind == KIND_PURCHASE
        and side == 'buyer'
        and execution == EXEC_EXECUTED
        and 'contract' in allowed
    ):
        suggested = 'contract'
    elif (
        kind in (KIND_ADDENDUM, KIND_DISCLOSURE, KIND_POF)
        and side == 'seller'
        and 'offer' in allowed
    ):
        suggested = 'offer'
    elif kind in (KIND_ADDENDUM, KIND_DISCLOSURE, KIND_POF) and len(allowed - {'other'}) == 1:
        suggested = next(iter(allowed - {'other'}))

    suggested_offer_id = None
    if suggested == 'offer' or any(o['value'] == 'offer' for o in scope_options):
        suggested_offer_id = _match_suggested_offer_id(
            routing_context=rc,
            identity=identity,
            field_data=field_data,
            linked_offer_id=linked_offer_id,
        )
        if suggested_offer_id and suggested is None and 'offer' in allowed:
            suggested = 'offer'

    require_choice = suggested is None and len(scope_options) > 1
    selected_slug = slug if any(slug == opt[0] for opt in _SLUG_CHOICES) else ''
    slug_options = [
        {
            'value': value,
            'label': label,
            'selected': value == selected_slug,
        }
        for value, label in _SLUG_CHOICES
    ]

    return {
        'slug_options': slug_options,
        'selected_slug': selected_slug,
        'scope_options': scope_options,
        'suggested_scope': suggested,
        'suggested_offer_id': suggested_offer_id,
        'require_destination_choice': require_choice,
        'show_offer_fields': (suggested == 'offer') or any(
            o['value'] == 'offer' for o in scope_options
        ),
        'show_explicit_controlling': side == 'seller' and any(
            o['value'] == 'contract' for o in scope_options
        ),
        'model_observation': {
            'label': identity_display_label(identity),
            'form_number': identity.get('form_number'),
            'kind': kind,
            'template_slug': identity.get('template_slug'),
            'confidence_wording': confidence_wording(identity),
        },
        'has_baseline': has_baseline,
        'side': side,
    }
