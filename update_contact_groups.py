# update_contact_groups.py

from app import create_app
from models import db, ContactGroup

def update_groups():
    # Create the app using the factory
    app = create_app()
    
    # Use the application context
    with app.app_context():
        try:
            # 1. Rename "Sales Agent" to "Builder Sales Agent"
            sales_agent = ContactGroup.query.filter_by(name="Sales Agent").first()
            if sales_agent:
                sales_agent.name = "Builder Sales Agent"
                print("Renamed 'Sales Agent' to 'Builder Sales Agent'")

            # 2. Add new groups if they don't exist
            new_groups = [
                {"name": "Insurance Broker", "category": "Professional", "sort_order": 55},
                {"name": "Third Party Professional", "category": "Professional", "sort_order": 56},
                {"name": "Property Tax Protests", "category": "Professional", "sort_order": 57}
            ]

            for group_data in new_groups:
                existing = ContactGroup.query.filter_by(name=group_data['name']).first()
                if not existing:
                    new_group = ContactGroup(**group_data)
                    db.session.add(new_group)
                    print(f"Added new group: {group_data['name']}")

            # Commit all changes
            db.session.commit()
            print("Successfully updated contact groups!")

            # Print current groups
            print("\nCurrent groups in database:")
            all_groups = ContactGroup.query.order_by(ContactGroup.sort_order).all()
            for group in all_groups:
                print(f"{group.sort_order}: {group.name} ({group.category})")

        except Exception as e:
            db.session.rollback()
            print(f"Error updating groups: {str(e)}")

if __name__ == '__main__':
    update_groups()