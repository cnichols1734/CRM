from .auth import auth_bp
from .contacts import contacts_bp
from .tasks import tasks_bp
from .main import main_bp
from .admin import admin_bp
from .marketing import marketing

def register_blueprints(app):
    app.register_blueprint(auth_bp)
    app.register_blueprint(contacts_bp)
    app.register_blueprint(tasks_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(marketing)
