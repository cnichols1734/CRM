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

    if open_reviews or pending_proposals:
        doc = open_reviews[0].document if open_reviews else None
        next_step = {
            'action': 'review_document',
            'summary': (
                'Review the uploaded document and approve the extracted terms '
                'in the side-by-side workspace.'
            ),
            'document_id': open_reviews[0].document_id if open_reviews else None,
            'document_name': (
                (doc.template_name or doc.template_slug) if doc else None
            ),
        }
    elif side == 'seller' and not questionnaire_done:
        next_step = {
            'action': 'complete_questionnaire',
            'summary': (
                'Finish the property questionnaire — it builds the '
                'required-document list for this listing.'
            ),
        }
    elif missing:
        next_step = {
            'action': 'upload_missing_documents',
            'summary': (
                f'{len(missing)} required document(s) still needed: '
                + ', '.join(
                    (d.template_name or d.template_slug or 'document')
                    for d in missing[:5]
                )
            ),
            'missing_documents': [
                {
                    'document_id': d.id,
                    'name': d.template_name or d.template_slug,
                }
                for d in missing[:20]
            ],
        }
    elif overdue:
        next_step = {
            'action': 'resolve_overdue',
            'summary': f'{overdue} checklist deadline(s) are overdue.',
        }
    else:
        next_step = {
            'action': 'none',
            'summary': 'Nothing urgent. Reviews, questionnaire, and required documents are all handled.',
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
