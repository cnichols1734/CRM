import warnings
from sqlalchemy.exc import SAWarning
warnings.filterwarnings('ignore', category=SAWarning, message='.*relationship .* will copy column .*')

from flask import Flask
from flask_login import LoginManager
from flask_mail import Mail
from models import db, User
from routes import register_blueprints
from routes.ai_chat import ai_chat

def create_app():
    app = Flask(__name__)
    app.config.from_object('config.Config')

    # Initialize extensions
    db.init_app(app)
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

    return app

app = create_app()

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(host='0.0.0.0', port=5005, debug=False)