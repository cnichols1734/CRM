"""Add campaign engagement history without changing existing send rows."""
from alembic import op
import sqlalchemy as sa

revision = 'add_marketing_tracking'
down_revision = 'add_offer_non_realty_items'
branch_labels = None
depends_on = None

TABLES = ('marketing_tracking', 'marketing_tracking_links', 'marketing_tracking_events')


def statements(dialect):
    identity = 'SERIAL PRIMARY KEY' if dialect == 'postgresql' else 'INTEGER PRIMARY KEY'
    yield f'''CREATE TABLE IF NOT EXISTS marketing_tracking (
        id {identity},
        organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
        send_id INTEGER NOT NULL UNIQUE REFERENCES marketing_sends(id) ON DELETE CASCADE,
        token VARCHAR(100) NOT NULL UNIQUE,
        subject VARCHAR(300) NOT NULL,
        html_body TEXT NOT NULL,
        text_body TEXT NOT NULL,
        created_at TIMESTAMP NOT NULL
    )'''
    yield f'''CREATE TABLE IF NOT EXISTS marketing_tracking_links (
        id {identity},
        organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
        tracking_id INTEGER NOT NULL REFERENCES marketing_tracking(id) ON DELETE CASCADE,
        token VARCHAR(100) NOT NULL UNIQUE,
        destination TEXT NOT NULL,
        label VARCHAR(300) NOT NULL
    )'''
    yield f'''CREATE TABLE IF NOT EXISTS marketing_tracking_events (
        id {identity},
        organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
        tracking_id INTEGER NOT NULL REFERENCES marketing_tracking(id) ON DELETE CASCADE,
        link_id INTEGER REFERENCES marketing_tracking_links(id) ON DELETE CASCADE,
        kind VARCHAR(10) NOT NULL,
        classification VARCHAR(20) NOT NULL,
        occurred_at TIMESTAMP NOT NULL,
        dedupe_key VARCHAR(64) NOT NULL UNIQUE,
        CONSTRAINT ck_marketing_event_kind CHECK (kind IN ('open', 'click')),
        CONSTRAINT ck_marketing_event_class CHECK (classification IN ('observed', 'automated', 'sender'))
    )'''
    for table in TABLES:
        yield f'CREATE INDEX IF NOT EXISTS ix_{table}_organization_id ON {table} (organization_id)'
    yield 'CREATE INDEX IF NOT EXISTS ix_marketing_tracking_links_tracking_id ON marketing_tracking_links (tracking_id)'
    yield 'CREATE INDEX IF NOT EXISTS ix_marketing_tracking_events_link_id ON marketing_tracking_events (link_id)'
    yield 'CREATE INDEX IF NOT EXISTS ix_marketing_events_tracking_time ON marketing_tracking_events (tracking_id, occurred_at, id)'
    if dialect == 'postgresql':
        for table in TABLES:
            yield f'ALTER TABLE {table} ENABLE ROW LEVEL SECURITY'
            yield f'ALTER TABLE {table} FORCE ROW LEVEL SECURITY'
            yield f'REVOKE ALL ON TABLE {table} FROM anon, authenticated'
            yield f'REVOKE ALL ON SEQUENCE {table}_id_seq FROM anon, authenticated'
            # A rerun keeps the existing policy and never opens a policy-free window.
            yield f'''DO $$ BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname='public'
                    AND tablename='{table}' AND policyname='tenant_isolation_{table}') THEN
                    CREATE POLICY tenant_isolation_{table} ON {table}
                    USING (organization_id = NULLIF(current_setting('app.current_org_id', true), '')::integer)
                    WITH CHECK (organization_id = NULLIF(current_setting('app.current_org_id', true), '')::integer);
                END IF;
            END $$'''


def upgrade():
    conn = op.get_bind()
    if conn.dialect.name == 'postgresql':
        op.execute("SET LOCAL lock_timeout = '5s'")
        op.execute("SET LOCAL statement_timeout = '30s'")
    for statement in statements(conn.dialect.name):
        op.execute(sa.text(statement))


def downgrade():
    for table in reversed(TABLES):
        op.execute(sa.text(f'DROP TABLE IF EXISTS {table}'))
