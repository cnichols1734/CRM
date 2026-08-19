"""TransactionAssignment list/set APIs for the control-tower panel."""

from flask import abort, jsonify, request, url_for
from flask_login import login_required, current_user

from models import Task, User, TransactionAssignment, db
from services.transaction_auth import (
    CAP_ASSIGN,
    CAP_EDIT,
    CAP_VIEW,
    ROLE_COLLABORATOR,
    ROLE_LEAD,
    ROLE_TC,
    get_transaction_for_user,
    has_capability,
    is_org_break_glass,
)
from . import transactions_bp
from .decorators import transactions_required

ALLOWED_ROLES = frozenset({ROLE_LEAD, ROLE_TC, ROLE_COLLABORATOR})


def _assignment_payload(row: TransactionAssignment) -> dict:
    user = row.user
    return {
        'id': row.id,
        'user_id': row.user_id,
        'role': row.role,
        'capabilities': row.capabilities or [],
        'name': (
            f'{(user.first_name or "").strip()} {(user.last_name or "").strip()}'.strip()
            if user else f'User {row.user_id}'
        ),
        'email': user.email if user else None,
        'created_at': row.created_at.isoformat() if row.created_at else None,
    }


def _task_card_payload(task: Task | None) -> dict | None:
    if task is None:
        return None
    user = task.assigned_to
    last = (user.last_name or '').strip() if user else ''
    first = (user.first_name or '').strip() if user else ''
    assigned = None
    if first or last:
        assigned = f'{first} {last[:1] + "." if last else ""}'.strip()
    due = task.due_date
    return {
        'id': task.id,
        'subject': task.subject,
        'status': task.status,
        'due_at': due.isoformat() if due else None,
        'due_label': due.strftime('%b %d') if due else None,
        'assigned_to': assigned,
        'url': url_for('tasks.view_task', task_id=task.id),
    }


def _task_for_requirement(req) -> Task | None:
    if not req or not req.task_id:
        return None
    return Task.query.get(req.task_id)


def _can_assign(tx) -> bool:
    if is_org_break_glass(current_user):
        return True
    return has_capability(tx, CAP_ASSIGN, current_user).allowed


@transactions_bp.route('/<int:id>/assignments', methods=['GET'])
@login_required
@transactions_required
def list_transaction_assignments(id):
    tx, decision = get_transaction_for_user(id, capability=CAP_VIEW)
    if not tx:
        abort(403 if decision.reason != 'not_found' else 404)

    rows = (
        TransactionAssignment.query.filter_by(
            transaction_id=tx.id,
            organization_id=current_user.organization_id,
        )
        .order_by(TransactionAssignment.created_at.asc())
        .all()
    )
    return jsonify({
        'assignments': [_assignment_payload(r) for r in rows],
        'can_assign': _can_assign(tx),
    })


@transactions_bp.route('/<int:id>/assignments', methods=['POST'])
@login_required
@transactions_required
def set_transaction_assignment(id):
    tx, decision = get_transaction_for_user(id, capability=CAP_VIEW)
    if not tx:
        abort(403 if decision.reason != 'not_found' else 404)
    if not _can_assign(tx):
        return jsonify({'error': 'Not authorized to assign roles.', 'code': 'forbidden'}), 403

    data = request.get_json(silent=True) or {}
    user_id = data.get('user_id')
    role = (data.get('role') or '').strip()
    if not user_id or role not in ALLOWED_ROLES:
        return jsonify({
            'error': 'user_id and role (lead_agent|transaction_coordinator|collaborator) required.',
        }), 400

    try:
        user_id = int(user_id)
    except (TypeError, ValueError):
        return jsonify({'error': 'Invalid user_id.'}), 400

    assignee = User.query.filter_by(
        id=user_id,
        organization_id=current_user.organization_id,
    ).first()
    if not assignee:
        return jsonify({'error': 'User not found in organization.'}), 404

    # One lead / one TC per transaction; collaborators may stack.
    if role in (ROLE_LEAD, ROLE_TC):
        prior = TransactionAssignment.query.filter_by(
            transaction_id=tx.id,
            organization_id=current_user.organization_id,
            role=role,
        ).all()
        for row in prior:
            if row.user_id != user_id:
                db.session.delete(row)

    row = TransactionAssignment.query.filter_by(
        transaction_id=tx.id,
        user_id=user_id,
        organization_id=current_user.organization_id,
    ).first()
    if row:
        row.role = role
        if 'capabilities' in data:
            row.capabilities = data.get('capabilities') or []
    else:
        row = TransactionAssignment(
            organization_id=current_user.organization_id,
            transaction_id=tx.id,
            user_id=user_id,
            role=role,
            capabilities=data.get('capabilities') or [],
        )
        db.session.add(row)

    db.session.commit()
    return jsonify({'ok': True, 'assignment': _assignment_payload(row)})


@transactions_bp.route('/<int:id>/assignments/<int:assignment_id>', methods=['DELETE'])
@login_required
@transactions_required
def delete_transaction_assignment(id, assignment_id):
    tx, decision = get_transaction_for_user(id, capability=CAP_VIEW)
    if not tx:
        abort(403 if decision.reason != 'not_found' else 404)
    if not _can_assign(tx):
        return jsonify({'error': 'Not authorized to assign roles.', 'code': 'forbidden'}), 403

    row = TransactionAssignment.query.filter_by(
        id=assignment_id,
        transaction_id=tx.id,
        organization_id=current_user.organization_id,
    ).first()
    if not row:
        abort(404)
    db.session.delete(row)
    db.session.commit()
    return jsonify({'ok': True})


@transactions_bp.route('/<int:id>/requirements/backfill', methods=['POST'])
@login_required
@transactions_required
def backfill_transaction_requirements(id):
    """Bridge SellerContractMilestone rows into TransactionRequirement (idempotent)."""
    from feature_flags import org_has_feature
    from services.requirements_service import RequirementsService

    tx, decision = get_transaction_for_user(id, capability=CAP_VIEW)
    if not tx:
        abort(403 if decision.reason != 'not_found' else 404)
    if not org_has_feature('BOB_VTC_PILOT', current_user.organization):
        return jsonify({'error': 'VTC pilot not enabled.', 'code': 'pilot_disabled'}), 403
    from services.transaction_auth import can_edit_transaction
    if not can_edit_transaction(tx, current_user).allowed:
        return jsonify({'error': 'Not authorized.', 'code': 'forbidden'}), 403

    result = RequirementsService.backfill_transaction_requirements(
        tx.id, current_user.organization_id,
    )
    db.session.commit()
    return jsonify({'ok': True, **result})


@transactions_bp.route(
    '/<int:id>/requirements/<int:requirement_id>/expected-document',
    methods=['POST'],
)
@login_required
@transactions_required
def ensure_requirement_expected_document(id, requirement_id):
    """Create (or return) the placeholder document a requirement expects."""
    from models import TransactionRequirement
    from services.checklist_service import ensure_expected_placeholder

    tx, decision = get_transaction_for_user(id, capability=CAP_EDIT)
    if not tx:
        abort(403 if decision.reason != 'not_found' else 404)

    req = TransactionRequirement.query.filter_by(
        id=requirement_id,
        transaction_id=tx.id,
        organization_id=current_user.organization_id,
    ).first()
    if not req:
        abort(404)

    try:
        doc = ensure_expected_placeholder(
            tx,
            current_user.organization_id,
            req,
            actor_id=current_user.id,
        )
        db.session.commit()
    except ValueError as exc:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(exc)}), 400
    except Exception as exc:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(exc)}), 500

    return jsonify({
        'success': True,
        'document': {
            'id': doc.id,
            'template_slug': doc.template_slug,
            'template_name': doc.template_name,
            'name': doc.template_name,
            'status': doc.status,
            'is_placeholder': bool(doc.is_placeholder),
            'document_source': doc.document_source,
            'extraction_status': doc.extraction_status,
            'requirement_id': req.id,
            'requirement_key': req.requirement_key,
        },
    })


@transactions_bp.route(
    '/<int:id>/requirements/<int:requirement_id>/due-date',
    methods=['POST'],
)
@login_required
@transactions_required
def set_requirement_due_date(id, requirement_id):
    """Manually set (or clear) a checklist requirement's due date.

    Body: { due_date: 'YYYY-MM-DD' | '' }. Manual dates are protected from
    automated deadline recompute until cleared.
    """
    from datetime import datetime as dt
    from models import TransactionRequirement
    from services.requirements_service import RequirementsService

    tx, decision = get_transaction_for_user(id, capability=CAP_EDIT)
    if not tx:
        abort(403 if decision.reason != 'not_found' else 404)

    req = TransactionRequirement.query.filter_by(
        id=requirement_id,
        transaction_id=tx.id,
        organization_id=current_user.organization_id,
    ).first()
    if not req:
        abort(404)

    data = request.get_json(silent=True) or {}
    raw = str(data.get('due_date') or '').strip()
    due_at = None
    if raw:
        try:
            due_at = dt.strptime(raw, '%Y-%m-%d')
        except ValueError:
            return jsonify({
                'success': False,
                'error': 'Enter the date as YYYY-MM-DD.',
            }), 400

    try:
        updated = RequirementsService.update_due_at(
            req.id,
            due_at,
            actor_id=current_user.id,
            manual=True,
        )
        db.session.commit()
    except ValueError as exc:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(exc)}), 400
    except Exception:
        db.session.rollback()
        return jsonify({
            'success': False,
            'error': 'Could not update the due date. Try again.',
        }), 500

    return jsonify({
        'success': True,
        'requirement_id': updated.id,
        'task_id': updated.task_id,
        'task': _task_card_payload(_task_for_requirement(updated)),
        'due_at': updated.due_at.isoformat() if updated.due_at else None,
        'due_at_display': (
            updated.due_at.strftime('%m/%d/%Y') if updated.due_at else None
        ),
        'manual_override': bool(updated.due_at_manual_override),
    })


@transactions_bp.route(
    '/<int:id>/requirements/<int:requirement_id>/toggle',
    methods=['POST'],
)
@login_required
@transactions_required
def toggle_requirement(id, requirement_id):
    """Check or uncheck a manual listing-prep checklist row."""
    from models import TransactionRequirement
    from services.listing_prep_checklist import AUTO_KEYS
    from services.requirements_service import RequirementsService

    tx, decision = get_transaction_for_user(id, capability=CAP_EDIT)
    if not tx:
        abort(403 if decision.reason != 'not_found' else 404)

    req = TransactionRequirement.query.filter_by(
        id=requirement_id,
        transaction_id=tx.id,
        organization_id=current_user.organization_id,
    ).first()
    if not req:
        abort(404)

    if req.requirement_key in AUTO_KEYS:
        return jsonify({
            'success': False,
            'error': 'This item checks itself when the work is on file.',
        }), 400

    current = (req.work_status or 'pending').lower()
    new_status = 'pending' if current == 'completed' else 'completed'
    try:
        updated = RequirementsService.update_work_status(
            req.id, new_status, actor_id=current_user.id,
        )
        db.session.commit()
    except Exception:
        db.session.rollback()
        return jsonify({
            'success': False,
            'error': 'Could not update this item. Try again.',
        }), 500

    return jsonify({
        'success': True,
        'requirement_id': updated.id,
        'work_status': updated.work_status,
        'done': updated.work_status == 'completed',
        'task': _task_card_payload(_task_for_requirement(updated)),
    })


@transactions_bp.route('/<int:id>/requirements', methods=['POST'])
@login_required
@transactions_required
def create_listing_requirement(id):
    """Add a custom Preparing-to-List checklist row."""
    from datetime import datetime as dt
    from services.listing_prep_checklist import create_custom_listing_item

    tx, decision = get_transaction_for_user(id, capability=CAP_EDIT)
    if not tx:
        abort(403 if decision.reason != 'not_found' else 404)
    if not tx.transaction_type or tx.transaction_type.name != 'seller':
        return jsonify({'success': False, 'error': 'Custom items are for seller listings.'}), 400

    data = request.get_json(silent=True) or {}
    raw = str(data.get('due_date') or '').strip()
    due_at = None
    if raw:
        try:
            due_at = dt.strptime(raw, '%Y-%m-%d')
        except ValueError:
            return jsonify({
                'success': False,
                'error': 'Enter the date as YYYY-MM-DD.',
            }), 400

    try:
        req = create_custom_listing_item(
            tx,
            str(data.get('title') or ''),
            due_at=due_at,
            actor_id=current_user.id,
        )
        db.session.commit()
    except ValueError as exc:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(exc)}), 400
    except Exception:
        db.session.rollback()
        return jsonify({
            'success': False,
            'error': 'Could not add this item. Try again.',
        }), 500

    return jsonify({
        'success': True,
        'requirement_id': req.id,
        'key': req.requirement_key,
        'title': req.title,
        'due_at': req.due_at.isoformat() if req.due_at else None,
        'task_id': req.task_id,
        'task': _task_card_payload(_task_for_requirement(req)),
    })


@transactions_bp.route(
    '/<int:id>/requirements/<int:requirement_id>/remove',
    methods=['POST'],
)
@login_required
@transactions_required
def remove_listing_requirement(id, requirement_id):
    """Remove a custom Preparing-to-List row and cancel its task."""
    from models import TransactionRequirement
    from services.listing_prep_checklist import delete_custom_listing_item

    tx, decision = get_transaction_for_user(id, capability=CAP_EDIT)
    if not tx:
        abort(403 if decision.reason != 'not_found' else 404)

    req = TransactionRequirement.query.filter_by(
        id=requirement_id,
        transaction_id=tx.id,
        organization_id=current_user.organization_id,
    ).first()
    if not req:
        abort(404)

    linked_task = _task_for_requirement(req)
    try:
        delete_custom_listing_item(req, actor_id=current_user.id)
        db.session.commit()
    except ValueError as exc:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(exc)}), 400
    except Exception:
        db.session.rollback()
        return jsonify({
            'success': False,
            'error': 'Could not remove this item. Try again.',
        }), 500

    return jsonify({
        'success': True,
        'task': _task_card_payload(linked_task),
    })
