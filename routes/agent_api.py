"""JSON API for the agent iPhone app.

Auth is an agent JWT (typ=agent). Flask-Login cookies are ignored.
Do not reuse /transactions/api/* or the client_portal JWT.
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from functools import wraps
from types import SimpleNamespace

from flask import Blueprint, g, jsonify, request
from sqlalchemy import or_, text
from sqlalchemy.orm import joinedload, selectinload

from feature_flags import get_org_features, org_has_feature
from models import (
    Contact,
    ContactGroup,
    DeviceToken,
    Interaction,
    PortalMessage,
    SellerAcceptedContract,
    SellerContractMilestone,
    SellerListingProfile,
    Task,
    Transaction,
    TransactionDocument,
    TransactionParticipant,
    TransactionType,
    db,
)
from services.agent_auth import (
    JWT_TTL_SECONDS,
    find_login_user,
    issue_agent_jwt,
    load_user_from_jwt,
    login_input_from_body,
    org_is_active,
    serialize_org,
    serialize_user,
)
from services.agent_dashboard import build_dashboard, me_payload
from services.contact_group_service import (
    ContactGroupError,
    list_user_groups,
    resolve_groups_for_owner,
)
from services.controlling_contracts import get_active_primary_contract
from services.device_push import enqueue_portal_push, register_device
from services.portal_service import CLIENT_PORTAL_ROLES, list_client_messages, portal_tracker
from services.tenant_service import org_query_for_id
from services.transaction_auth import (
    CAP_EDIT,
    CAP_VIEW,
    get_transaction_for_user,
    transactions_visible_query,
)

logger = logging.getLogger(__name__)

agent_api_bp = Blueprint('agent_api', __name__, url_prefix='/api/agent/v1')

PORTAL_DATE_FIELDS = frozenset({
    'expected_close_date',
    'go_live_date',
    'effective_date',
    'closing_date',
    'actual_close_date',
})

STATUS_OPTIONS_BY_TYPE = {
    'seller': ['preparing_to_list', 'active', 'under_contract', 'closed', 'cancelled'],
    'buyer': ['showing', 'under_contract', 'closed', 'cancelled'],
    'landlord': ['preparing_to_list', 'active', 'under_contract', 'closed', 'cancelled'],
    'tenant': ['showing', 'under_contract', 'closed', 'cancelled'],
    'referral': ['preparing_to_list', 'active', 'under_contract', 'closed', 'cancelled'],
}

MILESTONE_STATUSES = {
    'not_started', 'waiting', 'due_soon', 'overdue', 'completed', 'not_applicable',
}

ROLE_MAP = {
    'seller': 'seller',
    'buyer': 'buyer',
    'landlord': 'landlord',
    'tenant': 'tenant',
    'referral': 'referral_client',
}

MAX_UPLOAD_BYTES = 25 * 1024 * 1024


def _json_error(message, status=401, code=None):
    payload = {'error': message}
    if code:
        payload['code'] = code
    return jsonify(payload), status


def _set_org_context(org_id: int) -> None:
    try:
        db.session.execute(
            text("SELECT set_config('app.current_org_id', :org_id, false)"),
            {'org_id': str(org_id)},
        )
        db.session.commit()
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass


@agent_api_bp.teardown_request
def _reset_org_context(exc=None):
    try:
        db.session.execute(text('RESET app.current_org_id'))
        db.session.commit()
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass


def _bearer_token():
    header = request.headers.get('Authorization') or ''
    if header.lower().startswith('bearer '):
        return header[7:].strip()
    return ''


def _json_body():
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _contacts_query(user):
    """AgentDesk is personal CRM. Match the web default and /dashboard KPIs."""
    return org_query_for_id(Contact, user.organization_id).filter_by(user_id=user.id)


def _contact_visible(user, contact):
    if contact is None or contact.organization_id != user.organization_id:
        return False
    return contact.user_id == user.id


def agent_jwt_required(view):
    """Authorize from the agent JWT only. A CRM cookie is not enough."""
    @wraps(view)
    def wrapper(*args, **kwargs):
        token = _bearer_token()
        if not token:
            return _json_error('Sign in with your email and password.', 401)
        user, error = load_user_from_jwt(token)
        if error:
            return _json_error(error, 401)
        _set_org_context(user.organization_id)
        g.agent_user = user
        return view(user, *args, **kwargs)

    return wrapper


def transactions_flag_required(view):
    @wraps(view)
    def wrapper(user, *args, **kwargs):
        if not org_has_feature('TRANSACTIONS', user.organization):
            return jsonify({
                'code': 'transactions_required',
                'error': 'Transactions are not on this plan.',
            }), 403
        return view(user, *args, **kwargs)

    return wrapper


def _parse_date(value):
    if value in (None, ''):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(str(value)[:10], '%Y-%m-%d').date()
    except (TypeError, ValueError) as exc:
        raise ValueError('Use YYYY-MM-DD for dates.') from exc


def _parse_datetime(value):
    if value in (None, ''):
        return None
    if isinstance(value, datetime):
        return value
    text_value = str(value).strip()
    for fmt in ('%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S.%f'):
        try:
            return datetime.strptime(text_value[:26], fmt)
        except ValueError:
            continue
    try:
        return datetime.combine(_parse_date(text_value), datetime.min.time())
    except ValueError as exc:
        raise ValueError('Use an ISO datetime.') from exc


def _serialize_contact(contact):
    return {
        'id': contact.id,
        'first_name': contact.first_name,
        'last_name': contact.last_name,
        'email': contact.email,
        'phone': contact.phone,
        'street_address': contact.street_address,
        'city': contact.city,
        'state': contact.state,
        'zip_code': contact.zip_code,
        'notes': contact.notes,
        'potential_commission': float(contact.potential_commission or 0),
        'current_objective': contact.current_objective,
        'group_ids': [g.id for g in (contact.groups or [])],
        'groups': [
            {
                'id': g.id,
                'name': g.name,
                'category': getattr(g, 'category', None),
            }
            for g in (contact.groups or [])
        ],
        'user_id': contact.user_id,
        'created_at': contact.created_at.isoformat() if contact.created_at else None,
    }


def _tx_dates(tx):
    profile = getattr(tx, 'seller_listing_profile', None)
    contract = get_active_primary_contract(tx.id, tx.organization_id)
    return {
        'expected_close_date': (
            tx.expected_close_date.isoformat() if tx.expected_close_date else None
        ),
        'actual_close_date': (
            tx.actual_close_date.isoformat() if tx.actual_close_date else None
        ),
        'go_live_date': (
            profile.go_live_date.isoformat()
            if profile and profile.go_live_date else None
        ),
        'effective_date': (
            contract.effective_date.isoformat()
            if contract and contract.effective_date else None
        ),
        'closing_date': (
            contract.closing_date.isoformat()
            if contract and contract.closing_date else None
        ),
    }


def _serialize_transaction(tx, *, detail=False, participants=None):
    people = participants if participants is not None else tx.participants.all()
    tracker = portal_tracker(tx, rich=detail)
    payload = {
        'id': tx.id,
        'street_address': tx.street_address,
        'city': tx.city,
        'state': tx.state,
        'zip_code': tx.zip_code,
        'county': tx.county,
        'status': tx.status,
        'transaction_type_id': tx.transaction_type_id,
        'transaction_type': (
            tx.transaction_type.name if tx.transaction_type else None
        ),
        'mls_listing_url': tx.mls_listing_url,
        'created_by_id': tx.created_by_id,
        'headline_statement': tracker['headline_statement'],
        'current_stage_index': tracker['current_stage_index'],
        'stages': tracker['stages'],
        'participants': [_serialize_participant(p) for p in people],
        **_tx_dates(tx),
    }
    if detail:
        payload['milestones'] = [
            _serialize_milestone(m)
            for m in tx.seller_contract_milestones.order_by(
                SellerContractMilestone.due_at.asc().nullslast(),
            ).all()
        ]
        payload['documents'] = [
            _serialize_document(doc)
            for doc in tx.documents.order_by(
                TransactionDocument.created_at.desc(),
            ).all()
        ]
    return payload


def _participants_by_transaction(transaction_ids):
    if not transaction_ids:
        return {}
    rows = TransactionParticipant.query.filter(
        TransactionParticipant.transaction_id.in_(transaction_ids),
    ).all()
    grouped = {}
    for row in rows:
        grouped.setdefault(row.transaction_id, []).append(row)
    return grouped


def _ensure_primary_contract(tx, user):
    contract = get_active_primary_contract(tx.id, tx.organization_id)
    if contract is not None:
        return contract
    contract = SellerAcceptedContract(
        organization_id=user.organization_id,
        transaction_id=tx.id,
        created_by_id=user.id,
        position='primary',
        status='active',
    )
    db.session.add(contract)
    db.session.flush()
    return contract


def _serialize_participant(participant):
    return {
        'id': participant.id,
        'role': participant.role,
        'name': participant.display_name,
        'email': participant.display_email,
        'phone': participant.display_phone,
        'contact_id': participant.contact_id,
        'is_primary': bool(participant.is_primary),
    }


def _serialize_milestone(milestone):
    return {
        'id': milestone.id,
        'title': milestone.title,
        'milestone_key': milestone.milestone_key,
        'due_at': milestone.due_at.isoformat() if milestone.due_at else None,
        'status': milestone.status,
        'responsible_party': milestone.responsible_party,
        'source': milestone.source,
        'notes': milestone.notes,
        'completed_at': (
            milestone.completed_at.isoformat() if milestone.completed_at else None
        ),
    }


def _serialize_document(doc):
    title = doc.review_filename or doc.template_name or 'Document'
    return {
        'id': doc.id,
        'title': title,
        'name': doc.template_name,
        'slug': doc.template_slug,
        'status': doc.status,
        'source': doc.document_source,
        'original_filename': getattr(doc, 'signed_original_filename', None),
        'has_file': bool(doc.signed_file_path or doc.source_file_path),
        'created_at': doc.created_at.isoformat() if doc.created_at else None,
    }


def _load_tx(user, transaction_id, capability=CAP_VIEW):
    tx, decision = get_transaction_for_user(
        transaction_id, user=user, capability=capability,
    )
    if not tx:
        status = 404 if decision.reason == 'not_found' else 403
        return None, _json_error('Transaction not found.', status)
    return tx, None


@agent_api_bp.route('/session', methods=['POST'])
def create_session():
    data = _json_body()
    password = data.get('password') or ''
    login_input = login_input_from_body(data)
    user = find_login_user(login_input)
    if not user or not user.check_password(password):
        return _json_error('Invalid email or password.', 401)
    if not org_is_active(user):
        return _json_error('This account is not active.', 401)

    user.last_login = datetime.utcnow()
    db.session.commit()
    _set_org_context(user.organization_id)
    token = issue_agent_jwt(user)
    return jsonify({
        'token': token,
        'token_type': 'Bearer',
        'expires_in': JWT_TTL_SECONDS,
        'user': serialize_user(user),
        'org': serialize_org(user.organization),
        'features': get_org_features(user.organization),
    })


@agent_api_bp.route('/session', methods=['DELETE'])
@agent_jwt_required
def delete_session(user):
    user.bump_session()
    db.session.commit()
    return jsonify({'ok': True})


@agent_api_bp.route('/me', methods=['GET'])
@agent_jwt_required
def get_me(user):
    return jsonify(me_payload(user))


@agent_api_bp.route('/devices', methods=['POST'])
@agent_jwt_required
def register_agent_device(user):
    data = _json_body()
    try:
        row = register_device(
            organization_id=user.organization_id,
            audience=DeviceToken.AUDIENCE_AGENT,
            token=data.get('token'),
            platform=data.get('platform'),
            user_id=user.id,
        )
    except ValueError as exc:
        return _json_error(str(exc), 400)
    return jsonify({
        'ok': True,
        'device_id': row.id,
        'platform': row.platform,
    }), 201


@agent_api_bp.route('/dashboard', methods=['GET'])
@agent_jwt_required
def get_dashboard(user):
    return jsonify(build_dashboard(user))


@agent_api_bp.route('/contacts', methods=['GET'])
@agent_jwt_required
def list_contacts(user):
    query = _contacts_query(user).options(selectinload(Contact.groups))
    q = (request.args.get('q') or '').strip()
    if q:
        like = f'%{q}%'
        query = query.filter(or_(
            Contact.first_name.ilike(like),
            Contact.last_name.ilike(like),
            Contact.email.ilike(like),
            Contact.phone.ilike(like),
        ))
    group_id = request.args.get('group_id', type=int)
    if group_id:
        query = query.join(Contact.groups).filter(ContactGroup.id == group_id)
    page = max(request.args.get('page', 1, type=int) or 1, 1)
    per_page = request.args.get('per_page', 50, type=int) or 50
    per_page = min(max(per_page, 1), 100)
    pagination = query.order_by(Contact.last_name, Contact.first_name).paginate(
        page=page, per_page=per_page, error_out=False,
    )
    return jsonify({
        'contacts': [_serialize_contact(c) for c in pagination.items],
        'page': pagination.page,
        'per_page': pagination.per_page,
        'total': pagination.total,
        'pages': pagination.pages,
    })


@agent_api_bp.route('/contacts', methods=['POST'])
@agent_jwt_required
def create_contact(user):
    data = _json_body()
    first_name = (data.get('first_name') or '').strip()
    last_name = (data.get('last_name') or '').strip()
    if not first_name or not last_name:
        return _json_error('First name and last name are required.', 400)

    org = user.organization
    if org and org.max_contacts is not None:
        current_count = org_query_for_id(Contact, user.organization_id).count()
        if current_count >= org.max_contacts:
            return _json_error('Contact limit reached.', 403)

    contact = Contact(
        organization_id=user.organization_id,
        user_id=user.id,
        created_by_id=user.id,
        first_name=first_name,
        last_name=last_name,
        email=(data.get('email') or '').strip() or None,
        phone=(data.get('phone') or '').strip() or None,
        street_address=(data.get('street_address') or '').strip() or None,
        city=(data.get('city') or '').strip() or None,
        state=(data.get('state') or '').strip() or None,
        zip_code=(data.get('zip_code') or '').strip() or None,
        notes=(data.get('notes') or '').strip() or None,
        potential_commission=data.get('potential_commission') or 5000.00,
        current_objective=(data.get('current_objective') or '').strip() or None,
    )
    try:
        contact.groups = resolve_groups_for_owner(
            user.organization_id,
            user.id,
            data.get('group_ids') or [],
            active_only=True,
        )
    except ContactGroupError as exc:
        return _json_error(exc.message, exc.status_code)
    db.session.add(contact)
    db.session.commit()
    return jsonify({'contact': _serialize_contact(contact)}), 201


@agent_api_bp.route('/contacts/<int:contact_id>', methods=['GET'])
@agent_jwt_required
def get_contact(user, contact_id):
    contact = org_query_for_id(Contact, user.organization_id).filter_by(
        id=contact_id,
    ).first()
    if not _contact_visible(user, contact):
        return _json_error('Contact not found.', 404)
    return jsonify({'contact': _serialize_contact(contact)})


@agent_api_bp.route('/contacts/<int:contact_id>', methods=['PATCH'])
@agent_jwt_required
def patch_contact(user, contact_id):
    contact = org_query_for_id(Contact, user.organization_id).filter_by(
        id=contact_id,
    ).first()
    if not _contact_visible(user, contact):
        return _json_error('Contact not found.', 404)
    data = _json_body()
    if 'first_name' in data:
        first_name = (data.get('first_name') or '').strip()
        if not first_name:
            return _json_error('First name and last name are required.', 400)
        contact.first_name = first_name
    if 'last_name' in data:
        last_name = (data.get('last_name') or '').strip()
        if not last_name:
            return _json_error('First name and last name are required.', 400)
        contact.last_name = last_name
    for field in (
        'email', 'phone', 'street_address', 'city', 'state', 'zip_code',
        'notes', 'current_objective',
    ):
        if field in data:
            value = data.get(field)
            setattr(contact, field, (value or '').strip() or None)
    if 'potential_commission' in data and data.get('potential_commission') is not None:
        contact.potential_commission = data.get('potential_commission')
    if 'group_ids' in data:
        try:
            contact.groups = resolve_groups_for_owner(
                user.organization_id,
                contact.user_id,
                data.get('group_ids') or [],
                active_only=True,
            )
        except ContactGroupError as exc:
            return _json_error(exc.message, exc.status_code)
    db.session.commit()
    return jsonify({'contact': _serialize_contact(contact)})


@agent_api_bp.route('/contacts/<int:contact_id>', methods=['DELETE'])
@agent_jwt_required
def delete_contact(user, contact_id):
    contact = org_query_for_id(Contact, user.organization_id).filter_by(
        id=contact_id,
    ).first()
    if not _contact_visible(user, contact):
        return _json_error('Contact not found.', 404)

    task_count = Task.query.filter_by(contact_id=contact_id).count()
    interaction_count = Interaction.query.filter_by(contact_id=contact_id).count()
    force = (
        str(request.args.get('force') or '').lower() == 'true'
        or str(_json_body().get('force') or '').lower() == 'true'
    )
    if (task_count > 0 or interaction_count > 0) and not force:
        parts = []
        if task_count:
            parts.append(f'{task_count} task{"s" if task_count != 1 else ""}')
        if interaction_count:
            parts.append(
                f'{interaction_count} interaction{"s" if interaction_count != 1 else ""}'
            )
        return jsonify({
            'error': (
                'Cannot delete contact. It has '
                + ' and '.join(parts)
                + '. Pass force=true to delete them too.'
            ),
            'has_associated_data': True,
            'task_count': task_count,
            'interaction_count': interaction_count,
        }), 400

    if force:
        Task.query.filter_by(contact_id=contact_id).delete()
        Interaction.query.filter_by(contact_id=contact_id).delete()
    db.session.delete(contact)
    db.session.commit()
    return jsonify({'ok': True})


@agent_api_bp.route('/contact-groups', methods=['GET'])
@agent_jwt_required
def list_contact_groups(user):
    groups = list_user_groups(user.organization_id, user.id, active_only=True)
    return jsonify({
        'groups': [
            {
                'id': group.id,
                'name': group.name,
                'category': getattr(group, 'category', None),
            }
            for group in groups
        ],
    })


@agent_api_bp.route('/transactions', methods=['GET'])
@agent_jwt_required
@transactions_flag_required
def list_transactions(user):
    query = transactions_visible_query(user).options(
        joinedload(Transaction.transaction_type),
    )
    status_filter = (request.args.get('status') or '').strip()
    if status_filter:
        query = query.filter_by(status=status_filter)
    q = (request.args.get('q') or '').strip()
    if q:
        like = f'%{q}%'
        query = query.filter(or_(
            Transaction.street_address.ilike(like),
            Transaction.city.ilike(like),
        ))
    rows = query.order_by(Transaction.created_at.desc()).limit(200).all()
    people = _participants_by_transaction([tx.id for tx in rows])
    return jsonify({
        'transactions': [
            _serialize_transaction(
                tx,
                participants=people.get(tx.id, []),
            )
            for tx in rows
        ],
    })


@agent_api_bp.route('/transactions', methods=['POST'])
@agent_jwt_required
@transactions_flag_required
def create_transaction(user):
    data = _json_body()
    street_address = (data.get('street_address') or '').strip()
    transaction_type_id = data.get('transaction_type_id')
    if not street_address:
        return _json_error('A property address is required.', 400)
    if not transaction_type_id:
        return _json_error('A transaction type is required.', 400)
    tx_type = TransactionType.query.filter_by(
        id=int(transaction_type_id),
        organization_id=user.organization_id,
    ).first()
    if not tx_type:
        return _json_error('Transaction type not found.', 400)

    contact_ids = data.get('contact_ids') or []
    contacts = []
    for raw_id in contact_ids:
        contact = org_query_for_id(Contact, user.organization_id).filter_by(
            id=int(raw_id),
        ).first()
        if not _contact_visible(user, contact):
            return _json_error('One or more contacts were not found.', 400)
        contacts.append(contact)

    default_status = (
        'showing' if tx_type.name in {'buyer', 'tenant'} else 'preparing_to_list'
    )
    transaction = Transaction(
        organization_id=user.organization_id,
        created_by_id=user.id,
        transaction_type_id=tx_type.id,
        street_address=street_address,
        city=(data.get('city') or '').strip() or None,
        state=(data.get('state') or 'TX').strip() or 'TX',
        zip_code=(data.get('zip_code') or '').strip() or None,
        county=(data.get('county') or '').strip() or None,
        ownership_status=(data.get('ownership_status') or '').strip() or None,
        status=default_status,
    )
    db.session.add(transaction)
    db.session.flush()

    participant_role = ROLE_MAP.get(tx_type.name, 'client')
    for index, contact in enumerate(contacts):
        role = participant_role if index == 0 else f'co_{participant_role}'
        db.session.add(TransactionParticipant(
            organization_id=user.organization_id,
            transaction_id=transaction.id,
            contact_id=contact.id,
            role=role,
            name=f'{contact.first_name} {contact.last_name}',
            email=contact.email,
            phone=contact.phone,
            is_primary=(index == 0),
        ))

    agent_role = (
        'listing_agent' if tx_type.name in ('seller', 'landlord') else 'buyers_agent'
    )
    if tx_type.name in ('seller', 'landlord', 'buyer', 'tenant'):
        db.session.add(TransactionParticipant(
            organization_id=user.organization_id,
            transaction_id=transaction.id,
            user_id=user.id,
            role=agent_role,
            is_primary=True,
        ))
    db.session.commit()
    return jsonify({'transaction': _serialize_transaction(transaction, detail=True)}), 201


@agent_api_bp.route('/transactions/<int:transaction_id>', methods=['GET'])
@agent_jwt_required
@transactions_flag_required
def get_transaction(user, transaction_id):
    tx, error = _load_tx(user, transaction_id, CAP_VIEW)
    if error:
        return error
    return jsonify({'transaction': _serialize_transaction(tx, detail=True)})


@agent_api_bp.route('/transactions/<int:transaction_id>', methods=['PATCH'])
@agent_jwt_required
@transactions_flag_required
def patch_transaction(user, transaction_id):
    tx, error = _load_tx(user, transaction_id, CAP_EDIT)
    if error:
        return error
    data = _json_body()
    for field in (
        'street_address', 'city', 'state', 'zip_code', 'county',
        'ownership_status', 'mls_listing_url',
    ):
        if field in data:
            value = (data.get(field) or '').strip()
            if field == 'street_address' and not value:
                return _json_error('A property address is required.', 400)
            setattr(tx, field, value or None)
    db.session.commit()
    return jsonify({'transaction': _serialize_transaction(tx, detail=True)})


@agent_api_bp.route('/transactions/<int:transaction_id>', methods=['DELETE'])
@agent_jwt_required
@transactions_flag_required
def delete_transaction(user, transaction_id):
    tx, error = _load_tx(user, transaction_id, CAP_EDIT)
    if error:
        return error
    from services.transaction_helpers import purge_transaction_dependent_rows
    purge_transaction_dependent_rows(tx.id)
    db.session.delete(tx)
    db.session.commit()
    return jsonify({'ok': True})


@agent_api_bp.route('/transactions/<int:transaction_id>/status', methods=['POST'])
@agent_jwt_required
@transactions_flag_required
def post_transaction_status(user, transaction_id):
    tx, error = _load_tx(user, transaction_id, CAP_EDIT)
    if error:
        return error
    new_status = (_json_body().get('status') or '').strip()
    tx_type_name = tx.transaction_type.name if tx.transaction_type else 'seller'
    valid = STATUS_OPTIONS_BY_TYPE.get(tx_type_name, STATUS_OPTIONS_BY_TYPE['seller'])
    if new_status not in valid:
        return _json_error('Invalid status.', 400)
    tx.status = new_status
    db.session.commit()
    return jsonify({'ok': True, 'status': tx.status})


@agent_api_bp.route('/transactions/<int:transaction_id>/dates', methods=['PATCH'])
@agent_jwt_required
@transactions_flag_required
def patch_transaction_dates(user, transaction_id):
    """Write portal-visible dates only.

    Do not call seller contract-details replace here. That rebuilds
    milestones from frozen terms and can wipe dates that were not sent.
    """
    tx, error = _load_tx(user, transaction_id, CAP_EDIT)
    if error:
        return error
    data = _json_body()
    unknown = [key for key in data.keys() if key not in PORTAL_DATE_FIELDS]
    if unknown:
        return _json_error(
            'Only portal-visible dates can be updated: '
            + ', '.join(sorted(PORTAL_DATE_FIELDS))
            + '.',
            400,
        )
    try:
        if 'expected_close_date' in data:
            tx.expected_close_date = _parse_date(data.get('expected_close_date'))
        if 'actual_close_date' in data:
            tx.actual_close_date = _parse_date(data.get('actual_close_date'))
        if 'go_live_date' in data:
            profile = tx.seller_listing_profile
            if profile is None:
                profile = SellerListingProfile(
                    organization_id=user.organization_id,
                    transaction_id=tx.id,
                    created_by_id=user.id,
                )
                db.session.add(profile)
            profile.go_live_date = _parse_date(data.get('go_live_date'))
        if 'effective_date' in data or 'closing_date' in data:
            contract = _ensure_primary_contract(tx, user)
            if 'effective_date' in data:
                contract.effective_date = _parse_date(data.get('effective_date'))
            if 'closing_date' in data:
                contract.closing_date = _parse_date(data.get('closing_date'))
    except ValueError as exc:
        return _json_error(str(exc), 400)
    db.session.commit()
    return jsonify({'ok': True, 'dates': _tx_dates(tx)})


@agent_api_bp.route('/transactions/<int:transaction_id>/participants', methods=['GET'])
@agent_jwt_required
@transactions_flag_required
def list_participants(user, transaction_id):
    tx, error = _load_tx(user, transaction_id, CAP_VIEW)
    if error:
        return error
    return jsonify({
        'participants': [_serialize_participant(p) for p in tx.participants.all()],
    })


@agent_api_bp.route('/transactions/<int:transaction_id>/participants', methods=['POST'])
@agent_jwt_required
@transactions_flag_required
def add_participant(user, transaction_id):
    tx, error = _load_tx(user, transaction_id, CAP_EDIT)
    if error:
        return error
    data = _json_body()
    role = (data.get('role') or '').strip()
    if not role:
        return _json_error('Role is required.', 400)
    contact_id = data.get('contact_id')
    if contact_id:
        contact = org_query_for_id(Contact, user.organization_id).filter_by(
            id=int(contact_id),
        ).first()
        if not _contact_visible(user, contact):
            return _json_error('Contact not found.', 404)
        if not contact.first_name or not contact.last_name:
            return _json_error('This contact is missing a name.', 400)
        participant = TransactionParticipant(
            organization_id=user.organization_id,
            transaction_id=tx.id,
            role=role,
            contact_id=contact.id,
            name=f'{contact.first_name} {contact.last_name}',
            email=contact.email,
            phone=contact.phone,
            is_primary=False,
        )
    else:
        name = (data.get('name') or '').strip()
        if not name:
            return _json_error('Select a contact or provide a name.', 400)
        participant = TransactionParticipant(
            organization_id=user.organization_id,
            transaction_id=tx.id,
            role=role,
            name=name,
            email=(data.get('email') or '').strip() or None,
            phone=(data.get('phone') or '').strip() or None,
            is_primary=False,
        )
    db.session.add(participant)
    db.session.commit()
    return jsonify({'participant': _serialize_participant(participant)}), 201


@agent_api_bp.route('/transactions/<int:transaction_id>/milestones', methods=['GET'])
@agent_jwt_required
@transactions_flag_required
def list_milestones(user, transaction_id):
    tx, error = _load_tx(user, transaction_id, CAP_VIEW)
    if error:
        return error
    rows = SellerContractMilestone.query.filter_by(
        transaction_id=tx.id,
        organization_id=user.organization_id,
    ).order_by(SellerContractMilestone.due_at.asc().nullslast()).all()
    return jsonify({'milestones': [_serialize_milestone(m) for m in rows]})


@agent_api_bp.route('/transactions/<int:transaction_id>/milestones', methods=['POST'])
@agent_jwt_required
@transactions_flag_required
def create_milestone(user, transaction_id):
    tx, error = _load_tx(user, transaction_id, CAP_EDIT)
    if error:
        return error
    contract = get_active_primary_contract(tx.id, tx.organization_id)
    if contract is None:
        return _json_error('Add a controlling contract before adding milestones.', 400)
    data = _json_body()
    title = (data.get('title') or '').strip()
    if not title:
        return _json_error('Milestone title is required.', 400)
    status = data.get('status') or 'not_started'
    if status not in MILESTONE_STATUSES:
        return _json_error('Invalid milestone status.', 400)
    try:
        due_at = _parse_datetime(data.get('due_at'))
    except ValueError as exc:
        return _json_error(str(exc), 400)
    milestone = SellerContractMilestone(
        organization_id=user.organization_id,
        transaction_id=tx.id,
        accepted_contract_id=contract.id,
        created_by_id=user.id,
        milestone_key=(data.get('milestone_key') or 'manual').strip() or 'manual',
        title=title,
        due_at=due_at,
        status=status,
        responsible_party=(data.get('responsible_party') or '').strip() or None,
        notes=(data.get('notes') or '').strip() or None,
        source='manual',
    )
    if status == 'completed':
        milestone.completed_at = datetime.utcnow()
    db.session.add(milestone)
    db.session.commit()
    return jsonify({'milestone': _serialize_milestone(milestone)}), 201


@agent_api_bp.route(
    '/transactions/<int:transaction_id>/milestones/<int:milestone_id>',
    methods=['PATCH'],
)
@agent_jwt_required
@transactions_flag_required
def patch_milestone(user, transaction_id, milestone_id):
    tx, error = _load_tx(user, transaction_id, CAP_EDIT)
    if error:
        return error
    milestone = SellerContractMilestone.query.filter_by(
        id=milestone_id,
        transaction_id=tx.id,
        organization_id=user.organization_id,
    ).first()
    if not milestone:
        return _json_error('Milestone not found.', 404)
    data = _json_body()
    if 'title' in data:
        title = (data.get('title') or '').strip()
        if not title:
            return _json_error('Milestone title is required.', 400)
        milestone.title = title
    if 'status' in data:
        status = data.get('status')
        if status not in MILESTONE_STATUSES:
            return _json_error('Invalid milestone status.', 400)
        milestone.status = status
        if status == 'completed':
            milestone.completed_at = milestone.completed_at or datetime.utcnow()
        else:
            milestone.completed_at = None
    if 'due_at' in data:
        try:
            milestone.due_at = _parse_datetime(data.get('due_at'))
        except ValueError as exc:
            return _json_error(str(exc), 400)
    if 'responsible_party' in data:
        milestone.responsible_party = (data.get('responsible_party') or '').strip() or None
    if 'notes' in data:
        milestone.notes = (data.get('notes') or '').strip() or None
    milestone.source = 'manual'
    db.session.commit()
    return jsonify({'milestone': _serialize_milestone(milestone)})


@agent_api_bp.route('/transactions/<int:transaction_id>/documents', methods=['GET'])
@agent_jwt_required
@transactions_flag_required
def list_documents(user, transaction_id):
    tx, error = _load_tx(user, transaction_id, CAP_VIEW)
    if error:
        return error
    docs = TransactionDocument.query.filter_by(
        transaction_id=tx.id,
        organization_id=user.organization_id,
    ).order_by(TransactionDocument.created_at.desc()).all()
    return jsonify({'documents': [_serialize_document(doc) for doc in docs]})


@agent_api_bp.route('/transactions/<int:transaction_id>/documents', methods=['POST'])
@agent_jwt_required
@transactions_flag_required
def upload_document(user, transaction_id):
    tx, error = _load_tx(user, transaction_id, CAP_EDIT)
    if error:
        return error
    uploaded = request.files.get('file')
    if uploaded is None or not uploaded.filename:
        return _json_error('Upload a PDF.', 400)
    filename = uploaded.filename
    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
    if ext != 'pdf':
        return _json_error('PDF only.', 400)
    file_data = uploaded.read()
    if len(file_data) > MAX_UPLOAD_BYTES:
        return _json_error('File too large. Maximum size is 25MB.', 400)
    document_name = (
        (request.form.get('name') or _json_body().get('name') or '').strip()
        or filename.rsplit('.', 1)[0]
    )
    from services.supabase_storage import upload_external_document

    try:
        result = upload_external_document(
            transaction_id=tx.id,
            file_data=file_data,
            original_filename=filename,
            content_type='application/pdf',
        )
    except Exception:
        logger.exception('Agent API document upload failed')
        return _json_error('Could not store that PDF.', 502)

    doc = TransactionDocument(
        organization_id=user.organization_id,
        transaction_id=tx.id,
        template_slug='external',
        template_name=document_name,
        status='pending',
        document_source='external',
        source_file_path=result.get('path'),
        signed_original_filename=filename,
    )
    db.session.add(doc)
    db.session.commit()
    return jsonify({'document': _serialize_document(doc)}), 201


@agent_api_bp.route(
    '/transactions/<int:transaction_id>/documents/<int:doc_id>/file',
    methods=['GET'],
)
@agent_jwt_required
@transactions_flag_required
def get_document_file(user, transaction_id, doc_id):
    tx, error = _load_tx(user, transaction_id, CAP_VIEW)
    if error:
        return error
    doc = TransactionDocument.query.filter_by(
        id=doc_id,
        transaction_id=tx.id,
        organization_id=user.organization_id,
    ).first()
    if not doc:
        return _json_error('Document not found.', 404)
    path = doc.signed_file_path or doc.source_file_path
    if not path:
        return _json_error('That document has no file yet.', 404)
    from services.supabase_storage import get_transaction_document_url
    try:
        url = get_transaction_document_url(path, expires_in=3600)
    except Exception:
        logger.exception('Agent API signed URL failed')
        return _json_error('Could not create a file link.', 502)
    return jsonify({'url': url})


@agent_api_bp.route('/conversations', methods=['GET'])
@agent_jwt_required
@transactions_flag_required
def list_conversations(user):
    txs = transactions_visible_query(user).options(
        joinedload(Transaction.transaction_type),
    ).all()
    tx_ids = [tx.id for tx in txs]
    if not tx_ids:
        return jsonify({'conversations': []})

    participants = TransactionParticipant.query.filter(
        TransactionParticipant.organization_id == user.organization_id,
        TransactionParticipant.transaction_id.in_(tx_ids),
        TransactionParticipant.role.in_(tuple(CLIENT_PORTAL_ROLES)),
    ).options(joinedload(TransactionParticipant.contact)).all()

    messages = PortalMessage.query.filter(
        PortalMessage.organization_id == user.organization_id,
        PortalMessage.transaction_id.in_(tx_ids),
    ).order_by(PortalMessage.created_at.desc()).all()

    latest_by_participant = {}
    unread_by_participant = {}
    for msg in messages:
        latest_by_participant.setdefault(msg.participant_id, msg)
        if msg.sender == 'client' and msg.read_by_agent_at is None:
            unread_by_participant[msg.participant_id] = (
                unread_by_participant.get(msg.participant_id, 0) + 1
            )

    tx_by_id = {tx.id: tx for tx in txs}
    conversations = []
    for participant in participants:
        tx = tx_by_id.get(participant.transaction_id)
        if tx is None:
            continue
        last = latest_by_participant.get(participant.id)
        conversations.append({
            'transaction_id': tx.id,
            'address': tx.street_address,
            'participant_id': participant.id,
            'name': participant.display_name,
            'role': participant.role,
            'last_preview': last.body if last else None,
            'last_at': last.created_at.isoformat() if last and last.created_at else None,
            'unread': unread_by_participant.get(participant.id, 0),
        })
    conversations.sort(key=lambda row: row['last_at'] or '', reverse=True)
    return jsonify({'conversations': conversations})


def _conversation_access(user, participant_id):
    participant = TransactionParticipant.query.filter_by(
        id=participant_id,
        organization_id=user.organization_id,
    ).first()
    if (
        participant is None
        or (participant.role or '').strip().lower() not in CLIENT_PORTAL_ROLES
    ):
        return None, None, _json_error('Conversation not found.', 404)
    tx, error = _load_tx(user, participant.transaction_id, CAP_VIEW)
    if error:
        return None, None, error
    return tx, participant, None


@agent_api_bp.route(
    '/conversations/<int:participant_id>/messages',
    methods=['GET'],
)
@agent_jwt_required
@transactions_flag_required
def list_conversation_messages(user, participant_id):
    tx, participant, error = _conversation_access(user, participant_id)
    if error:
        return error
    access = SimpleNamespace(
        transaction_id=tx.id,
        participant_id=participant.id,
        participant=participant,
    )
    messages = list_client_messages(access)
    unread = PortalMessage.query.filter_by(
        transaction_id=tx.id,
        participant_id=participant.id,
        sender='client',
    ).filter(PortalMessage.read_by_agent_at.is_(None)).all()
    now = datetime.utcnow()
    for msg in unread:
        msg.read_by_agent_at = now
    if unread:
        db.session.commit()
    return jsonify({'messages': messages})


@agent_api_bp.route(
    '/conversations/<int:participant_id>/messages',
    methods=['POST'],
)
@agent_jwt_required
@transactions_flag_required
def post_conversation_message(user, participant_id):
    tx, participant, error = _conversation_access(user, participant_id)
    if error:
        return error
    body = (_json_body().get('body') or '').strip()
    if not body:
        return _json_error('Write a message before sending.', 400)
    if len(body) > 4000:
        body = body[:4000]
    msg = PortalMessage(
        organization_id=user.organization_id,
        transaction_id=tx.id,
        participant_id=participant.id,
        sender='agent',
        kind='message',
        body=body,
        author_user_id=user.id,
    )
    db.session.add(msg)
    db.session.commit()
    try:
        enqueue_portal_push(msg)
    except Exception:
        logger.exception('Agent API: failed to enqueue APNs for agent message.')
    access = SimpleNamespace(
        transaction_id=tx.id,
        participant_id=participant.id,
        participant=participant,
    )
    listed = list_client_messages(access)
    return jsonify({'message': listed[-1] if listed else None}), 201
