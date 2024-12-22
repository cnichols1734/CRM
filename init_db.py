from app import create_app
from models import db, ContactGroup

INITIAL_GROUPS = [
    # Buyer Groups (10-19)
    {"name": "Buyer - New Potential Client", "category": "Buyer", "sort_order": 10},
    {"name": "Buyer - Actively Showing Homes", "category": "Buyer", "sort_order": 11},
    {"name": "Buyer - Under Contract", "category": "Buyer", "sort_order": 12},
    {"name": "Buyer - Previous Client", "category": "Buyer", "sort_order": 13},

    # Seller Groups (20-29)
    {"name": "Seller - New Potential Client", "category": "Seller", "sort_order": 20},
    {"name": "Seller - Active Listing", "category": "Seller", "sort_order": 21},
    {"name": "Seller - Under Contract", "category": "Seller", "sort_order": 22},
    {"name": "Seller - Previous Client", "category": "Seller", "sort_order": 23},

    # Rating Groups (30-39)
    {"name": "A", "category": "Rating", "sort_order": 30},
    {"name": "B", "category": "Rating", "sort_order": 31},
    {"name": "C", "category": "Rating", "sort_order": 32},
    {"name": "D", "category": "Rating", "sort_order": 33},

    # Personal Network (40-49)
    {"name": "Family", "category": "Network", "sort_order": 40},
    {"name": "Friend", "category": "Network", "sort_order": 41},

    # Professional Network (50-59)
    {"name": "Builder", "category": "Professional", "sort_order": 50},
    {"name": "Sales Agent", "category": "Professional", "sort_order": 51},
    {"name": "Inspector", "category": "Professional", "sort_order": 52},
    {"name": "Lender", "category": "Professional", "sort_order": 53},
    {"name": "Real Estate Agent", "category": "Professional", "sort_order": 54}
]


def init_db():
    app = create_app()
    with app.app_context():
        # Create all tables
        db.create_all()

        # Check if groups already exist
        if ContactGroup.query.count() == 0:
            print("Initializing database with contact groups...")
            # Add contact groups
            for group_data in INITIAL_GROUPS:
                print(f"Adding group: {group_data['name']}")
                group = ContactGroup(**group_data)
                db.session.add(group)

            try:
                db.session.commit()
                print("Successfully initialized database with contact groups!")
            except Exception as e:
                db.session.rollback()
                print(f"Error initializing database: {str(e)}")
        else:
            print("Groups already exist in database!")

        # Verify groups were added
        all_groups = ContactGroup.query.order_by(ContactGroup.sort_order).all()
        print("\nCurrent groups in database:")
        for group in all_groups:
            print(f"{group.sort_order}: {group.name} ({group.category})")


if __name__ == '__main__':
    init_db()