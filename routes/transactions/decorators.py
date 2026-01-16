# routes/transactions/decorators.py
"""
Shared decorators for transaction routes.
"""

from functools import wraps
from flask import flash, redirect, url_for
from flask_login import current_user
from feature_flags import can_access_transactions


def transactions_required(f):
    """Decorator to check if user can access transactions module."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not can_access_transactions(current_user):
            flash('You do not have access to this feature.', 'error')
            return redirect(url_for('main.dashboard'))
        return f(*args, **kwargs)
    return decorated_function
