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
    
    transaction = Transaction.query.filter_by(id=id, organization_id=current_user.organization_id).first_or_404()
    
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
    
    transaction = Transaction.query.filter_by(id=id, organization_id=current_user.organization_id).first_or_404()
    
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
    Uses the shared compute_document_diff helper so preview and generate
    stay in sync.
    """
    from services.intake_service import (
        get_intake_schema, validate_intake_data, compute_document_diff,
        get_question_labels
    )
    
    transaction = Transaction.query.filter_by(id=id, organization_id=current_user.organization_id).first_or_404()
    
    if transaction.created_by_id != current_user.id and current_user.role != 'admin':
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    
    schema = get_intake_schema(
        transaction.transaction_type.name,
        transaction.ownership_status
    )
    
    if not schema:
        return jsonify({'success': False, 'error': 'Schema not found'}), 404
    
    question_labels = get_question_labels(schema)
    
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
    
    old_intake_data = transaction.intake_data or {}
    
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
    
    # Build a map of document slug -> triggering rule condition for explanations
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
    
    # Use shared diff helper
    existing_docs = {doc.template_slug: doc for doc in transaction.documents.all()}
    diff = compute_document_diff(schema, new_intake_data, existing_docs)
    
    def get_change_explanation(slug, is_addition):
        rule = doc_rules.get(slug, {})
        if rule.get('always'):
            return None
        field = rule.get('field')
        if field and field in changed_questions:
            change = changed_questions[field]
            return f"You changed \"{change['label']}\" from {change['old_value']} to {change['new_value']}"
        return None
    
    # Enrich removals with explanations
    for removal in diff['safe_removals']:
        removal['explanation'] = get_change_explanation(removal['slug'], False)
    
    blocked_with_detail = []
    for blocked in diff['blocked_removals']:
        blocked['explanation'] = get_change_explanation(blocked['slug'], False)
        blocked['blocked_reason'] = f"This document is already {blocked['status']} and cannot be automatically removed. Void it first if you need to remove it."
        blocked_with_detail.append(blocked)
    
    additions = []
    for slug in diff['to_add']:
        doc_info = diff['required_docs_by_slug'][slug]
        additions.append({
            'slug': slug,
            'name': doc_info['name'],
            'explanation': get_change_explanation(slug, True)
        })
    
    kept = []
    for slug in diff['to_keep']:
        doc = existing_docs[slug]
        kept.append({
            'slug': slug,
            'name': doc.template_name,
            'status': doc.status
        })
    
    managed_existing = {s for s in existing_docs if not s.startswith('custom-')}
    is_initial = len(managed_existing) == 0
    has_changes = len(diff['to_add']) > 0 or len(diff['safe_removals']) > 0
    
    document_workflow = schema.get('document_workflow', 'docuseal')
    
    return jsonify({
        'success': True,
        'is_initial': is_initial,
        'has_changes': has_changes,
        'document_workflow': document_workflow,
        'summary': {
            'total_docs': len(diff['required_docs']),
            'adding': len(additions),
            'removing': len(diff['safe_removals']),
            'keeping': len(kept),
            'blocked': len(blocked_with_detail)
        },
        'additions': additions,
        'removals': diff['safe_removals'],
        'kept': kept,
        'blocked': blocked_with_detail,
        'changed_questions': list(changed_questions.values())
    })


@transactions_bp.route('/<int:id>/intake/generate-package', methods=['POST'])
@login_required
@transactions_required
def generate_document_package(id):
    """Generate the document package based on intake answers.

    Branches on schema.document_workflow:
      - 'placeholder_upload_only': all docs become placeholders for external
        creation/signing (e.g. ZipForms).
      - default / 'docuseal': legacy template-based flow with DocuSeal
        integration (form-driven, pdf-preview, etc.).
    """
    from services.intake_service import (
        get_intake_schema, validate_intake_data, compute_document_diff
    )
    
    transaction = Transaction.query.filter_by(id=id, organization_id=current_user.organization_id).first_or_404()
    
    if transaction.created_by_id != current_user.id and current_user.role != 'admin':
        abort(403)
    
    schema = get_intake_schema(
        transaction.transaction_type.name,
        transaction.ownership_status
    )
    
    if not schema:
        flash('Schema not found for this transaction type.', 'error')
        return redirect(url_for('transactions.view_transaction', id=id))
    
    is_valid, missing = validate_intake_data(schema, transaction.intake_data or {})
    
    if not is_valid:
        flash('Please answer all required questions before generating the document package.', 'error')
        return redirect(url_for('transactions.intake_questionnaire', id=id))
    
    document_workflow = schema.get('document_workflow', 'docuseal')
    
    try:
        existing_docs = {doc.template_slug: doc for doc in transaction.documents.all()}
        diff = compute_document_diff(schema, transaction.intake_data, existing_docs)

        managed_existing = {s for s in existing_docs if not s.startswith('custom-')}

        added_count = 0
        removed_count = 0
        blocked_names = [b['name'] for b in diff['blocked_removals']]

        # ---- REMOVALS (same for both workflows) ----
        for removal in diff['safe_removals']:
            doc = existing_docs[removal['slug']]
            audit_service.log_document_removed(
                transaction_id=transaction.id,
                document_id=doc.id,
                template_name=doc.template_name
            )
            db.session.delete(doc)
            removed_count += 1

        # ---- ADDITIONS ----
        if document_workflow == 'placeholder_upload_only':
            # All documents are placeholders — no YAML templates, no DocuSeal
            for slug in diff['to_add']:
                doc_info = diff['required_docs_by_slug'][slug]
                tx_doc = TransactionDocument(
                    organization_id=current_user.organization_id,
                    transaction_id=transaction.id,
                    template_slug=slug,
                    template_name=doc_info['name'],
                    included_reason=doc_info['reason'] if not doc_info.get('always') else None,
                    status='pending',
                    is_placeholder=True,
                    document_source='placeholder'
                )
                db.session.add(tx_doc)
                db.session.flush()
                audit_service.log_document_added(tx_doc, tx_doc.included_reason)
                added_count += 1
        else:
            # Legacy DocuSeal template flow
            from services.documents import DocumentLoader, FieldResolver

            for slug in diff['to_add']:
                doc_info = diff['required_docs_by_slug'][slug]
                is_placeholder = doc_info.get('is_placeholder', False)
                definition = DocumentLoader.get(slug)
                is_preview = definition and definition.is_pdf_preview

                if is_placeholder:
                    document_source = 'placeholder'
                    status = 'pending'
                elif is_preview:
                    document_source = 'template'
                    status = 'filled'
                else:
                    document_source = 'template'
                    status = 'pending'

                tx_doc = TransactionDocument(
                    organization_id=current_user.organization_id,
                    transaction_id=transaction.id,
                    template_slug=slug,
                    template_name=doc_info['name'],
                    included_reason=doc_info['reason'] if not doc_info.get('always') else None,
                    status=status,
                    is_placeholder=is_placeholder,
                    document_source=document_source
                )

                if is_preview and definition and not is_placeholder:
                    context = {
                        'user': current_user,
                        'transaction': transaction,
                        'form': {},
                        'organization': current_user.organization
                    }
                    resolved_fields = FieldResolver.resolve(definition, context)
                    field_data = {}
                    for field in resolved_fields:
                        if field.value:
                            field_data[field.field_key] = field.value
                    tx_doc.field_data = field_data

                db.session.add(tx_doc)
                db.session.flush()
                audit_service.log_document_added(tx_doc, tx_doc.included_reason)
                added_count += 1

        # ---- AUDIT ----
        if managed_existing:
            actually_removed = [r['slug'] for r in diff['safe_removals']]
            audit_service.log_event(
                event_type=AuditEvent.DOCUMENT_PACKAGE_SYNCED,
                transaction_id=transaction.id,
                event_data={
                    'added': list(diff['to_add']),
                    'removed': actually_removed,
                    'kept': list(diff['to_keep']),
                    'blocked': blocked_names,
                    'workflow': document_workflow
                }
            )
        else:
            all_docs = transaction.documents.all()
            audit_service.log_document_package_generated(transaction, all_docs)

        db.session.commit()

        # ---- USER FEEDBACK ----
        messages = []
        if added_count:
            messages.append(f'{added_count} document(s) added')
        if removed_count:
            messages.append(f'{removed_count} document(s) removed')
        if diff['to_keep'] and not added_count and not removed_count:
            messages.append('No changes needed')
        if not managed_existing:
            messages = [f'{len(diff["required_docs"])} document(s) generated']

        if blocked_names:
            flash(f'Warning: Could not remove {", ".join(blocked_names)} because they are already sent/signed. Void them first if needed.', 'warning')

        flash(f'Document package updated: {", ".join(messages)}!', 'success')
        return redirect(url_for('transactions.view_transaction', id=id))
        
    except Exception as e:
        db.session.rollback()
        flash(f'Error generating document package: {str(e)}', 'error')
        return redirect(url_for('transactions.view_transaction', id=id))
