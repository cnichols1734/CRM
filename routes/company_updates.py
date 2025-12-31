"""
Company Updates routes - Blog-style announcements visible to all users.
Admin users can create, edit, and delete posts.
"""
from functools import wraps
from flask import Blueprint, render_template, redirect, url_for, flash, request, abort, jsonify
from flask_login import login_required, current_user
from models import db, CompanyUpdate
from datetime import datetime
import re
import html

company_updates_bp = Blueprint('company_updates', __name__)


def admin_required(f):
    """Decorator to require admin role for a route."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role != 'admin':
            abort(403)
        return f(*args, **kwargs)
    return decorated_function


def generate_excerpt(content, max_length=200):
    """Generate a plain text excerpt from HTML content."""
    # Remove HTML tags
    text = re.sub(r'<[^>]+>', '', content)
    # Decode HTML entities (like &nbsp; &amp; etc.)
    text = html.unescape(text)
    # Normalize whitespace
    text = ' '.join(text.split())
    # Truncate
    if len(text) > max_length:
        text = text[:max_length].rsplit(' ', 1)[0] + '...'
    return text


@company_updates_bp.route('/updates')
@login_required
def list_updates():
    """List all company updates in reverse chronological order."""
    updates = CompanyUpdate.query.order_by(CompanyUpdate.created_at.desc()).all()
    return render_template('company_updates/list.html', updates=updates)


@company_updates_bp.route('/updates/<int:update_id>')
@login_required
def view_update(update_id):
    """View a single company update."""
    update = CompanyUpdate.query.get_or_404(update_id)
    
    # Get previous and next updates for navigation
    prev_update = CompanyUpdate.query.filter(
        CompanyUpdate.created_at > update.created_at
    ).order_by(CompanyUpdate.created_at.asc()).first()
    
    next_update = CompanyUpdate.query.filter(
        CompanyUpdate.created_at < update.created_at
    ).order_by(CompanyUpdate.created_at.desc()).first()
    
    return render_template('company_updates/view.html', 
                          update=update, 
                          prev_update=prev_update, 
                          next_update=next_update)


@company_updates_bp.route('/updates/new', methods=['GET', 'POST'])
@login_required
@admin_required
def create_update():
    """Create a new company update."""
    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        content = request.form.get('content', '').strip()
        cover_image_url = request.form.get('cover_image_url', '').strip() or None
        
        if not title:
            flash('Title is required.', 'error')
            return render_template('company_updates/form.html', update=None)
        
        if not content:
            flash('Content is required.', 'error')
            return render_template('company_updates/form.html', update=None)
        
        # Auto-generate excerpt from content
        excerpt = generate_excerpt(content)
        
        update = CompanyUpdate(
            title=title,
            content=content,
            excerpt=excerpt,
            cover_image_url=cover_image_url,
            author_id=current_user.id
        )
        
        db.session.add(update)
        db.session.commit()
        
        flash('Update published successfully!', 'success')
        return redirect(url_for('company_updates.view_update', update_id=update.id))
    
    return render_template('company_updates/form.html', update=None)


@company_updates_bp.route('/updates/<int:update_id>/edit', methods=['GET', 'POST'])
@login_required
@admin_required
def edit_update(update_id):
    """Edit an existing company update."""
    update = CompanyUpdate.query.get_or_404(update_id)
    
    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        content = request.form.get('content', '').strip()
        cover_image_url = request.form.get('cover_image_url', '').strip() or None
        
        if not title:
            flash('Title is required.', 'error')
            return render_template('company_updates/form.html', update=update)
        
        if not content:
            flash('Content is required.', 'error')
            return render_template('company_updates/form.html', update=update)
        
        # Auto-generate excerpt from content
        excerpt = generate_excerpt(content)
        
        update.title = title
        update.content = content
        update.excerpt = excerpt
        update.cover_image_url = cover_image_url
        update.updated_at = datetime.utcnow()
        
        db.session.commit()
        
        flash('Update saved successfully!', 'success')
        return redirect(url_for('company_updates.view_update', update_id=update.id))
    
    return render_template('company_updates/form.html', update=update)


@company_updates_bp.route('/updates/<int:update_id>/delete', methods=['POST'])
@login_required
@admin_required
def delete_update(update_id):
    """Delete a company update."""
    update = CompanyUpdate.query.get_or_404(update_id)
    
    db.session.delete(update)
    db.session.commit()
    
    flash('Update deleted successfully.', 'success')
    return redirect(url_for('company_updates.list_updates'))


@company_updates_bp.route('/api/updates/latest')
@login_required
def get_latest_update():
    """API endpoint to get the latest update for dashboard teaser."""
    update = CompanyUpdate.query.order_by(CompanyUpdate.created_at.desc()).first()
    
    if update:
        return jsonify({
            'id': update.id,
            'title': update.title,
            'excerpt': update.excerpt,
            'created_at': update.created_at.isoformat(),
            'author_name': f"{update.author.first_name} {update.author.last_name}" if update.author else None
        })
    
    return jsonify(None)

