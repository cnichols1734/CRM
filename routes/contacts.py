from flask import Blueprint, render_template, redirect, url_for, flash, request, abort, Response, jsonify
from flask_login import login_required, current_user
from models import db, Contact, ContactGroup
from forms import ContactForm
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

    return render_template('view_contact.html', 
                         contact=contact, 
                         all_groups=all_groups,
                         next_contact=next_contact,
                         prev_contact=prev_contact,
                         now=now)


@contacts_bp.route('/contacts/create', methods=['GET', 'POST'])
@login_required
def create_contact():
    form = ContactForm()
    form.group_ids.choices = [(g.id, g.name) for g in ContactGroup.query.order_by('sort_order')]

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
        flash('Contact created successfully!', 'success')
        return redirect(url_for('main.index'))

    return render_template('contact_form.html', form=form)


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
    contact = Contact.query.get_or_404(contact_id)

    if not current_user.role == 'admin' and contact.user_id != current_user.id:
        abort(403)

    try:
        db.session.delete(contact)
        db.session.commit()
        flash('Contact deleted successfully!', 'success')
        return {'status': 'success'}, 200
    except Exception as e:
        db.session.rollback()
        return {'status': 'error', 'message': 'Error deleting contact'}, 500


@contacts_bp.route('/import-contacts', methods=['POST'])
@login_required
def import_contacts():
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

        for row_num, row in enumerate(csv_data, start=1):
            try:
                # Validate required fields
                if not row['first_name'].strip() or not row['last_name'].strip():
                    error_details.append(f"Row {row_num}: First name and last name are required")
                    error_count += 1
                    continue

                # Handle phone number
                phone = row['phone'].strip() if row.get('phone') else None
                if phone:
                    # Remove any non-digit characters first
                    phone = ''.join(filter(str.isdigit, phone))
                    if not phone:
                        error_details.append(f"Row {row_num}: Invalid phone number format")
                        error_count += 1
                        continue
                    
                    # Format the phone number
                    formatted_phone = format_phone_number(phone)
                    if not formatted_phone:
                        error_details.append(f"Row {row_num}: Invalid phone number length")
                        error_count += 1
                        continue
                else:
                    formatted_phone = None

                contact = Contact(
                    user_id=current_user.id,
                    first_name=row['first_name'].strip(),
                    last_name=row['last_name'].strip(),
                    email=row.get('email', '').strip() or None,
                    phone=formatted_phone,
                    street_address=row.get('street_address', '').strip() or None,
                    city=row.get('city', '').strip() or None,
                    state=row.get('state', '').strip() or None,
                    zip_code=row.get('zip_code', '').strip() or None,
                    notes=row.get('notes', '').strip() or None
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
                    'error_details': error_details
                }, 500
        
        # If we have any errors, include them in the response
        if error_count > 0:
            return {
                'status': 'partial_success' if success_count > 0 else 'error',
                'success_count': success_count,
                'error_count': error_count,
                'error_details': error_details
            }
        
        return {
            'status': 'success',
            'success_count': success_count,
            'message': f'Successfully imported {success_count} contacts'
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