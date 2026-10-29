"""Personal links shown alongside the organization's shared resources."""
import secrets
from urllib.parse import urlsplit

from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required

from models import PersonalResource, db

resources_bp = Blueprint('resources', __name__, url_prefix='/resources')


def form_token():
    token = session.setdefault('personal_resources_csrf', secrets.token_urlsafe(32))
    if request.method == 'POST' and not secrets.compare_digest(
        token.encode(), request.form.get('csrf_token', '').encode()
    ):
        abort(400)
    return token


def personal_resources():
    return PersonalResource.query.filter_by(
        organization_id=current_user.organization_id, user_id=current_user.id
    )


def validate_resource(form):
    label = form.get('label', '').strip()
    url = form.get('url', '').strip()
    if not label or len(label) > 100:
        return label, url, 'Enter a name with 1 to 100 characters.'
    if not url or len(url) > 2000 or any(c.isspace() or ord(c) < 32 for c in url) or '\\' in url:
        return label, url, 'Enter a valid website address with up to 2,000 characters.'
    if '://' not in url and ':' not in url.split('/')[0]:
        url = 'https://' + url
    try:
        parsed = urlsplit(url)
        valid = (parsed.scheme.lower() in ('http', 'https') and parsed.hostname
                 and not parsed.username and not parsed.password and parsed.port != 0)
    except ValueError:
        valid = False
    if not valid or len(url) > 2000:
        return label, url, 'Enter an http:// or https:// website address.'
    return label, url, None


@resources_bp.route('/')
@login_required
def index():
    return render_template('resources/index.html', resources=personal_resources().order_by(
        PersonalResource.label, PersonalResource.id
    ).all())


@resources_bp.route('/new', methods=['GET', 'POST'])
@resources_bp.route('/<int:resource_id>/edit', methods=['GET', 'POST'])
@login_required
def edit(resource_id=None):
    resource = personal_resources().filter_by(id=resource_id).first_or_404() if resource_id else None
    csrf_token = form_token()
    values = {'label': resource.label, 'url': resource.url} if resource else {}
    error = None
    if request.method == 'POST':
        label, url, error = validate_resource(request.form)
        values = {'label': label, 'url': url}
        if not error:
            if resource is None:
                resource = PersonalResource(organization_id=current_user.organization_id, user_id=current_user.id)
                db.session.add(resource)
            resource.label, resource.url = label, url
            db.session.commit()
            flash('Resource saved.', 'success')
            return redirect(url_for('resources.index'))
    return render_template('resources/edit.html', resource=resource, values=values, error=error, csrf_token=csrf_token), 400 if error else 200


@resources_bp.route('/<int:resource_id>/delete', methods=['GET', 'POST'])
@login_required
def delete(resource_id):
    resource = personal_resources().filter_by(id=resource_id).first_or_404()
    csrf_token = form_token()
    if request.method == 'POST':
        db.session.delete(resource)
        db.session.commit()
        flash('Resource deleted.', 'success')
        return redirect(url_for('resources.index'))
    return render_template('resources/delete.html', resource=resource, csrf_token=csrf_token)
