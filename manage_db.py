#!/usr/bin/env python3
"""
Database Management Script
Helps manage database operations across different environments.
"""

from dotenv import load_dotenv
load_dotenv()  # Load .env file before any other imports

import os
import sys
from flask import Flask
from flask_migrate import Migrate, init, migrate, upgrade, current, history
from config import Config
from models import db

def create_app(config_class=Config):
    """Create Flask application with specified config."""
    app = Flask(__name__)
    app.config.from_object(config_class)

    # Initialize database
    db.init_app(app)

    # Initialize Flask-Migrate
    migrate_obj = Migrate(app, db)

    return app, migrate_obj

def init_database():
    """Initialize a new database with migrations."""
    app, migrate_obj = create_app()

    with app.app_context():
        print(f"Initializing database: {app.config['SQLALCHEMY_DATABASE_URI']}")

        # Create database directory if it doesn't exist
        db_path = app.config['SQLALCHEMY_DATABASE_URI']
        if db_path.startswith('sqlite:///'):
            db_file = db_path.replace('sqlite:///', '')
            os.makedirs(os.path.dirname(db_file), exist_ok=True)

        # Create all tables
        db.create_all()
        print("Database tables created successfully!")

def setup_migrations():
    """Set up Flask-Migrate for the current database."""
    app, migrate_obj = create_app()

    with app.app_context():
        print(f"Setting up migrations for: {app.config['SQLALCHEMY_DATABASE_URI']}")
        init()
        print("Migration repository initialized!")

def create_migration(message="auto migration"):
    """Create a new migration."""
    app, migrate_obj = create_app()

    with app.app_context():
        print(f"Creating migration for: {app.config['SQLALCHEMY_DATABASE_URI']}")
        migrate(message=message)
        print(f"Migration created with message: {message}")

def upgrade_database():
    """Upgrade database to latest migration."""
    app, migrate_obj = create_app()

    with app.app_context():
        print(f"Upgrading database: {app.config['SQLALCHEMY_DATABASE_URI']}")
        upgrade()
        print("Database upgraded successfully!")

def show_migration_status():
    """Show current migration status."""
    app, migrate_obj = create_app()

    with app.app_context():
        print(f"Database: {app.config['SQLALCHEMY_DATABASE_URI']}")
        print("\nCurrent revision:")
        current()
        print("\nMigration history:")
        history()

def backup_database():
    """Create a backup of the current database."""
    app, migrate_obj = create_app()

    db_uri = app.config['SQLALCHEMY_DATABASE_URI']

    if db_uri.startswith('sqlite:///'):
        db_file = db_uri.replace('sqlite:///', '')
        if os.path.exists(db_file):
            import shutil
            from datetime import datetime

            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            backup_file = f"{db_file}.backup_{timestamp}"

            shutil.copy2(db_file, backup_file)
            print(f"Database backed up to: {backup_file}")
        else:
            print("Database file not found!")
    else:
        print("Backup only supported for SQLite databases")


def seed_existing_orgs():
    """
    Seed task types and transaction types for existing active organizations
    that don't have them yet. Run this after deploying the fix for new orgs.
    """
    app, migrate_obj = create_app()
    
    with app.app_context():
        from models import Organization, TaskType, TransactionType
        from services.tenant_service import (
            create_default_task_types_for_org,
            create_default_transaction_types_for_org
        )
        
        # Get all active organizations
        orgs = Organization.query.filter(Organization.status == 'active').all()
        print(f"Found {len(orgs)} active organizations")
        
        for org in orgs:
            print(f"\nProcessing org {org.id}: {org.name}")
            
            # Check if org has task types
            task_type_count = TaskType.query.filter_by(organization_id=org.id).count()
            if task_type_count == 0:
                print(f"  - Creating default task types for org {org.id}...")
                try:
                    created = create_default_task_types_for_org(org.id)
                    print(f"  - Created {len(created)} task types with subtypes")
                except Exception as e:
                    db.session.rollback()
                    print(f"  - ERROR creating task types: {e}")
            else:
                print(f"  - Task types already exist ({task_type_count} found)")
            
            # Check if org has transaction types
            tx_type_count = TransactionType.query.filter_by(organization_id=org.id).count()
            if tx_type_count == 0:
                print(f"  - Creating default transaction types for org {org.id}...")
                print(f"    NOTE: This may fail if there's a global unique constraint on 'name'")
                print(f"    A database migration is needed to fix this constraint.")
                try:
                    created = create_default_transaction_types_for_org(org.id)
                    print(f"  - Created {len(created)} transaction types")
                except Exception as e:
                    db.session.rollback()
                    print(f"  - SKIPPED: Transaction types require a migration to fix unique constraint")
            else:
                print(f"  - Transaction types already exist ({tx_type_count} found)")
        
        print("\nSeeding complete!")


def main():
    if len(sys.argv) < 2:
        print("Usage: python manage_db.py <command>")
        print("Commands:")
        print("  init        - Initialize new database")
        print("  setup       - Set up migration repository")
        print("  migrate     - Create new migration")
        print("  upgrade     - Upgrade database to latest migration")
        print("  status      - Show migration status")
        print("  backup      - Create database backup")
        print("  seed_orgs   - Seed task/transaction types for existing orgs")
        return

    command = sys.argv[1]

    try:
        if command == 'init':
            init_database()
        elif command == 'setup':
            setup_migrations()
        elif command == 'migrate':
            message = sys.argv[2] if len(sys.argv) > 2 else "auto migration"
            create_migration(message)
        elif command == 'upgrade':
            upgrade_database()
        elif command == 'status':
            show_migration_status()
        elif command == 'backup':
            backup_database()
        elif command == 'seed_orgs':
            seed_existing_orgs()
        else:
            print(f"Unknown command: {command}")
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()
