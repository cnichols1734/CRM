import warnings
from sqlalchemy.exc import SAWarning
warnings.filterwarnings('ignore', category=SAWarning, message='.*relationship .* will copy column .*')

from flask import Flask, render_template
from flask_login import LoginManager
from flask_mail import Mail
from flask_migrate import Migrate
from models import db, User
from routes import register_blueprints
from routes.ai_chat import ai_chat
from routes.daily_todo import daily_todo

def create_app():
    app = Flask(__name__)
    app.config.from_object('config.Config')

    # Initialize extensions
    db.init_app(app)
    migrate = Migrate(app, db)
    
    login_manager = LoginManager()
    login_manager.init_app(app)
    login_manager.login_view = 'auth.login'

    # Add abs filter for templates
    app.jinja_env.filters['abs'] = abs

    # Initialize Flask-Mail
    mail = Mail()
    mail.init_app(app)

    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    # Register blueprints
    register_blueprints(app)

    # Register AI chat blueprint
    app.register_blueprint(ai_chat)
    app.register_blueprint(daily_todo)

    return app

app = create_app()

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(host='0.0.0.0', port=5005, debug=False)