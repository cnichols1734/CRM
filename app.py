from dotenv import load_dotenv
load_dotenv()  # Load .env file before any other imports

import warnings
import html
import pytz
from datetime import datetime
from sqlalchemy.exc import SAWarning
warnings.filterwarnings('ignore', category=SAWarning, message='.*relationship .* will copy column .*')

# Timezone for display (Central Time)
CENTRAL_TZ = pytz.timezone('America/Chicago')

from flask import Flask, render_template
from flask_login import LoginManager
from flask_mail import Mail
from flask_migrate import Migrate
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

def create_app():
    app = Flask(__name__)
    app.config.from_object('config.Config')

    # Initialize extensions
    db.init_app(app)
    migrate = Migrate(app, db)
    
    login_manager = LoginManager()
    login_manager.init_app(app)
    login_manager.login_view = 'auth.login'

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

import os

app = create_app()

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    # Only use debug mode when running locally via python app.py
    # In production (Gunicorn), this code block is never executed
    debug_mode = os.environ.get('FLASK_DEBUG', 'false').lower() == 'true'
    app.run(host='0.0.0.0', port=5011, debug=debug_mode)