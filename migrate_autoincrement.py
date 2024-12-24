from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import text
from datetime import datetime

# Initialize Flask and SQLAlchemy
app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///crm.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy()
db.init_app(app)

def cleanup_temp_tables():
    """Clean up any temporary tables from failed migrations"""
    temp_tables = [
        "user_new", "contact_new", "contact_group_new", "contact_groups_new",
        "interaction_new", "task_type_new", "task_subtype_new", "task_new"
    ]
    for table in temp_tables:
        try:
            db.session.execute(text(f"DROP TABLE IF EXISTS {table}"))
        except Exception as e:
            print(f"Note: Could not drop {table}: {str(e)}")
    db.session.commit()

def migrate_table(table_name, columns, foreign_keys=None, skip_autoincrement=False):
    """Helper function to migrate a single table to use AUTOINCREMENT"""
    print(f"\nMigrating {table_name}...")
    
    try:
        # Drop the temporary table if it exists
        db.session.execute(text(f"DROP TABLE IF EXISTS {table_name}_new"))
        
        # Create new table with AUTOINCREMENT
        columns_sql = ", ".join(columns)
        foreign_keys_sql = ""
        if foreign_keys:
            foreign_keys_sql = ", " + ", ".join(foreign_keys)
        
        # For association tables, we don't want an autoincrement ID
        id_column = "id INTEGER PRIMARY KEY AUTOINCREMENT," if not skip_autoincrement else ""
        
        create_sql = f"""
            CREATE TABLE {table_name}_new (
                {id_column}
                {columns_sql}
                {foreign_keys_sql}
            )
        """
        
        # Copy data
        copy_sql = f"""
            INSERT INTO {table_name}_new 
            SELECT * FROM {table_name}
        """
        
        # Execute migration
        db.session.execute(text(create_sql))
        db.session.execute(text(copy_sql))
        db.session.execute(text(f"DROP TABLE {table_name}"))
        db.session.execute(text(f"ALTER TABLE {table_name}_new RENAME TO {table_name}"))
        db.session.commit()
        
    except Exception as e:
        db.session.rollback()
        print(f"Error migrating {table_name}: {str(e)}")
        raise

def run_migration():
    """
    Migration script that updates all tables to use AUTOINCREMENT for their ID columns
    to prevent ID reuse when records are deleted.
    """
    with app.app_context():
        print("Starting autoincrement migration...")
        
        try:
            # Clean up any temporary tables from failed migrations
            print("Cleaning up any temporary tables...")
            cleanup_temp_tables()
            
            # Migrate User table
            user_columns = [
                "username VARCHAR(80) UNIQUE NOT NULL",
                "email VARCHAR(120) UNIQUE NOT NULL",
                "password_hash VARCHAR(128)",
                "first_name VARCHAR(80) NOT NULL",
                "last_name VARCHAR(80) NOT NULL",
                "role VARCHAR(20) NOT NULL DEFAULT 'agent'",
                "created_at DATETIME NOT NULL",
                "last_login DATETIME"
            ]
            migrate_table("user", user_columns)

            # Migrate Contact table
            contact_columns = [
                "user_id INTEGER NOT NULL",
                "first_name VARCHAR(80) NOT NULL",
                "last_name VARCHAR(80) NOT NULL",
                "email VARCHAR(120)",
                "phone VARCHAR(20)",
                "street_address VARCHAR(200)",
                "city VARCHAR(100)",
                "state VARCHAR(50)",
                "zip_code VARCHAR(20)",
                "notes TEXT",
                "created_at DATETIME NOT NULL",
                "updated_at DATETIME NOT NULL",
                "potential_commission DECIMAL(10,2) NOT NULL DEFAULT 5000.00"
            ]
            contact_fks = ["FOREIGN KEY(user_id) REFERENCES user (id)"]
            migrate_table("contact", contact_columns, contact_fks)

            # Migrate ContactGroup table
            group_columns = [
                "name VARCHAR(100) UNIQUE NOT NULL",
                "category VARCHAR(50) NOT NULL",
                "sort_order INTEGER NOT NULL",
                "created_at DATETIME NOT NULL"
            ]
            migrate_table("contact_group", group_columns)

            # Migrate contact_groups association table
            contact_groups_columns = [
                "contact_id INTEGER NOT NULL",
                "group_id INTEGER NOT NULL"
            ]
            contact_groups_fks = [
                "FOREIGN KEY(contact_id) REFERENCES contact (id)",
                "FOREIGN KEY(group_id) REFERENCES contact_group (id)",
                "PRIMARY KEY(contact_id, group_id)"
            ]
            migrate_table("contact_groups", contact_groups_columns, contact_groups_fks, skip_autoincrement=True)

            # Migrate Interaction table
            interaction_columns = [
                "contact_id INTEGER NOT NULL",
                "user_id INTEGER NOT NULL",
                "type VARCHAR(50) NOT NULL",
                "notes TEXT",
                "date DATETIME NOT NULL",
                "follow_up_date DATETIME",
                "created_at DATETIME NOT NULL"
            ]
            interaction_fks = [
                "FOREIGN KEY(contact_id) REFERENCES contact (id)",
                "FOREIGN KEY(user_id) REFERENCES user (id)"
            ]
            migrate_table("interaction", interaction_columns, interaction_fks)

            # Migrate TaskType table
            task_type_columns = [
                "name VARCHAR(50) NOT NULL",
                "sort_order INTEGER NOT NULL"
            ]
            migrate_table("task_type", task_type_columns)

            # Migrate TaskSubtype table
            task_subtype_columns = [
                "task_type_id INTEGER NOT NULL",
                "name VARCHAR(50) NOT NULL",
                "sort_order INTEGER NOT NULL"
            ]
            task_subtype_fks = ["FOREIGN KEY(task_type_id) REFERENCES task_type (id)"]
            migrate_table("task_subtype", task_subtype_columns, task_subtype_fks)

            # Migrate Task table
            task_columns = [
                "contact_id INTEGER NOT NULL",
                "assigned_to_id INTEGER NOT NULL",
                "created_by_id INTEGER NOT NULL",
                "type_id INTEGER NOT NULL",
                "subtype_id INTEGER NOT NULL",
                "subject VARCHAR(200) NOT NULL",
                "description TEXT",
                "priority VARCHAR(20) NOT NULL DEFAULT 'medium'",
                "status VARCHAR(20) NOT NULL DEFAULT 'pending'",
                "outcome TEXT",
                "created_at DATETIME NOT NULL",
                "due_date DATETIME NOT NULL",
                "completed_at DATETIME",
                "property_address VARCHAR(200)",
                "scheduled_time DATETIME",
                "reminder_sent BOOLEAN DEFAULT FALSE"
            ]
            task_fks = [
                "FOREIGN KEY(contact_id) REFERENCES contact (id)",
                "FOREIGN KEY(assigned_to_id) REFERENCES user (id)",
                "FOREIGN KEY(created_by_id) REFERENCES user (id)",
                "FOREIGN KEY(type_id) REFERENCES task_type (id)",
                "FOREIGN KEY(subtype_id) REFERENCES task_subtype (id)"
            ]
            migrate_table("task", task_columns, task_fks)

            # Commit all changes
            db.session.commit()
            print("\n✓ Migration completed successfully! All tables now use AUTOINCREMENT for IDs.")

        except Exception as e:
            db.session.rollback()
            print(f"\nError during migration: {str(e)}")
            raise

if __name__ == '__main__':
    run_migration() 