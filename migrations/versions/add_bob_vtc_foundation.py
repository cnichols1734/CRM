"""Add Bob VTC Foundation (Phase 1A/1B/1C)

Revision ID: add_bob_vtc_foundation
Revises: add_bob_action_notif
Create Date: 2026-08-04

Adds Virtual Transaction Coordinator foundation:
- TransactionAssignment (user roles per transaction)
- TransactionRequirement (actionable requirements from deadline packs)
- TransactionRequirementEvidence, Event, Dependency
- DocumentExtractionRun + ExtractedField (AI extraction)
- TransactionChangeProposal (Bob's proposed changes)
- TransactionCommunication + CommunicationDeliveryAttempt (outbound comms)
- NotificationEvent + NotificationDelivery (in-app notifications)
- Expands BobAction, AuditEvent, Task, TransactionDocument
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect, text

revision = 'add_bob_vtc_foundation'
down_revision = 'add_bob_action_notif'
branch_labels = None
depends_on = None


TENANT_TABLES = (
    'transaction_assignments',
    'transaction_requirements',
    'transaction_requirement_evidence',
    'transaction_requirement_events',
    'transaction_requirement_dependencies',
    'document_extraction_runs',
    'extracted_fields',
    'transaction_change_proposals',
    'document_review_reports',
    'transaction_communications',
    'communication_delivery_attempts',
    'contract_bootstrap_sessions',
    'notification_events',
    'notification_deliveries',
)


def _table_exists(conn, table_name):
    return table_name in inspect(conn).get_table_names()


def _column_exists(conn, table_name, column_name):
    if not _table_exists(conn, table_name):
        return False
    return column_name in {col['name'] for col in inspect(conn).get_columns(table_name)}


def _index_exists(conn, table_name, index_name):
    if not _table_exists(conn, table_name):
        return False
    return index_name in {idx['name'] for idx in inspect(conn).get_indexes(table_name)}


def _enable_rls(conn):
    if conn.dialect.name != 'postgresql':
        return

    for table in TENANT_TABLES:
        if _table_exists(conn, table):
            op.execute(text(f'ALTER TABLE {table} ENABLE ROW LEVEL SECURITY'))
            op.execute(text(f'ALTER TABLE {table} FORCE ROW LEVEL SECURITY'))
            op.execute(text(f'DROP POLICY IF EXISTS tenant_isolation_{table} ON {table}'))
            op.execute(text(f"""
                CREATE POLICY tenant_isolation_{table} ON {table}
                FOR ALL
                USING (
                    organization_id = current_setting(
                        'app.current_org_id', true
                    )::integer
                )
                WITH CHECK (
                    organization_id = current_setting(
                        'app.current_org_id', true
                    )::integer
                )
            """))


def _disable_rls(conn):
    if conn.dialect.name != 'postgresql':
        return

    for table in reversed(TENANT_TABLES):
        if _table_exists(conn, table):
            op.execute(text(f'DROP POLICY IF EXISTS tenant_isolation_{table} ON {table}'))
            op.execute(text(f'ALTER TABLE {table} DISABLE ROW LEVEL SECURITY'))


def upgrade():
    conn = op.get_bind()
    is_postgres = conn.dialect.name == 'postgresql'

    # ==========================================================================
    # NEW TABLES
    # ==========================================================================

    # TransactionAssignment
    if not _table_exists(conn, 'transaction_assignments'):
        op.create_table(
            'transaction_assignments',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('organization_id', sa.Integer(), sa.ForeignKey('organizations.id', ondelete='RESTRICT'), nullable=False),
            sa.Column('transaction_id', sa.Integer(), sa.ForeignKey('transactions.id', ondelete='CASCADE'), nullable=False),
            sa.Column('user_id', sa.Integer(), sa.ForeignKey('user.id', ondelete='CASCADE'), nullable=False),
            sa.Column('role', sa.String(50), nullable=False),
            sa.Column('capabilities', sa.JSON()),
            sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint('transaction_id', 'user_id', name='uq_transaction_assignments_transaction_user'),
        )
        op.create_index('ix_transaction_assignments_organization_id', 'transaction_assignments', ['organization_id'])
        op.create_index('ix_transaction_assignments_transaction_id', 'transaction_assignments', ['transaction_id'])
        op.create_index('ix_transaction_assignments_user_id', 'transaction_assignments', ['user_id'])

    # TransactionRequirement
    if not _table_exists(conn, 'transaction_requirements'):
        op.create_table(
            'transaction_requirements',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('organization_id', sa.Integer(), sa.ForeignKey('organizations.id', ondelete='RESTRICT'), nullable=False),
            sa.Column('transaction_id', sa.Integer(), sa.ForeignKey('transactions.id', ondelete='CASCADE'), nullable=False),
            sa.Column('package_key', sa.String(100), nullable=False),
            sa.Column('phase_key', sa.String(100), nullable=False),
            sa.Column('requirement_key', sa.String(100), nullable=False),
            sa.Column('template_version', sa.String(50)),
            sa.Column('deadline_rule_version', sa.String(50)),
            sa.Column('title', sa.String(300), nullable=False),
            sa.Column('work_status', sa.String(50), server_default='pending', nullable=False),
            sa.Column('timing_state', sa.String(50)),
            sa.Column('risk_level', sa.String(20)),
            sa.Column('due_at', sa.DateTime()),
            sa.Column('due_at_superseded_at', sa.DateTime()),
            sa.Column('prior_due_at', sa.DateTime()),
            sa.Column('assignee_user_id', sa.Integer(), sa.ForeignKey('user.id', ondelete='SET NULL')),
            sa.Column('responsibility_type', sa.String(50)),
            sa.Column('participant_id', sa.Integer(), sa.ForeignKey('transaction_participants.id', ondelete='SET NULL')),
            sa.Column('responsible_party_label', sa.String(200)),
            sa.Column('task_id', sa.Integer(), sa.ForeignKey('task.id', ondelete='SET NULL')),
            sa.Column('source', sa.String(50), server_default='deadline_pack'),
            sa.Column('source_milestone_id', sa.Integer(), sa.ForeignKey('seller_contract_milestones.id', ondelete='SET NULL')),
            sa.Column('version', sa.Integer(), server_default='1', nullable=False),
            sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint('transaction_id', 'requirement_key', name='uq_transaction_requirements_key'),
        )
        op.create_index('ix_transaction_requirements_organization_id', 'transaction_requirements', ['organization_id'])
        op.create_index('ix_transaction_requirements_transaction_id', 'transaction_requirements', ['transaction_id'])
        op.create_index('ix_transaction_requirements_requirement_key', 'transaction_requirements', ['requirement_key'])
        op.create_index('ix_transaction_requirements_work_status', 'transaction_requirements', ['work_status'])
        op.create_index('ix_transaction_requirements_due_at', 'transaction_requirements', ['due_at'])
        op.create_index('ix_transaction_requirements_task_id', 'transaction_requirements', ['task_id'])

    # TransactionRequirementEvidence
    if not _table_exists(conn, 'transaction_requirement_evidence'):
        op.create_table(
            'transaction_requirement_evidence',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('organization_id', sa.Integer(), sa.ForeignKey('organizations.id', ondelete='RESTRICT'), nullable=False),
            sa.Column('requirement_id', sa.Integer(), sa.ForeignKey('transaction_requirements.id', ondelete='CASCADE'), nullable=False),
            sa.Column('evidence_type', sa.String(50), nullable=False),
            sa.Column('document_id', sa.Integer(), sa.ForeignKey('transaction_documents.id', ondelete='SET NULL')),
            sa.Column('file_path', sa.String(500)),
            sa.Column('description', sa.Text()),
            sa.Column('evidence_metadata', sa.JSON()),
            sa.Column('uploaded_by_id', sa.Integer(), sa.ForeignKey('user.id', ondelete='SET NULL')),
            sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        )
        op.create_index('ix_transaction_requirement_evidence_organization_id', 'transaction_requirement_evidence', ['organization_id'])
        op.create_index('ix_transaction_requirement_evidence_requirement_id', 'transaction_requirement_evidence', ['requirement_id'])

    # TransactionRequirementEvent
    if not _table_exists(conn, 'transaction_requirement_events'):
        op.create_table(
            'transaction_requirement_events',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('organization_id', sa.Integer(), sa.ForeignKey('organizations.id', ondelete='RESTRICT'), nullable=False),
            sa.Column('requirement_id', sa.Integer(), sa.ForeignKey('transaction_requirements.id', ondelete='CASCADE'), nullable=False),
            sa.Column('event_type', sa.String(50), nullable=False),
            sa.Column('actor_id', sa.Integer(), sa.ForeignKey('user.id', ondelete='SET NULL')),
            sa.Column('actor_type', sa.String(50), server_default='user'),
            sa.Column('old_value', sa.JSON()),
            sa.Column('new_value', sa.JSON()),
            sa.Column('description', sa.String(500)),
            sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        )
        op.create_index('ix_transaction_requirement_events_organization_id', 'transaction_requirement_events', ['organization_id'])
        op.create_index('ix_transaction_requirement_events_requirement_id', 'transaction_requirement_events', ['requirement_id'])
        op.create_index('ix_transaction_requirement_events_created_at', 'transaction_requirement_events', ['created_at'])

    # TransactionRequirementDependency
    if not _table_exists(conn, 'transaction_requirement_dependencies'):
        op.create_table(
            'transaction_requirement_dependencies',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('organization_id', sa.Integer(), sa.ForeignKey('organizations.id', ondelete='RESTRICT'), nullable=False),
            sa.Column('requirement_id', sa.Integer(), sa.ForeignKey('transaction_requirements.id', ondelete='CASCADE'), nullable=False),
            sa.Column('blocks_requirement_id', sa.Integer(), sa.ForeignKey('transaction_requirements.id', ondelete='CASCADE'), nullable=False),
            sa.Column('dependency_type', sa.String(20), server_default='hard'),
            sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        )
        op.create_index('ix_transaction_requirement_dependencies_organization_id', 'transaction_requirement_dependencies', ['organization_id'])
        op.create_index('ix_transaction_requirement_dependencies_requirement_id', 'transaction_requirement_dependencies', ['requirement_id'])
        op.create_index('ix_transaction_requirement_dependencies_blocks_requirement_id', 'transaction_requirement_dependencies', ['blocks_requirement_id'])

    # DocumentExtractionRun
    if not _table_exists(conn, 'document_extraction_runs'):
        op.create_table(
            'document_extraction_runs',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('organization_id', sa.Integer(), sa.ForeignKey('organizations.id', ondelete='RESTRICT'), nullable=False),
            sa.Column('transaction_id', sa.Integer(), sa.ForeignKey('transactions.id', ondelete='CASCADE'), nullable=False),
            sa.Column('document_id', sa.Integer(), sa.ForeignKey('transaction_documents.id', ondelete='CASCADE'), nullable=False),
            sa.Column('status', sa.String(50), server_default='queued', nullable=False),
            sa.Column('extraction_type', sa.String(100), nullable=False),
            sa.Column('model', sa.String(100)),
            sa.Column('raw_output', sa.JSON()),
            sa.Column('extracted_data', sa.JSON()),
            sa.Column('confidence_scores', sa.JSON()),
            sa.Column('file_sha256', sa.String(64)),
            sa.Column('error', sa.Text()),
            sa.Column('started_at', sa.DateTime()),
            sa.Column('completed_at', sa.DateTime()),
            sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        )
        op.create_index('ix_document_extraction_runs_organization_id', 'document_extraction_runs', ['organization_id'])
        op.create_index('ix_document_extraction_runs_transaction_id', 'document_extraction_runs', ['transaction_id'])
        op.create_index('ix_document_extraction_runs_document_id', 'document_extraction_runs', ['document_id'])
        op.create_index('ix_document_extraction_runs_status', 'document_extraction_runs', ['status'])
        op.create_index('ix_document_extraction_runs_file_sha256', 'document_extraction_runs', ['file_sha256'])

    # ExtractedField
    if not _table_exists(conn, 'extracted_fields'):
        op.create_table(
            'extracted_fields',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('organization_id', sa.Integer(), sa.ForeignKey('organizations.id', ondelete='RESTRICT'), nullable=False),
            sa.Column('extraction_run_id', sa.Integer(), sa.ForeignKey('document_extraction_runs.id', ondelete='CASCADE'), nullable=False),
            sa.Column('field_key', sa.String(100), nullable=False),
            sa.Column('field_value', sa.Text()),
            sa.Column('field_type', sa.String(50)),
            sa.Column('confidence', sa.Numeric(5, 2)),
            sa.Column('verified', sa.Boolean(), server_default=sa.text('false')),
            sa.Column('verified_by_id', sa.Integer(), sa.ForeignKey('user.id', ondelete='SET NULL')),
            sa.Column('verified_at', sa.DateTime()),
            sa.Column('corrected_value', sa.Text()),
            sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        )
        op.create_index('ix_extracted_fields_organization_id', 'extracted_fields', ['organization_id'])
        op.create_index('ix_extracted_fields_extraction_run_id', 'extracted_fields', ['extraction_run_id'])

    # TransactionChangeProposal
    if not _table_exists(conn, 'transaction_change_proposals'):
        op.create_table(
            'transaction_change_proposals',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('organization_id', sa.Integer(), sa.ForeignKey('organizations.id', ondelete='RESTRICT'), nullable=False),
            sa.Column('transaction_id', sa.Integer(), sa.ForeignKey('transactions.id', ondelete='CASCADE'), nullable=False),
            sa.Column('change_type', sa.String(100), nullable=False),
            sa.Column('target_model', sa.String(100)),
            sa.Column('target_id', sa.Integer()),
            sa.Column('proposed_changes', sa.JSON(), nullable=False),
            sa.Column('source_extraction_run_id', sa.Integer(), sa.ForeignKey('document_extraction_runs.id', ondelete='SET NULL')),
            sa.Column('source_document_id', sa.Integer(), sa.ForeignKey('transaction_documents.id', ondelete='SET NULL')),
            sa.Column('rationale', sa.Text()),
            sa.Column('status', sa.String(50), server_default='pending', nullable=False),
            sa.Column('reviewed_by_id', sa.Integer(), sa.ForeignKey('user.id', ondelete='SET NULL')),
            sa.Column('reviewed_at', sa.DateTime()),
            sa.Column('rejection_reason', sa.Text()),
            sa.Column('applied_audit_event_id', sa.Integer(), sa.ForeignKey('audit_events.id', ondelete='SET NULL')),
            sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        )
        op.create_index('ix_transaction_change_proposals_organization_id', 'transaction_change_proposals', ['organization_id'])
        op.create_index('ix_transaction_change_proposals_transaction_id', 'transaction_change_proposals', ['transaction_id'])
        op.create_index('ix_transaction_change_proposals_status', 'transaction_change_proposals', ['status'])
        op.create_index('ix_transaction_change_proposals_created_at', 'transaction_change_proposals', ['created_at'])

    # DocumentReviewReport — post-upload BOB review + toast/banner/bell
    if not _table_exists(conn, 'document_review_reports'):
        op.create_table(
            'document_review_reports',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('organization_id', sa.Integer(), sa.ForeignKey('organizations.id', ondelete='RESTRICT'), nullable=False),
            sa.Column('transaction_id', sa.Integer(), sa.ForeignKey('transactions.id', ondelete='CASCADE'), nullable=True),  # Nullable for bootstrap
            sa.Column('document_id', sa.Integer(), sa.ForeignKey('transaction_documents.id', ondelete='CASCADE'), nullable=False),
            sa.Column('extraction_run_id', sa.Integer(), sa.ForeignKey('document_extraction_runs.id', ondelete='SET NULL')),
            sa.Column('severity', sa.String(20), server_default='ok', nullable=False),
            sa.Column('status', sa.String(20), server_default='open', nullable=False),
            sa.Column('title', sa.String(300), nullable=False),
            sa.Column('summary', sa.Text(), nullable=False),
            sa.Column('findings', sa.JSON(), nullable=False),
            sa.Column('field_count', sa.Integer(), server_default='0', nullable=False),
            sa.Column('toast_required', sa.Boolean(), server_default=sa.false(), nullable=False),
            sa.Column('toast_dismissed_at', sa.DateTime()),
            sa.Column('toast_dismissed_by_id', sa.Integer(), sa.ForeignKey('user.id', ondelete='SET NULL')),
            sa.Column('notification_id', sa.Integer(), sa.ForeignKey('notifications.id', ondelete='SET NULL')),
            sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        )
        op.create_index('ix_document_review_reports_organization_id', 'document_review_reports', ['organization_id'])
        op.create_index('ix_document_review_reports_transaction_id', 'document_review_reports', ['transaction_id'])
        op.create_index('ix_document_review_reports_document_id', 'document_review_reports', ['document_id'])
        op.create_index('ix_document_review_reports_severity', 'document_review_reports', ['severity'])
        op.create_index('ix_document_review_reports_status', 'document_review_reports', ['status'])
        op.create_index('ix_document_review_reports_created_at', 'document_review_reports', ['created_at'])
    else:
        # Make transaction_id nullable if it isn't
        if _column_exists(conn, 'document_review_reports', 'transaction_id'):
            columns = inspect(conn).get_columns('document_review_reports')
            tx_col = next((c for c in columns if c['name'] == 'transaction_id'), None)
            if tx_col and not tx_col.get('nullable', True) and is_postgres:
                op.alter_column('document_review_reports', 'transaction_id', nullable=True)

    # TransactionCommunication
    if not _table_exists(conn, 'transaction_communications'):
        op.create_table(
            'transaction_communications',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('organization_id', sa.Integer(), sa.ForeignKey('organizations.id', ondelete='RESTRICT'), nullable=False),
            sa.Column('transaction_id', sa.Integer(), sa.ForeignKey('transactions.id', ondelete='CASCADE'), nullable=False),
            sa.Column('participant_id', sa.Integer(), sa.ForeignKey('transaction_participants.id', ondelete='SET NULL')),
            sa.Column('requirement_id', sa.Integer(), sa.ForeignKey('transaction_requirements.id', ondelete='SET NULL')),
            sa.Column('communication_type', sa.String(100), nullable=False),
            sa.Column('channel', sa.String(50), nullable=False),
            sa.Column('direction', sa.String(20), server_default='outbound', nullable=False),
            sa.Column('purpose', sa.String(200)),
            sa.Column('subject', sa.String(300)),
            sa.Column('body', sa.Text(), nullable=False),
            sa.Column('recipients', sa.JSON()),
            sa.Column('cc', sa.JSON()),
            sa.Column('attachment_refs', sa.JSON()),
            sa.Column('approved_payload_hash', sa.String(128)),
            sa.Column('client_idempotency_key', sa.String(200)),
            sa.Column('provider_message_id', sa.String(200)),
            sa.Column('provider_thread_id', sa.String(200)),
            sa.Column('communication_metadata', sa.JSON()),
            sa.Column('status', sa.String(50), server_default='draft', nullable=False),
            sa.Column('next_attempt_at', sa.DateTime()),
            sa.Column('locked_at', sa.DateTime()),
            sa.Column('locked_by', sa.String(100)),
            sa.Column('last_error', sa.Text()),
            sa.Column('created_by_user_id', sa.Integer(), sa.ForeignKey('user.id', ondelete='SET NULL')),
            sa.Column('approved_by_user_id', sa.Integer(), sa.ForeignKey('user.id', ondelete='SET NULL')),
            sa.Column('approved_at', sa.DateTime()),
            sa.Column('created_by_bob', sa.Boolean(), server_default=sa.text('false')),
            sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint(
                'organization_id', 'client_idempotency_key',
                name='uq_transaction_communications_org_idempotency',
            ),
        )
        op.create_index('ix_transaction_communications_organization_id', 'transaction_communications', ['organization_id'])
        op.create_index('ix_transaction_communications_transaction_id', 'transaction_communications', ['transaction_id'])
        op.create_index('ix_transaction_communications_participant_id', 'transaction_communications', ['participant_id'])
        op.create_index('ix_transaction_communications_requirement_id', 'transaction_communications', ['requirement_id'])
        op.create_index('ix_transaction_communications_status', 'transaction_communications', ['status'])
        op.create_index('ix_transaction_communications_created_at', 'transaction_communications', ['created_at'])
        op.create_index('ix_transaction_communications_client_idempotency_key', 'transaction_communications', ['client_idempotency_key'])
    else:
        for col_name, col_def in [
            ('requirement_id', sa.Column('requirement_id', sa.Integer(), sa.ForeignKey('transaction_requirements.id', ondelete='SET NULL'))),
            ('direction', sa.Column('direction', sa.String(20), server_default='outbound')),
            ('purpose', sa.Column('purpose', sa.String(200))),
            ('recipients', sa.Column('recipients', sa.JSON())),
            ('cc', sa.Column('cc', sa.JSON())),
            ('attachment_refs', sa.Column('attachment_refs', sa.JSON())),
            ('approved_payload_hash', sa.Column('approved_payload_hash', sa.String(128))),
            ('client_idempotency_key', sa.Column('client_idempotency_key', sa.String(200))),
            ('provider_message_id', sa.Column('provider_message_id', sa.String(200))),
            ('provider_thread_id', sa.Column('provider_thread_id', sa.String(200))),
            ('next_attempt_at', sa.Column('next_attempt_at', sa.DateTime())),
            ('locked_at', sa.Column('locked_at', sa.DateTime())),
            ('locked_by', sa.Column('locked_by', sa.String(100))),
            ('last_error', sa.Column('last_error', sa.Text())),
            ('created_by_user_id', sa.Column('created_by_user_id', sa.Integer(), sa.ForeignKey('user.id', ondelete='SET NULL'))),
            ('approved_by_user_id', sa.Column('approved_by_user_id', sa.Integer(), sa.ForeignKey('user.id', ondelete='SET NULL'))),
            ('approved_at', sa.Column('approved_at', sa.DateTime())),
        ]:
            if not _column_exists(conn, 'transaction_communications', col_name):
                op.add_column('transaction_communications', col_def)
        if not _index_exists(conn, 'transaction_communications', 'ix_transaction_communications_client_idempotency_key'):
            op.create_index(
                'ix_transaction_communications_client_idempotency_key',
                'transaction_communications',
                ['client_idempotency_key'],
            )
        # Unique idempotency when key is present (NULL keys remain allowed).
        if is_postgres:
            op.execute(text("""
                CREATE UNIQUE INDEX IF NOT EXISTS uq_transaction_communications_org_idempotency
                ON transaction_communications (organization_id, client_idempotency_key)
                WHERE client_idempotency_key IS NOT NULL
            """))

    # CommunicationDeliveryAttempt
    if not _table_exists(conn, 'communication_delivery_attempts'):
        op.create_table(
            'communication_delivery_attempts',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('organization_id', sa.Integer(), sa.ForeignKey('organizations.id', ondelete='RESTRICT'), nullable=False),
            sa.Column('communication_id', sa.Integer(), sa.ForeignKey('transaction_communications.id', ondelete='CASCADE'), nullable=False),
            sa.Column('attempt_number', sa.Integer(), server_default='1', nullable=False),
            sa.Column('status', sa.String(50), nullable=False),
            sa.Column('provider', sa.String(50)),
            sa.Column('provider_message_id', sa.String(200)),
            sa.Column('provider_response', sa.JSON()),
            sa.Column('error', sa.Text()),
            sa.Column('started_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.Column('finished_at', sa.DateTime()),
            sa.Column('attempted_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        )
        op.create_index('ix_communication_delivery_attempts_organization_id', 'communication_delivery_attempts', ['organization_id'])
        op.create_index('ix_communication_delivery_attempts_communication_id', 'communication_delivery_attempts', ['communication_id'])
    else:
        for col_name, col_def in [
            ('started_at', sa.Column('started_at', sa.DateTime(), server_default=sa.func.now())),
            ('finished_at', sa.Column('finished_at', sa.DateTime())),
        ]:
            if not _column_exists(conn, 'communication_delivery_attempts', col_name):
                op.add_column('communication_delivery_attempts', col_def)

    # NotificationEvent
    if not _table_exists(conn, 'notification_events'):
        op.create_table(
            'notification_events',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('organization_id', sa.Integer(), sa.ForeignKey('organizations.id', ondelete='RESTRICT'), nullable=False),
            sa.Column('event_type', sa.String(100), nullable=False),
            sa.Column('user_id', sa.Integer(), sa.ForeignKey('user.id', ondelete='CASCADE'), nullable=False),
            sa.Column('payload', sa.JSON()),
            sa.Column('priority', sa.String(20), server_default='normal'),
            sa.Column('status', sa.String(50), server_default='pending', nullable=False),
            sa.Column('dedupe_key', sa.String(200)),
            sa.Column('dedupe_bucket', sa.String(100)),
            sa.Column('not_before', sa.DateTime()),
            sa.Column('snoozed_until', sa.DateTime()),
            sa.Column('related_transaction_id', sa.Integer(), sa.ForeignKey('transactions.id', ondelete='CASCADE')),
            sa.Column('related_requirement_id', sa.Integer(), sa.ForeignKey('transaction_requirements.id', ondelete='CASCADE')),
            sa.Column('category', sa.String(50)),
            sa.Column('escalation_level', sa.Integer(), server_default='0'),
            sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        )
        op.create_index('ix_notification_events_organization_id', 'notification_events', ['organization_id'])
        op.create_index('ix_notification_events_event_type', 'notification_events', ['event_type'])
        op.create_index('ix_notification_events_user_id', 'notification_events', ['user_id'])
        op.create_index('ix_notification_events_status', 'notification_events', ['status'])
        op.create_index('ix_notification_events_created_at', 'notification_events', ['created_at'])
        op.create_index('ix_notification_events_dedupe_key', 'notification_events', ['dedupe_key'])
        
        # Add unique constraint for dedupe on Postgres (SQLite doesn't support partial indexes well)
        if is_postgres:
            op.execute(text("""
                CREATE UNIQUE INDEX uq_notification_events_dedupe 
                ON notification_events (user_id, dedupe_key, dedupe_bucket)
                WHERE dedupe_key IS NOT NULL
            """))
    else:
        # Add new columns to existing notification_events table
        for col_name, col_def in [
            ('dedupe_key', sa.Column('dedupe_key', sa.String(200))),
            ('dedupe_bucket', sa.Column('dedupe_bucket', sa.String(100))),
            ('not_before', sa.Column('not_before', sa.DateTime())),
            ('snoozed_until', sa.Column('snoozed_until', sa.DateTime())),
            ('related_transaction_id', sa.Column('related_transaction_id', sa.Integer(), sa.ForeignKey('transactions.id', ondelete='CASCADE'))),
            ('related_requirement_id', sa.Column('related_requirement_id', sa.Integer(), sa.ForeignKey('transaction_requirements.id', ondelete='CASCADE'))),
            ('category', sa.Column('category', sa.String(50))),
            ('escalation_level', sa.Column('escalation_level', sa.Integer(), server_default='0')),
        ]:
            if not _column_exists(conn, 'notification_events', col_name):
                op.add_column('notification_events', col_def)
        
        if not _index_exists(conn, 'notification_events', 'ix_notification_events_dedupe_key'):
            op.create_index('ix_notification_events_dedupe_key', 'notification_events', ['dedupe_key'])
        
        # Add unique constraint for dedupe on Postgres
        if is_postgres and not _index_exists(conn, 'notification_events', 'uq_notification_events_dedupe'):
            op.execute(text("""
                CREATE UNIQUE INDEX uq_notification_events_dedupe 
                ON notification_events (user_id, dedupe_key, dedupe_bucket)
                WHERE dedupe_key IS NOT NULL
            """))

    # NotificationDelivery
    if not _table_exists(conn, 'notification_deliveries'):
        op.create_table(
            'notification_deliveries',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('organization_id', sa.Integer(), sa.ForeignKey('organizations.id', ondelete='RESTRICT'), nullable=False),
            sa.Column('event_id', sa.Integer(), sa.ForeignKey('notification_events.id', ondelete='CASCADE'), nullable=False),
            sa.Column('delivery_method', sa.String(50), nullable=False),
            sa.Column('status', sa.String(50), nullable=False),
            sa.Column('scheduled_for', sa.DateTime()),
            sa.Column('delivered_at', sa.DateTime()),
            sa.Column('read_at', sa.DateTime()),
            sa.Column('error', sa.Text()),
            sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        )
        op.create_index('ix_notification_deliveries_organization_id', 'notification_deliveries', ['organization_id'])
        op.create_index('ix_notification_deliveries_event_id', 'notification_deliveries', ['event_id'])
        op.create_index('ix_notification_deliveries_status', 'notification_deliveries', ['status'])

    # ContractBootstrapSession
    if not _table_exists(conn, 'contract_bootstrap_sessions'):
        op.create_table(
            'contract_bootstrap_sessions',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('organization_id', sa.Integer(), sa.ForeignKey('organizations.id', ondelete='RESTRICT'), nullable=False),
            sa.Column('uploader_user_id', sa.Integer(), sa.ForeignKey('user.id', ondelete='SET NULL')),
            sa.Column('document_id', sa.Integer(), sa.ForeignKey('transaction_documents.id', ondelete='SET NULL')),
            sa.Column('file_sha256', sa.String(64)),
            sa.Column('original_filename', sa.String(500)),
            sa.Column('mime_type', sa.String(100)),
            sa.Column('page_count', sa.Integer(), server_default='0'),
            sa.Column('upload_source', sa.String(50)),
            sa.Column('storage_path', sa.String(1000)),
            sa.Column('classification', sa.JSON()),
            sa.Column('extracted_candidates', sa.JSON()),
            sa.Column('match_status', sa.String(50), server_default='pending', nullable=False),
            sa.Column('matched_transaction_id', sa.Integer(), sa.ForeignKey('transactions.id', ondelete='SET NULL')),
            sa.Column('match_candidates', sa.JSON()),
            sa.Column('proposal_id', sa.Integer(), sa.ForeignKey('transaction_change_proposals.id', ondelete='SET NULL')),
            sa.Column('review_report_id', sa.Integer(), sa.ForeignKey('document_review_reports.id', ondelete='SET NULL')),
            sa.Column('status', sa.String(50), server_default='uploaded', nullable=False),
            sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.Column('applied_at', sa.DateTime()),
            sa.Column('applied_by_id', sa.Integer(), sa.ForeignKey('user.id', ondelete='SET NULL')),
        )
        op.create_index('ix_contract_bootstrap_sessions_organization_id', 'contract_bootstrap_sessions', ['organization_id'])
        op.create_index('ix_contract_bootstrap_sessions_file_sha256', 'contract_bootstrap_sessions', ['file_sha256'])
        op.create_index('ix_contract_bootstrap_sessions_match_status', 'contract_bootstrap_sessions', ['match_status'])
        op.create_index('ix_contract_bootstrap_sessions_status', 'contract_bootstrap_sessions', ['status'])
        op.create_index('ix_contract_bootstrap_sessions_created_at', 'contract_bootstrap_sessions', ['created_at'])

    # ==========================================================================
    # EXPAND EXISTING TABLES
    # ==========================================================================

    # AuditEvent: add organization_id and bob_action_id
    if _table_exists(conn, 'audit_events'):
        if not _column_exists(conn, 'audit_events', 'organization_id'):
            op.add_column('audit_events', sa.Column('organization_id', sa.Integer(), sa.ForeignKey('organizations.id', ondelete='RESTRICT'), nullable=True))
            op.create_index('ix_audit_events_organization_id', 'audit_events', ['organization_id'])
        if not _column_exists(conn, 'audit_events', 'bob_action_id'):
            op.add_column('audit_events', sa.Column('bob_action_id', sa.Integer(), sa.ForeignKey('bob_actions.id', ondelete='SET NULL'), nullable=True))
            op.create_index('ix_audit_events_bob_action_id', 'audit_events', ['bob_action_id'])

    # BobAction: add VTC expansion fields
    if _table_exists(conn, 'bob_actions'):
        for col_name, col_def in [
            ('transaction_id', sa.Column('transaction_id', sa.Integer(), sa.ForeignKey('transactions.id', ondelete='SET NULL'), nullable=True)),
            ('model', sa.Column('model', sa.String(100), nullable=True)),
            ('response_trace_id', sa.Column('response_trace_id', sa.String(100), nullable=True)),
            ('preview_digest', sa.Column('preview_digest', sa.String(500), nullable=True)),
            ('source_document_id', sa.Column('source_document_id', sa.Integer(), sa.ForeignKey('transaction_documents.id', ondelete='SET NULL'), nullable=True)),
            ('extraction_run_id', sa.Column('extraction_run_id', sa.Integer(), sa.ForeignKey('document_extraction_runs.id', ondelete='SET NULL'), nullable=True)),
            ('record_version', sa.Column('record_version', sa.JSON(), nullable=True)),
            ('approving_user_id', sa.Column('approving_user_id', sa.Integer(), sa.ForeignKey('user.id', ondelete='SET NULL'), nullable=True)),
            ('approved_at', sa.Column('approved_at', sa.DateTime(), nullable=True)),
            ('rejecting_user_id', sa.Column('rejecting_user_id', sa.Integer(), sa.ForeignKey('user.id', ondelete='SET NULL'), nullable=True)),
            ('rejected_at', sa.Column('rejected_at', sa.DateTime(), nullable=True)),
            ('provider_event_id', sa.Column('provider_event_id', sa.String(200), nullable=True)),
            ('idempotency_key', sa.Column('idempotency_key', sa.String(200), nullable=True)),
            ('requirement_id', sa.Column('requirement_id', sa.Integer(), sa.ForeignKey('transaction_requirements.id', ondelete='SET NULL'), nullable=True)),
            ('proposal_id', sa.Column('proposal_id', sa.Integer(), sa.ForeignKey('transaction_change_proposals.id', ondelete='SET NULL'), nullable=True)),
            ('resulting_audit_event_ids', sa.Column('resulting_audit_event_ids', sa.JSON(), nullable=True)),
            ('tool_bundle', sa.Column('tool_bundle', sa.JSON(), nullable=True)),
            ('risk', sa.Column('risk', sa.String(20), nullable=True)),
            ('confirmation_surface', sa.Column('confirmation_surface', sa.String(50), nullable=True)),
            ('page_context', sa.Column('page_context', sa.JSON(), nullable=True)),
        ]:
            if not _column_exists(conn, 'bob_actions', col_name):
                op.add_column('bob_actions', col_def)

        # Add indexes for commonly queried fields
        if not _index_exists(conn, 'bob_actions', 'ix_bob_actions_transaction_id'):
            op.create_index('ix_bob_actions_transaction_id', 'bob_actions', ['transaction_id'])
        if not _index_exists(conn, 'bob_actions', 'ix_bob_actions_idempotency_key'):
            op.create_index('ix_bob_actions_idempotency_key', 'bob_actions', ['idempotency_key'])

    # Task: make contact_id nullable + add transaction_id + CHECK
    if _table_exists(conn, 'task'):
        if _column_exists(conn, 'task', 'contact_id'):
            # Get column info to check if it's already nullable
            columns = inspect(conn).get_columns('task')
            contact_col = next((c for c in columns if c['name'] == 'contact_id'), None)
            if contact_col and not contact_col.get('nullable', True):
                # Make nullable - SQLite doesn't support ALTER COLUMN, so check dialect
                if is_postgres:
                    op.alter_column('task', 'contact_id', nullable=True)

        if not _column_exists(conn, 'task', 'transaction_id'):
            op.add_column(
                'task',
                sa.Column(
                    'transaction_id',
                    sa.Integer(),
                    sa.ForeignKey('transactions.id', ondelete='SET NULL'),
                    nullable=True,
                ),
            )
            op.create_index('ix_task_transaction_id', 'task', ['transaction_id'])

        if is_postgres:
            op.execute(text(
                'ALTER TABLE task DROP CONSTRAINT IF EXISTS task_has_contact_or_transaction'
            ))
            op.execute(text(
                'ALTER TABLE task ADD CONSTRAINT task_has_contact_or_transaction '
                'CHECK (contact_id IS NOT NULL OR transaction_id IS NOT NULL)'
            ))

    # TransactionDocument: add privacy/retention fields
    if _table_exists(conn, 'transaction_documents'):
        for col_name, col_def in [
            ('sensitivity_class', sa.Column('sensitivity_class', sa.String(50), nullable=True)),
            ('retention_until', sa.Column('retention_until', sa.DateTime(), nullable=True)),
            ('ai_processing_allowed', sa.Column('ai_processing_allowed', sa.Boolean(), server_default=sa.text('true'))),
        ]:
            if not _column_exists(conn, 'transaction_documents', col_name):
                op.add_column('transaction_documents', col_def)

    # AgentMessagingChannel: durable Telegram transaction disambiguation
    if _table_exists(conn, 'agent_messaging_channels'):
        if not _column_exists(conn, 'agent_messaging_channels', 'selected_transaction_id'):
            op.add_column(
                'agent_messaging_channels',
                sa.Column(
                    'selected_transaction_id',
                    sa.Integer(),
                    sa.ForeignKey('transactions.id', ondelete='SET NULL'),
                    nullable=True,
                ),
            )
        if not _index_exists(
            conn, 'agent_messaging_channels',
            'ix_agent_messaging_channels_selected_transaction_id',
        ):
            op.create_index(
                'ix_agent_messaging_channels_selected_transaction_id',
                'agent_messaging_channels',
                ['selected_transaction_id'],
            )

    # User: optional per-user notification quiet hours / cadence prefs (Phase 2)
    if _table_exists(conn, 'user'):
        if not _column_exists(conn, 'user', 'notification_prefs'):
            op.add_column(
                'user',
                sa.Column('notification_prefs', sa.JSON(), nullable=True),
            )

    # ==========================================================================
    # ENABLE RLS
    # ==========================================================================

    _enable_rls(conn)


def downgrade():
    conn = op.get_bind()
    is_postgres = conn.dialect.name == 'postgresql'

    _disable_rls(conn)

    # Drop added columns from existing tables
    if _table_exists(conn, 'user'):
        if _column_exists(conn, 'user', 'notification_prefs'):
            op.drop_column('user', 'notification_prefs')

    if _table_exists(conn, 'agent_messaging_channels'):
        if _index_exists(
            conn, 'agent_messaging_channels',
            'ix_agent_messaging_channels_selected_transaction_id',
        ):
            op.drop_index(
                'ix_agent_messaging_channels_selected_transaction_id',
                table_name='agent_messaging_channels',
            )
        if _column_exists(conn, 'agent_messaging_channels', 'selected_transaction_id'):
            op.drop_column('agent_messaging_channels', 'selected_transaction_id')

    if _table_exists(conn, 'transaction_documents'):
        for col_name in ['ai_processing_allowed', 'retention_until', 'sensitivity_class']:
            if _column_exists(conn, 'transaction_documents', col_name):
                op.drop_column('transaction_documents', col_name)

    if _table_exists(conn, 'bob_actions'):
        for col_name in [
            'page_context', 'confirmation_surface', 'risk', 'tool_bundle',
            'resulting_audit_event_ids', 'proposal_id', 'requirement_id',
            'idempotency_key', 'provider_event_id', 'rejected_at',
            'rejecting_user_id', 'approved_at', 'approving_user_id',
            'record_version', 'extraction_run_id', 'source_document_id',
            'preview_digest', 'response_trace_id', 'model', 'transaction_id'
        ]:
            if _column_exists(conn, 'bob_actions', col_name):
                op.drop_column('bob_actions', col_name)

    if _table_exists(conn, 'audit_events'):
        for col_name in ['bob_action_id', 'organization_id']:
            if _column_exists(conn, 'audit_events', col_name):
                op.drop_column('audit_events', col_name)

    # Drop new tables in reverse order
    for table in reversed(TENANT_TABLES):
        if _table_exists(conn, table):
            op.drop_table(table)
