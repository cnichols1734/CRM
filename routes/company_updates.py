"""
Company Updates routes - Blog-style announcements visible to all users.
Admin users can create, edit, and delete posts.
Includes reactions, comments, and view tracking for engagement.
"""
from functools import wraps
from flask import Blueprint, render_template, redirect, url_for, flash, request, abort, jsonify
from flask_login import login_required, current_user
from models import db, CompanyUpdate, CompanyUpdateReaction, CompanyUpdateComment, CompanyUpdateView
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
    
    # Prepare engagement data for each update
    engagement_data = {}
    for update in updates:
        engagement_data[update.id] = {
            'reaction_counts': update.get_reaction_counts(),
            'comment_count': update.comments.count(),
            'total_reactions': sum(update.get_reaction_counts().values())
        }
    
    return render_template('company_updates/list.html', 
                          updates=updates,
                          engagement_data=engagement_data,
                          reaction_emojis=CompanyUpdateReaction.REACTION_EMOJIS)


@company_updates_bp.route('/updates/<int:update_id>')
@login_required
def view_update(update_id):
    """View a single company update."""
    update = CompanyUpdate.query.get_or_404(update_id)
    
    # Track view (first view per user only)
    existing_view = CompanyUpdateView.query.filter_by(
        update_id=update_id, 
        user_id=current_user.id
    ).first()
    
    if not existing_view:
        view = CompanyUpdateView(update_id=update_id, user_id=current_user.id)
        db.session.add(view)
        db.session.commit()
    
    # Get previous and next updates for navigation
    prev_update = CompanyUpdate.query.filter(
        CompanyUpdate.created_at > update.created_at
    ).order_by(CompanyUpdate.created_at.asc()).first()
    
    next_update = CompanyUpdate.query.filter(
        CompanyUpdate.created_at < update.created_at
    ).order_by(CompanyUpdate.created_at.desc()).first()
    
    # Get engagement data
    reaction_counts = update.get_reaction_counts()
    user_reactions = update.get_user_reactions(current_user.id)
    comments = update.comments.order_by(CompanyUpdateComment.created_at.asc()).all()
    
    # Get views for admin
    views = None
    if current_user.role == 'admin':
        views = update.views.order_by(CompanyUpdateView.viewed_at.desc()).all()
    
    return render_template('company_updates/view.html', 
                          update=update, 
                          prev_update=prev_update, 
                          next_update=next_update,
                          reaction_counts=reaction_counts,
                          user_reactions=user_reactions,
                          comments=comments,
                          views=views,
                          reaction_emojis=CompanyUpdateReaction.REACTION_EMOJIS)


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


# ============================================
# REACTIONS API
# ============================================

@company_updates_bp.route('/api/updates/<int:update_id>/reactions', methods=['POST'])
@login_required
def toggle_reaction(update_id):
    """Toggle a reaction on an update. If exists, remove it. If not, add it."""
    update = CompanyUpdate.query.get_or_404(update_id)
    
    data = request.get_json()
    reaction_type = data.get('reaction_type')
    
    if reaction_type not in CompanyUpdateReaction.REACTION_TYPES:
        return jsonify({'error': 'Invalid reaction type'}), 400
    
    # Check if reaction already exists
    existing = CompanyUpdateReaction.query.filter_by(
        update_id=update_id,
        user_id=current_user.id,
        reaction_type=reaction_type
    ).first()
    
    if existing:
        # Remove reaction
        db.session.delete(existing)
        db.session.commit()
        action = 'removed'
    else:
        # Add reaction
        reaction = CompanyUpdateReaction(
            update_id=update_id,
            user_id=current_user.id,
            reaction_type=reaction_type
        )
        db.session.add(reaction)
        db.session.commit()
        action = 'added'
    
    # Return updated counts
    reaction_counts = update.get_reaction_counts()
    user_reactions = update.get_user_reactions(current_user.id)
    
    return jsonify({
        'action': action,
        'reaction_type': reaction_type,
        'reaction_counts': reaction_counts,
        'user_reactions': user_reactions
    })


@company_updates_bp.route('/api/updates/<int:update_id>/reactions', methods=['GET'])
@login_required
def get_reactions(update_id):
    """Get all reactions for an update with user details."""
    update = CompanyUpdate.query.get_or_404(update_id)
    
    reactions_by_type = {}
    for reaction_type in CompanyUpdateReaction.REACTION_TYPES:
        reactions = CompanyUpdateReaction.query.filter_by(
            update_id=update_id,
            reaction_type=reaction_type
        ).all()
        if reactions:
            reactions_by_type[reaction_type] = [
                {
                    'user_id': r.user_id,
                    'user_name': f"{r.user.first_name} {r.user.last_name}",
                    'created_at': r.created_at.isoformat()
                }
                for r in reactions
            ]
    
    return jsonify({
        'reaction_counts': update.get_reaction_counts(),
        'reactions_by_type': reactions_by_type,
        'user_reactions': update.get_user_reactions(current_user.id)
    })


# ============================================
# COMMENTS API
# ============================================

@company_updates_bp.route('/api/updates/<int:update_id>/comments', methods=['POST'])
@login_required
def add_comment(update_id):
    """Add a comment to an update."""
    update = CompanyUpdate.query.get_or_404(update_id)
    
    data = request.get_json()
    content = data.get('content', '').strip()
    
    if not content:
        return jsonify({'error': 'Comment content is required'}), 400
    
    if len(content) > 2000:
        return jsonify({'error': 'Comment too long (max 2000 characters)'}), 400
    
    comment = CompanyUpdateComment(
        update_id=update_id,
        user_id=current_user.id,
        content=content
    )
    db.session.add(comment)
    db.session.commit()
    
    return jsonify({
        'id': comment.id,
        'content': comment.content,
        'user_id': comment.user_id,
        'user_name': f"{comment.user.first_name} {comment.user.last_name}",
        'created_at': comment.created_at.isoformat(),
        'comment_count': update.comments.count()
    })


@company_updates_bp.route('/api/updates/<int:update_id>/comments', methods=['GET'])
@login_required
def get_comments(update_id):
    """Get all comments for an update."""
    update = CompanyUpdate.query.get_or_404(update_id)
    
    comments = update.comments.order_by(CompanyUpdateComment.created_at.asc()).all()
    
    return jsonify({
        'comments': [
            {
                'id': c.id,
                'content': c.content,
                'user_id': c.user_id,
                'user_name': f"{c.user.first_name} {c.user.last_name}",
                'created_at': c.created_at.isoformat(),
                'can_delete': c.user_id == current_user.id or current_user.role == 'admin'
            }
            for c in comments
        ],
        'comment_count': len(comments)
    })


@company_updates_bp.route('/api/updates/comments/<int:comment_id>', methods=['DELETE'])
@login_required
def delete_comment(comment_id):
    """Delete a comment. Users can delete their own, admins can delete any."""
    comment = CompanyUpdateComment.query.get_or_404(comment_id)
    
    # Check permission
    if comment.user_id != current_user.id and current_user.role != 'admin':
        return jsonify({'error': 'Permission denied'}), 403
    
    update_id = comment.update_id
    db.session.delete(comment)
    db.session.commit()
    
    # Get new count
    update = CompanyUpdate.query.get(update_id)
    
    return jsonify({
        'success': True,
        'comment_count': update.comments.count() if update else 0
    })

