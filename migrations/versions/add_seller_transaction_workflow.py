"""Add seller transaction workflow tables

Revision ID: add_seller_transaction_workflow
Revises: enable_magic_inbox_rls
Create Date: 2026-04-26

Adds first-class seller listing, showing, offer, backup contract,
under-contract milestone, amendment, termination, closing, commission,
and listing price history records.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = 'add_seller_transaction_workflow'
down_revision = 'enable_magic_inbox_rls'
branch_labels = None
depends_on = None


TENANT_TABLES = (
    'seller_listing_profiles',
    'seller_showings',
    'seller_offers',
    'seller_offer_versions',
    'seller_offer_documents',
    'seller_offer_activities',
    'seller_accepted_contracts',
    'seller_contract_milestones',
    'seller_contract_amendments',
    'seller_contract_amendment_versions',
    'seller_contract_terminations',
    'seller_closing_summaries',
    'seller_commission_terms',
    'seller_listing_price_changes',
)


def _table_exists(conn, table_name):
    return table_name in inspect(conn).get_table_names()


def _index_exists(conn, table_name, index_name):
    if not _table_exists(conn, table_name):
        return False
    return index_name in {idx['name'] for idx in inspect(conn).get_indexes(table_name)}


def _create_index_if_missing(conn, index_name, table_name, columns, unique=False):
    if not _index_exists(conn, table_name, index_name):
        op.create_index(index_name, table_name, columns, unique=unique)


def _enable_rls(conn):
    if conn.dialect.name != 'postgresql':
        return

    for table in TENANT_TABLES:
        op.execute(f'ALTER TABLE {table} ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE {table} FORCE ROW LEVEL SECURITY')
        op.execute(f'DROP POLICY IF EXISTS tenant_isolation_{table} ON {table}')
        op.execute(f"""
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
        """)


def _disable_rls(conn):
    if conn.dialect.name != 'postgresql':
        return

    for table in reversed(TENANT_TABLES):
        if _table_exists(conn, table):
            op.execute(f'DROP POLICY IF EXISTS tenant_isolation_{table} ON {table}')
            op.execute(f'ALTER TABLE {table} DISABLE ROW LEVEL SECURITY')


def upgrade():
    conn = op.get_bind()

    if not _table_exists(conn, 'seller_listing_profiles'):
        op.create_table(
            'seller_listing_profiles',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('organization_id', sa.Integer(), sa.ForeignKey('organizations.id', ondelete='RESTRICT'), nullable=False),
            sa.Column('transaction_id', sa.Integer(), sa.ForeignKey('transactions.id', ondelete='CASCADE'), nullable=False),
            sa.Column('created_by_id', sa.Integer(), sa.ForeignKey('user.id'), nullable=False),
            sa.Column('appointment_required', sa.Boolean(), server_default=sa.text('true')),
            sa.Column('showing_approval_policy', sa.String(length=50), server_default='manual'),
            sa.Column('access_type', sa.String(length=50)),
            sa.Column('lockbox_type', sa.String(length=50)),
            sa.Column('gate_code', sa.String(length=100)),
            sa.Column('alarm_notes', sa.Text()),
            sa.Column('pet_notes', sa.Text()),
            sa.Column('occupancy_status', sa.String(length=50)),
            sa.Column('preferred_showing_windows', sa.JSON()),
            sa.Column('restricted_showing_times', sa.JSON()),
            sa.Column('public_showing_instructions', sa.Text()),
            sa.Column('private_showing_notes', sa.Text()),
            sa.Column('showing_service_url', sa.String(length=500)),
            sa.Column('mls_number', sa.String(length=100)),
            sa.Column('current_list_price', sa.Numeric(12, 2)),
            sa.Column('original_list_price', sa.Numeric(12, 2)),
            sa.Column('go_live_date', sa.Date()),
            sa.Column('highest_best_enabled', sa.Boolean(), server_default=sa.text('false')),
            sa.Column('highest_best_deadline_at', sa.DateTime()),
            sa.Column('highest_best_message', sa.Text()),
            sa.Column('highest_best_sent_at', sa.DateTime()),
            sa.Column('highest_best_sent_by_id', sa.Integer(), sa.ForeignKey('user.id')),
            sa.Column('extra_data', sa.JSON()),
            sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
            sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now()),
            sa.UniqueConstraint('transaction_id', name='uq_seller_listing_profiles_transaction_id'),
        )

    if not _table_exists(conn, 'seller_showings'):
        op.create_table(
            'seller_showings',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('organization_id', sa.Integer(), sa.ForeignKey('organizations.id', ondelete='RESTRICT'), nullable=False),
            sa.Column('transaction_id', sa.Integer(), sa.ForeignKey('transactions.id', ondelete='CASCADE'), nullable=False),
            sa.Column('created_by_id', sa.Integer(), sa.ForeignKey('user.id'), nullable=False),
            sa.Column('showing_agent_name', sa.String(length=200), nullable=False),
            sa.Column('showing_agent_email', sa.String(length=200)),
            sa.Column('showing_agent_phone', sa.String(length=50)),
            sa.Column('showing_agent_brokerage', sa.String(length=200)),
            sa.Column('buyer_name', sa.String(length=200)),
            sa.Column('source', sa.String(length=100)),
            sa.Column('showing_agent_contact_id', sa.Integer(), sa.ForeignKey('contact.id')),
            sa.Column('showing_agent_participant_id', sa.Integer(), sa.ForeignKey('transaction_participants.id')),
            sa.Column('requested_start_at', sa.DateTime()),
            sa.Column('scheduled_start_at', sa.DateTime(), nullable=False),
            sa.Column('scheduled_end_at', sa.DateTime()),
            sa.Column('status', sa.String(length=50), server_default='scheduled', nullable=False),
            sa.Column('approved_at', sa.DateTime()),
            sa.Column('approved_by_id', sa.Integer(), sa.ForeignKey('user.id')),
            sa.Column('cancellation_reason', sa.Text()),
            sa.Column('showing_service_confirmation', sa.String(length=100)),
            sa.Column('access_instructions_snapshot', sa.Text()),
            sa.Column('private_notes', sa.Text()),
            sa.Column('feedback_received_at', sa.DateTime()),
            sa.Column('feedback_interest_level', sa.String(length=50)),
            sa.Column('feedback_price_opinion', sa.String(length=50)),
            sa.Column('feedback_condition_comments', sa.Text()),
            sa.Column('feedback_objections', sa.Text()),
            sa.Column('feedback_likelihood', sa.String(length=50)),
            sa.Column('feedback_follow_up_requested', sa.Boolean(), server_default=sa.text('false')),
            sa.Column('feedback_notes', sa.Text()),
            sa.Column('extra_data', sa.JSON()),
            sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
            sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now()),
        )

    if not _table_exists(conn, 'seller_offers'):
        op.create_table(
            'seller_offers',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('organization_id', sa.Integer(), sa.ForeignKey('organizations.id', ondelete='RESTRICT'), nullable=False),
            sa.Column('transaction_id', sa.Integer(), sa.ForeignKey('transactions.id', ondelete='CASCADE'), nullable=False),
            sa.Column('created_by_id', sa.Integer(), sa.ForeignKey('user.id'), nullable=False),
            sa.Column('source_showing_id', sa.Integer(), sa.ForeignKey('seller_showings.id', ondelete='SET NULL')),
            sa.Column('buyer_names', sa.String(length=500)),
            sa.Column('buyer_agent_name', sa.String(length=200)),
            sa.Column('buyer_agent_email', sa.String(length=200)),
            sa.Column('buyer_agent_phone', sa.String(length=50)),
            sa.Column('buyer_agent_brokerage', sa.String(length=200)),
            sa.Column('received_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.Column('creation_source', sa.String(length=50), server_default='uploaded_document'),
            sa.Column('status', sa.String(length=50), server_default='new', nullable=False),
            sa.Column('response_deadline_at', sa.DateTime()),
            sa.Column('response_deadline_source', sa.String(length=50)),
            sa.Column('expired_at', sa.DateTime()),
            sa.Column('expiration_warning_sent_at', sa.DateTime()),
            sa.Column('included_in_highest_best', sa.Boolean(), server_default=sa.text('false')),
            sa.Column('highest_best_requested_at', sa.DateTime()),
            sa.Column('highest_best_response_received_at', sa.DateTime()),
            sa.Column('highest_best_response_status', sa.String(length=50)),
            sa.Column('backup_position', sa.Integer()),
            sa.Column('backup_addendum_document_id', sa.Integer(), sa.ForeignKey('transaction_documents.id', ondelete='SET NULL')),
            sa.Column('backup_notice_received_at', sa.DateTime()),
            sa.Column('backup_promoted_at', sa.DateTime()),
            sa.Column('current_version_id', sa.Integer()),
            sa.Column('accepted_version_id', sa.Integer()),
            sa.Column('replacement_offer_id', sa.Integer(), sa.ForeignKey('seller_offers.id', ondelete='SET NULL')),
            sa.Column('offer_price', sa.Numeric(12, 2)),
            sa.Column('financing_type', sa.String(length=100)),
            sa.Column('cash_down_payment', sa.Numeric(12, 2)),
            sa.Column('earnest_money', sa.Numeric(12, 2)),
            sa.Column('additional_earnest_money', sa.Numeric(12, 2)),
            sa.Column('option_fee', sa.Numeric(12, 2)),
            sa.Column('option_period_days', sa.Integer()),
            sa.Column('seller_concessions_amount', sa.Numeric(12, 2)),
            sa.Column('proposed_close_date', sa.Date()),
            sa.Column('possession_type', sa.String(length=100)),
            sa.Column('leaseback_days', sa.Integer()),
            sa.Column('appraisal_contingency', sa.Boolean()),
            sa.Column('financing_contingency', sa.Boolean()),
            sa.Column('sale_of_other_property_contingency', sa.Boolean()),
            sa.Column('inspection_or_repair_terms_summary', sa.Text()),
            sa.Column('title_policy_payer', sa.String(length=50)),
            sa.Column('survey_payer', sa.String(length=50)),
            sa.Column('hoa_resale_certificate_payer', sa.String(length=50)),
            sa.Column('net_to_seller_estimate', sa.Numeric(12, 2)),
            sa.Column('last_activity_at', sa.DateTime()),
            sa.Column('last_activity_label', sa.String(length=200)),
            sa.Column('next_action', sa.String(length=200)),
            sa.Column('next_deadline_at', sa.DateTime()),
            sa.Column('terms_summary', sa.JSON()),
            sa.Column('extra_data', sa.JSON()),
            sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
            sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now()),
        )

    if not _table_exists(conn, 'seller_offer_versions'):
        op.create_table(
            'seller_offer_versions',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('organization_id', sa.Integer(), sa.ForeignKey('organizations.id', ondelete='RESTRICT'), nullable=False),
            sa.Column('transaction_id', sa.Integer(), sa.ForeignKey('transactions.id', ondelete='CASCADE'), nullable=False),
            sa.Column('offer_id', sa.Integer(), sa.ForeignKey('seller_offers.id', ondelete='CASCADE'), nullable=False),
            sa.Column('created_by_id', sa.Integer(), sa.ForeignKey('user.id'), nullable=False),
            sa.Column('transaction_document_id', sa.Integer(), sa.ForeignKey('transaction_documents.id', ondelete='SET NULL')),
            sa.Column('version_number', sa.Integer(), server_default='1', nullable=False),
            sa.Column('direction', sa.String(length=50), nullable=False),
            sa.Column('status', sa.String(length=50), server_default='draft'),
            sa.Column('submitted_at', sa.DateTime()),
            sa.Column('sent_at', sa.DateTime()),
            sa.Column('terms_data', sa.JSON()),
            sa.Column('extraction_reviewed_at', sa.DateTime()),
            sa.Column('extraction_reviewed_by_id', sa.Integer(), sa.ForeignKey('user.id')),
            sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
            sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now()),
        )

    if not _table_exists(conn, 'seller_offer_documents'):
        op.create_table(
            'seller_offer_documents',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('organization_id', sa.Integer(), sa.ForeignKey('organizations.id', ondelete='RESTRICT'), nullable=False),
            sa.Column('transaction_id', sa.Integer(), sa.ForeignKey('transactions.id', ondelete='CASCADE'), nullable=False),
            sa.Column('offer_id', sa.Integer(), sa.ForeignKey('seller_offers.id', ondelete='CASCADE'), nullable=False),
            sa.Column('transaction_document_id', sa.Integer(), sa.ForeignKey('transaction_documents.id', ondelete='CASCADE'), nullable=False),
            sa.Column('offer_version_id', sa.Integer(), sa.ForeignKey('seller_offer_versions.id', ondelete='SET NULL')),
            sa.Column('created_by_id', sa.Integer(), sa.ForeignKey('user.id'), nullable=False),
            sa.Column('document_type', sa.String(length=100), nullable=False),
            sa.Column('display_name', sa.String(length=200), nullable=False),
            sa.Column('is_primary_terms_document', sa.Boolean(), server_default=sa.text('false')),
            sa.Column('extraction_summary', sa.JSON()),
            sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
            sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now()),
        )

    if not _table_exists(conn, 'seller_offer_activities'):
        op.create_table(
            'seller_offer_activities',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('organization_id', sa.Integer(), sa.ForeignKey('organizations.id', ondelete='RESTRICT'), nullable=False),
            sa.Column('transaction_id', sa.Integer(), sa.ForeignKey('transactions.id', ondelete='CASCADE'), nullable=False),
            sa.Column('offer_id', sa.Integer(), sa.ForeignKey('seller_offers.id', ondelete='CASCADE'), nullable=False),
            sa.Column('version_id', sa.Integer(), sa.ForeignKey('seller_offer_versions.id', ondelete='SET NULL')),
            sa.Column('document_id', sa.Integer(), sa.ForeignKey('transaction_documents.id', ondelete='SET NULL')),
            sa.Column('actor_id', sa.Integer(), sa.ForeignKey('user.id', ondelete='SET NULL')),
            sa.Column('event_type', sa.String(length=50), nullable=False),
            sa.Column('label', sa.String(length=200), nullable=False),
            sa.Column('event_data', sa.JSON()),
            sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
        )

    if not _table_exists(conn, 'seller_accepted_contracts'):
        op.create_table(
            'seller_accepted_contracts',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('organization_id', sa.Integer(), sa.ForeignKey('organizations.id', ondelete='RESTRICT'), nullable=False),
            sa.Column('transaction_id', sa.Integer(), sa.ForeignKey('transactions.id', ondelete='CASCADE'), nullable=False),
            sa.Column('offer_id', sa.Integer(), sa.ForeignKey('seller_offers.id', ondelete='SET NULL')),
            sa.Column('accepted_version_id', sa.Integer(), sa.ForeignKey('seller_offer_versions.id', ondelete='SET NULL')),
            sa.Column('created_by_id', sa.Integer(), sa.ForeignKey('user.id'), nullable=False),
            sa.Column('status', sa.String(length=50), server_default='active', nullable=False),
            sa.Column('position', sa.String(length=20), server_default='primary', nullable=False),
            sa.Column('backup_position', sa.Integer()),
            sa.Column('backup_addendum_document_id', sa.Integer(), sa.ForeignKey('transaction_documents.id', ondelete='SET NULL')),
            sa.Column('backup_notice_sent_at', sa.DateTime()),
            sa.Column('backup_notice_received_at', sa.DateTime()),
            sa.Column('backup_promoted_at', sa.DateTime()),
            sa.Column('accepted_price', sa.Numeric(12, 2)),
            sa.Column('effective_date', sa.Date()),
            sa.Column('effective_at', sa.DateTime()),
            sa.Column('closing_date', sa.Date()),
            sa.Column('option_period_days', sa.Integer()),
            sa.Column('financing_approval_deadline', sa.Date()),
            sa.Column('title_company', sa.String(length=200)),
            sa.Column('escrow_officer', sa.String(length=200)),
            sa.Column('survey_choice', sa.String(length=100)),
            sa.Column('hoa_applicable', sa.Boolean()),
            sa.Column('seller_disclosure_required', sa.Boolean()),
            sa.Column('seller_disclosure_delivered_at', sa.DateTime()),
            sa.Column('lead_based_paint_required', sa.Boolean()),
            sa.Column('frozen_terms', sa.JSON()),
            sa.Column('addenda_data', sa.JSON()),
            sa.Column('extra_data', sa.JSON()),
            sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
            sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now()),
        )

    if not _table_exists(conn, 'seller_contract_milestones'):
        op.create_table(
            'seller_contract_milestones',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('organization_id', sa.Integer(), sa.ForeignKey('organizations.id', ondelete='RESTRICT'), nullable=False),
            sa.Column('transaction_id', sa.Integer(), sa.ForeignKey('transactions.id', ondelete='CASCADE'), nullable=False),
            sa.Column('accepted_contract_id', sa.Integer(), sa.ForeignKey('seller_accepted_contracts.id', ondelete='CASCADE'), nullable=False),
            sa.Column('created_by_id', sa.Integer(), sa.ForeignKey('user.id')),
            sa.Column('milestone_key', sa.String(length=100), nullable=False),
            sa.Column('title', sa.String(length=200), nullable=False),
            sa.Column('due_at', sa.DateTime()),
            sa.Column('status', sa.String(length=50), server_default='not_started'),
            sa.Column('completed_at', sa.DateTime()),
            sa.Column('responsible_party', sa.String(length=100)),
            sa.Column('source', sa.String(length=50), server_default='calculated'),
            sa.Column('notes', sa.Text()),
            sa.Column('source_data', sa.JSON()),
            sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
            sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now()),
        )

    if not _table_exists(conn, 'seller_contract_amendments'):
        op.create_table(
            'seller_contract_amendments',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('organization_id', sa.Integer(), sa.ForeignKey('organizations.id', ondelete='RESTRICT'), nullable=False),
            sa.Column('transaction_id', sa.Integer(), sa.ForeignKey('transactions.id', ondelete='CASCADE'), nullable=False),
            sa.Column('accepted_contract_id', sa.Integer(), sa.ForeignKey('seller_accepted_contracts.id', ondelete='CASCADE'), nullable=False),
            sa.Column('created_by_id', sa.Integer(), sa.ForeignKey('user.id'), nullable=False),
            sa.Column('current_version_id', sa.Integer()),
            sa.Column('accepted_version_id', sa.Integer()),
            sa.Column('amendment_type', sa.String(length=100), server_default='other'),
            sa.Column('status', sa.String(length=50), server_default='received'),
            sa.Column('response_deadline_at', sa.DateTime()),
            sa.Column('summary', sa.Text()),
            sa.Column('extra_data', sa.JSON()),
            sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
            sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now()),
        )

    if not _table_exists(conn, 'seller_contract_amendment_versions'):
        op.create_table(
            'seller_contract_amendment_versions',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('organization_id', sa.Integer(), sa.ForeignKey('organizations.id', ondelete='RESTRICT'), nullable=False),
            sa.Column('transaction_id', sa.Integer(), sa.ForeignKey('transactions.id', ondelete='CASCADE'), nullable=False),
            sa.Column('amendment_id', sa.Integer(), sa.ForeignKey('seller_contract_amendments.id', ondelete='CASCADE'), nullable=False),
            sa.Column('created_by_id', sa.Integer(), sa.ForeignKey('user.id'), nullable=False),
            sa.Column('transaction_document_id', sa.Integer(), sa.ForeignKey('transaction_documents.id', ondelete='SET NULL')),
            sa.Column('version_number', sa.Integer(), server_default='1', nullable=False),
            sa.Column('direction', sa.String(length=50), nullable=False),
            sa.Column('status', sa.String(length=50), server_default='draft'),
            sa.Column('submitted_at', sa.DateTime()),
            sa.Column('terms_data', sa.JSON()),
            sa.Column('reviewed_at', sa.DateTime()),
            sa.Column('reviewed_by_id', sa.Integer(), sa.ForeignKey('user.id')),
            sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
            sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now()),
        )

    if not _table_exists(conn, 'seller_contract_terminations'):
        op.create_table(
            'seller_contract_terminations',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('organization_id', sa.Integer(), sa.ForeignKey('organizations.id', ondelete='RESTRICT'), nullable=False),
            sa.Column('transaction_id', sa.Integer(), sa.ForeignKey('transactions.id', ondelete='CASCADE'), nullable=False),
            sa.Column('accepted_contract_id', sa.Integer(), sa.ForeignKey('seller_accepted_contracts.id', ondelete='CASCADE'), nullable=False),
            sa.Column('created_by_id', sa.Integer(), sa.ForeignKey('user.id'), nullable=False),
            sa.Column('termination_document_id', sa.Integer(), sa.ForeignKey('transaction_documents.id', ondelete='SET NULL')),
            sa.Column('promoted_backup_contract_id', sa.Integer(), sa.ForeignKey('seller_accepted_contracts.id', ondelete='SET NULL')),
            sa.Column('termination_reason', sa.String(length=100), nullable=False),
            sa.Column('terminated_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.Column('earnest_money_disposition', sa.String(length=200)),
            sa.Column('notes', sa.Text()),
            sa.Column('returned_to_active', sa.Boolean(), server_default=sa.text('false')),
            sa.Column('backup_promoted', sa.Boolean(), server_default=sa.text('false')),
            sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
        )

    if not _table_exists(conn, 'seller_closing_summaries'):
        op.create_table(
            'seller_closing_summaries',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('organization_id', sa.Integer(), sa.ForeignKey('organizations.id', ondelete='RESTRICT'), nullable=False),
            sa.Column('transaction_id', sa.Integer(), sa.ForeignKey('transactions.id', ondelete='CASCADE'), nullable=False),
            sa.Column('accepted_contract_id', sa.Integer(), sa.ForeignKey('seller_accepted_contracts.id', ondelete='CASCADE'), nullable=False),
            sa.Column('created_by_id', sa.Integer(), sa.ForeignKey('user.id'), nullable=False),
            sa.Column('final_net_sheet_document_id', sa.Integer(), sa.ForeignKey('transaction_documents.id', ondelete='SET NULL')),
            sa.Column('actual_closing_date', sa.Date()),
            sa.Column('funded_recorded_at', sa.DateTime()),
            sa.Column('final_sales_price', sa.Numeric(12, 2)),
            sa.Column('final_seller_concessions', sa.Numeric(12, 2)),
            sa.Column('final_listing_commission', sa.Numeric(12, 2)),
            sa.Column('final_coop_compensation', sa.Numeric(12, 2)),
            sa.Column('final_referral_fee', sa.Numeric(12, 2)),
            sa.Column('final_net_proceeds', sa.Numeric(12, 2)),
            sa.Column('deed_recording_reference', sa.String(length=200)),
            sa.Column('final_walkthrough_complete', sa.Boolean(), server_default=sa.text('false')),
            sa.Column('key_access_handoff_complete', sa.Boolean(), server_default=sa.text('false')),
            sa.Column('possession_status', sa.String(length=100)),
            sa.Column('notes', sa.Text()),
            sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
            sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now()),
            sa.UniqueConstraint('accepted_contract_id', name='uq_seller_closing_summaries_contract_id'),
        )

    if not _table_exists(conn, 'seller_commission_terms'):
        op.create_table(
            'seller_commission_terms',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('organization_id', sa.Integer(), sa.ForeignKey('organizations.id', ondelete='RESTRICT'), nullable=False),
            sa.Column('transaction_id', sa.Integer(), sa.ForeignKey('transactions.id', ondelete='CASCADE'), nullable=False),
            sa.Column('created_by_id', sa.Integer(), sa.ForeignKey('user.id'), nullable=False),
            sa.Column('listing_commission_percent', sa.Numeric(6, 3)),
            sa.Column('listing_commission_flat', sa.Numeric(12, 2)),
            sa.Column('coop_compensation_percent', sa.Numeric(6, 3)),
            sa.Column('coop_compensation_flat', sa.Numeric(12, 2)),
            sa.Column('bonus_amount', sa.Numeric(12, 2)),
            sa.Column('referral_fee_percent', sa.Numeric(6, 3)),
            sa.Column('referral_fee_flat', sa.Numeric(12, 2)),
            sa.Column('admin_transaction_fee', sa.Numeric(12, 2)),
            sa.Column('representation_mode', sa.String(length=50), server_default='unknown'),
            sa.Column('source', sa.String(length=50), server_default='manual'),
            sa.Column('notes', sa.Text()),
            sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
            sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now()),
            sa.UniqueConstraint('transaction_id', name='uq_seller_commission_terms_transaction_id'),
        )

    if not _table_exists(conn, 'seller_listing_price_changes'):
        op.create_table(
            'seller_listing_price_changes',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('organization_id', sa.Integer(), sa.ForeignKey('organizations.id', ondelete='RESTRICT'), nullable=False),
            sa.Column('transaction_id', sa.Integer(), sa.ForeignKey('transactions.id', ondelete='CASCADE'), nullable=False),
            sa.Column('created_by_id', sa.Integer(), sa.ForeignKey('user.id'), nullable=False),
            sa.Column('old_price', sa.Numeric(12, 2)),
            sa.Column('new_price', sa.Numeric(12, 2), nullable=False),
            sa.Column('changed_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.Column('reason', sa.String(length=200)),
            sa.Column('notes', sa.Text()),
            sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
        )

    index_specs = (
        ('ix_seller_listing_profiles_org_id', 'seller_listing_profiles', ['organization_id']),
        ('ix_seller_listing_profiles_transaction_id', 'seller_listing_profiles', ['transaction_id']),
        ('ix_seller_showings_org_id', 'seller_showings', ['organization_id']),
        ('ix_seller_showings_transaction_id', 'seller_showings', ['transaction_id']),
        ('ix_seller_showings_start_status', 'seller_showings', ['scheduled_start_at', 'status']),
        ('ix_seller_offers_org_id', 'seller_offers', ['organization_id']),
        ('ix_seller_offers_transaction_id', 'seller_offers', ['transaction_id']),
        ('ix_seller_offers_status_deadline', 'seller_offers', ['status', 'response_deadline_at']),
        ('ix_seller_offers_current_version_id', 'seller_offers', ['current_version_id']),
        ('ix_seller_offers_accepted_version_id', 'seller_offers', ['accepted_version_id']),
        ('ix_seller_offer_versions_org_id', 'seller_offer_versions', ['organization_id']),
        ('ix_seller_offer_versions_transaction_id', 'seller_offer_versions', ['transaction_id']),
        ('ix_seller_offer_versions_offer_id', 'seller_offer_versions', ['offer_id']),
        ('ix_seller_offer_documents_org_id', 'seller_offer_documents', ['organization_id']),
        ('ix_seller_offer_documents_transaction_id', 'seller_offer_documents', ['transaction_id']),
        ('ix_seller_offer_documents_offer_id', 'seller_offer_documents', ['offer_id']),
        ('ix_seller_offer_documents_document_id', 'seller_offer_documents', ['transaction_document_id']),
        ('ix_seller_offer_documents_type', 'seller_offer_documents', ['document_type']),
        ('ix_seller_offer_activities_org_id', 'seller_offer_activities', ['organization_id']),
        ('ix_seller_offer_activities_transaction_id', 'seller_offer_activities', ['transaction_id']),
        ('ix_seller_offer_activities_offer_created', 'seller_offer_activities', ['offer_id', 'created_at']),
        ('ix_seller_accepted_contracts_org_id', 'seller_accepted_contracts', ['organization_id']),
        ('ix_seller_accepted_contracts_transaction_id', 'seller_accepted_contracts', ['transaction_id']),
        ('ix_seller_accepted_contracts_position_status', 'seller_accepted_contracts', ['position', 'status']),
        ('ix_seller_contract_milestones_org_id', 'seller_contract_milestones', ['organization_id']),
        ('ix_seller_contract_milestones_transaction_id', 'seller_contract_milestones', ['transaction_id']),
        ('ix_seller_contract_milestones_contract_due', 'seller_contract_milestones', ['accepted_contract_id', 'due_at']),
        ('ix_seller_contract_amendments_org_id', 'seller_contract_amendments', ['organization_id']),
        ('ix_seller_contract_amendments_transaction_id', 'seller_contract_amendments', ['transaction_id']),
        ('ix_seller_contract_amendment_versions_org_id', 'seller_contract_amendment_versions', ['organization_id']),
        ('ix_seller_contract_amendment_versions_transaction_id', 'seller_contract_amendment_versions', ['transaction_id']),
        ('ix_seller_contract_terminations_org_id', 'seller_contract_terminations', ['organization_id']),
        ('ix_seller_contract_terminations_transaction_id', 'seller_contract_terminations', ['transaction_id']),
        ('ix_seller_closing_summaries_org_id', 'seller_closing_summaries', ['organization_id']),
        ('ix_seller_closing_summaries_transaction_id', 'seller_closing_summaries', ['transaction_id']),
        ('ix_seller_commission_terms_org_id', 'seller_commission_terms', ['organization_id']),
        ('ix_seller_commission_terms_transaction_id', 'seller_commission_terms', ['transaction_id']),
        ('ix_seller_listing_price_changes_org_id', 'seller_listing_price_changes', ['organization_id']),
        ('ix_seller_listing_price_changes_transaction_id', 'seller_listing_price_changes', ['transaction_id']),
    )
    for index_name, table_name, columns in index_specs:
        _create_index_if_missing(conn, index_name, table_name, columns)

    _enable_rls(conn)


def downgrade():
    conn = op.get_bind()
    _disable_rls(conn)

    for table in reversed(TENANT_TABLES):
        if _table_exists(conn, table):
            op.drop_table(table)
