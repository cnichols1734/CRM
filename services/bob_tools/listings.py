"""Seller listing read/write tools shared by B.O.B. and MCP."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation

from models import SellerListingProfile, Transaction, db
from services.bob_tools.context import BobContext, ToolResult
from services.bob_tools.transactions import _load_tx, _user_stub
from services.transaction_auth import CAP_EDIT, CAP_VIEW, require_transaction_access


def list_listings(ctx: BobContext, *, query: str = '', limit: int = 10) -> ToolResult:
    limit = max(1, min(int(limit or 10), 25))
    q = (query or '').strip().lower()
    rows = (
        Transaction.query
        .filter_by(organization_id=ctx.organization_id)
        .order_by(Transaction.updated_at.desc())
        .limit(50)
        .all()
    )
    user = _user_stub(ctx)
    results = []
    for tx in rows:
        type_name = getattr(tx.transaction_type, 'name', '') or ''
        if type_name != 'seller':
            continue
        if not require_transaction_access(tx, CAP_VIEW, user).allowed:
            continue
        hay = ' '.join(filter(None, [tx.street_address or '', tx.city or '', tx.status or ''])).lower()
        if q and q not in hay:
            continue
        profile = SellerListingProfile.query.filter_by(
            transaction_id=tx.id, organization_id=ctx.organization_id,
        ).first()
        results.append({
            'transaction_id': tx.id,
            'address': tx.street_address,
            'city': tx.city,
            'status': tx.status,
            'list_price': str(profile.current_list_price) if profile and profile.current_list_price else None,
            'mls_number': profile.mls_number if profile else None,
        })
        if len(results) >= limit:
            break
    return ToolResult.success(
        f'Found {len(results)} listing(s).',
        {'listings': results, 'count': len(results)},
    )


def get_listing(ctx: BobContext, *, transaction_id: int | None = None) -> ToolResult:
    tx, err = _load_tx(ctx, transaction_id, CAP_VIEW)
    if err:
        return err
    type_name = getattr(tx.transaction_type, 'name', '') or ''
    if type_name != 'seller':
        return ToolResult.failure('That transaction is not a seller listing.')
    profile = SellerListingProfile.query.filter_by(
        transaction_id=tx.id, organization_id=ctx.organization_id,
    ).first()
    extra = dict((profile.extra_data if profile else None) or {})
    return ToolResult.success(
        f'Listing {tx.street_address}.',
        {
            'transaction_id': tx.id,
            'address': tx.full_address,
            'status': tx.status,
            'list_price': str(profile.current_list_price) if profile and profile.current_list_price else None,
            'mls_number': profile.mls_number if profile else None,
            'mls_listing_url': tx.mls_listing_url,
            'go_live_date': profile.go_live_date.isoformat() if profile and profile.go_live_date else None,
            'occupancy_status': profile.occupancy_status if profile else None,
            'listing_description': extra.get('listing_description') or '',
        },
        record_url=f'/transactions/{tx.id}',
    )


def update_listing_fields(
    ctx: BobContext,
    *,
    transaction_id: int,
    list_price=None,
    mls_number: str = '',
    mls_listing_url: str = '',
    go_live_date: str = '',
    occupancy_status: str = '',
    public_showing_instructions: str = '',
) -> ToolResult:
    tx, err = _load_tx(ctx, transaction_id, CAP_EDIT)
    if err:
        return err
    type_name = getattr(tx.transaction_type, 'name', '') or ''
    if type_name != 'seller':
        return ToolResult.failure('Listing fields are only on seller transactions.')
    profile = SellerListingProfile.query.filter_by(
        transaction_id=tx.id, organization_id=ctx.organization_id,
    ).first()
    if profile is None:
        profile = SellerListingProfile(
            organization_id=ctx.organization_id,
            transaction_id=tx.id,
            created_by_id=ctx.user_id,
        )
        db.session.add(profile)
        db.session.flush()

    before = {
        'list_price': str(profile.current_list_price) if profile.current_list_price else None,
        'mls_number': profile.mls_number,
        'mls_listing_url': tx.mls_listing_url,
    }
    if list_price not in (None, ''):
        try:
            profile.current_list_price = Decimal(str(list_price))
            if profile.original_list_price is None:
                profile.original_list_price = profile.current_list_price
        except (InvalidOperation, TypeError, ValueError):
            return ToolResult.failure('list_price must be a number.')
    if mls_number:
        profile.mls_number = mls_number.strip()[:100]
    if mls_listing_url != '':
        from services.portal_service import normalize_mls_listing_url
        try:
            tx.mls_listing_url = normalize_mls_listing_url(mls_listing_url)
        except ValueError as exc:
            return ToolResult.failure(str(exc))
    if go_live_date:
        try:
            profile.go_live_date = datetime.strptime(go_live_date, '%Y-%m-%d').date()
        except ValueError:
            return ToolResult.failure('go_live_date must be YYYY-MM-DD.')
    if occupancy_status:
        profile.occupancy_status = occupancy_status.strip()[:50]
    if public_showing_instructions:
        profile.public_showing_instructions = public_showing_instructions.strip()[:2000]
    db.session.commit()
    return ToolResult.success(
        f'Updated listing fields for {tx.street_address}.',
        {
            'transaction_id': tx.id,
            'before': before,
            'after': {
                'list_price': str(profile.current_list_price) if profile.current_list_price else None,
                'mls_number': profile.mls_number,
                'mls_listing_url': tx.mls_listing_url,
                'go_live_date': profile.go_live_date.isoformat() if profile.go_live_date else None,
            },
        },
        record_url=f'/transactions/{tx.id}',
    )


def preview_update_listing_fields(args: dict, ctx: BobContext) -> dict:
    tx, err = _load_tx(ctx, args.get('transaction_id'), CAP_EDIT)
    if err:
        return {'action': 'update_listing_fields', 'error': err.error}
    profile = SellerListingProfile.query.filter_by(
        transaction_id=tx.id, organization_id=ctx.organization_id,
    ).first()
    return {
        'action': 'update_listing_fields',
        'before': {
            'list_price': str(profile.current_list_price) if profile and profile.current_list_price else None,
            'mls_number': profile.mls_number if profile else None,
            'mls_listing_url': tx.mls_listing_url if tx else None,
        },
        'after': {
            'list_price': args.get('list_price'),
            'mls_number': args.get('mls_number'),
            'mls_listing_url': args.get('mls_listing_url'),
        },
    }


def generate_listing_description(
    ctx: BobContext,
    *,
    transaction_id: int,
    save: bool = False,
) -> ToolResult:
    from config import Config
    from models import TransactionDocument
    from services.listing_description import (
        LISTING_DESCRIPTION_SYSTEM_PROMPT,
        build_listing_description_user_prompt,
        collect_listing_description_facts,
        sanitize_listing_copy,
        web_search_location,
    )
    from services.transaction_helpers import build_listing_info

    tx, err = _load_tx(ctx, transaction_id, CAP_EDIT if save else CAP_VIEW)
    if err:
        return err
    documents = TransactionDocument.query.filter_by(transaction_id=tx.id).all()
    profile = SellerListingProfile.query.filter_by(
        transaction_id=tx.id, organization_id=ctx.organization_id,
    ).first()
    listing_info = build_listing_info(
        documents,
        (tx.extra_data or {}).get('listing_info_overrides') or {},
        transaction=tx,
        listing_profile=profile,
    ) or {}
    facts = collect_listing_description_facts(tx, listing_info, documents)
    draft = ''
    if Config.OPENAI_API_KEY:
        from services.ai_service import generate_ai_response
        draft = sanitize_listing_copy(generate_ai_response(
            system_prompt=LISTING_DESCRIPTION_SYSTEM_PROMPT,
            user_prompt=build_listing_description_user_prompt(facts),
            temperature=0.5,
            reasoning_effort='low',
            web_search=True,
            user_location=web_search_location(facts),
        ))
        if save:
            if profile is None:
                profile = SellerListingProfile(
                    organization_id=ctx.organization_id,
                    transaction_id=tx.id,
                    created_by_id=ctx.user_id,
                    extra_data={},
                )
                db.session.add(profile)
            extra = dict(profile.extra_data or {})
            extra['listing_description'] = draft
            extra['listing_description_source'] = 'ai'
            profile.extra_data = extra
            db.session.commit()
    return ToolResult.success(
        'Drafted listing remarks.' if draft else 'Collected listing facts. AI drafting is not configured.',
        {
            'transaction_id': tx.id,
            'draft': draft,
            'facts': facts,
            'saved': bool(save and draft),
        },
        record_url=f'/transactions/{tx.id}',
    )
