"""
Fix script to migrate remaining tables that failed in the initial migration.
"""

import os
import sqlite3
from datetime import datetime
from dotenv import load_dotenv
from sqlalchemy import create_engine, text, MetaData

# Load environment variables
load_dotenv()

# Source SQLite database path
SQLITE_DB_PATH = os.path.join(os.path.dirname(__file__), 'instance', 'crm (1).db')
POSTGRES_URL = os.getenv('DATABASE_URL')

def migrate_remaining():
    """Migrate the tables that failed."""
    
    # Connect to SQLite
    sqlite_conn = sqlite3.connect(SQLITE_DB_PATH)
    sqlite_conn.row_factory = sqlite3.Row
    sqlite_cursor = sqlite_conn.cursor()
    
    # Connect to PostgreSQL
    pg_engine = create_engine(POSTGRES_URL)
    
    with pg_engine.connect() as pg_conn:
        # First, let's create any missing tables via SQLAlchemy models
        print("Creating missing tables if needed...")
        
        # Create daily_todo_list table
        pg_conn.execute(text("""
            CREATE TABLE IF NOT EXISTS daily_todo_list (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES "user"(id),
                date DATE NOT NULL,
                items JSONB,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, date)
            )
        """))
        pg_conn.commit()
        print("Created daily_todo_list table")
        
        # Create user_todos table
        pg_conn.execute(text("""
            CREATE TABLE IF NOT EXISTS user_todos (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES "user"(id),
                title VARCHAR(200) NOT NULL,
                description TEXT,
                is_completed BOOLEAN DEFAULT FALSE,
                due_date DATE,
                priority INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))
        pg_conn.commit()
        print("Created user_todos table")
        
        # Create sendgrid_template table (simplified, without the huge preview_url)
        pg_conn.execute(text("""
            CREATE TABLE IF NOT EXISTS sendgrid_template (
                id SERIAL PRIMARY KEY,
                sendgrid_id VARCHAR(100) NOT NULL,
                name VARCHAR(200) NOT NULL,
                subject VARCHAR(500),
                version VARCHAR(100),
                active_version_id VARCHAR(100),
                preview_url TEXT,
                is_active BOOLEAN DEFAULT TRUE,
                last_modified TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))
        pg_conn.commit()
        print("Created sendgrid_template table")
        
        # Create action_plan table if not exists
        pg_conn.execute(text("""
            CREATE TABLE IF NOT EXISTS action_plan (
                id SERIAL PRIMARY KEY,
                contact_id INTEGER REFERENCES contact(id),
                user_id INTEGER REFERENCES "user"(id),
                title VARCHAR(200),
                description TEXT,
                status VARCHAR(50) DEFAULT 'active',
                due_date DATE,
                steps JSONB,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))
        pg_conn.commit()
        print("Created action_plan table")
        
        # Now migrate data
        # 1. Migrate daily_todo_list
        print("\nMigrating daily_todo_list...")
        sqlite_cursor.execute("SELECT * FROM daily_todo_list")
        rows = sqlite_cursor.fetchall()
        migrated = 0
        for row in rows:
            try:
                pg_conn.execute(text("""
                    INSERT INTO daily_todo_list (id, user_id, date, items, created_at, updated_at)
                    VALUES (:id, :user_id, :date, :items, :created_at, :updated_at)
                    ON CONFLICT (id) DO NOTHING
                """), {
                    'id': row['id'],
                    'user_id': row['user_id'],
                    'date': row['date'],
                    'items': row['items'],
                    'created_at': row['created_at'],
                    'updated_at': row['updated_at']
                })
                migrated += 1
            except Exception as e:
                print(f"  Error: {e}")
        pg_conn.commit()
        print(f"  Migrated {migrated} daily_todo_list records")
        
        # 2. Migrate user_todos
        print("\nMigrating user_todos...")
        sqlite_cursor.execute("SELECT * FROM user_todos")
        rows = sqlite_cursor.fetchall()
        migrated = 0
        for row in rows:
            try:
                pg_conn.execute(text("""
                    INSERT INTO user_todos (id, user_id, title, description, is_completed, due_date, priority, created_at, updated_at)
                    VALUES (:id, :user_id, :title, :description, :is_completed, :due_date, :priority, :created_at, :updated_at)
                    ON CONFLICT (id) DO NOTHING
                """), {
                    'id': row['id'],
                    'user_id': row['user_id'],
                    'title': row['title'],
                    'description': row.get('description', ''),
                    'is_completed': bool(row['is_completed']),
                    'due_date': row.get('due_date'),
                    'priority': row.get('priority', 0),
                    'created_at': row['created_at'],
                    'updated_at': row['updated_at']
                })
                migrated += 1
            except Exception as e:
                print(f"  Error: {e}")
        pg_conn.commit()
        print(f"  Migrated {migrated} user_todos records")
        
        # 3. Migrate sendgrid_template (without preview_url to avoid huge data)
        print("\nMigrating sendgrid_template (truncating preview_url)...")
        sqlite_cursor.execute("SELECT id, sendgrid_id, name, subject, version, active_version_id, is_active, last_modified, created_at, updated_at FROM sendgrid_template")
        rows = sqlite_cursor.fetchall()
        migrated = 0
        for row in rows:
            try:
                pg_conn.execute(text("""
                    INSERT INTO sendgrid_template (id, sendgrid_id, name, subject, version, active_version_id, preview_url, is_active, last_modified, created_at, updated_at)
                    VALUES (:id, :sendgrid_id, :name, :subject, :version, :active_version_id, NULL, :is_active, :last_modified, :created_at, :updated_at)
                    ON CONFLICT (id) DO NOTHING
                """), {
                    'id': row['id'],
                    'sendgrid_id': row['sendgrid_id'],
                    'name': row['name'],
                    'subject': row['subject'],
                    'version': row['version'],
                    'active_version_id': row['active_version_id'],
                    'is_active': bool(row['is_active']),
                    'last_modified': row['last_modified'],
                    'created_at': row['created_at'],
                    'updated_at': row['updated_at']
                })
                migrated += 1
            except Exception as e:
                print(f"  Error: {e}")
        pg_conn.commit()
        print(f"  Migrated {migrated} sendgrid_template records")
        
        # 4. Migrate task table
        print("\nMigrating task...")
        sqlite_cursor.execute("SELECT * FROM task")
        rows = sqlite_cursor.fetchall()
        migrated = 0
        for row in rows:
            try:
                # Get column names dynamically
                cols = [description[0] for description in sqlite_cursor.description]
                row_dict = dict(zip(cols, row))
                
                pg_conn.execute(text("""
                    INSERT INTO task (id, user_id, contact_id, type_id, subtype_id, title, description, status, due_date, completed_at, created_at, updated_at)
                    VALUES (:id, :user_id, :contact_id, :type_id, :subtype_id, :title, :description, :status, :due_date, :completed_at, :created_at, :updated_at)
                    ON CONFLICT (id) DO NOTHING
                """), {
                    'id': row_dict.get('id'),
                    'user_id': row_dict.get('user_id'),
                    'contact_id': row_dict.get('contact_id'),
                    'type_id': row_dict.get('type_id'),
                    'subtype_id': row_dict.get('subtype_id'),
                    'title': row_dict.get('title'),
                    'description': row_dict.get('description'),
                    'status': row_dict.get('status'),
                    'due_date': row_dict.get('due_date'),
                    'completed_at': row_dict.get('completed_at'),
                    'created_at': row_dict.get('created_at'),
                    'updated_at': row_dict.get('updated_at')
                })
                migrated += 1
            except Exception as e:
                print(f"  Error: {e}")
        pg_conn.commit()
        print(f"  Migrated {migrated} task records")
        
        # Reset sequences for all tables
        print("\nResetting sequences...")
        tables = ['daily_todo_list', 'user_todos', 'sendgrid_template', 'task']
        for table in tables:
            try:
                result = pg_conn.execute(text(f"SELECT MAX(id) FROM {table}"))
                max_id = result.scalar()
                if max_id:
                    pg_conn.execute(text(f"SELECT setval('{table}_id_seq', {max_id})"))
                    print(f"  Reset {table}_id_seq to {max_id}")
            except Exception as e:
                print(f"  Error resetting {table} sequence: {e}")
        pg_conn.commit()
        
    sqlite_conn.close()
    print("\n✅ Migration fix complete!")

if __name__ == '__main__':
    migrate_remaining()

