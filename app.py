from dotenv import load_dotenv
load_dotenv()  # Load .env file before any other imports

# Initialize New Relic APM (must be before other imports to instrument them)
# Using NR_LICENSE_KEY to avoid Railway/Nixpacks auto-detection at build time
import os
nr_license = os.environ.get('NR_LICENSE_KEY')
if nr_license:
    try:
        os.environ['NEW_RELIC_LICENSE_KEY'] = nr_license
        import newrelic.agent
        newrelic.agent.initialize('newrelic.ini')
    except ImportError:
        print("Warning: newrelic package not installed, skipping APM initialization")

import warnings
import html
import pytz
from datetime import datetime
from sqlalchemy.exc import SAWarning
warnings.filterwarnings('ignore', category=SAWarning, message='.*relationship .* will copy column .*')

# Timezone for display (Central Time)
CENTRAL_TZ = pytz.timezone('America/Chicago')

from flask import Flask, render_template, session, redirect, url_for, flash
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

def create_app():
    app = Flask(__name__)
    app.config.from_object('config.Config')

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
    
    def to_central_time(dt):
        """Convert UTC datetime to Central Time for display."""
        if dt is None:
            return None
        # Assume dt is naive UTC, make it aware
        utc_dt = pytz.utc.localize(dt)
        # Convert to Central Time
        return utc_dt.astimezone(CENTRAL_TZ)
    
    app.jinja_env.filters['to_central'] = to_central_time

    # Context processor to make feature flags available in templates
    @app.context_processor
    def inject_feature_flags():
        from feature_flags import org_has_feature
        return dict(org_has_feature=org_has_feature)

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

    # =========================================================================
    # MULTI-TENANT RLS CONTEXT
    # =========================================================================
    
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
        return response

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

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(host='0.0.0.0', port=5011, debug=True)