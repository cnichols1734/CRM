"""
Notification routes — bell popover API + preference settings page.
"""
from flask import Blueprint, jsonify, request, render_template, flash, redirect, url_for
from flask_login import login_required, current_user
from services import notification_service as ns
from models import Notification

notifications_bp = Blueprint('notifications', __name__)


# ── JSON API (consumed by the bell icon JS) ──────────────────────────────

@notifications_bp.route('/api/notifications')
@login_required
def list_notifications():
    include_read = request.args.get('include_read', '0') == '1'
    limit = min(int(request.args.get('limit', 20)), 50)
    notifs = ns.get_notifications(current_user.id, limit=limit,
                                  include_read=include_read)
    return jsonify({
        'notifications': [n.to_dict() for n in notifs],
        'unread_count': ns.get_unread_count(current_user.id),
    })


@notifications_bp.route('/api/notifications/unread-count')
@login_required
def unread_count():
    return jsonify({'unread_count': ns.get_unread_count(current_user.id)})


@notifications_bp.route('/api/notifications/<int:notif_id>/read', methods=['POST'])
@login_required
def mark_read(notif_id):
    ok = ns.mark_read(notif_id, current_user.id)
    return jsonify({'ok': ok, 'unread_count': ns.get_unread_count(current_user.id)})


@notifications_bp.route('/api/notifications/read-all', methods=['POST'])
@login_required
def mark_all_read():
    count = ns.mark_all_read(current_user.id)
    return jsonify({'marked': count, 'unread_count': 0})


@notifications_bp.route('/api/notifications/clear-all', methods=['POST'])
@login_required
def clear_all():
    count = ns.clear_all(current_user.id)
    return jsonify({'cleared': count, 'unread_count': 0})


# ── Preference settings (HTML page) ─────────────────────────────────────

@notifications_bp.route('/settings/notifications')
@login_required
def settings_page():
    prefs = ns.get_all_preferences(current_user.id)
    return render_template('notifications/settings.html', prefs=prefs,
                           categories=Notification.CATEGORIES)


@notifications_bp.route('/settings/notifications', methods=['POST'])
@login_required
def save_settings():
    for cat_key in Notification.CATEGORIES:
        in_app = request.form.get(f'{cat_key}_in_app') == 'on'
        email = request.form.get(f'{cat_key}_email') == 'on'
        ns.set_preference(
            user_id=current_user.id,
            organization_id=current_user.organization_id,
            category=cat_key,
            in_app=in_app,
            email=email,
        )
    flash('Notification preferences saved.', 'success')
    return redirect(url_for('notifications.settings_page'))
