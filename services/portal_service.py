"""Client portal view-model builder.

Assembles a single, client-safe projection of a seller's transaction for the
passwordless portal. Everything the portal template needs is computed here so
the route stays thin and the template never reaches into agent-only data.

Design rules:
- Only expose what a seller should see (no internal agent notes, no other
  parties' contact PII, no commission breakdowns beyond net proceeds).
- Degrade gracefully: every section renders even when its data is absent.
"""
from __future__ import annotations

import logging
from datetime import datetime, date

logger = logging.getLogger(__name__)

SELLER_ROLES = {'seller', 'co_seller'}

# The seller's journey, in order. Each stage maps to data we already store.
STAGE_DEFS = [
    ('preparing', 'Getting ready', 'fa-clipboard-check'),
    ('active', 'Live on the market', 'fa-house'),
    ('showings', 'Showings & feedback', 'fa-users'),
    ('offers', 'Reviewing offers', 'fa-file-signature'),
    ('under_contract', 'Under contract', 'fa-handshake'),
    ('closing', 'Closing', 'fa-key'),
    ('closed', 'Sold', 'fa-champagne-glasses'),
]

# The big editorial sentence shown for the current stage.
STAGE_STATEMENTS = {
    'preparing': 'Getting your home ready',
    'active': 'Live on the market',
    'showings': 'Buyers are touring your home',
    'offers': 'Reviewing your offers',
    'under_contract': 'Under contract',
    'closing': 'Headed to the closing table',
    'closed': 'Sold',
}

ACTIVE_OFFER_STATUSES = {'new', 'reviewing', 'needs_review', 'countered'}

INTEREST_LABELS = {
    'high': 'Very interested',
    'medium': 'Somewhat interested',
    'low': 'Low interest',
    'none': 'Not interested',
}


# --------------------------------------------------------------------------
# small formatting helpers
# --------------------------------------------------------------------------

def _money(value, cents=False):
    if value is None:
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    if cents:
        num = num / 100.0
    return f'${num:,.0f}'


def _as_date(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return None


def _day(value):
    d = _as_date(value)
    return d.strftime('%b %-d') if d else None


def _full_day(value):
    d = _as_date(value)
    return d.strftime('%A, %B %-d, %Y') if d else None


def _initials(first, last):
    a = (first or '').strip()
    b = (last or '').strip()
    out = (a[:1] + b[:1]).upper()
    return out or 'AG'


# --------------------------------------------------------------------------
# main builder
# --------------------------------------------------------------------------

def build_portal_context(access):
    """Build the full client-safe view model from a ClientPortalAccess row."""
    tx = access.transaction
    participant = access.participant

    profile = getattr(tx, 'seller_listing_profile', None)

    seller_first = _participant_first_name(participant)

    ctx = {
        'access': access,
        'transaction': tx,
        'participant': participant,
        'seller_first_name': seller_first,
        'property': _property_block(tx),
        'agent': _agent_block(tx),
        'headline': _headline_block(tx, profile),
        'updated_label': _updated_label(tx),
    }

    stages, current_index = _build_stages(tx, profile)
    ctx['stages'] = stages
    ctx['current_stage_index'] = current_index
    ctx['current_stage'] = stages[current_index] if stages else None
    ctx['headline_statement'] = STAGE_STATEMENTS.get(
        stages[current_index]['key'] if stages else 'active', 'Live on the market')
    # Fraction of the journey complete, for the progress rail fill.
    last = max(len(stages) - 1, 1)
    ctx['progress_pct'] = round(current_index / last * 100)

    ctx['showings'] = _showings_block(tx)
    ctx['offers'] = _offers_block(tx)
    ctx['milestones'] = _milestones_block(tx)
    ctx['documents'] = _documents_block(tx, participant)
    ctx['price_changes'] = _price_changes_block(tx)
    ctx['net_proceeds'] = _net_proceeds_block(tx)
    ctx['updates'] = _updates_block(tx, participant)

    return ctx


def _participant_first_name(participant):
    if participant.contact and participant.contact.first_name:
        return participant.contact.first_name
    name = (participant.name or '').strip()
    if name:
        return name.split()[0]
    return 'there'


def _property_block(tx):
    parts = [p for p in [tx.city, tx.state] if p]
    locality = ', '.join(parts)
    if tx.zip_code:
        locality = f'{locality} {tx.zip_code}'.strip()
    return {
        'street': tx.street_address or 'Your property',
        'locality': locality,
        'full_address': getattr(tx, 'full_address', None) or tx.street_address,
        'city': tx.city,
        'state': tx.state,
        'zip_code': tx.zip_code,
    }


def _agent_block(tx):
    agent = getattr(tx, 'created_by', None)
    org = getattr(tx, 'organization', None) or (agent.organization if agent else None)
    if not agent:
        return {
            'name': (org.name if org else 'Your agent'),
            'initials': 'AG',
            'phone': None, 'email': None,
            'brokerage': org.name if org else None,
        }
    full = f'{agent.first_name} {agent.last_name}'.strip()
    return {
        'name': full or agent.username,
        'initials': _initials(agent.first_name, agent.last_name),
        'phone': agent.phone,
        'email': agent.email,
        'brokerage': org.name if org else None,
        'license_number': agent.license_number,
    }


def _headline_block(tx, profile):
    list_price = None
    if profile and profile.current_list_price:
        list_price = profile.current_list_price
    original_price = profile.original_list_price if profile else None

    # Days on market: prefer go_live_date, else listing start, else first-active.
    dom_start = None
    if profile and profile.go_live_date:
        dom_start = _as_date(profile.go_live_date)
    days_on_market = None
    if dom_start and tx.status in ('active', 'under_contract', 'closed'):
        days_on_market = max((date.today() - dom_start).days, 0)

    return {
        'list_price': _money(list_price),
        'list_price_raw': float(list_price) if list_price else None,
        'original_price': _money(original_price) if original_price else None,
        'price_reduced': bool(original_price and list_price and float(original_price) > float(list_price)),
        'days_on_market': days_on_market,
        'mls_number': profile.mls_number if profile else None,
        'status': tx.status,
        'status_label': _status_label(tx.status),
    }


def _status_label(status):
    return {
        'preparing_to_list': 'Preparing to list',
        'active': 'Active on the market',
        'under_contract': 'Under contract',
        'closed': 'Sold',
        'cancelled': 'Listing paused',
    }.get(status, (status or '').replace('_', ' ').title())


def _updated_label(tx):
    dt = getattr(tx, 'updated_at', None) or getattr(tx, 'created_at', None)
    if not dt:
        return None
    return dt


# --------------------------------------------------------------------------
# pizza tracker
# --------------------------------------------------------------------------

def _build_stages(tx, profile):
    status = tx.status
    primary_contract = _primary_contract(tx)
    has_offers = tx.seller_offers.count() > 0 if hasattr(tx.seller_offers, 'count') else False
    has_showings = tx.seller_showings.count() > 0 if hasattr(tx.seller_showings, 'count') else False

    # Resolve the current stage index from the macro status + signals.
    if status == 'closed':
        current = 6
    elif status == 'under_contract':
        current = 5 if _in_closing_window(primary_contract) else 4
    elif status == 'active':
        if has_offers:
            current = 3
        elif has_showings:
            current = 2
        else:
            current = 1
    elif status == 'cancelled':
        current = 1
    else:  # preparing_to_list / unknown
        current = 0

    # Dates for each stage where we have them.
    go_live = _as_date(profile.go_live_date) if profile else None
    first_showing = _first_showing_date(tx)
    first_offer = _first_offer_date(tx)
    effective = _as_date(primary_contract.effective_date) if primary_contract else None
    closing = _as_date(primary_contract.closing_date) if primary_contract else None
    closed_on = _as_date(getattr(tx, 'actual_close_date', None))
    stage_dates = {
        'preparing': _as_date(getattr(tx, 'created_at', None)),
        'active': go_live,
        'showings': first_showing,
        'offers': first_offer,
        'under_contract': effective,
        'closing': closing,
        'closed': closed_on or closing,
    }

    stages = []
    for idx, (key, label, icon) in enumerate(STAGE_DEFS):
        if idx < current:
            state = 'done'
        elif idx == current:
            state = 'current'
        else:
            state = 'upcoming'
        stages.append({
            'key': key,
            'label': label,
            'icon': icon,
            'state': state,
            'date': _day(stage_dates.get(key)),
            'index': idx,
        })
    return stages, current


def _in_closing_window(contract):
    if not contract or not contract.closing_date:
        return False
    closing = _as_date(contract.closing_date)
    if not closing:
        return False
    return (closing - date.today()).days <= 14


def _primary_contract(tx):
    try:
        return tx.seller_accepted_contracts.filter_by(
            position='primary', status='active').first() \
            or tx.seller_accepted_contracts.filter_by(position='primary').first() \
            or tx.seller_accepted_contracts.first()
    except Exception:
        return None


def _first_showing_date(tx):
    try:
        s = tx.seller_showings.order_by('scheduled_start_at').first()
        return _as_date(s.scheduled_start_at) if s else None
    except Exception:
        return None


def _first_offer_date(tx):
    try:
        o = tx.seller_offers.order_by('created_at').first()
        return _as_date(o.created_at) if o else None
    except Exception:
        return None


# --------------------------------------------------------------------------
# showings & feedback
# --------------------------------------------------------------------------

def _showings_block(tx):
    try:
        from models import SellerShowing
        showings = tx.seller_showings.order_by(
            SellerShowing.scheduled_start_at.desc()
        ).all()
    except Exception:
        try:
            showings = sorted(
                tx.seller_showings.all(),
                key=lambda s: s.scheduled_start_at or datetime.min,
                reverse=True,
            )
        except Exception:
            showings = []

    now = datetime.utcnow()
    week_ago = now.timestamp() - 7 * 86400
    items = []
    with_feedback = 0
    this_week = 0
    interest_counts = {'high': 0, 'medium': 0, 'low': 0, 'none': 0}

    for s in showings:
        start = s.scheduled_start_at
        has_fb = bool(getattr(s, 'feedback_received_at', None) or getattr(s, 'feedback_notes', None)
                      or getattr(s, 'feedback_interest_level', None))
        if has_fb:
            with_feedback += 1
        if start and start.timestamp() >= week_ago:
            this_week += 1
        lvl = getattr(s, 'feedback_interest_level', None)
        if lvl in interest_counts:
            interest_counts[lvl] += 1
        items.append({
            'date': start.strftime('%a, %b %-d') if start else 'Scheduled',
            'time': start.strftime('%-I:%M %p') if start else None,
            'status': (s.status or 'scheduled').replace('_', ' ').title(),
            'agent_brokerage': getattr(s, 'showing_agent_brokerage', None),
            'has_feedback': has_fb,
            'interest': INTEREST_LABELS.get(lvl) if lvl else None,
            'interest_level': lvl,
            'feedback_notes': getattr(s, 'feedback_notes', None),
            'price_opinion': getattr(s, 'feedback_price_opinion', None),
        })

    return {
        'items': items,
        'total': len(items),
        'this_week': this_week,
        'with_feedback': with_feedback,
        'interest_counts': interest_counts,
    }


# --------------------------------------------------------------------------
# offers
# --------------------------------------------------------------------------

def _offers_block(tx):
    try:
        from services.seller_workflow import offer_urgency
    except Exception:
        offer_urgency = None

    try:
        offers = tx.seller_offers.order_by('created_at').all()
    except Exception:
        offers = []

    items = []
    active_count = 0
    for idx, o in enumerate(offers, start=1):
        is_active = (o.status in ACTIVE_OFFER_STATUSES)
        if is_active:
            active_count += 1
        urgency = None
        if offer_urgency:
            try:
                urgency = offer_urgency(o)
            except Exception:
                urgency = None
        items.append({
            'label': f'Offer {idx}',
            'price': _money(o.offer_price),
            'price_raw': float(o.offer_price) if o.offer_price else None,
            'financing': (o.financing_type or '').replace('_', ' ').title() or None,
            'earnest': _money(getattr(o, 'earnest_money', None)),
            'option_days': getattr(o, 'option_period_days', None),
            'close_date': _day(getattr(o, 'proposed_close_date', None)),
            'status': _offer_status_label(o.status),
            'status_key': o.status,
            'is_active': is_active,
            'is_accepted': o.status in ('accepted_primary', 'accepted_backup'),
            'urgency': urgency,
            'net_to_seller': _money(getattr(o, 'net_to_seller_estimate', None)),
        })

    return {
        'items': items,
        'total': len(items),
        'active_count': active_count,
        'has_accepted': any(i['is_accepted'] for i in items),
    }


def _offer_status_label(status):
    return {
        'new': 'New',
        'needs_review': 'In review',
        'reviewing': 'In review',
        'countered': 'Countered',
        'accepted_primary': 'Accepted',
        'accepted_backup': 'Accepted (backup)',
        'declined': 'Declined',
        'withdrawn': 'Withdrawn',
        'expired': 'Expired',
    }.get(status, (status or '').replace('_', ' ').title())


# --------------------------------------------------------------------------
# milestones (under contract)
# --------------------------------------------------------------------------

def _milestones_block(tx):
    contract = _primary_contract(tx)
    if not contract:
        return {'items': [], 'next': None}
    try:
        milestones = contract.milestones.order_by('due_at').all()
    except Exception:
        try:
            milestones = sorted(contract.milestones.all(),
                                key=lambda m: (m.due_at or datetime.max))
        except Exception:
            milestones = []

    today = date.today()
    items = []
    next_item = None
    for m in milestones:
        due = _as_date(m.due_at)
        status = m.status or 'not_started'
        done = status == 'completed'
        na = status == 'not_applicable'
        # Derive due_soon/overdue client-side (server stores these manually only).
        derived = status
        if not done and not na and due:
            delta = (due - today).days
            if delta < 0:
                derived = 'overdue'
            elif delta <= 5:
                derived = 'due_soon'
        item = {
            'title': m.title,
            'due': _full_day(m.due_at),
            'due_short': _day(m.due_at),
            'status': derived,
            'done': done,
            'not_applicable': na,
        }
        items.append(item)
        if next_item is None and not done and not na and due and due >= today:
            next_item = item

    return {'items': items, 'next': next_item}


# --------------------------------------------------------------------------
# documents
# --------------------------------------------------------------------------

def _documents_block(tx, participant):
    email = (participant.display_email or '').strip().lower()
    needs = []
    completed = []
    try:
        docs = tx.documents.all()
    except Exception:
        docs = []

    for doc in docs:
        try:
            sigs = doc.signatures.all()
        except Exception:
            sigs = []
        mine = [s for s in sigs if (s.signer_email or '').strip().lower() == email]
        name = doc.template_name or (doc.template_slug or 'Document').replace('-', ' ').title()

        if doc.status == 'signed':
            # Only show completed docs the client was a party to (or all signed
            # docs on their transaction if we can't tell).
            if mine or not sigs:
                completed.append({
                    'name': name,
                    'doc_id': doc.id,
                    'signed_on': _day(getattr(doc, 'signed_at', None)),
                    'can_view': bool(getattr(doc, 'signed_file_path', None)),
                })
        elif doc.status == 'sent' and mine:
            pending = [s for s in mine if s.status in ('sent', 'viewed')]
            if pending:
                slug = pending[0].docuseal_submitter_slug
                needs.append({
                    'name': name,
                    'doc_id': doc.id,
                    'submitter_slug': slug,
                    'sign_url': f'https://docuseal.com/s/{slug}' if slug else None,
                    'viewed': any(s.status == 'viewed' for s in pending),
                })

    return {
        'needs_signature': needs,
        'completed': completed,
        'needs_count': len(needs),
        'completed_count': len(completed),
    }


# --------------------------------------------------------------------------
# price changes / net proceeds / updates
# --------------------------------------------------------------------------

def _price_changes_block(tx):
    try:
        changes = tx.seller_price_changes.order_by('changed_at').all()
    except Exception:
        changes = []
    out = []
    for c in changes:
        out.append({
            'old': _money(c.old_price),
            'new': _money(c.new_price),
            'date': _day(getattr(c, 'changed_at', None)),
            'reason': c.reason,
        })
    return out


def _net_proceeds_block(tx):
    # Closed: use the closing summary's final net. Under contract: the accepted
    # offer's estimate. Either way this is a number the seller should see.
    contract = _primary_contract(tx)
    if contract:
        summary = getattr(contract, 'closing_summary', None)
        if summary and summary.final_net_proceeds:
            return {'label': 'Net proceeds', 'value': _money(summary.final_net_proceeds), 'estimate': False}
        offer = getattr(contract, 'offer', None)
        if offer and getattr(offer, 'net_to_seller_estimate', None):
            return {'label': 'Estimated net proceeds', 'value': _money(offer.net_to_seller_estimate), 'estimate': True}
    return None


def _updates_block(tx, participant):
    try:
        from models import PortalMessage
        msgs = PortalMessage.query.filter_by(
            transaction_id=tx.id,
            participant_id=participant.id,
            kind='update',
        ).order_by(PortalMessage.created_at.desc()).limit(10).all()
    except Exception:
        msgs = []
    out = []
    for m in msgs:
        author = getattr(m, 'author', None)
        out.append({
            'body': m.body,
            'date': _day(getattr(m, 'created_at', None)),
            'author': (f'{author.first_name}' if author else 'Your agent'),
        })
    return out
