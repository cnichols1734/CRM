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

@contacts_bp.route('/contact/<int:contact_id>')
@login_required
def view_contact(contact_id):
    contact = Contact.query.get_or_404(contact_id)
    if not current_user.role == 'admin' and contact.user_id != current_user.id:
        abort(403)

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
            phone=form.phone.data,
            street_address=form.street_address.data,
            city=form.city.data,
            state=form.state.data,
            zip_code=form.zip_code.data,
            notes=form.notes.data,
            potential_commission=form.potential_commission.data or 5000.00,
            last_email_date=form.last_email_date.data,
            last_text_date=form.last_text_date.data,
            last_phone_call_date=form.last_phone_call_date.data
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
        contact.phone = request.form.get('phone')
        contact.street_address = request.form.get('street_address')
        contact.city = request.form.get('city')
        contact.state = request.form.get('state')
        contact.zip_code = request.form.get('zip_code')
        contact.notes = request.form.get('notes')
        contact.potential_commission = float(request.form.get('potential_commission', 5000.00))

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
        stream = StringIO(file.stream.read().decode("UTF8"), newline=None)
        csv_data = csv.DictReader(stream)

        success_count = 0
        error_count = 0

        for row in csv_data:
            try:
                contact = Contact(
                    user_id=current_user.id,
                    first_name=row['first_name'].strip(),
                    last_name=row['last_name'].strip(),
                    email=row['email'].strip() if row['email'] else None,
                    phone=row['phone'].strip() if row['phone'] else None,
                    street_address=row['street_address'].strip() if row['street_address'] else None,
                    city=row['city'].strip() if row['city'] else None,
                    state=row['state'].strip() if row['state'] else None,
                    zip_code=row['zip_code'].strip() if row['zip_code'] else None,
                    notes=row['notes'].strip() if row['notes'] else None
                )

                if 'groups' in row and row['groups']:
                    group_names = [name.strip() for name in row['groups'].split(';')]
                    groups = ContactGroup.query.filter(ContactGroup.name.in_(group_names)).all()
                    contact.groups = groups

                db.session.add(contact)
                success_count += 1

            except Exception:
                error_count += 1
                continue

        db.session.commit()
        return {
            'status': 'success',
            'success_count': success_count,
            'error_count': error_count
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