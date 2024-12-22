# update_contact_groups.py

from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import text
from datetime import datetime

# Initialize Flask and SQLAlchemy without loading models
app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///crm.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy()
db.init_app(app)


def run_migration():
    """
    Complete migration script that:
    1. Backs up existing relationships
    2. Creates new association table
    3. Migrates data
    4. Removes old column
    """
    with app.app_context():
        print("Starting contact groups migration...")

        # 1. Back up existing relationships
        print("Backing up existing relationships...")
        existing_relationships = db.session.execute(
            text("SELECT id, group_id FROM contact WHERE group_id IS NOT NULL")
        ).fetchall()
        print(f"Found {len(existing_relationships)} existing relationships")

        try:
            # 2. Create the new association table
            print("Creating contact_groups association table...")
            db.session.execute(text("""
                CREATE TABLE IF NOT EXISTS contact_groups (
                    contact_id INTEGER NOT NULL,
                    group_id INTEGER NOT NULL,
                    PRIMARY KEY (contact_id, group_id),
                    FOREIGN KEY(contact_id) REFERENCES contact (id),
                    FOREIGN KEY(group_id) REFERENCES contact_group (id)
                )
            """))

            # 3. Migrate existing relationships to the new table
            print("Migrating existing relationships...")
            for contact_id, group_id in existing_relationships:
                db.session.execute(
                    text("INSERT INTO contact_groups (contact_id, group_id) VALUES (:cid, :gid)"),
                    {"cid": contact_id, "gid": group_id}
                )

            # 4. Create new contact table without group_id
            print("Creating new contact table structure...")
            db.session.execute(text("""
                CREATE TABLE contact_new (
                    id INTEGER PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    first_name VARCHAR(80) NOT NULL,
                    last_name VARCHAR(80) NOT NULL,
                    email VARCHAR(120),
                    phone VARCHAR(20),
                    address VARCHAR(200),
                    notes TEXT,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES user (id)
                )
            """))

            # 5. Copy data to new table
            print("Copying contact data to new structure...")
            db.session.execute(text("""
                INSERT INTO contact_new 
                SELECT id, user_id, first_name, last_name, email, phone, 
                       address, notes, created_at, updated_at
                FROM contact
            """))

            # 6. Drop old table and rename new one
            print("Replacing old contact table...")
            db.session.execute(text("DROP TABLE contact"))
            db.session.execute(text("ALTER TABLE contact_new RENAME TO contact"))

            # 7. Commit all changes
            db.session.commit()

            # 8. Verify the migration
            print("\nVerifying migration...")
            original_count = len(existing_relationships)
            migrated_count = db.session.execute(
                text("SELECT COUNT(*) FROM contact_groups")
            ).scalar()
            print(f"Original relationships: {original_count}")
            print(f"Migrated relationships: {migrated_count}")

            if original_count == migrated_count:
                print("✓ Migration completed successfully!")
            else:
                print("! Warning: Relationship counts don't match")

        except Exception as e:
            db.session.rollback()
            print(f"Error during migration: {str(e)}")
            raise


if __name__ == '__main__':
    run_migration()