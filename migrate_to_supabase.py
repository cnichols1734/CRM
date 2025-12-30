"""
Migration script to transfer data from SQLite to Supabase PostgreSQL.
Reads from: instance/crm (1).db (production SQLite database)
Writes to: Supabase PostgreSQL (via DATABASE_URL in .env)
"""

import os
import sqlite3
from datetime import datetime
from dotenv import load_dotenv
from sqlalchemy import create_engine, text, inspect
from sqlalchemy.orm import sessionmaker

# Load environment variables
load_dotenv()

# Source SQLite database path
SQLITE_DB_PATH = os.path.join(os.path.dirname(__file__), 'instance', 'crm (1).db')

# Target PostgreSQL (Supabase) from environment
POSTGRES_URL = os.getenv('DATABASE_URL')

if not POSTGRES_URL:
    print("ERROR: DATABASE_URL not set in .env file")
    print("Please set DATABASE_URL to your Supabase connection string")
    exit(1)

if not os.path.exists(SQLITE_DB_PATH):
    print(f"ERROR: SQLite database not found at: {SQLITE_DB_PATH}")
    exit(1)

print(f"Source SQLite: {SQLITE_DB_PATH}")
print(f"Target PostgreSQL: {POSTGRES_URL[:50]}...")
print()

# Tables to migrate in order (respecting foreign key dependencies)
TABLES_IN_ORDER = [
    'user',
    'contact_group',
    'contact',
    'contact_groups',  # association table
    'interaction',
    'task_type',
    'task_subtype',
    'task',
    'daily_todo_list',
    'user_todos',
    'sendgrid_template',
    'action_plan',
    'alembic_version',
]


def get_sqlite_connection():
    """Get a connection to the SQLite database."""
    return sqlite3.connect(SQLITE_DB_PATH)


def get_postgres_engine():
    """Get SQLAlchemy engine for PostgreSQL."""
    return create_engine(POSTGRES_URL)


def get_sqlite_tables(conn):
    """Get list of tables in SQLite database."""
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';")
    return [row[0] for row in cursor.fetchall()]


def get_table_columns(conn, table_name):
    """Get column names for a table."""
    cursor = conn.cursor()
    cursor.execute(f"PRAGMA table_info('{table_name}')")
    return [row[1] for row in cursor.fetchall()]


def get_table_data(conn, table_name):
    """Get all rows from a table."""
    cursor = conn.cursor()
    cursor.execute(f"SELECT * FROM '{table_name}'")
    return cursor.fetchall()


def create_postgres_tables(engine):
    """Create tables in PostgreSQL using SQLAlchemy models."""
    print("Creating tables in PostgreSQL...")
    
    # Import models and create tables
    from models import db, User, ContactGroup, Contact, Interaction, TaskType, TaskSubtype
    from models import Task, DailyTodoList, UserTodo, SendGridTemplate, ActionPlan
    
    # Create a Flask app context to use db.create_all()
    from flask import Flask
    app = Flask(__name__)
    app.config['SQLALCHEMY_DATABASE_URI'] = POSTGRES_URL
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    
    db.init_app(app)
    
    with app.app_context():
        db.create_all()
        print("Tables created successfully!")
    
    return app


def escape_value(value):
    """Escape a value for SQL insertion."""
    if value is None:
        return 'NULL'
    elif isinstance(value, bool):
        return 'TRUE' if value else 'FALSE'
    elif isinstance(value, (int, float)):
        return str(value)
    elif isinstance(value, str):
        # Escape single quotes
        escaped = value.replace("'", "''")
        return f"'{escaped}'"
    elif isinstance(value, bytes):
        # Handle binary data
        return f"'{value.decode('utf-8', errors='replace')}'"
    else:
        escaped = str(value).replace("'", "''")
        return f"'{escaped}'"


def migrate_table(sqlite_conn, pg_engine, table_name):
    """Migrate a single table from SQLite to PostgreSQL."""
    print(f"\nMigrating table: {table_name}")
    
    # Get columns and data from SQLite
    columns = get_table_columns(sqlite_conn, table_name)
    rows = get_table_data(sqlite_conn, table_name)
    
    if not rows:
        print(f"  No data in {table_name}, skipping...")
        return 0
    
    print(f"  Found {len(rows)} rows to migrate")
    
    # Build insert statement
    column_names = ', '.join(f'"{col}"' for col in columns)
    
    with pg_engine.connect() as conn:
        # For tables with auto-increment IDs, we need to handle sequence reset
        has_id_column = 'id' in columns
        
        inserted = 0
        for row in rows:
            values = ', '.join(escape_value(val) for val in row)
            
            try:
                # Use INSERT with explicit column names
                insert_sql = f'INSERT INTO "{table_name}" ({column_names}) VALUES ({values})'
                conn.execute(text(insert_sql))
                inserted += 1
            except Exception as e:
                print(f"  Error inserting row: {e}")
                print(f"  SQL: {insert_sql[:200]}...")
                # Continue with other rows
                continue
        
        conn.commit()
        
        # Reset sequence for tables with id column
        if has_id_column and inserted > 0:
            try:
                # Get max id
                result = conn.execute(text(f'SELECT MAX(id) FROM "{table_name}"'))
                max_id = result.scalar() or 0
                
                # Reset sequence (PostgreSQL specific)
                seq_name = f"{table_name}_id_seq"
                conn.execute(text(f"SELECT setval('{seq_name}', {max_id + 1}, false)"))
                conn.commit()
                print(f"  Reset sequence {seq_name} to {max_id + 1}")
            except Exception as e:
                # Sequence might not exist for some tables
                pass
    
    print(f"  Migrated {inserted} rows")
    return inserted


def verify_migration(sqlite_conn, pg_engine):
    """Verify that data was migrated correctly."""
    print("\n" + "="*50)
    print("VERIFICATION")
    print("="*50)
    
    sqlite_tables = get_sqlite_tables(sqlite_conn)
    
    with pg_engine.connect() as conn:
        for table in sqlite_tables:
            # Count in SQLite
            sqlite_cursor = sqlite_conn.cursor()
            sqlite_cursor.execute(f"SELECT COUNT(*) FROM '{table}'")
            sqlite_count = sqlite_cursor.fetchone()[0]
            
            # Count in PostgreSQL
            try:
                result = conn.execute(text(f'SELECT COUNT(*) FROM "{table}"'))
                pg_count = result.scalar()
            except:
                pg_count = "ERROR"
            
            status = "✓" if sqlite_count == pg_count else "✗"
            print(f"  {status} {table}: SQLite={sqlite_count}, PostgreSQL={pg_count}")


def main():
    print("="*50)
    print("SQLite to Supabase Migration")
    print("="*50)
    
    # Connect to databases
    sqlite_conn = get_sqlite_connection()
    pg_engine = get_postgres_engine()
    
    # Get available tables from SQLite
    available_tables = get_sqlite_tables(sqlite_conn)
    print(f"\nFound {len(available_tables)} tables in SQLite:")
    for t in available_tables:
        print(f"  - {t}")
    
    # Create tables in PostgreSQL
    app = create_postgres_tables(pg_engine)
    
    # Determine migration order
    tables_to_migrate = []
    for table in TABLES_IN_ORDER:
        if table in available_tables:
            tables_to_migrate.append(table)
    
    # Add any tables not in our predefined order
    for table in available_tables:
        if table not in tables_to_migrate:
            tables_to_migrate.append(table)
    
    print(f"\nMigration order: {tables_to_migrate}")
    
    # Confirm before proceeding
    response = input("\nProceed with migration? (yes/no): ")
    if response.lower() != 'yes':
        print("Migration cancelled.")
        return
    
    # Migrate each table
    print("\n" + "="*50)
    print("MIGRATING DATA")
    print("="*50)
    
    total_rows = 0
    for table in tables_to_migrate:
        try:
            rows = migrate_table(sqlite_conn, pg_engine, table)
            total_rows += rows
        except Exception as e:
            print(f"  ERROR migrating {table}: {e}")
    
    print(f"\n\nTotal rows migrated: {total_rows}")
    
    # Verify migration
    verify_migration(sqlite_conn, pg_engine)
    
    sqlite_conn.close()
    
    print("\n" + "="*50)
    print("Migration complete!")
    print("="*50)
    print("\nNext steps:")
    print("1. Run the Flask app locally to test: python app.py")
    print("2. Verify all features work correctly")
    print("3. Once verified, set up PythonAnywhere to use GitHub")


if __name__ == '__main__':
    main()

