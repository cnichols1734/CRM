from flask import Blueprint, render_template, redirect, url_for, flash, request, abort, Response, jsonify, current_app
from flask_login import login_required, current_user
from models import db, Contact, ContactGroup, User, Transaction, TransactionParticipant, ContactFile, Interaction, Task
from feature_flags import can_access_transactions
from forms import ContactForm
from services import supabase_storage
from services.tenant_service import org_query, can_view_all_org_data, org_can_add_contact
import csv
from io import StringIO
from sqlalchemy import func
from sqlalchemy.orm import joinedload, selectinload
from datetime import datetime
import pytz

contacts_bp = Blueprint('contacts', __name__)

def get_user_timezone():
    """Helper function to get user's timezone"""
    return pytz.timezone('America/Chicago')

def format_phone_number(phone):
    """Format phone number to (XXX) XXX-XXXX format"""
    if not phone:
        return None
        
    # Remove any non-digit characters
    digits = ''.join(filter(str.isdigit, phone))
    
    # Handle numbers with or without country code
    if len(digits) == 11 and digits.startswith('1'):
        digits = digits[1:]  # Remove leading 1
    
    # If we don't have exactly 10 digits, return None
    if len(digits) != 10:
        return None
        
    # Format as (XXX) XXX-XXXX
    return f"({digits[0:3]}) {digits[3:6]}-{digits[6:]}"

@contacts_bp.route('/contact/<int:contact_id>')
@login_required
def view_contact(contact_id):
    # Multi-tenant: Get contact within org with eager loading for related data
    contact = org_query(Contact).options(
        selectinload(Contact.tasks).joinedload(Task.task_type),
        joinedload(Contact.groups)
    ).filter_by(id=contact_id).first_or_404()
    
    # Check permission: admins can see all contacts in org, others only their own
    if not can_view_all_org_data() and contact.user_id != current_user.id:
        abort(403)

    # Multi-tenant: Get next/prev contacts with single query instead of 4
    sibling_contacts = org_query(Contact).filter(
        Contact.user_id == contact.user_id
    ).with_entities(
        Contact.id, Contact.first_name, Contact.last_name
    ).order_by(Contact.first_name.asc(), Contact.last_name.asc()).all()
    
    # Find current contact position and get prev/next
    next_contact = None
    prev_contact = None
    current_idx = None
    for i, (cid, fname, lname) in enumerate(sibling_contacts):
        if cid == contact.id:
            current_idx = i
            break
    
    if current_idx is not None and len(sibling_contacts) > 1:
        # Next: wrap to first if at end
        next_idx = (current_idx + 1) % len(sibling_contacts)
        nid, nfname, nlname = sibling_contacts[next_idx]
        next_contact = type('Contact', (), {'id': nid, 'first_name': nfname, 'last_name': nlname})()
        
        # Prev: wrap to last if at start
        prev_idx = (current_idx - 1) % len(sibling_contacts)
        pid, pfname, plname = sibling_contacts[prev_idx]
        prev_contact = type('Contact', (), {'id': pid, 'first_name': pfname, 'last_name': plname})()

    # Check if it's an AJAX request
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        # Get active tasks for the contact
        active_tasks = [task for task in contact.tasks if task.status == 'pending']
        active_tasks.sort(key=lambda x: x.due_date)  # Sort by due date

        return jsonify({
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
            'potential_commission': float(contact.potential_commission) if contact.potential_commission else None,
            'created_at': contact.created_at.isoformat(),
            'last_email_date': contact.last_email_date.strftime('%Y-%m-%d') if contact.last_email_date else None,
            'last_text_date': contact.last_text_date.strftime('%Y-%m-%d') if contact.last_text_date else None,
            'last_phone_call_date': contact.last_phone_call_date.strftime('%Y-%m-%d') if contact.last_phone_call_date else None,
            'last_contact_date': contact.last_contact_date.strftime('%Y-%m-%d') if contact.last_contact_date else None,
            'current_objective': contact.current_objective,
            'move_timeline': contact.move_timeline,
            'motivation': contact.motivation,
            'financial_status': contact.financial_status,
            'additional_notes': contact.additional_notes,
            'groups': [{
                'id': group.id,
                'name': group.name
            } for group in contact.groups],
            'active_tasks': [{
                'id': task.id,
                'subject': task.subject,
                'priority': task.priority,
                'due_date': task.due_date.isoformat(),
                'task_type': {
                    'name': task.task_type.name
                }
            } for task in active_tasks]
        })

    # Multi-tenant: Get groups within org (cached)
    from services.cache_helpers import get_org_contact_groups
    all_groups = get_org_contact_groups(current_user.organization_id)
    user_tz = get_user_timezone()
    now = datetime.now(user_tz)

    # Get related transactions via TransactionParticipant (only if user has access)
    show_transactions = can_access_transactions(current_user)
    related_transactions = []
    if show_transactions:
        related_transactions = Transaction.query.join(TransactionParticipant).filter(
            TransactionParticipant.contact_id == contact.id
        ).order_by(Transaction.created_at.desc()).all()

    # Get contact files
    contact_files = ContactFile.query.filter_by(contact_id=contact.id).order_by(
        ContactFile.created_at.desc()
    ).all()
    
    # Get recent interactions/activity
    recent_interactions = Interaction.query.filter_by(contact_id=contact.id).order_by(
        Interaction.date.desc()
    ).limit(10).all()
    
    # Get Gmail integration status and email threads
    from models import UserEmailIntegration
    from services import gmail_service
    
    gmail_integration = UserEmailIntegration.query.filter_by(user_id=current_user.id).first()
    gmail_connected = gmail_integration and gmail_integration.sync_enabled
    email_threads = []
    
    if gmail_connected:
        email_threads = gmail_service.get_email_threads_for_contact(contact.id, current_user.id)

    return render_template('contacts/view.html', 
                         contact=contact, 
                         all_groups=all_groups,
                         next_contact=next_contact,
                         prev_contact=prev_contact,
                         now=now,
                         related_transactions=related_transactions,
                         show_transactions=show_transactions,
                         contact_files=contact_files,
                         recent_interactions=recent_interactions,
                         gmail_connected=gmail_connected,
                         email_threads=email_threads)


@contacts_bp.route('/contacts/create', methods=['GET', 'POST'])
@login_required
def create_contact():
    # Multi-tenant: Check contact limit
    allowed, message = org_can_add_contact()
    if not allowed:
        flash(message, 'error')
        return redirect(url_for('main.contacts'))
    
    form = ContactForm()
    # Multi-tenant: Get groups within org
    form.group_ids.choices = [(g.id, g.name) for g in org_query(ContactGroup).order_by(ContactGroup.sort_order)]
    
    # Check for return_to parameter (for redirecting back to transaction)
    return_to = request.args.get('return_to')
    transaction_id = request.args.get('transaction_id', type=int)

    if form.validate_on_submit():
        # Multi-tenant: Set organization_id and created_by_id
        contact = Contact(
            organization_id=current_user.organization_id,
            user_id=current_user.id,
            created_by_id=current_user.id,
            first_name=form.first_name.data,
            last_name=form.last_name.data,
            email=form.email.data,
            phone=format_phone_number(form.phone.data),
            street_address=form.street_address.data,
            city=form.city.data,
            state=form.state.data,
            zip_code=form.zip_code.data,
            notes=form.notes.data,
            potential_commission=form.potential_commission.data or 5000.00,
            last_email_date=form.last_email_date.data,
            last_text_date=form.last_text_date.data,
            last_phone_call_date=form.last_phone_call_date.data,
            current_objective=form.current_objective.data,
            move_timeline=form.move_timeline.data,
            motivation=form.motivation.data,
            financial_status=form.financial_status.data,
            additional_notes=form.additional_notes.data
        )

        # Update the last contact date
        contact.update_last_contact_date()

        # Multi-tenant: Get groups within org
        selected_groups = org_query(ContactGroup).filter(
            ContactGroup.id.in_(form.group_ids.data)
        ).all()
        contact.groups = selected_groups

        db.session.add(contact)
        db.session.commit()
        
        # Handle return_to=transaction redirect
        if return_to == 'transaction' and transaction_id:
            flash('Contact created! You can now add them as a participant.', 'success')
            return redirect(url_for('transactions.view_transaction', id=transaction_id, prompt_add_participant=1))
        
        flash('Contact created successfully!', 'success')
        return redirect(url_for('main.contacts'))

    return render_template('contacts/form.html', form=form, return_transaction_id=transaction_id)


@contacts_bp.route('/contacts/<int:contact_id>/edit', methods=['POST'])
@login_required
def edit_contact(contact_id):
    # Multi-tenant: Get contact within org
    contact = org_query(Contact).filter_by(id=contact_id).first_or_404()

    # Check permission: admins can edit all contacts in org, others only their own
    if not can_view_all_org_data() and contact.user_id != current_user.id:
        abort(403)

    first_name = request.form.get('first_name')
    last_name = request.form.get('last_name')

    if not first_name or not last_name:
        return {
            'status': 'error',
            'message': 'First name and last name are required'
        }, 400

    try:
        contact.first_name = first_name
        contact.last_name = last_name
        contact.email = request.form.get('email')
        contact.phone = format_phone_number(request.form.get('phone'))
        contact.street_address = request.form.get('street_address')
        contact.city = request.form.get('city')
        contact.state = request.form.get('state')
        contact.zip_code = request.form.get('zip_code')
        contact.notes = request.form.get('notes')
        contact.potential_commission = float(request.form.get('potential_commission', 5000.00))

        # Add new objective fields
        contact.current_objective = request.form.get('current_objective')
        contact.move_timeline = request.form.get('move_timeline')
        contact.motivation = request.form.get('motivation')
        contact.financial_status = request.form.get('financial_status')
        contact.additional_notes = request.form.get('additional_notes')

        # Handle date fields
        for date_field in ['last_email_date', 'last_text_date', 'last_phone_call_date']:
            date_str = request.form.get(date_field)
            if date_str:
                try:
                    setattr(contact, date_field, datetime.strptime(date_str, '%Y-%m-%d').date())
                except ValueError:
                    pass  # Skip invalid dates
            else:
                setattr(contact, date_field, None)

        # Update the last contact date
        contact.update_last_contact_date()

        selected_group_ids = request.form.getlist('group_ids')
        # Multi-tenant: Get groups within org
        contact.groups = org_query(ContactGroup).filter(
            ContactGroup.id.in_(selected_group_ids)
        ).all()

        db.session.commit()
        flash('Contact updated successfully!', 'success')
        return {'status': 'success'}, 200

    except Exception as e:
        db.session.rollback()
        return {'status': 'error', 'message': str(e)}, 500


@contacts_bp.route('/contacts/<int:contact_id>/delete', methods=['POST'])
@login_required
def delete_contact(contact_id):
    from models import Task, Interaction
    
    # Multi-tenant: Get contact within org
    contact = org_query(Contact).filter_by(id=contact_id).first_or_404()

    # Check permission
    if not can_view_all_org_data() and contact.user_id != current_user.id:
        abort(403)

    # Check for associated data
    task_count = Task.query.filter_by(contact_id=contact_id).count()
    interaction_count = Interaction.query.filter_by(contact_id=contact_id).count()
    
    # Check if force delete was requested
    force_delete = request.form.get('force', 'false').lower() == 'true'
    
    if (task_count > 0 or interaction_count > 0) and not force_delete:
        # Return informative error with counts
        associated_items = []
        if task_count > 0:
            associated_items.append(f"{task_count} task{'s' if task_count > 1 else ''}")
        if interaction_count > 0:
            associated_items.append(f"{interaction_count} interaction{'s' if interaction_count > 1 else ''}")
        
        return {
            'status': 'error',
            'message': f"Cannot delete contact - has {' and '.join(associated_items)} associated. Delete these first or use force delete.",
            'has_associated_data': True,
            'task_count': task_count,
            'interaction_count': interaction_count
        }, 400

    try:
        # If force delete, remove associated data first
        if force_delete:
            Task.query.filter_by(contact_id=contact_id).delete()
            Interaction.query.filter_by(contact_id=contact_id).delete()
        
        db.session.delete(contact)
        db.session.commit()
        flash('Contact deleted successfully!', 'success')
        return {'status': 'success'}, 200
    except Exception as e:
        db.session.rollback()
        return {'status': 'error', 'message': f'Error deleting contact: {str(e)}'}, 500


@contacts_bp.route('/contact/<int:contact_id>/log-activity', methods=['POST'])
@login_required
def log_activity(contact_id):
    """Log an interaction/activity for a contact."""
    # Multi-tenant: Get contact within org
    contact = org_query(Contact).filter_by(id=contact_id).first_or_404()
    
    # Check permission
    if not can_view_all_org_data() and contact.user_id != current_user.id:
        return jsonify({'success': False, 'error': 'Permission denied'}), 403
    
    # Get form data
    activity_type = request.form.get('activity_type')
    activity_date = request.form.get('activity_date')
    notes = request.form.get('notes', '').strip()
    follow_up_date = request.form.get('follow_up_date')
    
    # Validate required fields
    if not activity_type:
        return jsonify({'success': False, 'error': 'Activity type is required'}), 400
    
    if not activity_date:
        return jsonify({'success': False, 'error': 'Activity date is required'}), 400
    
    valid_types = ['call', 'email', 'text', 'meeting', 'other']
    if activity_type not in valid_types:
        return jsonify({'success': False, 'error': 'Invalid activity type'}), 400
    
    try:
        # Parse dates
        user_tz = get_user_timezone()
        activity_datetime = datetime.strptime(activity_date, '%Y-%m-%d')
        activity_datetime = user_tz.localize(activity_datetime)
        
        follow_up_datetime = None
        if follow_up_date:
            follow_up_datetime = datetime.strptime(follow_up_date, '%Y-%m-%d')
            follow_up_datetime = user_tz.localize(follow_up_datetime)
        
        # Create the interaction record
        interaction = Interaction(
            organization_id=current_user.organization_id,
            contact_id=contact_id,
            user_id=current_user.id,
            type=activity_type,
            notes=notes or None,
            date=activity_datetime,
            follow_up_date=follow_up_datetime
        )
        db.session.add(interaction)
        
        # Update contact's last contact date fields based on activity type
        activity_date_obj = activity_datetime.date()
        
        if activity_type == 'call':
            if contact.last_phone_call_date is None or activity_date_obj > contact.last_phone_call_date:
                contact.last_phone_call_date = activity_date_obj
        elif activity_type == 'email':
            if contact.last_email_date is None or activity_date_obj > contact.last_email_date:
                contact.last_email_date = activity_date_obj
        elif activity_type == 'text':
            if contact.last_text_date is None or activity_date_obj > contact.last_text_date:
                contact.last_text_date = activity_date_obj
        
        # Update the overall last contact date
        contact.update_last_contact_date()
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': 'Activity logged successfully',
            'interaction': {
                'id': interaction.id,
                'type': interaction.type,
                'date': interaction.date.strftime('%b %d, %Y'),
                'notes': interaction.notes
            },
            'updated_dates': {
                'last_email_date': contact.last_email_date.strftime('%Y-%m-%d') if contact.last_email_date else None,
                'last_text_date': contact.last_text_date.strftime('%Y-%m-%d') if contact.last_text_date else None,
                'last_phone_call_date': contact.last_phone_call_date.strftime('%Y-%m-%d') if contact.last_phone_call_date else None,
                'last_contact_date': contact.last_contact_date.strftime('%Y-%m-%d') if contact.last_contact_date else None
            }
        })
        
    except ValueError as e:
        return jsonify({'success': False, 'error': f'Invalid date format: {str(e)}'}), 400
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error logging activity: {e}")
        return jsonify({'success': False, 'error': 'Failed to log activity. Please try again.'}), 500


@contacts_bp.route('/contact/<int:contact_id>/interactions')
@login_required
def get_interactions(contact_id):
    """Get all interactions for a contact."""
    # Multi-tenant: Get contact within org
    contact = org_query(Contact).filter_by(id=contact_id).first_or_404()
    
    # Check permission
    if not can_view_all_org_data() and contact.user_id != current_user.id:
        return jsonify({'success': False, 'error': 'Permission denied'}), 403
    
    interactions = Interaction.query.filter_by(contact_id=contact_id)\
        .order_by(Interaction.date.desc()).all()
    
    return jsonify({
        'success': True,
        'interactions': [{
            'id': i.id,
            'type': i.type,
            'date': i.date.strftime('%b %d, %Y'),
            'notes': i.notes,
            'follow_up_date': i.follow_up_date.strftime('%b %d, %Y') if i.follow_up_date else None,
            'created_at': i.created_at.strftime('%b %d, %Y %I:%M %p')
        } for i in interactions]
    })


@contacts_bp.route('/import-contacts', methods=['POST'])
@login_required
def import_contacts():
    # Determine target user id (admins may upload on behalf of another user)
    target_user_id = current_user.id
    if current_user.role == 'admin':
        requested_user_id = request.form.get('user_id')
        if requested_user_id:
            try:
                requested_user_id_int = int(requested_user_id)
                # CRITICAL: Only allow selecting users from the same organization
                target_user = User.query.filter_by(
                    id=requested_user_id_int,
                    organization_id=current_user.organization_id
                ).first()
                if not target_user:
                    return {'status': 'error', 'message': 'Selected user not found'}, 400
                target_user_id = target_user.id
            except ValueError:
                return {'status': 'error', 'message': 'Invalid user id provided'}, 400

    if 'file' not in request.files:
        return {'status': 'error', 'message': 'No file uploaded'}, 400

    file = request.files['file']
    if file.filename == '':
        return {'status': 'error', 'message': 'No file selected'}, 400

    if not file.filename.endswith('.csv'):
        return {'status': 'error', 'message': 'Please upload a CSV file'}, 400

    try:
        # Read the CSV file
        stream = StringIO(file.stream.read().decode("utf-8-sig"), newline=None)
        csv_data = csv.DictReader(stream)
        
        # Define the expected columns for both formats
        our_format_columns = ['first_name', 'last_name', 'email', 'phone', 'street_address', 'city', 'state', 'zip_code', 'notes', 'groups']
        alt_format_columns = ['First Name', 'Last Name', 'Email 1', 'Phone Number 1', 'Mailing Address', 'Mailing City', 'Mailing State/Province', 'Mailing Postal Code', 'Groups']
        
        # Clean up any BOM characters from fieldnames
        if csv_data.fieldnames and csv_data.fieldnames[0].startswith('\ufeff'):
            csv_data.fieldnames[0] = csv_data.fieldnames[0].replace('\ufeff', '')
        
        # Detect format based on columns
        is_alt_format = any(col in csv_data.fieldnames for col in alt_format_columns)
        
        if is_alt_format:
            # Convert to our format using the transformation logic
            column_mapping = {
                'First Name': 'first_name',
                'Last Name': 'last_name',
                'Email 1': 'email',
                'Phone Number 1': 'phone',
                'Mailing Address': 'street_address',
                'Mailing City': 'city',
                'Mailing State/Province': 'state',
                'Mailing Postal Code': 'zip_code',
                'Groups': 'groups'
            }
            
            # Transform the data
            transformed_data = []
            for row in csv_data:
                transformed_row = {}
                # Map the columns
                for old_col, new_col in column_mapping.items():
                    transformed_row[new_col] = row.get(old_col, '').strip()
                
                # Handle groups delimiter
                if 'groups' in transformed_row:
                    transformed_row['groups'] = transformed_row['groups'].replace(',', ';')
                
                # Combine additional columns into notes
                additional_notes = []
                for col in row.keys():
                    if col not in column_mapping and row[col]:
                        additional_notes.append(f"{col}: {row[col]}")
                
                transformed_row['notes'] = '; '.join(additional_notes)
                transformed_data.append(transformed_row)
            
            # Replace csv_data with transformed data
            csv_data = transformed_data
        
        # Validate required columns for our format
        required_columns = ['first_name', 'last_name', 'phone']
        if not is_alt_format:  # Only check if not in alt format since we just transformed it
            missing_columns = [col for col in required_columns if col not in (csv_data.fieldnames or [])]
            if missing_columns:
                return {
                    'status': 'error',
                    'message': f'Missing required columns: {", ".join(missing_columns)}'
                }, 400

        success_count = 0
        error_count = 0
        error_details = []
        duplicates_skipped = 0
        invalid_phone_count = 0
        missing_name_count = 0

        for row_num, row in enumerate(csv_data, start=1):
            try:
                # Validate required fields (allow at least one of first or last)
                first_name_val = row['first_name'].strip() if row.get('first_name') else ''
                last_name_val = row['last_name'].strip() if row.get('last_name') else ''
                if not first_name_val and not last_name_val:
                    error_details.append(f"Row {row_num}: Missing both first and last name")
                    error_count += 1
                    missing_name_count += 1
                    current_app.logger.warning(f"Import skipped row {row_num}: missing both first and last name")
                    continue

                # Handle phone number
                phone = row['phone'].strip() if row.get('phone') else None
                formatted_phone = None
                if phone:
                    # Remove any non-digit characters first
                    digits_only = ''.join(filter(str.isdigit, phone))
                    # Handle numbers with a leading country code '1'
                    if len(digits_only) == 11 and digits_only.startswith('1'):
                        digits_only = digits_only[1:]
                    if len(digits_only) == 10:
                        formatted_phone = format_phone_number(digits_only)
                    else:
                        # Treat invalid phone as missing instead of failing the row
                        invalid_phone_count += 1
                        current_app.logger.info(f"Import row {row_num}: phone invalid or wrong length; treating as missing")

                # Dedupe by email or formatted phone (per target user)
                candidate_email = (row.get('email') or '').strip() or None
                candidate_email_lower = candidate_email.lower() if candidate_email else None
                if candidate_email_lower:
                    dup = Contact.query\
                        .filter(Contact.user_id == target_user_id)\
                        .filter(func.lower(Contact.email) == candidate_email_lower)\
                        .first()
                    if dup:
                        duplicates_skipped += 1
                        current_app.logger.info(f"Duplicate skipped at row {row_num}: email matches existing contact id={dup.id}")
                        continue
                if formatted_phone:
                    dup_phone = Contact.query\
                        .filter(Contact.user_id == target_user_id, Contact.phone == formatted_phone)\
                        .first()
                    if dup_phone:
                        duplicates_skipped += 1
                        current_app.logger.info(f"Duplicate skipped at row {row_num}: phone matches existing contact id={dup_phone.id}")
                        continue
                # If neither email nor phone, dedupe by first+last name (case-insensitive)
                if not candidate_email_lower and not formatted_phone:
                    if first_name_val or last_name_val:
                        dup_name = Contact.query\
                            .filter(Contact.user_id == target_user_id)\
                            .filter(func.lower(Contact.first_name) == first_name_val.lower())\
                            .filter(func.lower(Contact.last_name) == last_name_val.lower())\
                            .first()
                        if dup_name:
                            duplicates_skipped += 1
                            current_app.logger.info(f"Duplicate skipped at row {row_num}: name matches existing contact id={dup_name.id}")
                            continue

                contact = Contact(
                    organization_id=current_user.organization_id,
                    user_id=target_user_id,
                    created_by_id=current_user.id,
                    first_name=first_name_val or '',
                    last_name=last_name_val or '',
                    email=candidate_email,
                    phone=formatted_phone,
                    street_address=(row.get('street_address', '') or '').strip() or None,
                    city=(row.get('city', '') or '').strip() or None,
                    state=(row.get('state', '') or '').strip() or None,
                    zip_code=(row.get('zip_code', '') or '').strip() or None,
                    notes=(row.get('notes', '') or '').strip() or None
                )

                if row.get('groups'):
                    group_names = [name.strip() for name in row['groups'].split(';') if name.strip()]
                    if group_names:
                        # Multi-tenant: filter groups by organization
                        groups = org_query(ContactGroup).filter(ContactGroup.name.in_(group_names)).all()
                        if len(groups) < len(group_names):
                            missing_groups = set(group_names) - set(g.name for g in groups)
                            error_details.append(f"Row {row_num}: Some groups not found: {', '.join(missing_groups)}")
                        contact.groups = groups

                db.session.add(contact)
                success_count += 1

            except Exception as e:
                error_count += 1
                error_details.append(f"Row {row_num}: {str(e)}")
                continue

        if success_count > 0:
            try:
                db.session.commit()
            except Exception as e:
                db.session.rollback()
                return {
                    'status': 'error',
                    'message': f'Database error: {str(e)}',
                    'error_details': error_details,
                    'duplicates_skipped': duplicates_skipped,
                    'invalid_phone_count': invalid_phone_count,
                    'missing_name_count': missing_name_count
                }, 500
        
        # If we have any errors, include them in the response
        if error_count > 0:
            return {
                'status': 'partial_success' if success_count > 0 else 'error',
                'success_count': success_count,
                'error_count': error_count,
                'error_details': error_details,
                'duplicates_skipped': duplicates_skipped,
                'invalid_phone_count': invalid_phone_count,
                'missing_name_count': missing_name_count
            }
        
        return {
            'status': 'success',
            'success_count': success_count,
            'message': f'Successfully imported {success_count} contacts',
            'duplicates_skipped': duplicates_skipped,
            'invalid_phone_count': invalid_phone_count,
            'missing_name_count': missing_name_count
        }

    except Exception as e:
        return {
            'status': 'error',
            'message': f'Error processing CSV file: {str(e)}'
        }, 500


@contacts_bp.route('/export-contacts')
@login_required
def export_contacts():
    # Multi-tenant: Use org_query
    show_all = request.args.get('view') == 'all' and can_view_all_org_data()
    search_query = request.args.get('q', '').strip()

    if show_all:
        query = org_query(Contact)
    else:
        query = org_query(Contact).filter_by(user_id=current_user.id)

    if search_query:
        search_filter = (
                (Contact.first_name.ilike(f'%{search_query}%')) |
                (Contact.last_name.ilike(f'%{search_query}%')) |
                (Contact.email.ilike(f'%{search_query}%')) |
                (Contact.phone.ilike(f'%{search_query}%'))
        )
        query = query.filter(search_filter)

    contacts = query.all()

    output = StringIO()
    writer = csv.writer(output)

    writer.writerow(['first_name', 'last_name', 'email', 'phone', 'street_address',
                     'city', 'state', 'zip_code', 'notes', 'groups'])

    for contact in contacts:
        groups = ';'.join([group.name for group in contact.groups])
        writer.writerow([
            contact.first_name,
            contact.last_name,
            contact.email or '',
            contact.phone or '',
            contact.street_address or '',
            contact.city or '',
            contact.state or '',
            contact.zip_code or '',
            contact.notes or '',
            groups
        ])

    output.seek(0)
    filename = f"{current_user.first_name}_{current_user.last_name}_contacts.csv"

    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={
            "Content-Disposition": f"attachment; filename={filename}",
            "Content-Type": "text/csv",
        }
    )


# =============================================================================
# CONTACT FILE MANAGEMENT ENDPOINTS
# =============================================================================

@contacts_bp.route('/contact/<int:contact_id>/files', methods=['POST'])
@login_required
def upload_contact_file(contact_id):
    """Upload a file to a contact."""
    # Multi-tenant: Get contact within org
    contact = org_query(Contact).filter_by(id=contact_id).first_or_404()
    
    # Check permission
    if not can_view_all_org_data() and contact.user_id != current_user.id:
        return jsonify({'success': False, 'error': 'Permission denied'}), 403
    
    # Check if file was provided
    if 'file' not in request.files:
        return jsonify({'success': False, 'error': 'No file provided'}), 400
    
    file = request.files['file']
    
    if file.filename == '':
        return jsonify({'success': False, 'error': 'No file selected'}), 400
    
    # Validate file extension
    if not ContactFile.allowed_file(file.filename):
        allowed = ', '.join(ContactFile.ALLOWED_EXTENSIONS)
        return jsonify({
            'success': False, 
            'error': f'File type not allowed. Allowed types: {allowed}'
        }), 400
    
    # Read file data
    file_data = file.read()
    
    # Check file size
    if len(file_data) > ContactFile.MAX_FILE_SIZE:
        max_mb = ContactFile.MAX_FILE_SIZE / (1024 * 1024)
        return jsonify({
            'success': False, 
            'error': f'File too large. Maximum size is {max_mb:.0f} MB'
        }), 400
    
    try:
        # Upload to Supabase Storage
        result = supabase_storage.upload_contact_file(
            contact_id=contact_id,
            file_data=file_data,
            original_filename=file.filename,
            content_type=file.content_type
        )
        
        # Create database record
        contact_file = ContactFile(
            organization_id=current_user.organization_id,
            contact_id=contact_id,
            user_id=current_user.id,
            filename=result['filename'],
            original_filename=file.filename,
            file_type=file.content_type,
            file_size=result['size'],
            storage_path=result['path']
        )
        db.session.add(contact_file)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'file': {
                'id': contact_file.id,
                'original_filename': contact_file.original_filename,
                'file_type': contact_file.file_type,
                'file_size': supabase_storage.format_file_size(contact_file.file_size),
                'created_at': contact_file.created_at.strftime('%b %d, %Y'),
                'is_image': contact_file.is_image,
                'icon': supabase_storage.get_file_icon(contact_file.file_extension)
            }
        })
        
    except Exception as e:
        current_app.logger.error(f"File upload failed: {e}")
        return jsonify({
            'success': False, 
            'error': 'Upload failed. Please try again.'
        }), 500


@contacts_bp.route('/contact/<int:contact_id>/files/<int:file_id>/download')
@login_required
def download_contact_file(contact_id, file_id):
    """Get a signed URL for downloading a contact file."""
    # Multi-tenant: Get contact within org
    contact = org_query(Contact).filter_by(id=contact_id).first_or_404()
    
    # Check permission
    if not can_view_all_org_data() and contact.user_id != current_user.id:
        return jsonify({'success': False, 'error': 'Permission denied'}), 403
    
    # Get the file record
    contact_file = ContactFile.query.filter_by(
        id=file_id, 
        contact_id=contact_id
    ).first_or_404()
    
    try:
        # Generate signed URL (valid for 1 hour)
        signed_url = supabase_storage.get_signed_url(
            supabase_storage.CONTACT_FILES_BUCKET,
            contact_file.storage_path, 
            expires_in=3600
        )
        
        return jsonify({
            'success': True,
            'url': signed_url,
            'filename': contact_file.original_filename
        })
        
    except Exception as e:
        current_app.logger.error(f"Failed to generate download URL: {e}")
        return jsonify({
            'success': False, 
            'error': 'Could not generate download link'
        }), 500


@contacts_bp.route('/contact/<int:contact_id>/files/<int:file_id>', methods=['DELETE'])
@login_required
def delete_contact_file(contact_id, file_id):
    """Delete a contact file."""
    # Multi-tenant: Get contact within org
    contact = org_query(Contact).filter_by(id=contact_id).first_or_404()
    
    # Check permission
    if not can_view_all_org_data() and contact.user_id != current_user.id:
        return jsonify({'success': False, 'error': 'Permission denied'}), 403
    
    # Get the file record
    contact_file = ContactFile.query.filter_by(
        id=file_id, 
        contact_id=contact_id
    ).first_or_404()
    
    try:
        # Delete from Supabase Storage
        supabase_storage.delete_file(supabase_storage.CONTACT_FILES_BUCKET, contact_file.storage_path)
        
        # Delete database record
        db.session.delete(contact_file)
        db.session.commit()
        
        return jsonify({'success': True})
        
    except Exception as e:
        current_app.logger.error(f"Failed to delete file: {e}")
        return jsonify({
            'success': False, 
            'error': 'Could not delete file'
        }), 500


@contacts_bp.route('/contact/<int:contact_id>/files')
@login_required
def list_contact_files(contact_id):
    """List all files for a contact (JSON API)."""
    # Multi-tenant: Get contact within org
    contact = org_query(Contact).filter_by(id=contact_id).first_or_404()
    
    # Check permission
    if not can_view_all_org_data() and contact.user_id != current_user.id:
        return jsonify({'success': False, 'error': 'Permission denied'}), 403
    
    files = ContactFile.query.filter_by(contact_id=contact_id).order_by(
        ContactFile.created_at.desc()
    ).all()
    
    return jsonify({
        'success': True,
        'files': [{
            'id': f.id,
            'original_filename': f.original_filename,
            'file_type': f.file_type,
            'file_size': supabase_storage.format_file_size(f.file_size),
            'created_at': f.created_at.strftime('%b %d, %Y'),
            'is_image': f.is_image,
            'icon': supabase_storage.get_file_icon(f.file_extension)
        } for f in files]
    })


# =============================================================================
# VOICE MEMO ENDPOINTS
# =============================================================================

@contacts_bp.route('/contact/<int:contact_id>/voice-memos', methods=['POST'])
@login_required
def upload_voice_memo(contact_id):
    """Upload a voice memo for a contact."""
    from models import ContactVoiceMemo
    
    # Multi-tenant: Get contact within org
    contact = org_query(Contact).filter_by(id=contact_id).first_or_404()
    
    # Check permission
    if not can_view_all_org_data() and contact.user_id != current_user.id:
        return jsonify({'success': False, 'error': 'Permission denied'}), 403
    
    # Check if audio file was provided
    if 'audio' not in request.files:
        return jsonify({'success': False, 'error': 'No audio file provided'}), 400
    
    audio_file = request.files['audio']
    
    if audio_file.filename == '':
        return jsonify({'success': False, 'error': 'No file selected'}), 400
    
    # Read audio data
    audio_data = audio_file.read()
    
    # Check file size (max 10MB for ~3 minutes of audio)
    max_size = 10 * 1024 * 1024  # 10MB
    if len(audio_data) > max_size:
        return jsonify({
            'success': False, 
            'error': 'Audio file too large. Maximum size is 10MB (about 3 minutes).'
        }), 400
    
    # Get duration from form data (sent by frontend)
    duration_seconds = request.form.get('duration', type=int)
    
    try:
        # Upload to Supabase Storage
        result = supabase_storage.upload_voice_memo(
            contact_id=contact_id,
            file_data=audio_data,
            original_filename=audio_file.filename or 'memo.webm',
            content_type=audio_file.content_type or 'audio/webm'
        )
        
        # Create database record
        voice_memo = ContactVoiceMemo(
            organization_id=current_user.organization_id,
            contact_id=contact_id,
            user_id=current_user.id,
            storage_path=result['path'],
            file_name=result['filename'],
            duration_seconds=duration_seconds,
            file_size=result['size'],
            transcription_status='pending'
        )
        db.session.add(voice_memo)
        db.session.commit()
        
        # Auto-transcribe using Whisper API
        try:
            from services.ai_service import transcribe_audio
            transcription = transcribe_audio(
                audio_data=audio_data,
                filename=audio_file.filename or 'memo.webm'
            )
            voice_memo.transcription = transcription
            voice_memo.transcription_status = 'completed'
            db.session.commit()
            current_app.logger.info(f"Transcribed voice memo {voice_memo.id}: {len(transcription)} chars")
        except Exception as transcribe_error:
            current_app.logger.warning(f"Transcription failed for memo {voice_memo.id}: {transcribe_error}")
            voice_memo.transcription_status = 'failed'
            db.session.commit()
        
        # Get signed URL for immediate playback
        signed_url = supabase_storage.get_voice_memo_url(voice_memo.storage_path)
        
        return jsonify({
            'success': True,
            'memo': {
                'id': voice_memo.id,
                'duration_seconds': voice_memo.duration_seconds,
                'created_at': voice_memo.created_at.strftime('%b %d, %Y'),
                'transcription': voice_memo.transcription,
                'transcription_status': voice_memo.transcription_status,
                'audio_url': signed_url
            }
        })
        
    except Exception as e:
        current_app.logger.error(f"Voice memo upload failed: {e}")
        return jsonify({
            'success': False, 
            'error': 'Upload failed. Please try again.'
        }), 500


@contacts_bp.route('/contact/<int:contact_id>/voice-memos')
@login_required
def list_voice_memos(contact_id):
    """List all voice memos for a contact."""
    from models import ContactVoiceMemo
    
    # Multi-tenant: Get contact within org
    contact = org_query(Contact).filter_by(id=contact_id).first_or_404()
    
    # Check permission
    if not can_view_all_org_data() and contact.user_id != current_user.id:
        return jsonify({'success': False, 'error': 'Permission denied'}), 403
    
    # Defense-in-depth: filter by both contact_id AND organization_id
    memos = ContactVoiceMemo.query.filter_by(
        contact_id=contact_id,
        organization_id=current_user.organization_id
    ).order_by(ContactVoiceMemo.created_at.desc()).all()
    
    # Generate signed URLs for each memo
    memos_data = []
    for memo in memos:
        try:
            audio_url = supabase_storage.get_voice_memo_url(memo.storage_path)
        except Exception:
            audio_url = None
        
        memos_data.append({
            'id': memo.id,
            'duration_seconds': memo.duration_seconds,
            'created_at': memo.created_at.strftime('%b %d, %Y'),
            'created_at_iso': memo.created_at.isoformat(),
            'transcription': memo.transcription,
            'transcription_status': memo.transcription_status,
            'audio_url': audio_url
        })
    
    return jsonify({
        'success': True,
        'memos': memos_data
    })


@contacts_bp.route('/contact/<int:contact_id>/voice-memos/<int:memo_id>/url')
@login_required
def get_voice_memo_url(contact_id, memo_id):
    """Get a signed URL for a voice memo."""
    from models import ContactVoiceMemo
    
    # Multi-tenant: Get contact within org
    contact = org_query(Contact).filter_by(id=contact_id).first_or_404()
    
    # Check permission
    if not can_view_all_org_data() and contact.user_id != current_user.id:
        return jsonify({'success': False, 'error': 'Permission denied'}), 403
    
    # Get the memo record (defense-in-depth: also filter by org)
    memo = ContactVoiceMemo.query.filter_by(
        id=memo_id, 
        contact_id=contact_id,
        organization_id=current_user.organization_id
    ).first_or_404()
    
    try:
        signed_url = supabase_storage.get_voice_memo_url(memo.storage_path, expires_in=3600)
        return jsonify({
            'success': True,
            'url': signed_url
        })
    except Exception as e:
        current_app.logger.error(f"Failed to generate voice memo URL: {e}")
        return jsonify({
            'success': False, 
            'error': 'Could not generate playback link'
        }), 500


@contacts_bp.route('/contact/<int:contact_id>/voice-memos/<int:memo_id>', methods=['DELETE'])
@login_required
def delete_voice_memo(contact_id, memo_id):
    """Delete a voice memo."""
    from models import ContactVoiceMemo
    
    # Multi-tenant: Get contact within org
    contact = org_query(Contact).filter_by(id=contact_id).first_or_404()
    
    # Check permission
    if not can_view_all_org_data() and contact.user_id != current_user.id:
        return jsonify({'success': False, 'error': 'Permission denied'}), 403
    
    # Get the memo record (defense-in-depth: also filter by org)
    memo = ContactVoiceMemo.query.filter_by(
        id=memo_id, 
        contact_id=contact_id,
        organization_id=current_user.organization_id
    ).first_or_404()
    
    try:
        # Delete from Supabase Storage
        supabase_storage.delete_voice_memo(memo.storage_path)
        
        # Delete database record
        db.session.delete(memo)
        db.session.commit()
        
        return jsonify({'success': True})
        
    except Exception as e:
        current_app.logger.error(f"Failed to delete voice memo: {e}")
        return jsonify({
            'success': False, 
            'error': 'Could not delete voice memo'
        }), 500


# =============================================================================
# EMAIL HISTORY ENDPOINTS
# =============================================================================

@contacts_bp.route('/contact/<int:contact_id>/emails')
@login_required
def get_contact_emails(contact_id):
    """Get email threads for a contact (JSON API)."""
    from models import UserEmailIntegration
    from services import gmail_service
    
    # Multi-tenant: Get contact within org
    contact = org_query(Contact).filter_by(id=contact_id).first_or_404()
    
    # Check permission
    if not can_view_all_org_data() and contact.user_id != current_user.id:
        return jsonify({'success': False, 'error': 'Permission denied'}), 403
    
    # Check if Gmail is connected
    gmail_integration = UserEmailIntegration.query.filter_by(user_id=current_user.id).first()
    gmail_connected = gmail_integration and gmail_integration.sync_enabled
    
    if not gmail_connected:
        return jsonify({
            'success': True,
            'gmail_connected': False,
            'threads': []
        })
    
    # Get email threads
    threads = gmail_service.get_email_threads_for_contact(contact_id, current_user.id)
    
    return jsonify({
        'success': True,
        'gmail_connected': True,
        'threads': threads
    })


@contacts_bp.route('/contact/<int:contact_id>/emails/thread/<thread_id>')
@login_required
def get_email_thread(contact_id, thread_id):
    """Get all messages in a specific email thread."""
    from models import ContactEmail
    
    # Multi-tenant: Get contact within org
    contact = org_query(Contact).filter_by(id=contact_id).first_or_404()
    
    # Check permission
    if not can_view_all_org_data() and contact.user_id != current_user.id:
        return jsonify({'success': False, 'error': 'Permission denied'}), 403
    
    # Get all emails in this thread for this contact
    emails = ContactEmail.query.filter_by(
        contact_id=contact_id,
        user_id=current_user.id,
        gmail_thread_id=thread_id
    ).order_by(ContactEmail.sent_at.asc()).all()
    
    return jsonify({
        'success': True,
        'messages': [email.to_dict() for email in emails]
    })