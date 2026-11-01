"""Store staged and sent mobile message attachments with tenant isolation."""
from alembic import op
import sqlalchemy as sa

revision = 'add_mobile_chat_attachments'
down_revision = 'add_profile_photos'
branch_labels = None
depends_on = None

BACKEND_ONLY_TABLES = ('alembic_version', 'audit_events', 'chambers_properties', 'chat_conversations', 'chat_messages', 'client_portal_access', 'contact_emails', 'contact_groups', 'contact_voice_memos', 'fort_bend_properties', 'hcad_buildings', 'hcad_neighborhood_codes', 'hcad_properties', 'liberty_code_profiles', 'liberty_improvements', 'liberty_properties', 'market_data_cache', 'organization_metrics', 'organizations', 'partner_contacts', 'partner_organizations', 'platform_audit_log', 'rentcast_api_log', 'service_areas', 'user_email_integrations')


def upgrade():
    if not sa.inspect(op.get_bind()).has_table('mobile_chat_attachments'):
        op.create_table('mobile_chat_attachments',
            sa.Column('id', sa.String(36), primary_key=True),
            sa.Column('organization_id', sa.Integer(), sa.ForeignKey('organizations.id', ondelete='RESTRICT'), nullable=False),
            sa.Column('owner_kind', sa.String(16), nullable=False),
            sa.Column('owner_id', sa.Integer(), nullable=False),
            sa.Column('target_kind', sa.String(16), nullable=False),
            sa.Column('target_id', sa.Integer(), nullable=False),
            sa.Column('request_id', sa.String(36), nullable=False),
            sa.Column('filename', sa.String(180), nullable=False),
            sa.Column('content_type', sa.String(120), nullable=False),
            sa.Column('size', sa.Integer(), nullable=False),
            sa.Column('kind', sa.String(16), nullable=False),
            sa.Column('digest', sa.String(64), nullable=False),
            sa.Column('storage_path', sa.String(500), nullable=False),
            sa.Column('message_kind', sa.String(16), nullable=True),
            sa.Column('message_id', sa.Integer(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.UniqueConstraint('organization_id', 'owner_kind', 'owner_id', 'target_kind', 'target_id', 'request_id', name='uq_mobile_attachment_upload'),
        )
    op.execute('CREATE INDEX IF NOT EXISTS ix_mobile_attachment_message ON mobile_chat_attachments (organization_id, message_kind, message_id)')
    op.execute('CREATE INDEX IF NOT EXISTS ix_mobile_attachment_owner ON mobile_chat_attachments (organization_id, owner_kind, owner_id)')
    if op.get_bind().dialect.name == 'postgresql':
        # These backend tables are read through Flask, never the Supabase Data API.
        for table_name in BACKEND_ONLY_TABLES:
            op.execute(sa.text("""DO $$ DECLARE api_role text; BEGIN
                IF to_regclass('public.' || 'TABLE_NAME') IS NOT NULL THEN
                    FOR api_role IN SELECT rolname FROM pg_roles WHERE rolname IN ('anon', 'authenticated') LOOP
                        EXECUTE format('REVOKE ALL ON TABLE public.%I FROM %I', 'TABLE_NAME', api_role);
                    END LOOP;
                END IF;
            END $$""".replace('TABLE_NAME', table_name)))
        op.execute('ALTER TABLE mobile_chat_attachments ENABLE ROW LEVEL SECURITY')
        op.execute('DROP POLICY IF EXISTS mobile_attachment_org ON mobile_chat_attachments')
        op.execute("""CREATE POLICY mobile_attachment_org ON mobile_chat_attachments
            USING (organization_id = NULLIF(current_setting('app.current_org_id', true), '')::integer)
            WITH CHECK (organization_id = NULLIF(current_setting('app.current_org_id', true), '')::integer)""")
        op.execute("""DO $$ DECLARE api_role text; BEGIN
            FOR api_role IN SELECT rolname FROM pg_roles WHERE rolname IN ('anon', 'authenticated') LOOP
                EXECUTE format('REVOKE ALL ON TABLE mobile_chat_attachments FROM %I', api_role);
            END LOOP;
        END $$""")


def downgrade():
    op.execute('DROP TABLE IF EXISTS mobile_chat_attachments')
