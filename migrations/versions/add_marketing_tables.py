"""Add email marketing tables and contact marketing consent.

Revision ID: add_marketing_tables
Revises: add_mcp_oauth_tables
Create Date: 2026-08-19
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = 'add_marketing_tables'
down_revision = 'add_mcp_oauth_tables'
branch_labels = None
depends_on = None


# Every table here is tenant-scoped and gets forced RLS. The unauthenticated
# surfaces (the unsubscribe route and the SendGrid event webhook) set org
# context before querying, so none of them need an RLS exemption.
_TENANT_TABLES = (
    'marketing_templates',
    'marketing_template_versions',
    'marketing_audiences',
    'marketing_campaigns',
    'marketing_campaign_steps',
    'marketing_enrollments',
    'marketing_sends',
)


def _table_exists(conn, table_name):
    return table_name in inspect(conn).get_table_names()


def _column_exists(conn, table_name, column_name):
    if not _table_exists(conn, table_name):
        return False
    return column_name in {c['name'] for c in inspect(conn).get_columns(table_name)}


def upgrade():
    conn = op.get_bind()

    if not _column_exists(conn, 'contact', 'marketing_consent'):
        with op.batch_alter_table('contact') as batch:
            batch.add_column(sa.Column(
                'marketing_consent', sa.String(20), nullable=False,
                server_default='unknown',
            ))
            batch.add_column(sa.Column(
                'marketing_consent_source', sa.String(30), nullable=True,
            ))
            batch.add_column(sa.Column(
                'marketing_consent_at', sa.DateTime(), nullable=True,
            ))
        op.create_index('ix_contact_marketing_consent', 'contact', ['marketing_consent'])

    if not _table_exists(conn, 'marketing_templates'):
        op.create_table(
            'marketing_templates',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('organization_id', sa.Integer(), sa.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=False),
            sa.Column('created_by_id', sa.Integer(), sa.ForeignKey('user.id', ondelete='SET NULL'), nullable=True),
            sa.Column('name', sa.String(200), nullable=False),
            sa.Column('description', sa.String(500), nullable=True),
            sa.Column('category', sa.String(50), nullable=False, server_default='other'),
            sa.Column('subject', sa.String(300), nullable=False),
            sa.Column('preheader', sa.String(300), nullable=True),
            sa.Column('blocks', sa.JSON(), nullable=False),
            sa.Column('html_cached', sa.Text(), nullable=True),
            sa.Column('text_cached', sa.Text(), nullable=True),
            sa.Column('visibility', sa.String(20), nullable=False, server_default='private'),
            sa.Column('status', sa.String(20), nullable=False, server_default='draft'),
            sa.Column('source', sa.String(20), nullable=False, server_default='manual'),
            sa.Column('version', sa.Integer(), nullable=False, server_default='1'),
            sa.Column('compliance_state', sa.String(20), nullable=False, server_default='pass'),
            sa.Column('compliance_findings', sa.JSON(), nullable=False),
            sa.Column('compliance_ack_by_id', sa.Integer(), sa.ForeignKey('user.id', ondelete='SET NULL'), nullable=True),
            sa.Column('compliance_ack_at', sa.DateTime(), nullable=True),
            sa.Column('merge_fields_used', sa.JSON(), nullable=False),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('updated_at', sa.DateTime(), nullable=False),
            sa.Column('last_used_at', sa.DateTime(), nullable=True),
        )
        op.create_index('ix_marketing_templates_organization_id', 'marketing_templates', ['organization_id'])
        op.create_index('ix_marketing_templates_created_by_id', 'marketing_templates', ['created_by_id'])
        op.create_index('ix_marketing_templates_category', 'marketing_templates', ['category'])
        op.create_index('ix_marketing_templates_visibility', 'marketing_templates', ['visibility'])
        op.create_index('ix_marketing_templates_status', 'marketing_templates', ['status'])
        op.create_index('ix_marketing_templates_org_visibility', 'marketing_templates', ['organization_id', 'visibility'])
        op.create_index('ix_marketing_templates_org_creator', 'marketing_templates', ['organization_id', 'created_by_id'])

    if not _table_exists(conn, 'marketing_template_versions'):
        op.create_table(
            'marketing_template_versions',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('organization_id', sa.Integer(), sa.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=False),
            sa.Column('template_id', sa.Integer(), sa.ForeignKey('marketing_templates.id', ondelete='CASCADE'), nullable=False),
            sa.Column('version', sa.Integer(), nullable=False),
            sa.Column('subject', sa.String(300), nullable=False),
            sa.Column('preheader', sa.String(300), nullable=True),
            sa.Column('blocks', sa.JSON(), nullable=False),
            sa.Column('html', sa.Text(), nullable=True),
            sa.Column('created_by_id', sa.Integer(), sa.ForeignKey('user.id', ondelete='SET NULL'), nullable=True),
            sa.Column('change_note', sa.String(500), nullable=True),
            sa.Column('generated_by_ai', sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column('prompt', sa.Text(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.UniqueConstraint('template_id', 'version', name='uq_marketing_template_version'),
        )
        op.create_index('ix_marketing_template_versions_organization_id', 'marketing_template_versions', ['organization_id'])
        op.create_index('ix_marketing_template_versions_template_id', 'marketing_template_versions', ['template_id'])

    if not _table_exists(conn, 'marketing_audiences'):
        op.create_table(
            'marketing_audiences',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('organization_id', sa.Integer(), sa.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=False),
            sa.Column('user_id', sa.Integer(), sa.ForeignKey('user.id', ondelete='CASCADE'), nullable=False),
            sa.Column('name', sa.String(200), nullable=True),
            sa.Column('filter', sa.JSON(), nullable=False),
            sa.Column('is_saved', sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column('cached_count', sa.Integer(), nullable=True),
            sa.Column('cached_at', sa.DateTime(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('updated_at', sa.DateTime(), nullable=False),
        )
        op.create_index('ix_marketing_audiences_organization_id', 'marketing_audiences', ['organization_id'])
        op.create_index('ix_marketing_audiences_user_id', 'marketing_audiences', ['user_id'])
        op.create_index('ix_marketing_audiences_is_saved', 'marketing_audiences', ['is_saved'])

    if not _table_exists(conn, 'marketing_campaigns'):
        op.create_table(
            'marketing_campaigns',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('organization_id', sa.Integer(), sa.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=False),
            sa.Column('user_id', sa.Integer(), sa.ForeignKey('user.id', ondelete='CASCADE'), nullable=False),
            sa.Column('name', sa.String(200), nullable=False),
            sa.Column('kind', sa.String(20), nullable=False, server_default='one_time'),
            sa.Column('status', sa.String(20), nullable=False, server_default='draft'),
            sa.Column('audience_id', sa.Integer(), sa.ForeignKey('marketing_audiences.id', ondelete='RESTRICT'), nullable=True),
            sa.Column('from_name', sa.String(200), nullable=True),
            sa.Column('reply_to', sa.String(200), nullable=True),
            sa.Column('scheduled_at', sa.DateTime(), nullable=True),
            sa.Column('timezone', sa.String(64), nullable=False, server_default='America/Chicago'),
            sa.Column('launched_at', sa.DateTime(), nullable=True),
            sa.Column('completed_at', sa.DateTime(), nullable=True),
            sa.Column('paused_at', sa.DateTime(), nullable=True),
            sa.Column('auto_paused_reason', sa.String(200), nullable=True),
            sa.Column('created_via', sa.String(20), nullable=False, server_default='web'),
            sa.Column('total_recipients', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('queued_count', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('sent_count', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('delivered_count', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('bounced_count', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('failed_count', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('skipped_count', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('unsubscribed_count', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('updated_at', sa.DateTime(), nullable=False),
        )
        op.create_index('ix_marketing_campaigns_organization_id', 'marketing_campaigns', ['organization_id'])
        op.create_index('ix_marketing_campaigns_user_id', 'marketing_campaigns', ['user_id'])
        op.create_index('ix_marketing_campaigns_status', 'marketing_campaigns', ['status'])
        op.create_index('ix_marketing_campaigns_scheduled_at', 'marketing_campaigns', ['scheduled_at'])
        op.create_index('ix_marketing_campaigns_org_status', 'marketing_campaigns', ['organization_id', 'status'])
        op.create_index('ix_marketing_campaigns_org_user', 'marketing_campaigns', ['organization_id', 'user_id'])

    if not _table_exists(conn, 'marketing_campaign_steps'):
        op.create_table(
            'marketing_campaign_steps',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('organization_id', sa.Integer(), sa.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=False),
            sa.Column('campaign_id', sa.Integer(), sa.ForeignKey('marketing_campaigns.id', ondelete='CASCADE'), nullable=False),
            sa.Column('template_id', sa.Integer(), sa.ForeignKey('marketing_templates.id', ondelete='RESTRICT'), nullable=False),
            sa.Column('step_index', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('name', sa.String(200), nullable=True),
            sa.Column('delay_days', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('send_hour_local', sa.Integer(), nullable=False, server_default='9'),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.UniqueConstraint('campaign_id', 'step_index', name='uq_marketing_step_index'),
        )
        op.create_index('ix_marketing_campaign_steps_organization_id', 'marketing_campaign_steps', ['organization_id'])
        op.create_index('ix_marketing_campaign_steps_campaign_id', 'marketing_campaign_steps', ['campaign_id'])

    if not _table_exists(conn, 'marketing_enrollments'):
        op.create_table(
            'marketing_enrollments',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('organization_id', sa.Integer(), sa.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=False),
            sa.Column('campaign_id', sa.Integer(), sa.ForeignKey('marketing_campaigns.id', ondelete='CASCADE'), nullable=False),
            sa.Column('contact_id', sa.Integer(), sa.ForeignKey('contact.id', ondelete='CASCADE'), nullable=False),
            sa.Column('status', sa.String(20), nullable=False, server_default='active'),
            sa.Column('current_step_index', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('next_send_at', sa.DateTime(), nullable=True),
            sa.Column('enrolled_at', sa.DateTime(), nullable=False),
            sa.Column('completed_at', sa.DateTime(), nullable=True),
            sa.Column('stop_reason', sa.String(100), nullable=True),
            sa.UniqueConstraint('campaign_id', 'contact_id', name='uq_marketing_enrollment'),
        )
        op.create_index('ix_marketing_enrollments_organization_id', 'marketing_enrollments', ['organization_id'])
        op.create_index('ix_marketing_enrollments_campaign_id', 'marketing_enrollments', ['campaign_id'])
        op.create_index('ix_marketing_enrollments_contact_id', 'marketing_enrollments', ['contact_id'])
        op.create_index('ix_marketing_enrollments_status', 'marketing_enrollments', ['status'])
        op.create_index('ix_marketing_enrollments_next_send_at', 'marketing_enrollments', ['next_send_at'])
        op.create_index('ix_marketing_enrollments_due', 'marketing_enrollments', ['status', 'next_send_at'])

    if not _table_exists(conn, 'marketing_sends'):
        op.create_table(
            'marketing_sends',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('organization_id', sa.Integer(), sa.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=False),
            sa.Column('campaign_id', sa.Integer(), sa.ForeignKey('marketing_campaigns.id', ondelete='CASCADE'), nullable=False),
            sa.Column('step_id', sa.Integer(), sa.ForeignKey('marketing_campaign_steps.id', ondelete='CASCADE'), nullable=False),
            sa.Column('enrollment_id', sa.Integer(), sa.ForeignKey('marketing_enrollments.id', ondelete='CASCADE'), nullable=True),
            sa.Column('contact_id', sa.Integer(), sa.ForeignKey('contact.id', ondelete='CASCADE'), nullable=False),
            sa.Column('template_id', sa.Integer(), sa.ForeignKey('marketing_templates.id', ondelete='RESTRICT'), nullable=False),
            sa.Column('user_id', sa.Integer(), sa.ForeignKey('user.id', ondelete='SET NULL'), nullable=True),
            sa.Column('to_email', sa.String(200), nullable=False),
            sa.Column('subject_rendered', sa.String(300), nullable=True),
            sa.Column('status', sa.String(20), nullable=False, server_default='queued'),
            sa.Column('skip_reason', sa.String(50), nullable=True),
            sa.Column('scheduled_for', sa.DateTime(), nullable=True),
            sa.Column('sent_at', sa.DateTime(), nullable=True),
            sa.Column('delivered_at', sa.DateTime(), nullable=True),
            sa.Column('opened_at', sa.DateTime(), nullable=True),
            sa.Column('clicked_at', sa.DateTime(), nullable=True),
            sa.Column('provider_message_id', sa.String(200), nullable=True),
            sa.Column('error', sa.String(500), nullable=True),
            sa.Column('attempt_count', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('last_attempt_at', sa.DateTime(), nullable=True),
            sa.Column('unsubscribe_token', sa.String(120), nullable=False),
            sa.Column('created_at', sa.DateTime(), nullable=False),
        )
        op.create_index('ix_marketing_sends_organization_id', 'marketing_sends', ['organization_id'])
        op.create_index('ix_marketing_sends_campaign_id', 'marketing_sends', ['campaign_id'])
        op.create_index('ix_marketing_sends_enrollment_id', 'marketing_sends', ['enrollment_id'])
        op.create_index('ix_marketing_sends_contact_id', 'marketing_sends', ['contact_id'])
        op.create_index('ix_marketing_sends_to_email', 'marketing_sends', ['to_email'])
        op.create_index('ix_marketing_sends_status', 'marketing_sends', ['status'])
        op.create_index('ix_marketing_sends_scheduled_for', 'marketing_sends', ['scheduled_for'])
        op.create_index('ix_marketing_sends_provider_message_id', 'marketing_sends', ['provider_message_id'])
        op.create_index('ix_marketing_sends_unsubscribe_token', 'marketing_sends', ['unsubscribe_token'], unique=True)
        op.create_index('ix_marketing_sends_claim', 'marketing_sends', ['status', 'scheduled_for'])
        op.create_index('ix_marketing_sends_campaign_status', 'marketing_sends', ['campaign_id', 'status'])
        op.create_index('ix_marketing_sends_contact_created', 'marketing_sends', ['contact_id', 'created_at'])

    if not _table_exists(conn, 'marketing_suppressions'):
        op.create_table(
            'marketing_suppressions',
            sa.Column('id', sa.Integer(), primary_key=True),
            # Null for platform-scoped rows, which belong to no single tenant.
            sa.Column('organization_id', sa.Integer(), sa.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=True),
            sa.Column('email', sa.String(200), nullable=False),
            sa.Column('scope', sa.String(20), nullable=False, server_default='org'),
            sa.Column('reason', sa.String(30), nullable=False),
            sa.Column('source_send_id', sa.Integer(), sa.ForeignKey('marketing_sends.id', ondelete='SET NULL'), nullable=True),
            sa.Column('created_by_id', sa.Integer(), sa.ForeignKey('user.id', ondelete='SET NULL'), nullable=True),
            sa.Column('note', sa.String(300), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.UniqueConstraint('organization_id', 'email', 'scope', name='uq_marketing_suppression_email'),
        )
        op.create_index('ix_marketing_suppressions_organization_id', 'marketing_suppressions', ['organization_id'])
        op.create_index('ix_marketing_suppressions_email', 'marketing_suppressions', ['email'])
        op.create_index('ix_marketing_suppressions_scope', 'marketing_suppressions', ['scope'])
        op.create_index('ix_marketing_suppressions_lookup', 'marketing_suppressions', ['email', 'scope'])

    if conn.dialect.name == 'postgresql':
        for table in _TENANT_TABLES:
            conn.execute(sa.text(f'ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY'))
            conn.execute(sa.text(f'ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY'))
            conn.execute(sa.text(f'''
                CREATE POLICY tenant_isolation_{table} ON public.{table}
                FOR ALL
                USING (organization_id = current_setting('app.current_org_id', true)::int)
                WITH CHECK (organization_id = current_setting('app.current_org_id', true)::int)
            '''))
            conn.execute(sa.text(
                f'REVOKE ALL ON TABLE public.{table} FROM anon, authenticated'
            ))

        # Suppressions are the one table read across tenants: a platform-scoped
        # row has no organization_id and must be visible to every org so a
        # complained-about address stays dead everywhere.
        conn.execute(sa.text('ALTER TABLE public.marketing_suppressions ENABLE ROW LEVEL SECURITY'))
        conn.execute(sa.text('ALTER TABLE public.marketing_suppressions FORCE ROW LEVEL SECURITY'))
        conn.execute(sa.text('''
            CREATE POLICY tenant_isolation_marketing_suppressions
            ON public.marketing_suppressions
            FOR ALL
            USING (
                scope = 'platform'
                OR organization_id = current_setting('app.current_org_id', true)::int
            )
            WITH CHECK (
                scope = 'platform'
                OR organization_id = current_setting('app.current_org_id', true)::int
            )
        '''))
        conn.execute(sa.text(
            'REVOKE ALL ON TABLE public.marketing_suppressions FROM anon, authenticated'
        ))


def downgrade():
    conn = op.get_bind()

    for table in ('marketing_suppressions',) + tuple(reversed(_TENANT_TABLES)):
        if _table_exists(conn, table):
            if conn.dialect.name == 'postgresql':
                conn.execute(sa.text(
                    f'DROP POLICY IF EXISTS tenant_isolation_{table} ON public.{table}'
                ))
            op.drop_table(table)

    if _column_exists(conn, 'contact', 'marketing_consent'):
        op.drop_index('ix_contact_marketing_consent', table_name='contact')
        with op.batch_alter_table('contact') as batch:
            batch.drop_column('marketing_consent_at')
            batch.drop_column('marketing_consent_source')
            batch.drop_column('marketing_consent')
