import argparse
from app import app
from models import db, User, Contact, Task, Interaction, contact_groups

DEFAULT_EMAIL = "cassie@origenrealty.com"


def delete_contacts_for_user(email: str) -> None:
    with app.app_context():
        user = User.query.filter_by(email=email).first()
        if not user:
            print(f"No user found with email: {email}")
            return

        contact_ids = [c.id for c in Contact.query.filter_by(user_id=user.id).all()]
        if not contact_ids:
            print(f"No contacts found for user {user.first_name} {user.last_name} <{email}>")
            return

        # Delete dependent records first (tasks, interactions, group associations)
        tasks_deleted = Task.query.filter(Task.contact_id.in_(contact_ids)).delete(synchronize_session=False)
        interactions_deleted = Interaction.query.filter(Interaction.contact_id.in_(contact_ids)).delete(synchronize_session=False)
        groups_deleted = db.session.execute(
            contact_groups.delete().where(contact_groups.c.contact_id.in_(contact_ids))
        ).rowcount

        contacts_deleted = Contact.query.filter(Contact.id.in_(contact_ids)).delete(synchronize_session=False)
        db.session.commit()

        print(f"Deleted for {user.first_name} {user.last_name} <{email}>:")
        print(f"  Contacts:      {contacts_deleted}")
        print(f"  Tasks:         {tasks_deleted}")
        print(f"  Interactions:  {interactions_deleted}")
        print(f"  Group links:   {groups_deleted}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Delete all contacts and related records for a user by email")
    parser.add_argument("--email", required=False, help="User email to delete contacts for (defaults to cassie@origenrealty.com)")
    args = parser.parse_args()
    target_email = args.email or DEFAULT_EMAIL
    if not args.email:
        print(f"No --email provided; defaulting to {DEFAULT_EMAIL}")
    delete_contacts_for_user(target_email)


