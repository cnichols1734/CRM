from flask import Blueprint, render_template, redirect, url_for, flash, request, abort, Response, jsonify, current_app
from flask_login import login_required, current_user
from models import db, Contact, ContactGroup, User, Transaction, TransactionParticipant, ContactFile
from feature_flags import can_access_transactions
from forms import ContactForm
from services import supabase_storage
import csv
from io import StringIO
from sqlalchemy import func
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
    contact = Contact.query.get_or_404(contact_id)
    if not current_user.role == 'admin' and contact.user_id != current_user.id:
        abort(403)

    # Get the next contact in alphabetical order
    next_contact = Contact.query.filter(
        Contact.user_id == contact.user_id,
        Contact.first_name > contact.first_name
    ).order_by(Contact.first_name.asc(), Contact.last_name.asc()).first()

    # If no next contact (we're at the end), get the first contact
    if not next_contact:
        next_contact = Contact.query.filter(
            Contact.user_id == contact.user_id
        ).order_by(Contact.first_name.asc(), Contact.last_name.asc()).first()

    # Get the previous contact in alphabetical order
    prev_contact = Contact.query.filter(
        Contact.user_id == contact.user_id,
        Contact.first_name < contact.first_name
    ).order_by(Contact.first_name.desc(), Contact.last_name.desc()).first()

    # If no previous contact (we're at the start), get the last contact
    if not prev_contact:
        prev_contact = Contact.query.filter(
            Contact.user_id == contact.user_id
        ).order_by(Contact.first_name.desc(), Contact.last_name.desc()).first()

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

    all_groups = ContactGroup.query.all()
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

    return render_template('contacts/view.html', 
                         contact=contact, 
                         all_groups=all_groups,
                         next_contact=next_contact,
                         prev_contact=prev_contact,
                         now=now,
                         related_transactions=related_transactions,
                         show_transactions=show_transactions,
                         contact_files=contact_files)


@contacts_bp.route('/contacts/create', methods=['GET', 'POST'])
@login_required
def create_contact():
    form = ContactForm()
    form.group_ids.choices = [(g.id, g.name) for g in ContactGroup.query.order_by('sort_order')]
    
    # Check for return_to parameter (for redirecting back to transaction)
    return_to = request.args.get('return_to')
    transaction_id = request.args.get('transaction_id', type=int)

    if form.validate_on_submit():
        contact = Contact(
            user_id=current_user.id,
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

        selected_groups = ContactGroup.query.filter(
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
        return redirect(url_for('main.index'))

    return render_template('contacts/form.html', form=form, return_transaction_id=transaction_id)


@contacts_bp.route('/contacts/<int:contact_id>/edit', methods=['POST'])
@login_required
def edit_contact(contact_id):
    contact = Contact.query.get_or_404(contact_id)

    if not current_user.role == 'admin' and contact.user_id != current_user.id:
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
        contact.groups = ContactGroup.query.filter(
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
    
    contact = Contact.query.get_or_404(contact_id)

    if not current_user.role == 'admin' and contact.user_id != current_user.id:
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
                target_user = User.query.get(requested_user_id_int)
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
                    user_id=target_user_id,
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
                        groups = ContactGroup.query.filter(ContactGroup.name.in_(group_names)).all()
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
    show_all = request.args.get('view') == 'all' and current_user.role == 'admin'
    search_query = request.args.get('q', '').strip()

    if show_all:
        query = Contact.query
    else:
        query = Contact.query.filter_by(user_id=current_user.id)

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
    contact = Contact.query.get_or_404(contact_id)
    
    # Check permission
    if not current_user.role == 'admin' and contact.user_id != current_user.id:
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
    contact = Contact.query.get_or_404(contact_id)
    
    # Check permission
    if not current_user.role == 'admin' and contact.user_id != current_user.id:
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
    contact = Contact.query.get_or_404(contact_id)
    
    # Check permission
    if not current_user.role == 'admin' and contact.user_id != current_user.id:
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
    contact = Contact.query.get_or_404(contact_id)
    
    # Check permission
    if not current_user.role == 'admin' and contact.user_id != current_user.id:
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