"""Persist versioned Home Facts answers, scoped grants and durable work."""
from alembic import op

revision = 'add_home_facts'
down_revision = 'add_mobile_chat_attachments'
branch_labels = None
depends_on = None

TABLES = ('home_facts_responses', 'home_facts_revisions', 'home_facts_grants', 'home_facts_artifacts', 'home_facts_events', 'home_facts_mutations', 'home_facts_jobs')

def upgrade():
    op.execute("""CREATE TABLE IF NOT EXISTS home_facts_responses (
	id VARCHAR(36) NOT NULL,
	organization_id INTEGER NOT NULL,
	transaction_id INTEGER NOT NULL,
	schema_version INTEGER NOT NULL,
	revision INTEGER NOT NULL,
	state VARCHAR(24) NOT NULL,
	answers JSON NOT NULL,
	provenance JSON NOT NULL,
	submitted_revision INTEGER,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE RESTRICT,
	UNIQUE (transaction_id),
	FOREIGN KEY(transaction_id) REFERENCES transactions (id) ON DELETE CASCADE
)""")
    op.execute('CREATE INDEX IF NOT EXISTS ix_home_facts_responses_organization_id ON home_facts_responses (organization_id)')
    op.execute("""CREATE TABLE IF NOT EXISTS home_facts_revisions (
	id VARCHAR(36) NOT NULL,
	organization_id INTEGER NOT NULL,
	response_id VARCHAR(36) NOT NULL,
	revision INTEGER NOT NULL,
	answers JSON NOT NULL,
	provenance JSON NOT NULL,
	attachments JSON NOT NULL,
	participant_id INTEGER,
	acknowledgment TEXT NOT NULL,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	agent_reviewed_by INTEGER,
	agent_reviewed_at TIMESTAMP WITHOUT TIME ZONE,
	seller_reviewed_at TIMESTAMP WITHOUT TIME ZONE,
	seller_reviewed_by INTEGER,
	seller_review_method VARCHAR(24),
	seller_review_note TEXT,
	reviewed_artifact_hash VARCHAR(64),
	PRIMARY KEY (id),
	CONSTRAINT uq_home_facts_revision UNIQUE (response_id, revision),
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE RESTRICT,
	FOREIGN KEY(response_id) REFERENCES home_facts_responses (id) ON DELETE CASCADE,
	FOREIGN KEY(participant_id) REFERENCES transaction_participants (id) ON DELETE SET NULL,
	FOREIGN KEY(agent_reviewed_by) REFERENCES "user" (id) ON DELETE SET NULL
)""")
    op.execute('CREATE INDEX IF NOT EXISTS ix_home_facts_revisions_organization_id ON home_facts_revisions (organization_id)')
    op.execute('CREATE INDEX IF NOT EXISTS ix_home_facts_revisions_response_id ON home_facts_revisions (response_id)')
    op.execute("""CREATE TABLE IF NOT EXISTS home_facts_grants (
	id VARCHAR(36) NOT NULL,
	organization_id INTEGER NOT NULL,
	response_id VARCHAR(36) NOT NULL,
	participant_id INTEGER,
	audience VARCHAR(16) NOT NULL,
	email VARCHAR(254) NOT NULL,
	token_hash VARCHAR(64) NOT NULL,
	token_used_at TIMESTAMP WITHOUT TIME ZONE,
	session_version INTEGER NOT NULL,
	expires_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	revoked_at TIMESTAMP WITHOUT TIME ZONE,
	artifacts JSON NOT NULL,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE RESTRICT,
	FOREIGN KEY(response_id) REFERENCES home_facts_responses (id) ON DELETE CASCADE,
	FOREIGN KEY(participant_id) REFERENCES transaction_participants (id) ON DELETE CASCADE
)""")
    op.execute('CREATE INDEX IF NOT EXISTS ix_home_facts_grants_organization_id ON home_facts_grants (organization_id)')
    op.execute('CREATE INDEX IF NOT EXISTS ix_home_facts_grants_response_id ON home_facts_grants (response_id)')
    op.execute("""CREATE TABLE IF NOT EXISTS home_facts_artifacts (
	id VARCHAR(36) NOT NULL,
	organization_id INTEGER NOT NULL,
	response_id VARCHAR(36) NOT NULL,
	revision INTEGER NOT NULL,
	kind VARCHAR(24) NOT NULL,
	generation_key VARCHAR(100) NOT NULL,
	filename VARCHAR(180) NOT NULL,
	storage_path VARCHAR(500) NOT NULL,
	content_type VARCHAR(100) NOT NULL,
	digest VARCHAR(64) NOT NULL,
	size INTEGER NOT NULL,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_home_facts_artifact UNIQUE (response_id, generation_key),
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE RESTRICT,
	FOREIGN KEY(response_id) REFERENCES home_facts_responses (id) ON DELETE CASCADE
)""")
    op.execute('CREATE INDEX IF NOT EXISTS ix_home_facts_artifacts_organization_id ON home_facts_artifacts (organization_id)')
    op.execute('CREATE INDEX IF NOT EXISTS ix_home_facts_artifacts_response_id ON home_facts_artifacts (response_id)')
    op.execute("""CREATE TABLE IF NOT EXISTS home_facts_events (
	id VARCHAR(36) NOT NULL,
	organization_id INTEGER NOT NULL,
	response_id VARCHAR(36) NOT NULL,
	kind VARCHAR(40) NOT NULL,
	actor_kind VARCHAR(16) NOT NULL,
	actor_id VARCHAR(36),
	details JSON NOT NULL,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE RESTRICT,
	FOREIGN KEY(response_id) REFERENCES home_facts_responses (id) ON DELETE CASCADE
)""")
    op.execute('CREATE INDEX IF NOT EXISTS ix_home_facts_events_organization_id ON home_facts_events (organization_id)')
    op.execute('CREATE INDEX IF NOT EXISTS ix_home_facts_events_response_id ON home_facts_events (response_id)')
    op.execute("""CREATE TABLE IF NOT EXISTS home_facts_mutations (
	id VARCHAR(36) NOT NULL,
	organization_id INTEGER NOT NULL,
	response_id VARCHAR(36) NOT NULL,
	actor VARCHAR(80) NOT NULL,
	mutation_id VARCHAR(36) NOT NULL,
	payload_hash VARCHAR(64) NOT NULL,
	resulting_revision INTEGER NOT NULL,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_home_facts_mutation UNIQUE (response_id, actor, mutation_id),
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE RESTRICT,
	FOREIGN KEY(response_id) REFERENCES home_facts_responses (id) ON DELETE CASCADE
)""")
    op.execute('CREATE INDEX IF NOT EXISTS ix_home_facts_mutations_organization_id ON home_facts_mutations (organization_id)')
    op.execute('CREATE INDEX IF NOT EXISTS ix_home_facts_mutations_response_id ON home_facts_mutations (response_id)')
    op.execute("""CREATE TABLE IF NOT EXISTS home_facts_jobs (
	id VARCHAR(36) NOT NULL,
	organization_id INTEGER NOT NULL,
	response_id VARCHAR(36) NOT NULL,
	kind VARCHAR(24) NOT NULL,
	dedupe_key VARCHAR(150) NOT NULL,
	payload JSON NOT NULL,
	state VARCHAR(24) NOT NULL,
	attempts INTEGER NOT NULL,
	available_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	claimed_at TIMESTAMP WITHOUT TIME ZONE,
	completed_at TIMESTAMP WITHOUT TIME ZONE,
	last_error VARCHAR(300),
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_home_facts_job UNIQUE (organization_id, dedupe_key),
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE RESTRICT,
	FOREIGN KEY(response_id) REFERENCES home_facts_responses (id) ON DELETE CASCADE
)""")
    op.execute('CREATE INDEX IF NOT EXISTS ix_home_facts_jobs_organization_id ON home_facts_jobs (organization_id)')
    op.execute('CREATE INDEX IF NOT EXISTS ix_home_facts_jobs_response_id ON home_facts_jobs (response_id)')
    op.execute('CREATE INDEX IF NOT EXISTS ix_home_facts_jobs_state ON home_facts_jobs (state)')
    if op.get_bind().dialect.name == "postgresql":
        for name in TABLES:
            op.execute(f"ALTER TABLE {name} ENABLE ROW LEVEL SECURITY")
            op.execute(f"DROP POLICY IF EXISTS {name}_org ON {name}")
            op.execute(f"""CREATE POLICY {name}_org ON {name}
                USING (organization_id = NULLIF(current_setting('app.current_org_id', true), '')::integer)
                WITH CHECK (organization_id = NULLIF(current_setting('app.current_org_id', true), '')::integer)""")
            op.execute(f"""DO $$ DECLARE api_role text; BEGIN
                FOR api_role IN SELECT rolname FROM pg_roles WHERE rolname IN ('anon', 'authenticated') LOOP
                    EXECUTE format('REVOKE ALL ON TABLE {name} FROM %I', api_role);
                END LOOP;
            END $$""")

def downgrade():
    for name in reversed(TABLES):
        op.execute(f"DROP TABLE IF EXISTS {name}")
