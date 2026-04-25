"""
In-app notification service.

Handles creation, retrieval, marking-read, and preference checks
for the per-user notification system.
"""
from datetime import datetime, timedelta
from flask import current_app
from models import db, Notification, UserNotificationPreference


# ---------------------------------------------------------------------------
# Preference helpers
# ---------------------------------------------------------------------------

def get_user_preference(user_id, category):
    """Return the preference row for a user + category, or None (= default on)."""
    return UserNotificationPreference.query.filter_by(
        user_id=user_id, category=category
    ).first()


def is_channel_enabled(user_id, category, channel='in_app'):
    """Check whether *channel* is enabled for *category*.

    When no explicit preference row exists the default is **enabled**.
    """
    pref = get_user_preference(user_id, category)
    if pref is None:
        return True
    return getattr(pref, f'{channel}_enabled', True)


def set_preference(user_id, organization_id, category, *, in_app=None, email=None):
    """Create or update a notification preference row.

    Only the channels whose keyword argument is not None are touched.
    """
    pref = get_user_preference(user_id, category)
    if pref is None:
        pref = UserNotificationPreference(
            user_id=user_id,
            organization_id=organization_id,
            category=category,
        )
        db.session.add(pref)

    if in_app is not None:
        pref.in_app_enabled = in_app
    if email is not None:
        pref.email_enabled = email

    db.session.commit()
    return pref


def get_all_preferences(user_id):
    """Return a dict keyed by category with the current preference state.

    Categories that have no explicit row are filled in with defaults.
    """
    rows = UserNotificationPreference.query.filter_by(user_id=user_id).all()
    prefs = {r.category: r for r in rows}

    result = {}
    for cat_key, cat_label in Notification.CATEGORIES.items():
        row = prefs.get(cat_key)
        result[cat_key] = {
            'label': cat_label,
            'in_app': row.in_app_enabled if row else True,
            'email': row.email_enabled if row else True,
        }
    return result


# ---------------------------------------------------------------------------
# Notification CRUD
# ---------------------------------------------------------------------------

def create_notification(*, user_id, organization_id, category, title,
                        body=None, icon='fa-bell', action_url=None,
                        respect_preference=True):
    """Create an in-app notification if the user's preferences allow it.

    Returns the Notification row or None if suppressed by preference.
    """
    if respect_preference and not is_channel_enabled(user_id, category, 'in_app'):
        return None

    notif = Notification(
        user_id=user_id,
        organization_id=organization_id,
        category=category,
        title=title,
        body=body,
        icon=icon,
        action_url=action_url,
    )
    db.session.add(notif)
    db.session.commit()
    return notif


def create_notifications_bulk(items, *, respect_preference=True):
    """Create many notifications in one commit.

    *items* is a list of dicts with the same keys as create_notification().
    Returns the list of created Notification objects (skipped ones omitted).
    """
    created = []
    for item in items:
        if respect_preference and not is_channel_enabled(
                item['user_id'], item['category'], 'in_app'):
            continue
        notif = Notification(
            user_id=item['user_id'],
            organization_id=item['organization_id'],
            category=item['category'],
            title=item['title'],
            body=item.get('body'),
            icon=item.get('icon', 'fa-bell'),
            action_url=item.get('action_url'),
        )
        db.session.add(notif)
        created.append(notif)

    if created:
        db.session.commit()
    return created


def get_unread_count(user_id):
    """Return the number of unread notifications for a user."""
    return Notification.query.filter_by(
        user_id=user_id, is_read=False
    ).count()


def get_notifications(user_id, *, limit=20, include_read=False):
    """Return recent notifications for the bell popover."""
    q = Notification.query.filter_by(user_id=user_id)
    if not include_read:
        q = q.filter_by(is_read=False)
    return q.order_by(Notification.created_at.desc()).limit(limit).all()


def mark_read(notification_id, user_id):
    """Mark a single notification read. Returns True on success."""
    notif = Notification.query.filter_by(
        id=notification_id, user_id=user_id
    ).first()
    if notif:
        notif.mark_read()
        db.session.commit()
        return True
    return False


def mark_all_read(user_id):
    """Mark every unread notification for a user as read."""
    now = datetime.utcnow()
    count = Notification.query.filter_by(
        user_id=user_id, is_read=False
    ).update({'is_read': True, 'read_at': now})
    db.session.commit()
    return count


def clear_all(user_id):
    """Delete all notifications for a user."""
    count = Notification.query.filter_by(user_id=user_id).delete()
    db.session.commit()
    return count


def prune_old_notifications(days=90):
    """Delete notifications older than *days*. Intended for a periodic job."""
    cutoff = datetime.utcnow() - timedelta(days=days)
    count = Notification.query.filter(Notification.created_at < cutoff).delete()
    db.session.commit()
    current_app.logger.info(f"Pruned {count} notifications older than {days} days")
    return count
