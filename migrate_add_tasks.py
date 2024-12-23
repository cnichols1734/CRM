from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import text
from datetime import datetime

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///crm.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy()
db.init_app(app)

def populate_task_data():
    print("Populating task types and subtypes...")
    # Insert initial task types and subtypes
    task_types = [
        ("Call", 10),
        ("Meeting", 20),
        ("Email", 30),
        ("Document", 40)
    ]
    
    for task_type in task_types:
        db.session.execute(text(
            "INSERT INTO task_type (name, sort_order) VALUES (:name, :sort_order)"
        ), {"name": task_type[0], "sort_order": task_type[1]})
    
    # Insert subtypes
    subtypes = [
        # Call subtypes
        (1, "Check-in", 1),
        (1, "Schedule Showing", 2),
        (1, "Discuss Offer", 3),
        (1, "Follow-up", 4),
        # Meeting subtypes
        (2, "Initial Consultation", 1),
        (2, "Property Showing", 2),
        (2, "Contract Review", 3),
        (2, "Home Inspection", 4),
        # Email subtypes
        (3, "Send Listings", 1),
        (3, "Send Documents", 2),
        (3, "Market Update", 3),
        (3, "General Follow-up", 4),
        # Document subtypes
        (4, "Prepare Contract", 1),
        (4, "Review Documents", 2),
        (4, "Submit Offer", 3),
        (4, "Process Paperwork", 4)
    ]
    
    for subtype in subtypes:
        db.session.execute(text(
            "INSERT INTO task_subtype (task_type_id, name, sort_order) VALUES (:type_id, :name, :sort_order)"
        ), {"type_id": subtype[0], "name": subtype[1], "sort_order": subtype[2]})
    
    db.session.commit()
    print("Task data populated successfully!")

def run_migration():
    with app.app_context():
        # Check if tables have data
        result = db.session.execute(text("SELECT COUNT(*) FROM task_type"))
        type_count = result.scalar()
        result = db.session.execute(text("SELECT COUNT(*) FROM task_subtype"))
        subtype_count = result.scalar()
        
        if type_count == 0 and subtype_count == 0:
            print("Tables exist but are empty. Populating with initial data...")
            populate_task_data()
        else:
            print(f"Tables already contain data: {type_count} task types and {subtype_count} subtypes.")

if __name__ == '__main__':
    run_migration() 