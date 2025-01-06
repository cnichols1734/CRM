from flask import Blueprint, render_template, request, jsonify, flash, redirect, url_for
from flask_login import login_required, current_user
from models import db, ContactGroup
from functools import wraps

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