"""Read/write BOB tools for the virtual transaction coordinator."""

from __future__ import annotations

from datetime import datetime, timedelta

from models import (
    Transaction,
    TransactionDocument,
    TransactionParticipant,
    TransactionRequirement,
    db,
)
from services.bob_tools.context import BobContext, ToolResult
from services.transaction_auth import (
    CAP_EDIT,
    CAP_VIEW,
    get_transaction_for_user,
    require_transaction_access,
)


def _user_stub(ctx: BobContext):
    class _U:
        id = ctx.user_id
        organization_id = ctx.organization_id
        org_role = ctx.org_role
        is_authenticated = True

    return _U()


def _load_tx(ctx: BobContext, transaction_id: int | None, capability: str = CAP_VIEW):
    tid = transaction_id or ctx.active_transaction_id
    if not tid:
        return None, ToolResult.failure(
            'No transaction selected. Pass transaction_id or open a transaction page.'
        )
    tx, decision = get_transaction_for_user(tid, _user_stub(ctx), capability)
    if not tx:
        return None, ToolResult.failure(
            'Transaction not found or not authorized.'
            if decision.reason != 'not_found'
            else 'Transaction not found.'
        )
    return tx, None


def search_transactions(ctx: BobContext, *, query: str = '', limit: int = 10) -> ToolResult:
    q = (query or '').strip()
    limit = max(1, min(int(limit or 10), 25))
    filters = [Transaction.organization_id == ctx.organization_id]
    rows = Transaction.query.filter(*filters).order_by(Transaction.updated_at.desc()).limit(50).all()
    user = _user_stub(ctx)
    results = []
    for tx in rows:
        if not require_transaction_access(tx, CAP_VIEW, user).allowed:
            continue
        hay = ' '.join(filter(None, [
            tx.street_address or '',
            tx.city or '',
            getattr(tx.transaction_type, 'name', '') or '',
            tx.status or '',
        ])).lower()
        if q and q.lower() not in hay:
            continue
        results.append({
            'transaction_id': tx.id,
            'address': tx.street_address,
            'city': tx.city,
            'status': tx.status,
            'type': getattr(tx.transaction_type, 'name', None),
        })
        if len(results) >= limit:
            break
    return ToolResult.success(
        f'Found {len(results)} transaction(s).',
        {'transactions': results, 'count': len(results)},
    )


def get_transaction_summary(ctx: BobContext, *, transaction_id: int | None = None) -> ToolResult:
    tx, err = _load_tx(ctx, transaction_id)
    if err:
        return err
    reqs = TransactionRequirement.query.filter_by(
        organization_id=ctx.organization_id,
        transaction_id=tx.id,
    ).all()
    overdue = [r for r in reqs if r.timing_state == 'overdue' or (
        r.due_at and r.work_status not in ('completed', 'waived', 'cancelled')
        and r.due_at < datetime.utcnow()
    )]
    return ToolResult.success(
        f'Summary for {tx.street_address or f"transaction {tx.id}"}.',
        {
            'transaction_id': tx.id,
            'address': tx.street_address,
            'status': tx.status,
            'type': getattr(tx.transaction_type, 'name', None),
            'requirement_count': len(reqs),
            'overdue_count': len(overdue),
            'sources': {
                'status': 'crm',
                'requirements': 'transaction_requirement',
            },
        },
    )


def list_parties(ctx: BobContext, *, transaction_id: int | None = None) -> ToolResult:
    tx, err = _load_tx(ctx, transaction_id)
    if err:
        return err
    parties = []
    for p in TransactionParticipant.query.filter_by(
        transaction_id=tx.id,
        organization_id=ctx.organization_id,
    ).all():
        parties.append({
            'participant_id': p.id,
            'role': p.role,
            'name': p.display_name,
            'email': p.display_email,
        })
    return ToolResult.success(
        f'{len(parties)} party record(s).',
        {'transaction_id': tx.id, 'parties': parties},
    )


def list_documents(ctx: BobContext, *, transaction_id: int | None = None) -> ToolResult:
    tx, err = _load_tx(ctx, transaction_id)
    if err:
        return err
    docs = TransactionDocument.query.filter_by(
        transaction_id=tx.id,
        organization_id=ctx.organization_id,
    ).order_by(TransactionDocument.created_at.desc()).limit(50).all()
    payload = [{
        'document_id': d.id,
        'name': d.template_name or d.template_slug,
        'status': d.status,
        'is_placeholder': bool(getattr(d, 'is_placeholder', False)),
        'extraction_status': getattr(d, 'extraction_status', None),
        'sensitivity_class': getattr(d, 'sensitivity_class', None),
    } for d in docs]
    return ToolResult.success(
        f'{len(payload)} document(s).',
        {'transaction_id': tx.id, 'documents': payload},
    )


def get_upcoming_deadlines(
    ctx: BobContext,
    *,
    transaction_id: int | None = None,
    days: int = 14,
) -> ToolResult:
    tx, err = _load_tx(ctx, transaction_id)
    if err:
        return err
    days = max(1, min(int(days or 14), 90))
    now = datetime.utcnow()
    until = now + timedelta(days=days)
    rows = TransactionRequirement.query.filter(
        TransactionRequirement.organization_id == ctx.organization_id,
        TransactionRequirement.transaction_id == tx.id,
        TransactionRequirement.due_at.isnot(None),
        TransactionRequirement.due_at >= now,
        TransactionRequirement.due_at <= until,
        TransactionRequirement.work_status.notin_(('completed', 'waived', 'cancelled')),
    ).order_by(TransactionRequirement.due_at.asc()).all()
    items = [{
        'requirement_id': r.id,
        'key': r.requirement_key,
        'title': r.title,
        'due_at': r.due_at.isoformat() if r.due_at else None,
        'work_status': r.work_status,
        'timing_state': r.timing_state,
        'risk_level': r.risk_level,
        'deadline_rule_version': r.deadline_rule_version,
        'source': 'calculated' if r.deadline_rule_version else 'crm',
    } for r in rows]
    return ToolResult.success(
        f'{len(items)} upcoming deadline(s) in {days} days.',
        {'transaction_id': tx.id, 'deadlines': items},
    )


def get_overdue_work(ctx: BobContext, *, transaction_id: int | None = None) -> ToolResult:
    tx, err = _load_tx(ctx, transaction_id)
    if err:
        return err
    now = datetime.utcnow()
    rows = TransactionRequirement.query.filter(
        TransactionRequirement.organization_id == ctx.organization_id,
        TransactionRequirement.transaction_id == tx.id,
        TransactionRequirement.work_status.notin_(('completed', 'waived', 'cancelled')),
        TransactionRequirement.due_at.isnot(None),
        TransactionRequirement.due_at < now,
    ).order_by(TransactionRequirement.due_at.asc()).all()
    items = [{
        'requirement_id': r.id,
        'key': r.requirement_key,
        'title': r.title,
        'due_at': r.due_at.isoformat() if r.due_at else None,
        'work_status': r.work_status,
        'risk_level': r.risk_level,
    } for r in rows]
    return ToolResult.success(
        f'{len(items)} overdue item(s).',
        {'transaction_id': tx.id, 'overdue': items},
    )


def closing_readiness_summary(
    ctx: BobContext,
    *,
    transaction_id: int | None = None,
) -> ToolResult:
    tx, err = _load_tx(ctx, transaction_id)
    if err:
        return err
    reqs = TransactionRequirement.query.filter_by(
        organization_id=ctx.organization_id,
        transaction_id=tx.id,
    ).all()
    open_items = [r for r in reqs if r.work_status not in ('completed', 'waived', 'cancelled')]
    blockers = [r for r in open_items if (r.risk_level or '') in ('high', 'critical')]
    return ToolResult.success(
        'Closing readiness summary.',
        {
            'transaction_id': tx.id,
            'address': tx.street_address,
            'total_requirements': len(reqs),
            'open_count': len(open_items),
            'blocker_count': len(blockers),
            'blockers': [{
                'requirement_id': r.id,
                'title': r.title,
                'work_status': r.work_status,
                'due_at': r.due_at.isoformat() if r.due_at else None,
            } for r in blockers[:20]],
            'ready': len(blockers) == 0 and len(open_items) == 0,
        },
    )


def identify_missing_documents(
    ctx: BobContext,
    *,
    transaction_id: int | None = None,
) -> ToolResult:
    tx, err = _load_tx(ctx, transaction_id)
    if err:
        return err
    placeholders = TransactionDocument.query.filter_by(
        transaction_id=tx.id,
        organization_id=ctx.organization_id,
        is_placeholder=True,
    ).all()
    missing_docs = [{
        'document_id': d.id,
        'name': d.template_name or d.template_slug,
        'status': d.status,
        'kind': 'placeholder',
    } for d in placeholders if d.status in ('pending', 'draft', None, '')]
    waiting_reqs = TransactionRequirement.query.filter_by(
        organization_id=ctx.organization_id,
        transaction_id=tx.id,
        work_status='waiting',
    ).all()
    missing_reqs = [{
        'requirement_id': r.id,
        'key': r.requirement_key,
        'title': r.title,
        'kind': 'requirement',
    } for r in waiting_reqs]
    return ToolResult.success(
        f'{len(missing_docs)} missing placeholder(s), {len(missing_reqs)} waiting requirement(s).',
        {
            'transaction_id': tx.id,
            'missing_documents': missing_docs,
            'waiting_requirements': missing_reqs,
        },
    )


def get_next_step(
    ctx: BobContext,
    *,
    transaction_id: int | None = None,
) -> ToolResult:
    """The single most useful action right now, same priority as the workspace banner."""
    from models import DocumentReviewReport, TransactionChangeProposal

    tx, err = _load_tx(ctx, transaction_id)
    if err:
        return err

    open_reviews = (
        DocumentReviewReport.query.filter_by(
            transaction_id=tx.id,
            organization_id=ctx.organization_id,
        )
        .filter(DocumentReviewReport.status.in_([
            DocumentReviewReport.STATUS_OPEN,
            DocumentReviewReport.STATUS_ACKNOWLEDGED,
        ]))
        .order_by(DocumentReviewReport.created_at.desc())
        .all()
    )
    pending_proposals = TransactionChangeProposal.query.filter_by(
        transaction_id=tx.id,
        organization_id=ctx.organization_id,
        status='pending',
    ).count()

    side = (getattr(tx.transaction_type, 'name', '') or '').lower()
    questionnaire_done = bool(tx.intake_data)

    placeholders = TransactionDocument.query.filter_by(
        transaction_id=tx.id,
        organization_id=ctx.organization_id,
        is_placeholder=True,
    ).all()
    missing = [
        d for d in placeholders if d.status in ('pending', 'draft', None, '')
    ]

    overdue = 0
    for req in TransactionRequirement.query.filter_by(
        organization_id=ctx.organization_id,
        transaction_id=tx.id,
    ).all():
        if (
            req.work_status not in ('completed', 'not_applicable', 'superseded')
            and req.due_at
            and req.due_at < datetime.utcnow()
        ):
            overdue += 1

    open_prep = None
    if side == 'seller' and (tx.status or '') == 'preparing_to_list':
        from services.listing_prep_checklist import (
            first_open_listing_prep_item,
            listing_prep_groups,
        )
        open_prep = first_open_listing_prep_item(listing_prep_groups(tx))

    if open_prep:
        next_step = {
            'action': 'listing_prep',
            'summary': f'Next on the listing checklist: {open_prep["title"]}.',
            'item_key': open_prep.get('key'),
        }
    elif side == 'seller' and not questionnaire_done:
        next_step = {
            'action': 'complete_questionnaire',
            'summary': 'Finish the property questionnaire — HOA, year built, and districts.',
        }
    elif overdue:
        next_step = {
            'action': 'resolve_overdue',
            'summary': f'{overdue} checklist deadline(s) are overdue.',
        }
    else:
        next_step = {
            'action': 'none',
            'summary': 'Nothing urgent right now.',
        }

    return ToolResult.success(
        next_step['summary'],
        {
            'transaction_id': tx.id,
            'address': tx.street_address,
            'next_step': next_step,
            'open_review_count': len(open_reviews),
            'pending_proposal_count': pending_proposals,
            'missing_document_count': len(missing),
            'overdue_count': overdue,
            'questionnaire_completed': questionnaire_done,
        },
    )


def add_transaction_note(
    ctx: BobContext,
    *,
    note: str,
    transaction_id: int | None = None,
) -> ToolResult:
    tx, err = _load_tx(ctx, transaction_id, CAP_EDIT)
    if err:
        return err
    text = (note or '').strip()
    if not text:
        return ToolResult.failure('Note text is required.')
    text = text[:4000]
    extra = dict(tx.extra_data or {})
    notes = list(extra.get('bob_notes') or [])
    stamp = datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')
    notes.append({'at': stamp, 'user_id': ctx.user_id, 'text': text})
    extra['bob_notes'] = notes[-50:]
    tx.extra_data = extra
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(tx, 'extra_data')
    db.session.commit()
    return ToolResult.success(
        'Note added to transaction.',
        {'transaction_id': tx.id, 'note_preview': text[:200]},
    )


def escalate_transaction_risk(
    ctx: BobContext,
    *,
    requirement_id: int,
    risk_level: str,
    reason: str = '',
) -> ToolResult:
    from services.requirements_service import RequirementsService

    user = _user_stub(ctx)
    req = TransactionRequirement.query.filter_by(
        id=requirement_id,
        organization_id=ctx.organization_id,
    ).first()
    if not req:
        return ToolResult.failure('Requirement not found.')
    tx = Transaction.query.filter_by(
        id=req.transaction_id,
        organization_id=ctx.organization_id,
    ).first()
    if not tx or not require_transaction_access(tx, CAP_EDIT, user).allowed:
        return ToolResult.failure('Not authorized.')
    level = (risk_level or '').strip().lower()
    if level not in ('low', 'medium', 'high', 'critical'):
        return ToolResult.failure('risk_level must be low|medium|high|critical.')
    RequirementsService.update_risk_level(
        req.id, level, actor_id=ctx.user_id, reason=reason,
    )
    db.session.commit()
    return ToolResult.success(
        f'Risk set to {level}.',
        {'requirement_id': req.id, 'risk_level': level},
    )


def select_transaction_context(
    ctx: BobContext,
    *,
    transaction_id: int,
) -> ToolResult:
    """Confirm a transaction selection for Telegram disambiguation."""
    tx, err = _load_tx(ctx, transaction_id, CAP_VIEW)
    if err:
        return err

    # Persist on the messaging channel so later turns keep this file in context.
    if ctx.surface == 'bob_telegram':
        from models import AgentMessagingChannel
        channel = AgentMessagingChannel.query.filter_by(
            user_id=ctx.user_id,
            organization_id=ctx.organization_id,
            provider=AgentMessagingChannel.PROVIDER_TELEGRAM,
            disabled_at=None,
        ).first()
        if channel is not None:
            channel.selected_transaction_id = tx.id
            db.session.commit()

    return ToolResult.success(
        f'Selected {tx.street_address or f"transaction {tx.id}"}.',
        {
            'selected_transaction_id': tx.id,
            'address': tx.street_address,
            'status': tx.status,
        },
    )


def compare_offers(
    ctx: BobContext,
    *,
    transaction_id: int | None = None,
    offer_ids: list | None = None,
    include_terminal: bool = False,
) -> ToolResult:
    """Read-only side-by-side offer term comparison for authorized agents."""
    from services.offer_compare import OfferCompareService

    tx, err = _load_tx(ctx, transaction_id, CAP_VIEW)
    if err:
        return err

    ids = None
    if offer_ids:
        try:
            ids = [int(x) for x in offer_ids]
        except (TypeError, ValueError):
            return ToolResult.failure('offer_ids must be integers.')

    result = OfferCompareService.compare_offers(
        tx,
        offer_ids=ids,
        include_terminal=bool(include_terminal),
    )
    return ToolResult.success(result['summary'], result)


TX_STATUSES = (
    'preparing_to_list', 'showing', 'active', 'under_contract', 'closed', 'cancelled',
)
PARTY_ROLES = (
    'seller', 'co_seller', 'buyer', 'co_buyer', 'listing_agent', 'buyers_agent',
    'title_company', 'lender', 'transaction_coordinator', 'landlord', 'tenant',
    'referral_client',
)
OFFER_REVIEW_STATUSES = ('reviewing', 'needs_review')
REQ_STATUSES = (
    'pending', 'in_progress', 'waiting', 'completed', 'waived', 'cancelled',
    'not_applicable',
)


def create_transaction(
    ctx: BobContext,
    *,
    transaction_type: str,
    street_address: str,
    contact_id: int,
    city: str = '',
    state: str = 'TX',
    zip_code: str = '',
    county: str = '',
) -> ToolResult:
    from models import TransactionType
    from services.bob_tools.common import get_contact_for_write
    from services import audit_service

    address = (street_address or '').strip()
    if not address:
        return ToolResult.failure('street_address is required.')
    type_name = (transaction_type or '').strip().lower()
    tx_type = TransactionType.query.filter_by(
        organization_id=ctx.organization_id, name=type_name, is_active=True,
    ).first()
    if tx_type is None:
        return ToolResult.failure('transaction_type must be seller, buyer, landlord, tenant, or referral.')
    try:
        contact = get_contact_for_write(ctx, contact_id)
    except Exception as exc:
        return ToolResult.failure(str(exc))
    if not contact.first_name or not contact.last_name:
        return ToolResult.failure('The contact needs a first and last name first.')
    if not contact.email:
        return ToolResult.failure('The contact needs an email address first.')

    status = 'showing' if tx_type.name in {'buyer', 'tenant'} else 'preparing_to_list'
    tx = Transaction(
        organization_id=ctx.organization_id,
        created_by_id=ctx.user_id,
        transaction_type_id=tx_type.id,
        street_address=address[:200],
        city=(city or '').strip()[:100] or None,
        state=(state or 'TX').strip()[:50] or 'TX',
        zip_code=(zip_code or '').strip()[:20] or None,
        county=(county or '').strip()[:100] or None,
        status=status,
    )
    db.session.add(tx)
    db.session.flush()

    role_map = {
        'seller': 'seller', 'buyer': 'buyer', 'landlord': 'landlord',
        'tenant': 'tenant', 'referral': 'referral_client',
    }
    db.session.add(TransactionParticipant(
        organization_id=ctx.organization_id,
        transaction_id=tx.id,
        contact_id=contact.id,
        role=role_map.get(tx_type.name, 'client'),
        is_primary=True,
    ))
    agent_role = 'listing_agent' if tx_type.name in {'seller', 'landlord'} else 'buyers_agent'
    if tx_type.name in {'seller', 'landlord', 'buyer', 'tenant'}:
        db.session.add(TransactionParticipant(
            organization_id=ctx.organization_id,
            transaction_id=tx.id,
            user_id=ctx.user_id,
            role=agent_role,
            is_primary=True,
        ))
    audit_service.log_transaction_created(tx, actor_id=ctx.user_id)
    if tx_type.name == 'seller':
        from services.listing_prep_checklist import seed_listing_prep_checklist
        seed_listing_prep_checklist(tx, ctx.organization_id, actor_id=ctx.user_id)
    db.session.commit()
    return ToolResult.success(
        f'Created {tx_type.name} transaction at {tx.street_address}.',
        {
            'transaction_id': tx.id,
            'address': tx.street_address,
            'status': tx.status,
            'type': tx_type.name,
        },
        record_url=f'/transactions/{tx.id}',
    )


def preview_create_transaction(args: dict, ctx: BobContext) -> dict:
    return {
        'action': 'create_transaction',
        'after': {
            'street_address': args.get('street_address'),
            'transaction_type': args.get('transaction_type'),
            'city': args.get('city'),
            'contact_id': args.get('contact_id'),
        },
    }


def update_transaction_status(
    ctx: BobContext,
    *,
    transaction_id: int,
    status: str,
) -> ToolResult:
    tx, err = _load_tx(ctx, transaction_id, CAP_EDIT)
    if err:
        return err
    new_status = (status or '').strip().lower()
    if new_status not in TX_STATUSES:
        return ToolResult.failure(f'status must be one of: {", ".join(TX_STATUSES)}.')
    old = tx.status
    tx.status = new_status
    db.session.add(_status_audit(ctx, tx, old, new_status))
    db.session.commit()
    return ToolResult.success(
        f'Status updated from {old} to {new_status}.',
        {'transaction_id': tx.id, 'old_status': old, 'status': new_status},
        record_url=f'/transactions/{tx.id}',
    )


def preview_update_transaction_status(args: dict, ctx: BobContext) -> dict:
    tx, err = _load_tx(ctx, args.get('transaction_id'), CAP_EDIT)
    if err:
        return {'action': 'update_transaction_status', 'error': err.error}
    return {
        'action': 'update_transaction_status',
        'before': {'status': tx.status},
        'after': {'status': args.get('status')},
    }


def add_transaction_party(
    ctx: BobContext,
    *,
    transaction_id: int,
    role: str,
    contact_id: int | None = None,
    name: str = '',
    email: str = '',
    phone: str = '',
    company: str = '',
) -> ToolResult:
    from services.bob_tools.common import get_contact_for_read
    from services import audit_service

    tx, err = _load_tx(ctx, transaction_id, CAP_EDIT)
    if err:
        return err
    role_name = (role or '').strip().lower()
    if role_name not in PARTY_ROLES:
        return ToolResult.failure(f'role must be one of: {", ".join(PARTY_ROLES)}.')
    contact = None
    if contact_id:
        try:
            contact = get_contact_for_read(ctx, contact_id)
        except Exception as exc:
            return ToolResult.failure(str(exc))
    display_name = (name or '').strip()
    if contact:
        display_name = display_name or f'{contact.first_name} {contact.last_name}'.strip()
    if not display_name and not contact:
        return ToolResult.failure('Pass contact_id or a name for the party.')
    party = TransactionParticipant(
        organization_id=ctx.organization_id,
        transaction_id=tx.id,
        contact_id=contact.id if contact else None,
        role=role_name,
        name=display_name[:200] if not contact else None,
        email=(email or (contact.email if contact else '') or '')[:200] or None,
        phone=(phone or (contact.phone if contact else '') or '')[:20] or None,
        company=(company or '').strip()[:200] or None,
        is_primary=False,
    )
    db.session.add(party)
    db.session.flush()
    audit_service.log_participant_added(tx, party, actor_id=ctx.user_id)
    db.session.commit()
    return ToolResult.success(
        f'Added {role_name} to the transaction.',
        {
            'transaction_id': tx.id,
            'participant_id': party.id,
            'role': role_name,
            'name': display_name,
        },
        record_url=f'/transactions/{tx.id}',
    )


def _load_offer(ctx: BobContext, offer_id: int, capability: str = CAP_VIEW):
    from models import SellerOffer
    offer = SellerOffer.query.filter_by(
        id=offer_id, organization_id=ctx.organization_id,
    ).first()
    if offer is None:
        return None, ToolResult.failure('Offer not found.')
    tx, err = _load_tx(ctx, offer.transaction_id, capability)
    if err:
        return None, err
    return offer, None


def create_offer(
    ctx: BobContext,
    *,
    transaction_id: int,
    buyer_names: str = '',
    offer_price=None,
    financing_type: str = '',
    earnest_money=None,
    option_fee=None,
    option_period_days=None,
    proposed_close_date: str = '',
) -> ToolResult:
    from decimal import Decimal, InvalidOperation
    from models import SellerOffer
    from services.seller_workflow import create_offer_activity

    tx, err = _load_tx(ctx, transaction_id, CAP_EDIT)
    if err:
        return err
    names = (buyer_names or '').strip()
    if not names:
        return ToolResult.failure('buyer_names is required.')

    def _money(value):
        if value in (None, ''):
            return None
        try:
            return Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            return None

    close = None
    if proposed_close_date:
        try:
            close = datetime.strptime(proposed_close_date, '%Y-%m-%d').date()
        except ValueError:
            return ToolResult.failure('proposed_close_date must be YYYY-MM-DD.')

    offer = SellerOffer(
        organization_id=ctx.organization_id,
        transaction_id=tx.id,
        created_by_id=ctx.user_id,
        buyer_names=names[:500],
        offer_price=_money(offer_price),
        financing_type=(financing_type or '').strip()[:100] or None,
        earnest_money=_money(earnest_money),
        option_fee=_money(option_fee),
        option_period_days=int(option_period_days) if option_period_days not in (None, '') else None,
        proposed_close_date=close,
        creation_source='manual_entry',
        status='new',
    )
    db.session.add(offer)
    db.session.flush()
    create_offer_activity(offer, 'offer_created', 'Offer entered', actor_id=ctx.user_id)
    db.session.commit()
    return ToolResult.success(
        f'Offer {offer.id} recorded for {tx.street_address}.',
        {'offer_id': offer.id, 'transaction_id': tx.id, 'status': offer.status},
        record_url=f'/transactions/{tx.id}',
    )


def preview_create_offer(args: dict, ctx: BobContext) -> dict:
    return {
        'action': 'create_offer',
        'after': {
            'transaction_id': args.get('transaction_id'),
            'buyer_names': args.get('buyer_names'),
            'offer_price': args.get('offer_price'),
        },
    }


def review_offer(ctx: BobContext, *, offer_id: int, status: str = 'reviewing') -> ToolResult:
    from services.seller_workflow import create_offer_activity

    offer, err = _load_offer(ctx, offer_id, CAP_EDIT)
    if err:
        return err
    new_status = (status or 'reviewing').strip().lower()
    if new_status not in OFFER_REVIEW_STATUSES:
        return ToolResult.failure('status must be reviewing or needs_review.')
    old = offer.status
    offer.status = new_status
    create_offer_activity(
        offer, 'offer_reviewed', f'Offer marked {new_status}', actor_id=ctx.user_id,
    )
    db.session.commit()
    return ToolResult.success(
        f'Offer {offer.id} moved from {old} to {new_status}.',
        {'offer_id': offer.id, 'old_status': old, 'status': new_status},
        record_url=f'/transactions/{offer.transaction_id}',
    )


def accept_offer(ctx: BobContext, *, offer_id: int, as_backup: bool = False) -> ToolResult:
    from services.seller_workflow import create_offer_activity

    offer, err = _load_offer(ctx, offer_id, CAP_EDIT)
    if err:
        return err
    new_status = 'accepted_backup' if as_backup else 'accepted_primary'
    old = offer.status
    offer.status = new_status
    create_offer_activity(
        offer, 'offer_accepted', f'Offer marked {new_status}', actor_id=ctx.user_id,
    )
    db.session.commit()
    return ToolResult.success(
        f'Offer {offer.id} marked {new_status}. Contract bootstrap still happens in the app.',
        {'offer_id': offer.id, 'old_status': old, 'status': new_status},
        record_url=f'/transactions/{offer.transaction_id}',
    )


def preview_accept_offer(args: dict, ctx: BobContext) -> dict:
    offer, err = _load_offer(ctx, args.get('offer_id'), CAP_EDIT)
    if err:
        return {'action': 'accept_offer', 'error': err.error}
    return {
        'action': 'accept_offer',
        'before': {'status': offer.status},
        'after': {'status': 'accepted_backup' if args.get('as_backup') else 'accepted_primary'},
    }


def expire_offer(ctx: BobContext, *, offer_id: int) -> ToolResult:
    from services.seller_workflow import ACTIVE_OFFER_STATUSES, create_offer_activity

    offer, err = _load_offer(ctx, offer_id, CAP_EDIT)
    if err:
        return err
    if offer.status not in ACTIVE_OFFER_STATUSES and offer.status != 'expired':
        return ToolResult.failure(f'Offer {offer.id} is {offer.status} and cannot be expired.')
    if offer.status == 'expired':
        return ToolResult.success(
            f'Offer {offer.id} is already expired.',
            {'offer_id': offer.id, 'status': 'expired'},
        )
    now = datetime.utcnow()
    offer.status = 'expired'
    offer.expired_at = now
    create_offer_activity(offer, 'expired', 'Offer expired', actor_id=ctx.user_id)
    db.session.commit()
    return ToolResult.success(
        f'Offer {offer.id} expired.',
        {'offer_id': offer.id, 'status': 'expired'},
        record_url=f'/transactions/{offer.transaction_id}',
    )


def complete_requirement(ctx: BobContext, *, requirement_id: int) -> ToolResult:
    return update_requirement_status(ctx, requirement_id=requirement_id, work_status='completed')


def update_requirement_status(
    ctx: BobContext,
    *,
    requirement_id: int,
    work_status: str,
) -> ToolResult:
    from services.requirements_service import RequirementsService

    status = (work_status or '').strip().lower()
    if status not in REQ_STATUSES:
        return ToolResult.failure(f'work_status must be one of: {", ".join(REQ_STATUSES)}.')
    req = TransactionRequirement.query.filter_by(
        id=requirement_id, organization_id=ctx.organization_id,
    ).first()
    if req is None:
        return ToolResult.failure('Requirement not found.')
    tx, err = _load_tx(ctx, req.transaction_id, CAP_EDIT)
    if err:
        return err
    old = req.work_status
    RequirementsService.update_work_status(req.id, status, actor_id=ctx.user_id)
    db.session.commit()
    return ToolResult.success(
        f'Requirement {req.id} moved from {old} to {status}.',
        {
            'requirement_id': req.id,
            'transaction_id': tx.id,
            'old_status': old,
            'work_status': status,
            'title': req.title,
        },
        record_url=f'/transactions/{tx.id}',
    )


def _status_audit(ctx, tx, old, new_status):
    from models import AuditEvent
    return AuditEvent(
        organization_id=ctx.organization_id,
        transaction_id=tx.id,
        actor_id=ctx.user_id,
        event_type='transaction_status_changed',
        description=f'Status changed: {old} → {new_status}',
        event_data={'old_status': old, 'new_status': new_status},
        source='app',
    )
