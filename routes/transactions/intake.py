# routes/transactions/intake.py
"""
Transaction intake questionnaire routes.
"""

from flask import request, jsonify, render_template, redirect, url_for, flash, abort
from flask_login import login_required, current_user
from models import db, Transaction, TransactionDocument, AuditEvent
from services import audit_service
from . import transactions_bp
from .decorators import transactions_required


# =============================================================================
# INTAKE QUESTIONNAIRE
# =============================================================================

@transactions_bp.route('/<int:id>/intake')
@login_required
@transactions_required
def intake_questionnaire(id):
    """Show the intake questionnaire for a transaction."""
    from services.intake_service import get_intake_schema
    
    transaction = Transaction.query.get_or_404(id)
    
    if transaction.created_by_id != current_user.id and current_user.role != 'admin':
        abort(403)
    
    # Get the intake schema based on transaction type and ownership status
    schema = get_intake_schema(
        transaction.transaction_type.name,
        transaction.ownership_status
    )
    
    if not schema:
        flash('No intake questionnaire available for this transaction type.', 'error')
        return redirect(url_for('transactions.view_transaction', id=id))
    
    return render_template(
        'transactions/intake.html',
        transaction=transaction,
        schema=schema,
        intake_data=transaction.intake_data or {}
    )


@transactions_bp.route('/<int:id>/intake', methods=['POST'])
@login_required
@transactions_required
def save_intake(id):
    """Save intake questionnaire answers."""
    from services.intake_service import get_intake_schema, validate_intake_data
    
    transaction = Transaction.query.get_or_404(id)
    
    if transaction.created_by_id != current_user.id and current_user.role != 'admin':
        abort(403)
    
    # Get the schema
    schema = get_intake_schema(
        transaction.transaction_type.name,
        transaction.ownership_status
    )
    
    if not schema:
        return jsonify({'success': False, 'error': 'Schema not found'}), 404
    
    # Parse the incoming data
    data = request.get_json() if request.is_json else None
    
    if data is None:
        # Handle form submission
        intake_data = {}
        for section in schema.get('sections', []):
            for question in section.get('questions', []):
                field_id = question['id']
                value = request.form.get(field_id)
                
                # Convert boolean fields
                if question['type'] == 'boolean':
                    intake_data[field_id] = value == 'true' or value == 'yes'
                else:
                    intake_data[field_id] = value
    else:
        intake_data = data.get('intake_data', {})
    
    try:
        # Save the intake data
        transaction.intake_data = intake_data

        # Log audit event
        audit_service.log_intake_saved(transaction, intake_data)

        db.session.commit()

        if request.is_json:
            return jsonify({'success': True, 'intake_data': intake_data})
        else:
            flash('Questionnaire saved successfully!', 'success')
            return redirect(url_for('transactions.view_transaction', id=id))

    except Exception as e:
        db.session.rollback()
        if request.is_json:
            return jsonify({'success': False, 'error': str(e)}), 500
        else:
            flash(f'Error saving questionnaire: {str(e)}', 'error')
            return redirect(url_for('transactions.intake_questionnaire', id=id))


@transactions_bp.route('/<int:id>/intake/preview-changes', methods=['POST'])
@login_required
@transactions_required
def preview_document_changes(id):
    """
    Preview what documents will be added/removed/kept based on intake answers.
    Returns a diff with clear explanations of WHY each change is happening.
    """
    from services.intake_service import get_intake_schema, evaluate_document_rules, validate_intake_data
    
    transaction = Transaction.query.get_or_404(id)
    
    if transaction.created_by_id != current_user.id and current_user.role != 'admin':
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    
    # Get the schema
    schema = get_intake_schema(
        transaction.transaction_type.name,
        transaction.ownership_status
    )
    
    if not schema:
        return jsonify({'success': False, 'error': 'Schema not found'}), 404
    
    # Build question labels lookup
    question_labels = {}
    for section in schema.get('sections', []):
        for question in section.get('questions', []):
            question_labels[question['id']] = question['label']
    
    # Parse incoming intake data from request
    data = request.get_json() if request.is_json else None
    if data is None:
        new_intake_data = {}
        for section in schema.get('sections', []):
            for question in section.get('questions', []):
                field_id = question['id']
                value = request.form.get(field_id)
                if question['type'] == 'boolean':
                    new_intake_data[field_id] = value == 'true' or value == 'yes'
                else:
                    new_intake_data[field_id] = value
    else:
        new_intake_data = data.get('intake_data', {})
    
    # Get old intake data for comparison
    old_intake_data = transaction.intake_data or {}
    
    # Validate
    is_valid, missing = validate_intake_data(schema, new_intake_data)
    if not is_valid:
        return jsonify({
            'success': False, 
            'error': 'Please answer all required questions',
            'missing': missing
        }), 400
    
    # Find which questions changed
    changed_questions = {}
    for field_id in new_intake_data:
        old_val = old_intake_data.get(field_id)
        new_val = new_intake_data.get(field_id)
        if old_val != new_val:
            # Format values for display
            def format_val(v):
                if v is True:
                    return 'Yes'
                if v is False:
                    return 'No'
                if v is None:
                    return 'Not answered'
                return str(v)
            
            changed_questions[field_id] = {
                'label': question_labels.get(field_id, field_id),
                'old_value': format_val(old_val),
                'new_value': format_val(new_val)
            }
    
    # Build a map of document slug -> triggering rule condition
    doc_rules = {}
    for rule in schema.get('document_rules', []):
        slug = rule['slug']
        if rule.get('always'):
            doc_rules[slug] = {'always': True, 'name': rule['name']}
        elif 'condition' in rule:
            cond = rule['condition']
            doc_rules[slug] = {
                'field': cond.get('field'),
                'name': rule['name'],
                'condition': cond
            }
    
    # Evaluate document rules with new answers
    required_docs = evaluate_document_rules(schema, new_intake_data)
    
    # Get existing documents
    existing_docs = {doc.template_slug: doc for doc in transaction.documents.all()}
    existing_slugs = set(existing_docs.keys())
    
    # Get required slugs
    required_slugs = {doc['slug'] for doc in required_docs}
    required_docs_by_slug = {doc['slug']: doc for doc in required_docs}
    
    # Compute diff
    to_keep = existing_slugs & required_slugs
    to_remove = existing_slugs - required_slugs
    to_add = required_slugs - existing_slugs
    
    # Helper to build explanation for a document change
    def get_change_explanation(slug, is_addition):
        rule = doc_rules.get(slug, {})
        if rule.get('always'):
            return None  # Always-included docs don't need explanation
        
        field = rule.get('field')
        if field and field in changed_questions:
            change = changed_questions[field]
            if is_addition:
                return f"You changed \"{change['label']}\" from {change['old_value']} to {change['new_value']}"
            else:
                return f"You changed \"{change['label']}\" from {change['old_value']} to {change['new_value']}"
        return None
    
    # Check for blocked removals (sent/signed docs)
    blocked_removals = []
    safe_removals = []
    for slug in to_remove:
        doc = existing_docs[slug]
        explanation = get_change_explanation(slug, False)
        
        if doc.status in ('sent', 'signed'):
            blocked_removals.append({
                'slug': slug,
                'name': doc.template_name,
                'status': doc.status,
                'explanation': explanation,
                'blocked_reason': f'This document is already {doc.status} and cannot be automatically removed. Void it first if you need to remove it.'
            })
        else:
            safe_removals.append({
                'slug': slug,
                'name': doc.template_name,
                'status': doc.status,
                'explanation': explanation
            })
    
    # Build additions list with explanations
    additions = []
    for slug in to_add:
        doc_info = required_docs_by_slug[slug]
        explanation = get_change_explanation(slug, True)
        additions.append({
            'slug': slug,
            'name': doc_info['name'],
            'explanation': explanation
        })
    
    # Build keep list
    kept = []
    for slug in to_keep:
        doc = existing_docs[slug]
        kept.append({
            'slug': slug,
            'name': doc.template_name,
            'status': doc.status
        })
    
    # Determine if this is initial generation or update
    is_initial = len(existing_slugs) == 0
    has_changes = len(to_add) > 0 or len(safe_removals) > 0
    
    return jsonify({
        'success': True,
        'is_initial': is_initial,
        'has_changes': has_changes,
        'summary': {
            'total_docs': len(required_docs),
            'adding': len(additions),
            'removing': len(safe_removals),
            'keeping': len(kept),
            'blocked': len(blocked_removals)
        },
        'additions': additions,
        'removals': safe_removals,
        'kept': kept,
        'blocked': blocked_removals,
        'changed_questions': list(changed_questions.values())
    })


@transactions_bp.route('/<int:id>/intake/generate-package', methods=['POST'])
@login_required
@transactions_required
def generate_document_package(id):
    """Generate the document package based on intake answers."""
    from services.intake_service import get_intake_schema, evaluate_document_rules, validate_intake_data
    from services.documents import DocumentLoader, FieldResolver
    
    transaction = Transaction.query.get_or_404(id)
    
    if transaction.created_by_id != current_user.id and current_user.role != 'admin':
        abort(403)
    
    # Get the schema
    schema = get_intake_schema(
        transaction.transaction_type.name,
        transaction.ownership_status
    )
    
    if not schema:
        flash('Schema not found for this transaction type.', 'error')
        return redirect(url_for('transactions.view_transaction', id=id))
    
    # Validate that all required questions are answered
    is_valid, missing = validate_intake_data(schema, transaction.intake_data or {})
    
    if not is_valid:
        flash(f'Please answer all required questions before generating the document package.', 'error')
        return redirect(url_for('transactions.intake_questionnaire', id=id))
    
    # Evaluate document rules
    required_docs = evaluate_document_rules(schema, transaction.intake_data)
    
    try:
        # =================================================================
        # SMART DIFF-BASED SYNC
        # Instead of deleting all docs, compare old vs new and only
        # add/remove what changed. Preserves filled data and signatures.
        # =================================================================
        
        # Get existing documents indexed by slug
        existing_docs = {doc.template_slug: doc for doc in transaction.documents.all()}
        existing_slugs = set(existing_docs.keys())
        
        # Get required slugs from new rules
        required_slugs = {doc['slug'] for doc in required_docs}
        required_docs_by_slug = {doc['slug']: doc for doc in required_docs}
        
        # Compute diff
        to_keep = existing_slugs & required_slugs
        to_remove = existing_slugs - required_slugs
        to_add = required_slugs - existing_slugs
        
        # Track results for user feedback
        added_count = 0
        removed_count = 0
        blocked_removals = []
        
        # =================================================================
        # HANDLE REMOVALS (with safety check for sent/signed docs)
        # =================================================================
        for slug in to_remove:
            doc = existing_docs[slug]
            
            # Safety check: don't auto-remove docs that are sent or signed
            if doc.status in ('sent', 'signed'):
                blocked_removals.append(doc.template_name)
                continue
            
            # Log removal before deleting
            audit_service.log_document_removed(
                transaction_id=transaction.id,
                document_id=doc.id,
                template_name=doc.template_name
            )
            
            # Delete the document (cascade handles signatures)
            db.session.delete(doc)
            removed_count += 1
        
        # =================================================================
        # HANDLE ADDITIONS (create new TransactionDocument records)
        # =================================================================
        for slug in to_add:
            doc_info = required_docs_by_slug[slug]
            
            # Check if this is a preview-only document
            definition = DocumentLoader.get(slug)
            is_preview = definition and definition.is_pdf_preview
            
            tx_doc = TransactionDocument(
                organization_id=current_user.organization_id,
                transaction_id=transaction.id,
                template_slug=slug,
                template_name=doc_info['name'],
                included_reason=doc_info['reason'] if not doc_info.get('always') else None,
                status='filled' if is_preview else 'pending'
            )
            
            # Auto-populate field_data for preview-only documents
            if is_preview and definition:
                context = {
                    'user': current_user,
                    'transaction': transaction,
                    'form': {}
                }
                resolved_fields = FieldResolver.resolve(definition, context)
                field_data = {}
                for field in resolved_fields:
                    if field.value:
                        field_data[field.field_key] = field.value
                tx_doc.field_data = field_data
            
            db.session.add(tx_doc)
            db.session.flush()  # Get the ID for audit log
            
            # Log addition
            audit_service.log_document_added(tx_doc, tx_doc.included_reason)
            added_count += 1
        
        # =================================================================
        # LOG PACKAGE SYNC EVENT (if this is a regeneration)
        # =================================================================
        if existing_slugs:
            # This is a re-sync, not initial generation
            # Calculate actually removed (excluding blocked)
            actually_removed = [s for s in to_remove if existing_docs[s].status not in ('sent', 'signed')]
            
            audit_service.log_event(
                event_type=AuditEvent.DOCUMENT_PACKAGE_SYNCED,
                transaction_id=transaction.id,
                event_data={
                    'added': list(to_add),
                    'removed': actually_removed,
                    'kept': list(to_keep),
                    'blocked': blocked_removals
                }
            )
        else:
            # Initial generation
            all_docs = transaction.documents.all()
            audit_service.log_document_package_generated(transaction, all_docs)

        db.session.commit()
        
        # Build user feedback message
        messages = []
        if added_count:
            messages.append(f'{added_count} document(s) added')
        if removed_count:
            messages.append(f'{removed_count} document(s) removed')
        if to_keep and not added_count and not removed_count:
            messages.append('No changes needed')
        if not existing_slugs:
            messages = [f'{len(required_docs)} document(s) generated']
        
        if blocked_removals:
            flash(f'Warning: Could not remove {", ".join(blocked_removals)} because they are already sent/signed. Void them first if needed.', 'warning')
        
        flash(f'Document package updated: {", ".join(messages)}!', 'success')
        return redirect(url_for('transactions.view_transaction', id=id))
        
    except Exception as e:
        db.session.rollback()
        flash(f'Error generating document package: {str(e)}', 'error')
        return redirect(url_for('transactions.view_transaction', id=id))
