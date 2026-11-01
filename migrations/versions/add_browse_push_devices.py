"""Attach browsing accounts and APNs environments to registered devices."""
from alembic import op
import sqlalchemy as sa

revision = 'add_browse_push_devices'
down_revision = 'add_personal_todo_details'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    if conn.dialect.name == 'postgresql':
        op.execute('ALTER TABLE device_tokens ADD COLUMN IF NOT EXISTS browse_account_id INTEGER REFERENCES client_browse_accounts(id) ON DELETE CASCADE')
        op.execute('ALTER TABLE device_tokens ADD COLUMN IF NOT EXISTS environment VARCHAR(20)')
        op.execute('ALTER TABLE device_tokens ADD COLUMN IF NOT EXISTS expires_at TIMESTAMP')
        op.execute('CREATE INDEX IF NOT EXISTS ix_device_tokens_browse_account_id ON device_tokens (browse_account_id)')
        op.execute("""
            CREATE OR REPLACE FUNCTION public.release_other_device_bindings(p_token text, p_audience text, p_org integer)
            RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
            BEGIN
                IF p_org IS DISTINCT FROM NULLIF(current_setting('app.current_org_id', true), '')::integer THEN
                    RAISE EXCEPTION 'Device organization context does not match' USING ERRCODE = '42501';
                END IF;
                DELETE FROM public.device_tokens
                WHERE token = p_token AND audience = p_audience AND organization_id <> p_org;
            END;
            $$
        """)
        op.execute('REVOKE ALL ON FUNCTION public.release_other_device_bindings(text, text, integer) FROM PUBLIC')
        op.execute("""
            DO $$ DECLARE api_role text;
            BEGIN
                FOR api_role IN SELECT rolname FROM pg_roles WHERE rolname IN ('anon', 'authenticated', 'service_role')
                LOOP
                    EXECUTE format('REVOKE ALL ON FUNCTION public.release_other_device_bindings(text, text, integer) FROM %I', api_role);
                END LOOP;
            END $$
        """)
        op.execute('GRANT EXECUTE ON FUNCTION public.release_other_device_bindings(text, text, integer) TO CURRENT_USER')
    else:
        columns = {c['name'] for c in sa.inspect(conn).get_columns('device_tokens')}
        with op.batch_alter_table('device_tokens') as batch:
            if 'browse_account_id' not in columns:
                batch.add_column(sa.Column('browse_account_id', sa.Integer(), nullable=True))
                batch.create_foreign_key('fk_devices_browse_account', 'client_browse_accounts', ['browse_account_id'], ['id'], ondelete='CASCADE')
                batch.create_index('ix_device_tokens_browse_account_id', ['browse_account_id'])
            if 'expires_at' not in columns:
                batch.add_column(sa.Column('expires_at', sa.DateTime(), nullable=True))
            if 'environment' not in columns:
                batch.add_column(sa.Column('environment', sa.String(20), nullable=True))


def downgrade():
    if op.get_bind().dialect.name == 'postgresql':
        op.execute('DROP FUNCTION IF EXISTS public.release_other_device_bindings(text, text, integer)')
        op.execute('ALTER TABLE device_tokens DROP COLUMN IF EXISTS browse_account_id')
        op.execute('ALTER TABLE device_tokens DROP COLUMN IF EXISTS environment')
        op.execute('ALTER TABLE device_tokens DROP COLUMN IF EXISTS expires_at')
    else:
        with op.batch_alter_table('device_tokens') as batch:
            batch.drop_index('ix_device_tokens_browse_account_id')
            batch.drop_constraint('fk_devices_browse_account', type_='foreignkey')
            batch.drop_column('browse_account_id')
            batch.drop_column('environment')
            batch.drop_column('expires_at')
