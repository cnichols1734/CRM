"""Self-service per-user contact group management."""

from collections import defaultdict

from flask import Blueprint, jsonify, render_template, request
from flask_login import current_user, login_required

from services.contact_group_service import (
    ContactGroupError,
    add_missing_defaults,
    create_group,
    delete_group,
    group_usage_counts,
    list_user_groups,
    reorder_groups,
    serialize_group,
    update_group,
)
from services.tenant_service import DEFAULT_CONTACT_GROUPS

groups_bp = Blueprint('groups', __name__)


def _org_user():
    return current_user.organization_id, current_user.id


def _json_error(exc: ContactGroupError):
    return jsonify({'success': False, 'error': exc.message}), exc.status_code


@groups_bp.route('/groups')
@login_required
def customize():
    """Customize Groups page — each user manages their own catalog."""
    org_id, user_id = _org_user()
    groups = list_user_groups(org_id, user_id, active_only=False, use_cache=False)
    usage = group_usage_counts(org_id, user_id)

    by_category = defaultdict(list)
    for group in groups:
        by_category[group.category].append({
            **serialize_group(group, usage=usage.get(group.id, 0)),
        })

    # Stable category order: defaults first, then any custom categories
    default_categories = []
    seen = set()
    for item in DEFAULT_CONTACT_GROUPS:
        if item['category'] not in seen:
            default_categories.append(item['category'])
            seen.add(item['category'])
    extra = sorted(c for c in by_category if c not in seen)
    category_order = default_categories + extra

    categories = sorted(by_category.keys())

    return render_template(
        'groups/customize.html',
        groups_by_category=by_category,
        category_order=category_order,
        categories=categories,
        total_groups=len(groups),
    )


@groups_bp.route('/groups', methods=['POST'])
@login_required
def create():
    org_id, user_id = _org_user()
    data = request.get_json(silent=True) or request.form
    try:
        group = create_group(
            org_id,
            user_id,
            data.get('name'),
            data.get('category'),
        )
        return jsonify({
            'success': True,
            'group': serialize_group(group, usage=0),
        })
    except ContactGroupError as exc:
        return _json_error(exc)


@groups_bp.route('/groups/<int:group_id>', methods=['PUT'])
@login_required
def update(group_id):
    org_id, user_id = _org_user()
    data = request.get_json(silent=True) or {}
    try:
        kwargs = {}
        if 'name' in data:
            kwargs['name'] = data['name']
        if 'category' in data:
            kwargs['category'] = data['category']
        if 'sort_order' in data:
            kwargs['sort_order'] = data['sort_order']
        if 'is_active' in data:
            kwargs['is_active'] = bool(data['is_active'])
        group = update_group(org_id, user_id, group_id, **kwargs)
        usage = group_usage_counts(org_id, user_id).get(group.id, 0)
        return jsonify({
            'success': True,
            'group': serialize_group(group, usage=usage),
        })
    except ContactGroupError as exc:
        return _json_error(exc)


@groups_bp.route('/groups/<int:group_id>', methods=['DELETE'])
@login_required
def delete(group_id):
    org_id, user_id = _org_user()
    try:
        delete_group(org_id, user_id, group_id)
        return jsonify({'success': True})
    except ContactGroupError as exc:
        return _json_error(exc)


@groups_bp.route('/groups/reorder', methods=['POST'])
@login_required
def reorder():
    org_id, user_id = _org_user()
    data = request.get_json(silent=True) or []
    try:
        reorder_groups(org_id, user_id, data)
        return jsonify({'success': True})
    except ContactGroupError as exc:
        return _json_error(exc)


@groups_bp.route('/groups/restore-defaults', methods=['POST'])
@login_required
def restore_defaults():
    org_id, user_id = _org_user()
    try:
        created = add_missing_defaults(org_id, user_id)
        return jsonify({
            'success': True,
            'created_count': len(created),
            'groups': [serialize_group(g, usage=0) for g in created],
        })
    except ContactGroupError as exc:
        return _json_error(exc)
