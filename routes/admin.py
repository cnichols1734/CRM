from flask import Blueprint, render_template, request, jsonify, flash, redirect, url_for
from flask_login import login_required, current_user
from models import db, ContactGroup
from functools import wraps
from services.docuseal_service import (
    DOCUMENT_FORMS, 
    MAPPINGS_DIR,
    parse_form_fields, 
    get_existing_mappings, 
    save_field_mappings,
    has_yaml_mapping,
    get_yaml_template_id,
    get_full_yaml_mapping,
    get_template,
    get_template_fields,
    get_template_submitters
)

admin_bp = Blueprint('admin', __name__)

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role != 'admin':
            flash('You must be an admin to access this page.', 'error')
            return redirect(url_for('main.index'))
        return f(*args, **kwargs)
    return decorated_function

@admin_bp.route('/admin/groups')
@login_required
@admin_required
def manage_groups():
    groups = ContactGroup.query.order_by(ContactGroup.category, ContactGroup.sort_order).all()
    categories = sorted(set(group.category for group in groups))
    return render_template('admin/groups.html', groups=groups, categories=categories)

@admin_bp.route('/admin/groups/add', methods=['POST'])
@login_required
@admin_required
def add_group():
    name = request.form.get('name')
    category = request.form.get('category')
    
    if not name or not category:
        return jsonify({'success': False, 'error': 'Name and category are required'}), 400
    
    # Find the highest sort_order in the category and add 1
    highest_sort = db.session.query(db.func.max(ContactGroup.sort_order)).\
        filter(ContactGroup.category == category).scalar() or 0
    new_sort_order = highest_sort + 1
    
    try:
        group = ContactGroup(name=name, category=category, sort_order=new_sort_order)
        db.session.add(group)
        db.session.commit()
        return jsonify({
            'success': True,
            'group': {
                'id': group.id,
                'name': group.name,
                'category': group.category,
                'sort_order': group.sort_order
            }
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500

@admin_bp.route('/admin/groups/<int:group_id>', methods=['PUT'])
@login_required
@admin_required
def update_group(group_id):
    group = ContactGroup.query.get_or_404(group_id)
    data = request.get_json()
    
    try:
        if 'name' in data:
            group.name = data['name']
        if 'category' in data:
            group.category = data['category']
        if 'sort_order' in data:
            group.sort_order = data['sort_order']
            
        db.session.commit()
        return jsonify({
            'success': True,
            'group': {
                'id': group.id,
                'name': group.name,
                'category': group.category,
                'sort_order': group.sort_order
            }
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500

@admin_bp.route('/admin/groups/<int:group_id>', methods=['DELETE'])
@login_required
@admin_required
def delete_group(group_id):
    group = ContactGroup.query.get_or_404(group_id)
    
    try:
        db.session.delete(group)
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500

@admin_bp.route('/admin/groups/reorder', methods=['POST'])
@login_required
@admin_required
def reorder_groups():
    data = request.get_json()
    try:
        for item in data:
            group = ContactGroup.query.get(item['id'])
            if group:
                group.sort_order = item['sort_order']
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


# =============================================================================
# DOCUMENT MAPPING ROUTES
# =============================================================================

@admin_bp.route('/admin/document-mapping')
@login_required
@admin_required
def document_mapping_list():
    """Landing page showing all available document forms to map."""
    documents = []
    for slug, info in DOCUMENT_FORMS.items():
        # Check if this document has a YAML mapping file
        is_mapped = has_yaml_mapping(slug)
        template_id = get_yaml_template_id(slug) if is_mapped else info.get('template_id')
        
        documents.append({
            'slug': slug,
            'name': info['name'],
            'description': info.get('description', ''),
            'template_id': template_id,
            'is_mapped': is_mapped
        })
    return render_template('admin/document_mapping_list.html', documents=documents)


@admin_bp.route('/admin/document-mapping/<slug>')
@login_required
@admin_required
def document_mapping_edit(slug):
    """Mapping UI for a specific document form."""
    if slug not in DOCUMENT_FORMS:
        flash('Document type not found.', 'error')
        return redirect(url_for('admin.document_mapping_list'))
    
    doc_info = DOCUMENT_FORMS[slug]
    
    # Parse form fields from HTML template
    crm_fields = parse_form_fields(slug)
    
    # Check if YAML exists and get template ID
    yaml_exists = has_yaml_mapping(slug)
    existing_yaml = get_full_yaml_mapping(slug) if yaml_exists else None
    template_id = existing_yaml.get('template_id') if existing_yaml else doc_info.get('template_id')
    
    # If we have a YAML with template ID, fetch DocuSeal fields
    docuseal_fields = []
    docuseal_submitters = []
    if yaml_exists and template_id:
        try:
            template_data = get_template(template_id)
            submitters = template_data.get('submitters', [])
            submitter_map = {s['uuid']: s['name'] for s in submitters}
            docuseal_submitters = [s['name'] for s in submitters]
            
            for field in template_data.get('fields', []):
                docuseal_fields.append({
                    'name': field.get('name', ''),
                    'type': field.get('type', 'text'),
                    'role': submitter_map.get(field.get('submitter_uuid', ''), 'Unknown')
                })
        except Exception as e:
            # If we can't fetch from API, continue without docuseal fields
            pass
    
    return render_template(
        'admin/document_mapping.html',
        doc_info=doc_info,
        slug=slug,
        crm_fields=crm_fields,
        docuseal_fields=docuseal_fields,
        docuseal_submitters=docuseal_submitters,
        template_id=template_id,
        yaml_exists=yaml_exists,
        existing_yaml=existing_yaml
    )


@admin_bp.route('/admin/document-mapping/<slug>/save', methods=['POST'])
@login_required
@admin_required
def document_mapping_save(slug):
    """Save field mappings to YAML file."""
    if slug not in DOCUMENT_FORMS:
        return jsonify({'success': False, 'error': 'Document type not found'}), 404
    
    data = request.get_json()
    mappings = data.get('mappings', [])
    
    if not mappings:
        return jsonify({'success': False, 'error': 'No mappings provided'}), 400
    
    success = save_field_mappings(slug, mappings)
    
    if success:
        return jsonify({'success': True, 'message': 'Mappings saved successfully'})
    else:
        return jsonify({'success': False, 'error': 'Failed to save mappings'}), 500


@admin_bp.route('/admin/document-mapping/fetch-template', methods=['POST'])
@login_required
@admin_required
def fetch_docuseal_template():
    """Fetch template fields from DocuSeal API."""
    data = request.get_json()
    template_id = data.get('template_id')
    
    if not template_id:
        return jsonify({'success': False, 'error': 'Template ID is required'}), 400
    
    try:
        template_id = int(template_id)
    except (ValueError, TypeError):
        return jsonify({'success': False, 'error': 'Template ID must be a number'}), 400
    
    try:
        # Fetch template from DocuSeal API
        template_data = get_template(template_id)
        
        # Get fields and submitters
        fields = template_data.get('fields', [])
        submitters = template_data.get('submitters', [])
        
        # Build submitter UUID to name map
        submitter_map = {s['uuid']: s['name'] for s in submitters}
        
        # Organize fields by submitter role
        fields_by_role = {}
        for field in fields:
            submitter_uuid = field.get('submitter_uuid', '')
            role_name = submitter_map.get(submitter_uuid, 'Unknown')
            
            if role_name not in fields_by_role:
                fields_by_role[role_name] = []
            
            fields_by_role[role_name].append({
                'name': field.get('name', ''),
                'type': field.get('type', 'text'),
                'required': field.get('required', False),
                'uuid': field.get('uuid', '')
            })
        
        return jsonify({
            'success': True,
            'template_id': template_id,
            'template_name': template_data.get('name', ''),
            'submitters': [s['name'] for s in submitters],
            'fields_by_role': fields_by_role,
            'all_fields': [{
                'name': f.get('name', ''),
                'type': f.get('type', 'text'),
                'required': f.get('required', False),
                'role': submitter_map.get(f.get('submitter_uuid', ''), 'Unknown')
            } for f in fields]
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@admin_bp.route('/admin/document-mapping/<slug>/save-full', methods=['POST'])
@login_required
@admin_required
def document_mapping_save_full(slug):
    """Save complete mapping data to YAML file (supports all mapping types)."""
    if slug not in DOCUMENT_FORMS:
        return jsonify({'success': False, 'error': 'Document type not found'}), 404
    
    data = request.get_json()
    
    try:
        from services.docuseal_service import save_full_mapping
        success = save_full_mapping(slug, data)
        
        if success:
            return jsonify({'success': True, 'message': 'Mappings saved successfully'})
        else:
            return jsonify({'success': False, 'error': 'Failed to save mappings'}), 500
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500