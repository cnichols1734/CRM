"""
Shared pytest fixtures for integration tests.

Provides a fully seeded SQLite-backed Flask app with two organizations,
users at various permission levels, and seed data for contacts, tasks,
transactions, groups, etc.
"""
import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_TEST_DB = 'sqlite:////tmp/test_integration.db'

# Force pytest onto an isolated local database, even if the shell env points
# at a hosted database such as Supabase.
os.environ['DATABASE_URL'] = _TEST_DB
os.environ.setdefault('SECRET_KEY', 'test-secret-key')
os.environ.setdefault('FLASK_ENV', 'testing')

from app import create_app
from models import (
    db as _db, User, Organization, Contact, ContactGroup,
    Task, TaskType, TaskSubtype, Transaction, TransactionType,
    TransactionDocument, TransactionParticipant, Interaction,
    CompanyUpdate, CompanyUpdateComment, CompanyUpdateReaction,
    UserTodo, ActionPlan, ChatConversation, ChatMessage,
    AgentResource, contact_groups as contact_groups_table,
)


# ---------------------------------------------------------------------------
# App / DB fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope='session')
def app():
    """Create the Flask application for testing."""
    os.environ['DATABASE_URL'] = _TEST_DB
    application = create_app()
    application.config.update(
        TESTING=True,
        WTF_CSRF_ENABLED=False,
        SQLALCHEMY_DATABASE_URI=_TEST_DB,
        SERVER_NAME='localhost',
        SECRET_KEY='test-secret-key',
    )

    with application.app_context():
        _db.create_all()
        yield application
        _db.session.remove()
        _db.drop_all()

    if os.path.exists('/tmp/test_integration.db'):
        os.remove('/tmp/test_integration.db')


@pytest.fixture(scope='session')
def db(app):
    """Return the SQLAlchemy database instance."""
    return _db


# ---------------------------------------------------------------------------
# Seed data
# ---------------------------------------------------------------------------

@pytest.fixture(scope='session')
def seed(app, db):
    """Seed two organizations with users, contacts, tasks, transactions, etc."""
    with app.app_context():
        # ---- Org A (pro) ----
        org_a = Organization(
            name='Test Realty A', slug='test-realty-a',
            subscription_tier='pro', status='active',
            max_users=10, max_contacts=1000, can_invite_users=True,
        )
        db.session.add(org_a)
        db.session.flush()

        owner_a = _make_user(db, org_a, 'owner_a', 'owner_a@test.com',
                             'Alice', 'Owner', role='admin', org_role='owner')
        admin_a = _make_user(db, org_a, 'admin_a', 'admin_a@test.com',
                             'Adam', 'Admin', role='admin', org_role='admin')
        agent_a = _make_user(db, org_a, 'agent_a', 'agent_a@test.com',
                             'Amy', 'Agent', role='agent', org_role='agent')

        # ---- Org B (free) ----
        org_b = Organization(
            name='Test Realty B', slug='test-realty-b',
            subscription_tier='free', status='active',
            max_users=1, max_contacts=10000,
        )
        db.session.add(org_b)
        db.session.flush()

        owner_b = _make_user(db, org_b, 'owner_b', 'owner_b@test.com',
                             'Bob', 'Owner', role='admin', org_role='owner')

        # ---- Contact groups ----
        group_a1 = ContactGroup(name='Buyers', organization_id=org_a.id, category='general', sort_order=0)
        group_a2 = ContactGroup(name='Sellers', organization_id=org_a.id, category='general', sort_order=1)
        group_b1 = ContactGroup(name='Leads', organization_id=org_b.id, category='general', sort_order=0)
        db.session.add_all([group_a1, group_a2, group_b1])
        db.session.flush()

        # ---- Contacts ----
        contact_a = Contact(
            organization_id=org_a.id, user_id=owner_a.id, created_by_id=owner_a.id,
            first_name='Jane', last_name='Doe', email='jane@test.com', phone='5551110000',
        )
        contact_a2 = Contact(
            organization_id=org_a.id, user_id=agent_a.id, created_by_id=agent_a.id,
            first_name='John', last_name='Smith', email='john@test.com', phone='5552220000',
        )
        contact_b = Contact(
            organization_id=org_b.id, user_id=owner_b.id, created_by_id=owner_b.id,
            first_name='Bob', last_name='Contact', email='bc@test.com',
        )
        db.session.add_all([contact_a, contact_a2, contact_b])
        db.session.flush()

        contact_a.groups.append(group_a1)
        contact_a2.groups.append(group_a2)
        contact_b.groups.append(group_b1)

        # ---- Task types / subtypes ----
        tt_a = TaskType(name='Call', organization_id=org_a.id, sort_order=0)
        tt_a2 = TaskType(name='Email', organization_id=org_a.id, sort_order=1)
        tt_b = TaskType(name='Call', organization_id=org_b.id, sort_order=0)
        db.session.add_all([tt_a, tt_a2, tt_b])
        db.session.flush()

        st_a = TaskSubtype(name='Follow Up', task_type_id=tt_a.id, organization_id=org_a.id, sort_order=0)
        st_a2 = TaskSubtype(name='Check-in', task_type_id=tt_a.id, organization_id=org_a.id, sort_order=1)
        st_b = TaskSubtype(name='Follow Up', task_type_id=tt_b.id, organization_id=org_b.id, sort_order=0)
        db.session.add_all([st_a, st_a2, st_b])
        db.session.flush()

        # ---- Tasks ----
        from datetime import datetime, timedelta
        task_a = Task(
            organization_id=org_a.id, contact_id=contact_a.id,
            assigned_to_id=owner_a.id, created_by_id=owner_a.id,
            type_id=tt_a.id, subtype_id=st_a.id,
            subject='Call Jane', priority='medium', status='pending',
            due_date=datetime.utcnow() + timedelta(days=1),
        )
        task_a2 = Task(
            organization_id=org_a.id, contact_id=contact_a2.id,
            assigned_to_id=agent_a.id, created_by_id=admin_a.id,
            type_id=tt_a.id, subtype_id=st_a.id,
            subject='Follow up John', priority='high', status='pending',
            due_date=datetime.utcnow() + timedelta(days=3),
        )
        task_b = Task(
            organization_id=org_b.id, contact_id=contact_b.id,
            assigned_to_id=owner_b.id, created_by_id=owner_b.id,
            type_id=tt_b.id, subtype_id=st_b.id,
            subject='Task B Only', priority='low', status='pending',
            due_date=datetime.utcnow() + timedelta(days=2),
        )
        db.session.add_all([task_a, task_a2, task_b])
        db.session.flush()

        # ---- Transaction types ----
        tx_type_a = TransactionType(name='seller', display_name='Seller', organization_id=org_a.id)
        tx_type_a2 = TransactionType(name='buyer', display_name='Buyer', organization_id=org_a.id)
        tx_type_b = TransactionType(name='seller', display_name='Seller', organization_id=org_b.id)
        db.session.add_all([tx_type_a, tx_type_a2, tx_type_b])
        db.session.flush()

        # ---- Transactions ----
        tx_a = Transaction(
            organization_id=org_a.id, created_by_id=owner_a.id,
            transaction_type_id=tx_type_a.id, street_address='100 Main St',
            city='Austin', state='TX', status='active',
        )
        tx_b = Transaction(
            organization_id=org_b.id, created_by_id=owner_b.id,
            transaction_type_id=tx_type_b.id, street_address='200 Oak Ave',
            city='Dallas', state='TX', status='active',
        )
        db.session.add_all([tx_a, tx_b])
        db.session.flush()

        # ---- Transaction documents ----
        doc_a = TransactionDocument(
            organization_id=org_a.id, transaction_id=tx_a.id,
            template_slug='listing-agreement', template_name='Listing Agreement',
            status='pending',
        )
        db.session.add(doc_a)
        db.session.flush()

        # ---- Company updates ----
        update_a = CompanyUpdate(
            organization_id=org_a.id, author_id=owner_a.id,
            title='Welcome Update', content='<p>Welcome everyone!</p>',
            excerpt='Welcome everyone!',
        )
        db.session.add(update_a)
        db.session.flush()

        # ---- Agent resources ----
        resource_a = AgentResource(
            organization_id=org_a.id, label='Training Guide',
            url='https://example.com/guide', sort_order=0,
        )
        db.session.add(resource_a)
        db.session.flush()

        db.session.commit()

        return {
            'org_a': org_a.id, 'org_b': org_b.id,
            'owner_a': owner_a.id, 'admin_a': admin_a.id, 'agent_a': agent_a.id,
            'owner_b': owner_b.id,
            'contact_a': contact_a.id, 'contact_a2': contact_a2.id, 'contact_b': contact_b.id,
            'group_a1': group_a1.id, 'group_a2': group_a2.id, 'group_b1': group_b1.id,
            'task_a': task_a.id, 'task_a2': task_a2.id, 'task_b': task_b.id,
            'task_type_a': tt_a.id, 'task_type_a2': tt_a2.id, 'task_type_b': tt_b.id,
            'subtype_a': st_a.id, 'subtype_a2': st_a2.id, 'subtype_b': st_b.id,
            'tx_type_a': tx_type_a.id, 'tx_type_a2': tx_type_a2.id,
            'tx_a': tx_a.id, 'tx_b': tx_b.id,
            'doc_a': doc_a.id,
            'update_a': update_a.id,
            'resource_a': resource_a.id,
        }


# ---------------------------------------------------------------------------
# Client helpers
# ---------------------------------------------------------------------------

def _make_user(database, org, username, email, first, last,
               role='agent', org_role='agent'):
    u = User(
        organization_id=org.id, username=username, email=email,
        first_name=first, last_name=last, role=role, org_role=org_role,
    )
    u.set_password('password123')
    database.session.add(u)
    database.session.flush()
    return u


def login(client, username, password='password123'):
    """Log in via the auth route and return the response."""
    client.get('/logout')
    return client.post('/login', data={
        'username': username,
        'password': password,
    }, follow_redirects=True)


@pytest.fixture(autouse=True)
def _rollback_after_test(app):
    """Roll back uncommitted DB changes after each test to prevent state leakage."""
    yield
    with app.app_context():
        _db.session.rollback()


@pytest.fixture()
def client(app):
    """Provide a fresh test client per test."""
    return app.test_client()


@pytest.fixture()
def owner_a_client(app, seed):
    """Test client pre-logged in as owner of Org A (pro)."""
    with app.test_client() as c:
        login(c, 'owner_a')
        yield c


@pytest.fixture()
def admin_a_client(app, seed):
    """Test client pre-logged in as admin of Org A (pro)."""
    with app.test_client() as c:
        login(c, 'admin_a')
        yield c


@pytest.fixture()
def agent_a_client(app, seed):
    """Test client pre-logged in as agent of Org A (pro)."""
    with app.test_client() as c:
        login(c, 'agent_a')
        yield c


@pytest.fixture()
def owner_b_client(app, seed):
    """Test client pre-logged in as owner of Org B (free)."""
    with app.test_client() as c:
        login(c, 'owner_b')
        yield c
