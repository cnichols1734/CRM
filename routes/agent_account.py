"""Public JSON account routes. No browser cookies or HTML redirects."""
from flask import jsonify, request, current_app
from sqlalchemy.exc import IntegrityError
from email_validator import validate_email, EmailNotValidError

from models import db
from services import agent_account as accounts
from routes.client_discovery import limited


def register(bp):
    def error(message, status=400):
        return jsonify(error=message), status

    def body():
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or any(not isinstance(v, str) for v in data.values()):
            return None
        return data

    def valid_email(value):
        try:
            validate_email(value, check_deliverability=False)
            return len(value) <= 120
        except EmailNotValidError:
            return False

    @bp.post('/accounts')
    def create_agent_account():
        if limited('agent-signup', 5, 3600):
            return error('Too many attempts. Please try again later.', 429)
        data = body()
        if data is None:
            return error('Enter your account details.')
        for key in ('email', 'first_name', 'last_name', 'company_name'):
            data[key] = data.get(key, '').strip()
        data['email'] = data['email'].lower()
        if not valid_email(data['email']):
            return error('Enter a valid email address.')
        for key, title, maximum in [('first_name', 'first name', 80), ('last_name', 'last name', 80), ('company_name', 'brokerage or team', 120)]:
            if not data[key] or len(data[key]) > maximum:
                return error(f'Enter your {title} using {maximum} characters or fewer.')
        if not 8 <= len(data.get('password', '')) <= 128:
            return error('Use a password with 8 to 128 characters.')
        if data['password'] != data.get('confirm_password'):
            return error('Passwords must match.')
        if data.get('accepted_terms') != 'true':
            return error('Accept the Terms of Service and Privacy Policy to continue.')
        if accounts.find_email(data['email']):
            return error('An account with that email already exists. Sign in instead.', 409)
        try:
            user = accounts.create_account(data)
        except IntegrityError:
            db.session.rollback()
            return error('An account could not be created with those details. Try signing in or use a different email.', 409)
        except Exception:
            db.session.rollback()
            current_app.logger.exception('Native account creation failed')
            return error('We could not finish creating your account. Please try again.', 503)
        from routes.agent_api import _set_org_context
        from services.agent_auth import issue_agent_jwt, serialize_user, serialize_org, JWT_TTL_SECONDS
        from feature_flags import get_org_features
        _set_org_context(user.organization_id)
        return jsonify(token=issue_agent_jwt(user), token_type='Bearer', expires_in=JWT_TTL_SECONDS,
                       user=serialize_user(user), org=serialize_org(user.organization),
                       features=get_org_features(user.organization)), 201

    @bp.post('/password-reset')
    def request_agent_password_reset():
        if limited('agent-reset-ip', 10, 3600):
            return error('Too many attempts. Please try again later.', 429)
        data = body()
        email = data.get('email', '').strip().lower() if data else ''
        if not valid_email(email):
            return error('Enter a valid email address.')
        if limited('agent-reset-email', 3, 900, identity=email):
            return error('Please wait before requesting another code.', 429)
        user = accounts.find_email(email)
        challenge, code = accounts.make_reset_challenge(user)
        if user:
            from services.email_service import get_email_service
            if not get_email_service().send_password_reset_code(user, code):
                current_app.logger.warning('Native password reset email failed user_id=%s', user.id)
        # Same response for unknown accounts and delivery failures.
        return jsonify(challenge=challenge, expires_in=accounts.RESET_SECONDS)

    @bp.post('/password-reset/complete')
    def complete_agent_password_reset():
        if limited('agent-reset-verify-ip', 30, 900):
            return error('Too many attempts. Please try again later.', 429)
        data = body()
        if not data or len(data.get('challenge', '')) > 2048:
            return error('Request a new password reset code.')
        challenge = data.get('challenge', '')
        if limited('agent-reset-challenge', 5, 1800, identity=challenge):
            return error('Too many attempts. Request a new code.', 429)
        code = data.get('code', '')
        if len(code) != 8 or not code.isascii() or not code.isdigit():
            return error('Enter the 8-digit code from your email.')
        password = data.get('password', '')
        if not 8 <= len(password) <= 128:
            return error('Use a password with 8 to 128 characters.')
        if password != data.get('confirm_password'):
            return error('Passwords must match.')
        if not accounts.complete_reset(challenge, code, password):
            return error('That code is invalid or expired. Request a new code.')
        return jsonify(ok=True)
