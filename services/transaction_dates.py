"""Stage-specific client dates shared by the portal, iOS API and calendar feed."""
from datetime import datetime
from types import SimpleNamespace
from sqlalchemy import select
from models import (SellerAcceptedContract, SellerContractMilestone,
                    SellerListingProfile, Transaction, TransactionDocument, TransactionType)
from services.deadline_recompute import parse_date
from services.transaction_helpers import build_listing_info

LISTING_DATE_LABELS = {'go_live_date': 'Go-live date', 'listing_start_date': 'Listing agreement starts',
                       'listing_end_date': 'Listing agreement expires'}
SOURCE_LABELS = {'override': 'Set by your agent', 'listing_profile': 'Listing plan',
    'listing_agreement': 'Listing agreement', 'manual': 'Set by your agent',
    'calculated': 'Calculated from contract terms', 'ai_extracted': 'From contract documents'}


def transaction_date_rows(session, org_id, transaction_id):
    # Persisted values let flush listeners compare before/after snapshots.
    connection = session.connection()
    tx = connection.execute(select(Transaction.__table__).where(
        Transaction.id == transaction_id, Transaction.organization_id == org_id)).mappings().first()
    if not tx or tx['status'] == 'cancelled':
        return []
    contracts = connection.execute(select(SellerAcceptedContract.__table__).where(
        SellerAcceptedContract.organization_id == org_id,
        SellerAcceptedContract.transaction_id == transaction_id,
        SellerAcceptedContract.position == 'primary',
        SellerAcceptedContract.status.in_(['active', 'closed'] if tx['status'] == 'closed' else ['active']))).mappings().all()
    if contracts:
        contract = min(contracts, key=lambda c: (c['status'] != 'active', -c['id']))
        rows = connection.execute(select(SellerContractMilestone.__table__).where(
            SellerContractMilestone.organization_id == org_id,
            SellerContractMilestone.transaction_id == transaction_id,
            SellerContractMilestone.accepted_contract_id == contract['id'])).mappings().all()
        return [dict(r, scope='contract', source_label=SOURCE_LABELS.get(r['source'], 'Contract schedule'))
                for r in rows if not (r['source_data'] or {}).get('removed_at')
                and not (r['source_data'] or {}).get('duplicate_of') and r['status'] != 'not_applicable']
    if tx['status'] in ('under_contract', 'pending', 'closed'):
        return []
    side = connection.execute(select(TransactionType.name).where(TransactionType.id == tx['transaction_type_id'])).scalar()
    if side != 'seller':
        return []
    docs = connection.execute(select(TransactionDocument.__table__).where(
        TransactionDocument.transaction_id == transaction_id,
        TransactionDocument.organization_id == org_id).order_by(TransactionDocument.id)).mappings().all()
    profile = connection.execute(select(SellerListingProfile.__table__).where(
        SellerListingProfile.transaction_id == transaction_id,
        SellerListingProfile.organization_id == org_id)).mappings().first()
    info = build_listing_info([SimpleNamespace(**d) for d in docs],
        (tx['extra_data'] or {}).get('listing_info_overrides'),
        listing_profile=SimpleNamespace(**profile) if profile else None) or {}
    rows = []
    for key, title in LISTING_DATE_LABELS.items():
        day = listing_date(info.get(key))
        if not day:
            continue
        rows.append(dict(id='listing:' + key, milestone_key=key, accepted_contract_id='listing',
            title=title, due_at=datetime.combine(day, datetime.min.time()), status='not_started',
            source=info.get('_sources', {}).get(key), scope='listing',
            source_label=SOURCE_LABELS.get(info.get('_sources', {}).get(key), 'Listing schedule'),
            updated_at=tx['updated_at'], created_at=tx['created_at']))
    return rows


def listing_date(value):
    day = parse_date(value)
    if day or not value:
        return day
    try:
        return datetime.strptime(str(value), '%B %d, %Y').date()
    except ValueError:
        return None
