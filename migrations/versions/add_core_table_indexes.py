"""Add composite indexes for high-traffic multi-column query patterns.

Supabase auto-created single-column indexes on most FK columns, but the
app's hot query paths filter/sort on multiple columns at once — e.g.
(org + assignee + status + due_date) for the task list/dashboard.  A
composite index lets Postgres satisfy these in one index scan instead of
bitmap-ANDing several single-column indexes.

The single-column FK indexes (task.contact_id, etc.) already exist on
Postgres and are kept here for SQLite local dev only.

Safe to run repeatedly: uses _has_index / _has_index_on_columns guards.
Safe to revert: downgrade drops every index with IF EXISTS.

Revision ID: add_core_table_indexes
Revises: add_partner_directory
Create Date: 2026-05-11
"""

from alembic import op
import sqlalchemy as sa

revision = "add_core_table_indexes"
down_revision = "add_partner_directory"
branch_labels = None
depends_on = None


# ── helpers ──────────────────────────────────────────────────────────────

def _has_index(inspector, table_name, index_name):
    try:
        for idx in inspector.get_indexes(table_name):
            if idx["name"] == index_name:
                return True
    except Exception:
        pass
    return False


def _has_index_on_columns(inspector, table_name, columns):
    """Check if any existing index already covers exactly these columns."""
    try:
        for idx in inspector.get_indexes(table_name):
            if idx["column_names"] == list(columns):
                return True
    except Exception:
        pass
    return False


# Composite indexes shaped to the app's most common query patterns.
COMPOSITE_INDEXES = [
    # Dashboard upcoming/overdue, /tasks list, reminder jobs
    (
        "ix_task_assignee_status_due",
        "task",
        ["organization_id", "assigned_to_id", "status", "due_date"],
    ),
    # "My contacts" on every contacts list / dashboard load
    ("ix_contact_org_user", "contact", ["organization_id", "user_id"]),
    # Contact timeline: filter by contact, order by date
    ("ix_interaction_contact_date", "interaction", ["contact_id", "date"]),
    # "My transactions" pipeline + list page
    ("ix_txn_org_created_by", "transactions", ["organization_id", "created_by_id"]),
    # Pipeline / report filtering by status
    ("ix_txn_org_status", "transactions", ["organization_id", "status"]),
]

# Single-column FK indexes — Supabase auto-creates these on Postgres,
# but SQLite local dev needs them.  Skipped if any index already covers
# the column to avoid duplicates.
SINGLE_COLUMN_INDEXES = [
    ("ix_task_contact_id", "task", ["contact_id"]),
    ("ix_txn_participants_txn_id", "transaction_participants", ["transaction_id"]),
    ("ix_txn_documents_txn_id", "transaction_documents", ["transaction_id"]),
    ("ix_doc_signatures_doc_id", "document_signatures", ["document_id"]),
]

ALL_INDEXES = COMPOSITE_INDEXES + SINGLE_COLUMN_INDEXES


# ── upgrade / downgrade ─────────────────────────────────────────────────

def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    for name, table, columns in ALL_INDEXES:
        if _has_index(inspector, table, name):
            continue
        if len(columns) == 1 and _has_index_on_columns(inspector, table, columns):
            continue
        op.create_index(name, table, columns)


def downgrade():
    for name, table, _columns in reversed(ALL_INDEXES):
        op.drop_index(name, table_name=table, if_exists=True)
