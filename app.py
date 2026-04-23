from dotenv import load_dotenv
load_dotenv()  # Load .env file before any other imports

import os

import warnings
import html
import time
import logging
import sys
import pytz
from datetime import datetime
from urllib.parse import urlparse
from sqlalchemy.exc import SAWarning
warnings.filterwarnings('ignore', category=SAWarning, message='.*relationship .* will copy column .*')

# Timezone for display (Central Time)
CENTRAL_TZ = pytz.timezone('America/Chicago')

try:
    import psutil
except ImportError:  # pragma: no cover - psutil is installed in production
    psutil = None

from flask import Flask, render_template, session, redirect, url_for, flash, request, g
from flask.logging import default_handler
from flask_login import LoginManager, current_user, logout_user
from flask_mail import Mail
from flask_migrate import Migrate
from sqlalchemy import text
from models import db, User
from routes.main import main_bp
from routes.auth import auth_bp
from routes.tasks import tasks_bp
from routes.contacts import contacts_bp
from routes.ai_chat import ai_chat
from routes.daily_todo import daily_todo
from routes.user_todo import bp as user_todo_bp
from routes.admin import admin_bp
from routes.marketing import marketing
from routes.action_plan import action_plan_bp
from routes.company_updates import company_updates_bp
from routes.transactions import transactions_bp
from routes.organization import org_bp
from routes.platform_admin import platform_bp
from routes.contact_us import contact_bp
from routes.gmail_integration import gmail_bp
from routes.reports import reports_bp
from routes.tax_protest import tax_protest_bp
from routes.market_insights import market_insights_bp

SLOW_REQUEST_WARNING_MS = 2000


class _MaxLevelFilter(logging.Filter):
    def __init__(self, exclusive_upper_bound):
        super().__init__()
        self.exclusive_upper_bound = exclusive_upper_bound

    def filter(self, record):
        return record.levelno < self.exclusive_upper_bound


def configure_application_logging():
    """Send non-error app logs to stdout so Railway reserves red for real errors."""
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(logging.INFO)

    formatter = logging.Formatter('%(levelname)s:%(name)s:%(message)s')

    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setLevel(logging.INFO)
    stdout_handler.addFilter(_MaxLevelFilter(logging.ERROR))
    stdout_handler.setFormatter(formatter)

    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setLevel(logging.ERROR)
    stderr_handler.setFormatter(formatter)

    root_logger.addHandler(stdout_handler)
    root_logger.addHandler(stderr_handler)
    logging.captureWarnings(True)


configure_application_logging()


def _current_rss_mb():
    if psutil is None:
        return None
    try:
        process = psutil.Process(os.getpid())
        return round(process.memory_info().rss / 1024 / 1024, 1)
    except Exception:
        return None

def create_app():
    app = Flask(__name__)
    app.config.from_object('config.Config')

    if default_handler in app.logger.handlers:
        app.logger.removeHandler(default_handler)
    app.logger.propagate = True
    app.logger.setLevel(logging.INFO)

    # Initialize extensions
    db.init_app(app)
    migrate = Migrate(app, db)
    
    login_manager = LoginManager()
    login_manager.init_app(app)
    login_manager.login_view = 'auth.login'
    login_manager.login_message = 'Your session has expired. Please log in again to continue.'
    login_manager.login_message_category = 'info'

    # Add custom filters for templates
    app.jinja_env.filters['abs'] = abs
    app.jinja_env.filters['unescape'] = html.unescape
    
    def strip_html_smart(text):
        """Strip HTML tags while preserving word spacing."""
        import re
        if not text:
            return ''
        # Add space before/after block elements
        result = re.sub(r'<(p|div|br|li|h[1-6]|tr|td)[^>]*>', ' ', text, flags=re.IGNORECASE)
        result = re.sub(r'</(p|div|li|h[1-6]|tr|td|ul|ol)>', ' ', result, flags=re.IGNORECASE)
        result = re.sub(r'<br\s*/?>', ' ', result, flags=re.IGNORECASE)
        # Remove remaining tags
        result = re.sub(r'<[^>]+>', '', result)
        # Decode entities
        result = result.replace('&nbsp;', ' ').replace('&amp;', '&')
        # Collapse whitespace
        result = re.sub(r'\s+', ' ', result).strip()
        return result
    
    app.jinja_env.filters['strip_html'] = strip_html_smart
    
    def to_central_time(dt):
        """Convert UTC datetime to Central Time for display."""
        if dt is None:
            return None
        # Assume dt is naive UTC, make it aware
        utc_dt = pytz.utc.localize(dt)
        # Convert to Central Time
        return utc_dt.astimezone(CENTRAL_TZ)
    
    app.jinja_env.filters['to_central'] = to_central_time
    
    def timeago(dt):
        """Convert datetime to human-readable 'time ago' string."""
        if dt is None:
            return 'Never'
        now = datetime.utcnow()
        diff = now - dt
        
        seconds = diff.total_seconds()
        if seconds < 60:
            return 'Just now'
        elif seconds < 3600:
            mins = int(seconds / 60)
            return f'{mins} minute{"s" if mins != 1 else ""} ago'
        elif seconds < 86400:
            hours = int(seconds / 3600)
            return f'{hours} hour{"s" if hours != 1 else ""} ago'
        elif seconds < 604800:
            days = int(seconds / 86400)
            if days == 1:
                return 'Yesterday'
            return f'{days} days ago'
        else:
            return dt.strftime('%b %d, %Y')
    
    app.jinja_env.filters['timeago'] = timeago

    # Context processor to make feature flags available in templates
    @app.context_processor
    def inject_feature_flags():
        from feature_flags import org_has_feature, can_access_reports, can_access_transactions
        return dict(
            org_has_feature=org_has_feature,
            can_access_reports=can_access_reports,
            can_access_transactions=can_access_transactions,
        )

    # Initialize Flask-Mail
    mail = Mail()
    mail.init_app(app)

    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    # Register blueprints
    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(tasks_bp)
    app.register_blueprint(contacts_bp)
    app.register_blueprint(daily_todo)
    app.register_blueprint(ai_chat)
    app.register_blueprint(user_todo_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(marketing)
    app.register_blueprint(action_plan_bp)
    app.register_blueprint(company_updates_bp)
    app.register_blueprint(transactions_bp)
    app.register_blueprint(org_bp)
    app.register_blueprint(platform_bp)
    app.register_blueprint(contact_bp)
    app.register_blueprint(gmail_bp)
    app.register_blueprint(reports_bp)
    app.register_blueprint(tax_protest_bp)
    app.register_blueprint(market_insights_bp)

    # =========================================================================
    # MULTI-TENANT RLS CONTEXT
    # =========================================================================

    @app.before_request
    def start_request_timer():
        g._request_started_at = time.perf_counter()
    
    @app.before_request
    def set_tenant_context():
        """
        Set RLS context and validate org status with session caching.
        This runs before every request to:
        1. Check if the user's org is still active
        2. Check if the user's session has been invalidated
        3. Set the PostgreSQL app.current_org_id for RLS
        """
        if not current_user.is_authenticated:
            return
        
        org_id = current_user.organization_id
        
        # Skip if no organization (shouldn't happen after migration)
        if not org_id:
            return
        
        # Cache org status in Flask session to reduce DB hits
        cached_org_id = session.get('_org_id')
        cached_org_status = session.get('_org_status')
        cached_session_valid_at = session.get('_session_invalidated_at')
        
        # Refresh cache if org changed or cache is stale (every 5 minutes)
        cache_age = session.get('_org_cache_time', 0)
        now_ts = datetime.utcnow().timestamp()
        
        if cached_org_id != org_id or (now_ts - cache_age) > 300:
            org = current_user.organization
            if org:
                session['_org_id'] = org.id
                session['_org_status'] = org.status
                session['_session_invalidated_at'] = (
                    org.session_invalidated_at.timestamp() 
                    if org.session_invalidated_at else 0
                )
                session['_org_cache_time'] = now_ts
                cached_org_status = org.status
                cached_session_valid_at = session['_session_invalidated_at']
        
        # Check org is active
        if cached_org_status and cached_org_status != 'active':
            logout_user()
            session.clear()
            flash('Your organization account is no longer active.', 'error')
            return redirect(url_for('auth.login'))
        
        # Check session wasn't invalidated
        session_created = session.get('_session_created_at', 0)
        if cached_session_valid_at and session_created < cached_session_valid_at:
            logout_user()
            session.clear()
            flash('Your session has expired. Please log in again.', 'info')
            return redirect(url_for('auth.login'))
        
        # Set RLS context - SET LOCAL scopes to current transaction only
        try:
            db.session.execute(
                text("SET LOCAL app.current_org_id = :org_id"),
                {'org_id': org_id}
            )
        except Exception:
            # If setting fails (e.g., not PostgreSQL), continue anyway
            # RLS won't work but app-level filtering still protects data
            pass

    @app.after_request
    def record_session_creation(response):
        """Record when session was created for invalidation checks."""
        if current_user.is_authenticated and '_session_created_at' not in session:
            session['_session_created_at'] = datetime.utcnow().timestamp()

        started_at = getattr(g, '_request_started_at', None)
        if started_at is not None:
            duration_ms = round((time.perf_counter() - started_at) * 1000, 1)
            endpoint = request.endpoint or 'unknown'
            user_id = current_user.id if current_user.is_authenticated else None
            org_id = current_user.organization_id if current_user.is_authenticated else None
            should_log = (
                endpoint.startswith('tax_protest.')
                or duration_ms >= SLOW_REQUEST_WARNING_MS
                or response.status_code >= 500
            )
            if should_log:
                log_fn = app.logger.warning if (
                    duration_ms >= SLOW_REQUEST_WARNING_MS or response.status_code >= 500
                ) else app.logger.info
                log_fn(
                    'request_summary method=%s path=%s endpoint=%s status=%s duration_ms=%s rss_mb=%s user_id=%s org_id=%s pid=%s',
                    request.method,
                    request.path,
                    endpoint,
                    response.status_code,
                    duration_ms,
                    _current_rss_mb(),
                    user_id,
                    org_id,
                    os.getpid(),
                )
        return response

    @app.teardown_appcontext
    def cleanup_db_session(exception=None):
        """
        Ensure database session is properly closed after each request.
        This prevents connection leaks even if errors occur during request processing.
        """
        try:
            if exception:
                db.session.rollback()
            db.session.remove()
        except Exception:
            # Ignore errors during cleanup - session may already be closed
            pass

    # Load and validate document definitions on startup
    # This ensures all YAML configs are valid before the app starts
    with app.app_context():
        from services.documents import DocumentLoader
        try:
            DocumentLoader.load_all()
            app.logger.info(f"Loaded {len(DocumentLoader.all())} document definitions")
        except Exception as e:
            app.logger.error(f"Failed to load document definitions: {e}")
            # Don't fail startup - old system still works as fallback
            # raise

    return app

app = create_app()

def _is_local_database_url(database_url):
    """Only local SQLite/localhost databases are safe for direct app.py runs."""
    if not database_url:
        return False

    parsed = urlparse(database_url)
    if parsed.scheme.startswith('sqlite'):
        return True

    return (parsed.hostname or '').lower() in {'localhost', '127.0.0.1', '::1'}


if __name__ == '__main__':
    database_url = app.config.get('SQLALCHEMY_DATABASE_URI')
    if not _is_local_database_url(database_url) and os.getenv('ALLOW_REMOTE_APP_RUN') != '1':
        raise RuntimeError(
            "Refusing to run app.py directly against a non-local DATABASE_URL. "
            "Set ALLOW_REMOTE_APP_RUN=1 only if you intentionally want that."
        )

    if database_url and database_url.startswith('sqlite'):
        with app.app_context():
            db.create_all()
    app.run(host='0.0.0.0', port=5011, debug=True)
