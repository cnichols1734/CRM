import os
import sqlite3
import argparse


def get_instance_db_path() -> str:
    project_root = os.path.dirname(os.path.abspath(__file__))
    db_path = os.path.join(project_root, 'instance', 'crm.db')
    return db_path


def run_migration(db_path: str | None = None):
    db_path = db_path or get_instance_db_path()
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"Database not found at {db_path}")

    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()

        # Fetch current columns on user table (SQLite)
        cur.execute("PRAGMA table_info('user')")
        rows = cur.fetchall()
        existing_columns = {row[1] for row in rows}  # row[1] is column name

        statements = []
        if 'phone' not in existing_columns:
            statements.append("ALTER TABLE user ADD COLUMN phone TEXT")
        if 'license_number' not in existing_columns:
            statements.append("ALTER TABLE user ADD COLUMN license_number TEXT")
        if 'licensed_supervisor' not in existing_columns:
            statements.append("ALTER TABLE user ADD COLUMN licensed_supervisor TEXT")

        if not statements:
            print('No changes needed. All columns already exist.')
            return

        for stmt in statements:
            cur.execute(stmt)

        conn.commit()
        print('Migration completed successfully. Added columns:', ', '.join([s.split()[-1] for s in statements]))
        print(f"Database path: {db_path}")
    finally:
        conn.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Add user fields to SQLite DB (idempotent).')
    parser.add_argument('--db', dest='db_path', help='Path to SQLite DB file (e.g., /Users/you/Desktop/CRM_Prod_9.6.db)')
    args = parser.parse_args()

    run_migration(args.db_path)


