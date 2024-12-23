# migrate_add_commission.py
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

def run_migration():
    """
    Migration script that:
    1. Creates new table with potential_commission field
    2. Copies existing data and sets default commission value
    3. Replaces old table with new one
    """
    with app.app_context():
        print("Starting potential commission field migration...")

        try:
            # 1. Create new contact table with updated structure
            print("Creating new contact table structure...")
            db.session.execute(text("""
                CREATE TABLE contact_new (
                    id INTEGER PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    first_name VARCHAR(80) NOT NULL,
                    last_name VARCHAR(80) NOT NULL,
                    email VARCHAR(120),
                    phone VARCHAR(20),
                    street_address VARCHAR(200),
                    city VARCHAR(100),
                    state VARCHAR(50),
                    zip_code VARCHAR(20),
                    notes TEXT,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL,
                    potential_commission DECIMAL(10,2) NOT NULL DEFAULT 5000.00,
                    FOREIGN KEY(user_id) REFERENCES user (id)
                )
            """))

            # 2. Copy data to new table, setting default commission value
            print("Copying contact data to new structure...")
            db.session.execute(text("""
                INSERT INTO contact_new 
                SELECT id, user_id, first_name, last_name, email, phone, 
                       street_address, city, state, zip_code,
                       notes, created_at, updated_at,
                       5000.00 as potential_commission
                FROM contact
            """))

            # 3. Drop old table and rename new one
            print("Replacing old contact table...")
            db.session.execute(text("DROP TABLE contact"))
            db.session.execute(text("ALTER TABLE contact_new RENAME TO contact"))

            # 4. Recreate any indexes or constraints
            print("Recreating contact_groups associations...")
            db.session.execute(text("""
                CREATE TABLE IF NOT EXISTS contact_groups (
                    contact_id INTEGER NOT NULL,
                    group_id INTEGER NOT NULL,
                    PRIMARY KEY (contact_id, group_id),
                    FOREIGN KEY(contact_id) REFERENCES contact (id),
                    FOREIGN KEY(group_id) REFERENCES contact_group (id)
                )
            """))

            # 5. Commit all changes
            db.session.commit()
            print("✓ Migration completed successfully!")

        except Exception as e:
            db.session.rollback()
            print(f"Error during migration: {str(e)}")
            raise

if __name__ == '__main__':
    run_migration()