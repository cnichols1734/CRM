"""Convert contact groups from org-shared to per-user catalogs.

Revision ID: add_per_user_contact_groups
Revises: add_client_portal_tables
Create Date: 2026-07-21

Clones each legacy org-scoped ContactGroup to every user in that org, remaps
contact_groups junction rows to the contact owner's clone (by name), then
enforces user_id NOT NULL and a new unique constraint.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect, text


revision = 'add_per_user_contact_groups'
down_revision = 'add_client_portal_tables'
branch_labels = None
depends_on = None


def _column_names(inspector, table):
    if table not in inspector.get_table_names():
        return set()
    return {col['name'] for col in inspector.get_columns(table)}


def _index_names(inspector, table):
    if table not in inspector.get_table_names():
        return set()
    return {idx['name'] for idx in inspector.get_indexes(table)}


def _constraint_names(inspector, table):
    if table not in inspector.get_table_names():
        return set()
    names = set()
    for uc in inspector.get_unique_constraints(table):
        if uc.get('name'):
            names.add(uc['name'])
    return names


def upgrade():
    conn = op.get_bind()
    inspector = inspect(conn)
    dialect = conn.dialect.name
    columns = _column_names(inspector, 'contact_group')

    # --- Audit: fail fast on data that would make cloning ambiguous ---
    orphan_contacts = conn.execute(text("""
        SELECT c.id
        FROM contact c
        LEFT JOIN "user" u ON u.id = c.user_id
        WHERE u.id IS NULL
           OR (c.organization_id IS NOT NULL
               AND u.organization_id IS NOT NULL
               AND c.organization_id != u.organization_id)
        LIMIT 5
    """)).fetchall()
    if orphan_contacts:
        raise Exception(
            'Cannot migrate contact groups: contacts with invalid owners: '
            f'{[row[0] for row in orphan_contacts]}'
        )

    dup_names = conn.execute(text("""
        SELECT organization_id, name, COUNT(*) AS cnt
        FROM contact_group
        WHERE organization_id IS NOT NULL
        GROUP BY organization_id, name
        HAVING COUNT(*) > 1
        LIMIT 5
    """)).fetchall()
    if dup_names:
        raise Exception(
            'Cannot migrate contact groups: duplicate names within org: '
            f'{[(row[0], row[1], row[2]) for row in dup_names]}'
        )

    # --- Add nullable columns ---
    if 'user_id' not in columns:
        op.add_column(
            'contact_group',
            sa.Column('user_id', sa.Integer(), nullable=True),
        )
    if 'is_active' not in columns:
        op.add_column(
            'contact_group',
            sa.Column('is_active', sa.Boolean(), nullable=False,
                      server_default=sa.true()),
        )

    # Drop the old org-level unique constraint BEFORE cloning. Clones reuse
    # the same name for every user in an org, which would violate
    # uq_contact_group_org_name.
    if dialect == 'postgresql':
        op.execute(
            'ALTER TABLE contact_group '
            'DROP CONSTRAINT IF EXISTS uq_contact_group_org_name'
        )
    else:
        # SQLite: unique may be a named constraint or a unique index
        inspector = inspect(conn)
        constraints = _constraint_names(inspector, 'contact_group')
        indexes = _index_names(inspector, 'contact_group')
        if 'uq_contact_group_org_name' in constraints:
            with op.batch_alter_table('contact_group') as batch:
                batch.drop_constraint('uq_contact_group_org_name', type_='unique')
        elif 'uq_contact_group_org_name' in indexes:
            op.drop_index('uq_contact_group_org_name', table_name='contact_group')

    # Refresh inspector after column/constraint changes
    inspector = inspect(conn)
    columns = _column_names(inspector, 'contact_group')

    # --- Clone legacy org groups to every user in the org ---
    # Legacy rows are those with user_id IS NULL (pre-migration org catalog).
    legacy_groups = conn.execute(text("""
        SELECT id, organization_id, name, category, sort_order, created_at
        FROM contact_group
        WHERE user_id IS NULL
        ORDER BY organization_id, sort_order, id
    """)).fetchall()

    # Build (org_id -> [user_ids])
    users_by_org = {}
    for row in conn.execute(text("""
        SELECT id, organization_id FROM "user"
        WHERE organization_id IS NOT NULL
        ORDER BY organization_id, id
    """)).fetchall():
        users_by_org.setdefault(row[1], []).append(row[0])

    # Map (legacy_group_id, user_id) -> new_group_id
    id_map = {}
    # Legacy group metadata for fallback clones (cross-org dirty data)
    legacy_by_id = {
        row[0]: row for row in legacy_groups
    }

    def _ensure_clone(legacy_id, target_org_id, user_id):
        """Clone a legacy group onto a user in target_org_id (idempotent)."""
        cached = id_map.get((legacy_id, user_id))
        if cached:
            return cached

        legacy = legacy_by_id.get(legacy_id)
        if legacy is None:
            # May already be a per-user row — look it up
            row = conn.execute(text("""
                SELECT id, organization_id, name, category, sort_order, created_at
                FROM contact_group WHERE id = :gid
            """), {'gid': legacy_id}).fetchone()
            if row is None:
                return None
            legacy = row

        _lid, _src_org, name, category, sort_order, created_at = legacy
        existing = conn.execute(text("""
            SELECT id FROM contact_group
            WHERE organization_id = :org_id
              AND user_id = :user_id
              AND name = :name
            LIMIT 1
        """), {
            'org_id': target_org_id,
            'user_id': user_id,
            'name': name,
        }).fetchone()
        if existing:
            id_map[(legacy_id, user_id)] = existing[0]
            return existing[0]

        params = {
            'org_id': target_org_id,
            'user_id': user_id,
            'name': name,
            'category': category,
            'sort_order': sort_order,
            'created_at': created_at,
        }
        if dialect == 'postgresql':
            new_id = conn.execute(text("""
                INSERT INTO contact_group
                    (organization_id, user_id, name, category, sort_order,
                     is_active, created_at)
                VALUES
                    (:org_id, :user_id, :name, :category, :sort_order,
                     TRUE, :created_at)
                RETURNING id
            """), params).scalar()
        else:
            result = conn.execute(text("""
                INSERT INTO contact_group
                    (organization_id, user_id, name, category, sort_order,
                     is_active, created_at)
                VALUES
                    (:org_id, :user_id, :name, :category, :sort_order,
                     1, :created_at)
            """), params)
            new_id = result.lastrowid
            if not new_id:
                new_id = conn.execute(text("""
                    SELECT id FROM contact_group
                    WHERE organization_id = :org_id
                      AND user_id = :user_id
                      AND name = :name
                    LIMIT 1
                """), params).scalar()

        id_map[(legacy_id, user_id)] = new_id
        return new_id

    for legacy in legacy_groups:
        legacy_id, org_id, name, category, sort_order, created_at = legacy
        user_ids = users_by_org.get(org_id, [])
        if not user_ids:
            # Org with groups but no users — drop later with other legacy rows
            continue
        for user_id in user_ids:
            _ensure_clone(legacy_id, org_id, user_id)

    # Also ensure users with contacts but no org groups still get clones of
    # any groups currently attached to their contacts (handles cross-org dirt).
    dirty_memberships = conn.execute(text("""
        SELECT DISTINCT c.user_id, c.organization_id, cg.group_id
        FROM contact_groups cg
        JOIN contact c ON c.id = cg.contact_id
        JOIN contact_group g ON g.id = cg.group_id
        WHERE g.user_id IS NULL
    """)).fetchall()
    for owner_user_id, contact_org_id, old_group_id in dirty_memberships:
        if contact_org_id is None or owner_user_id is None:
            continue
        _ensure_clone(old_group_id, contact_org_id, owner_user_id)

    # --- Remap junction rows to contact owner's clone ---
    junction_rows = conn.execute(text("""
        SELECT cg.contact_id, cg.group_id, c.user_id, c.organization_id
        FROM contact_groups cg
        JOIN contact c ON c.id = cg.contact_id
    """)).fetchall()

    remapped = set()
    unmapped = []
    for contact_id, old_group_id, owner_user_id, contact_org_id in junction_rows:
        new_group_id = id_map.get((old_group_id, owner_user_id))
        if new_group_id is None and contact_org_id is not None:
            new_group_id = _ensure_clone(
                old_group_id, contact_org_id, owner_user_id
            )
        if new_group_id is None:
            # Already a per-user group (re-run) or orphaned — keep if owned
            owned = conn.execute(text("""
                SELECT id FROM contact_group
                WHERE id = :gid AND user_id = :uid
            """), {'gid': old_group_id, 'uid': owner_user_id}).fetchone()
            if owned:
                remapped.add((contact_id, old_group_id))
                continue
            unmapped.append((contact_id, old_group_id, owner_user_id))
            continue
        remapped.add((contact_id, new_group_id))

    if unmapped:
        raise Exception(
            'Cannot migrate contact groups: unmapped memberships: '
            f'{unmapped[:10]}'
        )

    # Rebuild junction table cleanly
    conn.execute(text('DELETE FROM contact_groups'))
    for contact_id, group_id in remapped:
        conn.execute(text("""
            INSERT INTO contact_groups (contact_id, group_id)
            VALUES (:cid, :gid)
        """), {'cid': contact_id, 'gid': group_id})

    # --- Delete legacy org-only rows (user_id IS NULL) ---
    conn.execute(text('DELETE FROM contact_group WHERE user_id IS NULL'))

    # --- Enforce NOT NULL + FK + unique constraint swap ---
    if dialect == 'postgresql':
        op.execute('ALTER TABLE contact_group ALTER COLUMN user_id SET NOT NULL')
        op.execute("""
            DO $$ BEGIN
                ALTER TABLE contact_group
                ADD CONSTRAINT fk_contact_group_user_id
                FOREIGN KEY (user_id) REFERENCES "user"(id) ON DELETE CASCADE;
            EXCEPTION WHEN duplicate_object THEN NULL;
            END $$;
        """)
        op.execute("""
            DO $$ BEGIN
                ALTER TABLE contact_group
                ADD CONSTRAINT uq_contact_group_org_user_name
                UNIQUE (organization_id, user_id, name);
            EXCEPTION WHEN duplicate_object THEN NULL;
            END $$;
        """)
        op.execute("""
            CREATE INDEX IF NOT EXISTS ix_contact_group_org_user_sort
            ON contact_group (organization_id, user_id, sort_order)
        """)
        op.execute("""
            CREATE INDEX IF NOT EXISTS ix_contact_group_user_id
            ON contact_group (user_id)
        """)
    else:
        # SQLite: recreate constraints via batch alter where needed
        inspector = inspect(conn)
        constraints = _constraint_names(inspector, 'contact_group')
        with op.batch_alter_table('contact_group') as batch:
            batch.alter_column('user_id', existing_type=sa.Integer(),
                               nullable=False)
            if 'uq_contact_group_org_user_name' not in constraints:
                batch.create_unique_constraint(
                    'uq_contact_group_org_user_name',
                    ['organization_id', 'user_id', 'name'],
                )
            batch.create_foreign_key(
                'fk_contact_group_user_id',
                'user',
                ['user_id'],
                ['id'],
                ondelete='CASCADE',
            )

        inspector = inspect(conn)
        indexes = _index_names(inspector, 'contact_group')
        if 'ix_contact_group_org_user_sort' not in indexes:
            op.create_index(
                'ix_contact_group_org_user_sort',
                'contact_group',
                ['organization_id', 'user_id', 'sort_order'],
                unique=False,
            )
        if 'ix_contact_group_user_id' not in indexes:
            op.create_index(
                'ix_contact_group_user_id',
                'contact_group',
                ['user_id'],
                unique=False,
            )


def downgrade():
    """Downgrade is intentionally limited after destructive remapping.

    Collapses per-user clones back to a single org-scoped catalog owned by the
    lowest user_id in each org (best-effort). Memberships for other users are
    remapped by name when possible.
    """
    conn = op.get_bind()
    dialect = conn.dialect.name

    # Pick one canonical row per (org, name): lowest user_id
    keep_ids = {
        row[0]
        for row in conn.execute(text("""
            SELECT MIN(id)
            FROM contact_group
            GROUP BY organization_id, name
        """)).fetchall()
    }

    # Remap junction rows to kept ids by name+org
    rows = conn.execute(text("""
        SELECT cg.contact_id, cg.group_id, g.organization_id, g.name
        FROM contact_groups cg
        JOIN contact_group g ON g.id = cg.group_id
    """)).fetchall()

    name_to_keep = {}
    for row in conn.execute(text("""
        SELECT id, organization_id, name
        FROM contact_group
        WHERE id IN (
            SELECT MIN(id) FROM contact_group GROUP BY organization_id, name
        )
    """)).fetchall():
        name_to_keep[(row[1], row[2])] = row[0]

    conn.execute(text('DELETE FROM contact_groups'))
    seen = set()
    for contact_id, _old_gid, org_id, name in rows:
        keep_id = name_to_keep.get((org_id, name))
        if keep_id is None or (contact_id, keep_id) in seen:
            continue
        seen.add((contact_id, keep_id))
        conn.execute(text("""
            INSERT INTO contact_groups (contact_id, group_id)
            VALUES (:cid, :gid)
        """), {'cid': contact_id, 'gid': keep_id})

    # Delete non-kept clones
    if keep_ids:
        # Parameterize carefully for SQLite
        id_list = ','.join(str(i) for i in keep_ids)
        conn.execute(text(
            f'DELETE FROM contact_group WHERE id NOT IN ({id_list})'
        ))

    if dialect == 'postgresql':
        op.execute(
            'ALTER TABLE contact_group '
            'DROP CONSTRAINT IF EXISTS uq_contact_group_org_user_name'
        )
        op.execute(
            'ALTER TABLE contact_group '
            'DROP CONSTRAINT IF EXISTS fk_contact_group_user_id'
        )
        op.execute('DROP INDEX IF EXISTS ix_contact_group_org_user_sort')
        op.execute('DROP INDEX IF EXISTS ix_contact_group_user_id')
        op.execute(
            'ALTER TABLE contact_group '
            'ADD CONSTRAINT uq_contact_group_org_name '
            'UNIQUE (organization_id, name)'
        )
        op.execute('ALTER TABLE contact_group ALTER COLUMN user_id DROP NOT NULL')
        op.execute('UPDATE contact_group SET user_id = NULL')
        op.execute('ALTER TABLE contact_group DROP COLUMN IF EXISTS is_active')
        op.execute('ALTER TABLE contact_group DROP COLUMN IF EXISTS user_id')
    else:
        with op.batch_alter_table('contact_group') as batch:
            try:
                batch.drop_constraint('uq_contact_group_org_user_name',
                                      type_='unique')
            except Exception:
                pass
            try:
                batch.drop_constraint('fk_contact_group_user_id', type_='foreignkey')
            except Exception:
                pass
            batch.create_unique_constraint(
                'uq_contact_group_org_name',
                ['organization_id', 'name'],
            )
            batch.drop_column('is_active')
            batch.drop_column('user_id')
