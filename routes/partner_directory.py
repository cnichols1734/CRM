"""Org-wide Partner Directory routes."""
from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError

from feature_flags import can_access_transactions
from models import PartnerContact, PartnerOrganization, Transaction, TransactionParticipant, db
from services.partners import (
    PARTNER_TYPES,
    find_company_duplicate_candidates,
    find_contact_duplicate_candidates,
    is_admin_role,
    sync_partner_contact_fields,
    sync_partner_organization_fields,
)
from services.tenant_service import org_query


partner_directory_bp = Blueprint(
    'partner_directory',
    __name__,
    url_prefix='/partners',
)


def _require_transactions_access():
    if not can_access_transactions(current_user):
        abort(403)


def _require_admin():
    if not is_admin_role(current_user):
        abort(403)


def _partner_or_404(partner_id):
    return org_query(PartnerOrganization).filter_by(id=partner_id).first_or_404()


def _contact_or_404(partner, contact_id):
    return PartnerContact.query.filter_by(
        id=contact_id,
        organization_id=current_user.organization_id,
        partner_organization_id=partner.id,
    ).first_or_404()


def _company_form_data():
    return {
        'name': request.form.get('name', '').strip(),
        'partner_type': request.form.get('partner_type', 'other').strip(),
        'phone': request.form.get('phone', '').strip() or None,
        'email': request.form.get('email', '').strip() or None,
        'website': request.form.get('website', '').strip() or None,
        'street_address': request.form.get('street_address', '').strip() or None,
        'city': request.form.get('city', '').strip() or None,
        'state': request.form.get('state', '').strip() or None,
        'zip_code': request.form.get('zip_code', '').strip() or None,
        'notes': request.form.get('notes', '').strip() or None,
    }


def _contact_form_data():
    return {
        'first_name': request.form.get('first_name', '').strip(),
        'last_name': request.form.get('last_name', '').strip(),
        'title': request.form.get('title', '').strip() or None,
        'email': request.form.get('email', '').strip() or None,
        'phone': request.form.get('phone', '').strip() or None,
        'notes': request.form.get('notes', '').strip() or None,
        'is_primary_contact': request.form.get('is_primary_contact') == 'on',
    }


def _usage_counts():
    rows = db.session.query(
        TransactionParticipant.partner_organization_id,
        func.count(TransactionParticipant.id),
    ).filter(
        TransactionParticipant.organization_id == current_user.organization_id,
        TransactionParticipant.partner_organization_id.isnot(None),
    ).group_by(TransactionParticipant.partner_organization_id).all()
    return {partner_id: count for partner_id, count in rows}


def _render_index(**extra_context):
    search_query = request.args.get('q', '').strip()
    selected_type = request.args.get('type', '').strip()
    active_filter = request.args.get('active', 'active')

    query = org_query(PartnerOrganization)

    if active_filter == 'inactive':
        query = query.filter_by(is_active=False)
    elif active_filter != 'all':
        query = query.filter_by(is_active=True)

    if selected_type in PARTNER_TYPES:
        query = query.filter_by(partner_type=selected_type)

    if search_query:
        search = f'%{search_query}%'
        query = query.filter(or_(
            PartnerOrganization.name.ilike(search),
            PartnerOrganization.email.ilike(search),
            PartnerOrganization.phone.ilike(search),
            PartnerOrganization.city.ilike(search),
        ))

    partners = query.order_by(PartnerOrganization.name.asc()).all()

    context = {
        'partners': partners,
        'partner_types': PARTNER_TYPES,
        'usage_counts': _usage_counts(),
        'search_query': search_query,
        'selected_type': selected_type,
        'active_filter': active_filter,
        'can_manage_partners': is_admin_role(current_user),
        'pending_company': None,
        'duplicate_warnings': [],
    }
    context.update(extra_context)
    return render_template('partner_directory/index.html', **context)


@partner_directory_bp.before_request
@login_required
def require_partner_directory_access():
    _require_transactions_access()


@partner_directory_bp.route('/')
def index():
    return _render_index()


@partner_directory_bp.route('/', methods=['POST'])
def create_partner():
    form_data = _company_form_data()
    if not form_data['name']:
        flash('Company name is required.', 'error')
        return _render_index(pending_company=form_data)

    duplicates = find_company_duplicate_candidates(
        current_user.organization_id,
        form_data['name'],
        form_data['street_address'],
        form_data['city'],
        form_data['state'],
        form_data['zip_code'],
    )

    if duplicates['exact']:
        duplicate = duplicates['exact'][0]
        flash(f'{duplicate.name} already exists in the Partner Directory.', 'error')
        return redirect(url_for('partner_directory.detail', partner_id=duplicate.id))

    if duplicates['warnings'] and request.form.get('force_create') != '1':
        flash('Possible duplicate found. Review the matches before creating a new partner.', 'warning')
        return _render_index(
            pending_company=form_data,
            duplicate_warnings=duplicates['warnings'],
        )

    partner = PartnerOrganization(
        organization_id=current_user.organization_id,
        created_by_id=current_user.id,
        updated_by_id=current_user.id,
        **form_data,
    )
    sync_partner_organization_fields(partner)

    try:
        db.session.add(partner)
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash('That company already exists in the Partner Directory.', 'error')
        return _render_index(pending_company=form_data)

    flash(f'{partner.name} was added to the Partner Directory.', 'success')
    return redirect(url_for('partner_directory.detail', partner_id=partner.id))


def _linked_transactions_for(partner):
    """Build a deduplicated list of transactions this partner has been attached to."""
    participants = (
        TransactionParticipant.query
        .join(Transaction, TransactionParticipant.transaction_id == Transaction.id)
        .filter(
            TransactionParticipant.organization_id == current_user.organization_id,
            TransactionParticipant.partner_organization_id == partner.id,
        )
        .order_by(Transaction.created_at.desc())
        .all()
    )
    seen = set()
    out = []
    for p in participants:
        txn = p.transaction
        if txn.id in seen:
            continue
        seen.add(txn.id)
        out.append({
            'id': txn.id,
            'address': txn.full_address or txn.street_address,
            'type': txn.transaction_type.display_name if txn.transaction_type else '',
            'status': (txn.status or '').replace('_', ' ').title(),
            'role': p.role.replace('_', ' ').title(),
            'person': p.display_name,
            'close_date': txn.actual_close_date or txn.expected_close_date,
        })
    return out


@partner_directory_bp.route('/<int:partner_id>')
def detail(partner_id):
    partner = _partner_or_404(partner_id)
    contacts = partner.contacts.order_by(
        PartnerContact.is_active.desc(),
        PartnerContact.last_name.asc(),
        PartnerContact.first_name.asc(),
    ).all()
    linked_transactions = _linked_transactions_for(partner)

    return render_template(
        'partner_directory/detail.html',
        partner=partner,
        contacts=contacts,
        partner_types=PARTNER_TYPES,
        usage_count=len(linked_transactions),
        linked_transactions=linked_transactions,
        can_manage_partners=is_admin_role(current_user),
        pending_contact=None,
        duplicate_warnings=[],
    )


@partner_directory_bp.route('/<int:partner_id>/edit', methods=['POST'])
def edit_partner(partner_id):
    _require_admin()
    partner = _partner_or_404(partner_id)
    form_data = _company_form_data()

    if not form_data['name']:
        flash('Company name is required.', 'error')
        return redirect(url_for('partner_directory.detail', partner_id=partner.id))

    duplicates = find_company_duplicate_candidates(
        current_user.organization_id,
        form_data['name'],
        form_data['street_address'],
        form_data['city'],
        form_data['state'],
        form_data['zip_code'],
        exclude_id=partner.id,
    )
    if duplicates['exact']:
        flash(f'{duplicates["exact"][0].name} already uses that company name.', 'error')
        return redirect(url_for('partner_directory.detail', partner_id=partner.id))

    for key, value in form_data.items():
        setattr(partner, key, value)
    partner.is_active = request.form.get('is_active') == 'on'
    partner.updated_by_id = current_user.id
    sync_partner_organization_fields(partner)

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash('That company name is already in use.', 'error')
        return redirect(url_for('partner_directory.detail', partner_id=partner.id))

    if duplicates['warnings']:
        flash('Saved. This company still looks similar to another directory record; review duplicates when convenient.', 'warning')
    else:
        flash('Partner company updated.', 'success')
    return redirect(url_for('partner_directory.detail', partner_id=partner.id))


@partner_directory_bp.route('/<int:partner_id>/deactivate', methods=['POST'])
def deactivate_partner(partner_id):
    _require_admin()
    partner = _partner_or_404(partner_id)
    partner.is_active = not partner.is_active
    partner.updated_by_id = current_user.id
    db.session.commit()
    flash(f'{partner.name} is now {"active" if partner.is_active else "inactive"}.', 'success')
    return redirect(url_for('partner_directory.detail', partner_id=partner.id))


@partner_directory_bp.route('/<int:partner_id>/contacts', methods=['POST'])
def create_contact(partner_id):
    partner = _partner_or_404(partner_id)
    form_data = _contact_form_data()

    if not form_data['first_name'] or not form_data['last_name']:
        flash('First and last name are required.', 'error')
        return _render_detail_with_contact_form(partner, form_data)

    duplicates = find_contact_duplicate_candidates(
        partner.id,
        form_data['first_name'],
        form_data['last_name'],
        form_data['email'],
        form_data['phone'],
    )

    if duplicates['exact']:
        flash(f'{duplicates["exact"][0].full_name} already exists under {partner.name}.', 'error')
        return _render_detail_with_contact_form(partner, form_data)

    if duplicates['warnings'] and request.form.get('force_create') != '1':
        flash('Possible duplicate person found. Review before creating another contact.', 'warning')
        return _render_detail_with_contact_form(partner, form_data, duplicates['warnings'])

    contact = PartnerContact(
        organization_id=current_user.organization_id,
        partner_organization_id=partner.id,
        created_by_id=current_user.id,
        updated_by_id=current_user.id,
        **form_data,
    )
    sync_partner_contact_fields(contact)

    try:
        db.session.add(contact)
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash('That person already exists under this company.', 'error')
        return _render_detail_with_contact_form(partner, form_data)

    flash(f'{contact.full_name} was added under {partner.name}.', 'success')
    return redirect(url_for('partner_directory.detail', partner_id=partner.id))


def _render_detail_with_contact_form(partner, form_data, duplicate_warnings=None):
    contacts = partner.contacts.order_by(
        PartnerContact.is_active.desc(),
        PartnerContact.last_name.asc(),
        PartnerContact.first_name.asc(),
    ).all()
    linked_transactions = _linked_transactions_for(partner)
    return render_template(
        'partner_directory/detail.html',
        partner=partner,
        contacts=contacts,
        partner_types=PARTNER_TYPES,
        usage_count=len(linked_transactions),
        linked_transactions=linked_transactions,
        can_manage_partners=is_admin_role(current_user),
        pending_contact=form_data,
        duplicate_warnings=duplicate_warnings or [],
    )


@partner_directory_bp.route('/<int:partner_id>/contacts/<int:contact_id>/edit', methods=['POST'])
def edit_contact(partner_id, contact_id):
    _require_admin()
    partner = _partner_or_404(partner_id)
    contact = _contact_or_404(partner, contact_id)
    form_data = _contact_form_data()

    if not form_data['first_name'] or not form_data['last_name']:
        flash('First and last name are required.', 'error')
        return redirect(url_for('partner_directory.detail', partner_id=partner.id))

    duplicates = find_contact_duplicate_candidates(
        partner.id,
        form_data['first_name'],
        form_data['last_name'],
        form_data['email'],
        form_data['phone'],
        exclude_id=contact.id,
    )
    if duplicates['exact']:
        flash(f'{duplicates["exact"][0].full_name} already exists under {partner.name}.', 'error')
        return redirect(url_for('partner_directory.detail', partner_id=partner.id))

    for key, value in form_data.items():
        setattr(contact, key, value)
    contact.is_active = request.form.get('is_active') == 'on'
    contact.updated_by_id = current_user.id
    sync_partner_contact_fields(contact)

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash('That person already exists under this company.', 'error')
        return redirect(url_for('partner_directory.detail', partner_id=partner.id))

    if duplicates['warnings']:
        flash('Saved. This person shares an email or phone with another contact under this company.', 'warning')
    else:
        flash('Partner contact updated.', 'success')
    return redirect(url_for('partner_directory.detail', partner_id=partner.id))


@partner_directory_bp.route('/<int:partner_id>/contacts/<int:contact_id>/deactivate', methods=['POST'])
def deactivate_contact(partner_id, contact_id):
    _require_admin()
    partner = _partner_or_404(partner_id)
    contact = _contact_or_404(partner, contact_id)
    contact.is_active = request.form.get('is_active') == '1'
    contact.updated_by_id = current_user.id
    db.session.commit()
    flash(f'{contact.full_name} is now {"active" if contact.is_active else "inactive"}.', 'success')
    return redirect(url_for('partner_directory.detail', partner_id=partner.id))
