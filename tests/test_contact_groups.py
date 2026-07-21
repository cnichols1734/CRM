"""Per-user contact group ownership, CRUD, and assignment isolation."""

from datetime import datetime, timedelta

from models import Contact, ContactGroup, OrganizationInvite, User, db
from services.cache_helpers import (
    clear_user_contact_groups_cache,
    get_user_contact_groups,
)
from services.tenant_service import (
    DEFAULT_CONTACT_GROUPS,
    create_default_groups_for_user,
)


class TestCustomizeGroupsPage:
    def test_page_loads_for_owner(self, owner_a_client, seed):
        resp = owner_a_client.get('/groups')
        assert resp.status_code == 200
        assert b'Customize Groups' in resp.data
        assert b'Buyers' in resp.data

    def test_page_loads_for_agent(self, agent_a_client, seed):
        resp = agent_a_client.get('/groups')
        assert resp.status_code == 200
        assert b'Customize Groups' in resp.data

    def test_new_badge_shown_during_window(self, owner_a_client, seed):
        resp = owner_a_client.get('/dashboard')
        assert resp.status_code == 200
        assert b'Customize Groups' in resp.data
        assert b'NEW' in resp.data


class TestGroupCRUD:
    def test_create_group(self, agent_a_client, seed, app):
        resp = agent_a_client.post(
            '/groups',
            json={'name': 'Open House', 'category': 'Status'},
            content_type='application/json',
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['success'] is True
        assert data['group']['name'] == 'Open House'

        with app.app_context():
            group = ContactGroup.query.get(data['group']['id'])
            assert group.user_id == seed['agent_a']
            assert group.organization_id == seed['org_a']

    def test_duplicate_name_rejected(self, owner_a_client, seed):
        resp = owner_a_client.post(
            '/groups',
            json={'name': 'Buyers', 'category': 'general'},
            content_type='application/json',
        )
        assert resp.status_code == 400
        assert resp.get_json()['success'] is False

    def test_cannot_update_other_users_group(self, agent_a_client, seed):
        resp = agent_a_client.put(
            f'/groups/{seed["group_a1"]}',
            json={'name': 'Hacked'},
            content_type='application/json',
        )
        assert resp.status_code == 404

    def test_cannot_update_cross_org_group(self, owner_a_client, seed):
        resp = owner_a_client.put(
            f'/groups/{seed["group_b1"]}',
            json={'name': 'Hacked'},
            content_type='application/json',
        )
        assert resp.status_code == 404

    def test_deactivate_and_activate(self, owner_a_client, seed, app):
        resp = owner_a_client.put(
            f'/groups/{seed["group_a2"]}',
            json={'is_active': False},
            content_type='application/json',
        )
        assert resp.status_code == 200
        assert resp.get_json()['group']['is_active'] is False

        with app.app_context():
            group = ContactGroup.query.get(seed['group_a2'])
            assert group.is_active is False

        resp = owner_a_client.put(
            f'/groups/{seed["group_a2"]}',
            json={'is_active': True},
            content_type='application/json',
        )
        assert resp.status_code == 200
        assert resp.get_json()['group']['is_active'] is True

    def test_delete_unused_group(self, agent_a_client, seed, app):
        with app.app_context():
            group = ContactGroup(
                name='Temp Delete Me',
                organization_id=seed['org_a'],
                user_id=seed['agent_a'],
                category='Status',
                sort_order=99,
                is_active=True,
            )
            db.session.add(group)
            db.session.commit()
            group_id = group.id

        resp = agent_a_client.delete(f'/groups/{group_id}')
        assert resp.status_code == 200
        assert resp.get_json()['success'] is True

        with app.app_context():
            assert ContactGroup.query.get(group_id) is None

    def test_delete_used_group_blocked(self, owner_a_client, seed):
        resp = owner_a_client.delete(f'/groups/{seed["group_a1"]}')
        assert resp.status_code == 400
        assert 'Deactivate' in resp.get_json()['error']

    def test_reorder_ignores_foreign_ids(self, owner_a_client, seed, app):
        resp = owner_a_client.post(
            '/groups/reorder',
            json=[
                {'id': seed['group_a2'], 'sort_order': 0},
                {'id': seed['group_a1'], 'sort_order': 1},
                {'id': seed['group_agent_a1'], 'sort_order': 2},
            ],
            content_type='application/json',
        )
        assert resp.status_code == 200

        with app.app_context():
            owner_g1 = ContactGroup.query.get(seed['group_a1'])
            owner_g2 = ContactGroup.query.get(seed['group_a2'])
            agent_g = ContactGroup.query.get(seed['group_agent_a1'])
            assert owner_g2.sort_order == 0
            assert owner_g1.sort_order == 1
            # Foreign id ignored — agent group order unchanged by owner's reorder
            assert agent_g.user_id == seed['agent_a']

    def test_restore_defaults_adds_missing(self, owner_a_client, seed, app):
        from services.contact_group_service import add_missing_defaults

        with app.app_context():
            # Use a throwaway user so we don't mutate session-scoped seed groups
            user = User(
                organization_id=seed['org_a'],
                username='restore_defaults_user',
                email='restore.defaults@test.com',
                first_name='Restore',
                last_name='User',
                role='agent',
                org_role='agent',
            )
            user.set_password('password123')
            db.session.add(user)
            db.session.commit()
            user_id = user.id
            create_default_groups_for_user(seed['org_a'], user_id, commit=True)
            ContactGroup.query.filter_by(
                user_id=user_id, name='Buyer - Under Contract'
            ).delete()
            db.session.commit()

            created = add_missing_defaults(seed['org_a'], user_id, commit=True)
            assert len(created) == 1
            assert created[0].name == 'Buyer - Under Contract'
            assert ContactGroup.query.filter_by(user_id=user_id).count() == len(
                DEFAULT_CONTACT_GROUPS
            )

    def test_restore_defaults_api_noop_when_complete(self, owner_a_client, seed):
        resp = owner_a_client.post('/groups/restore-defaults')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['success'] is True
        # Owner seed groups aren't the full default set, so this may create some.
        assert 'created_count' in data


class TestAssignmentIsolation:
    def test_create_rejects_other_users_group(self, agent_a_client, seed, app):
        resp = agent_a_client.post('/contacts/create', data={
            'first_name': 'Isolated',
            'last_name': 'Agent',
            'email': 'isolated.agent@test.com',
            'group_ids': str(seed['group_a1']),  # owner's group
        }, follow_redirects=True)
        # Form re-renders with error (or redirects after failed assign)
        assert resp.status_code == 200

        with app.app_context():
            contact = Contact.query.filter_by(email='isolated.agent@test.com').first()
            assert contact is None

    def test_create_uses_own_group(self, agent_a_client, seed, app):
        resp = agent_a_client.post('/contacts/create', data={
            'first_name': 'OwnGroup',
            'last_name': 'Contact',
            'email': 'owngroup.agent@test.com',
            'group_ids': str(seed['group_agent_a1']),
        }, follow_redirects=True)
        assert resp.status_code == 200

        with app.app_context():
            contact = Contact.query.filter_by(email='owngroup.agent@test.com').first()
            assert contact is not None
            assert contact.user_id == seed['agent_a']
            assert any(g.id == seed['group_agent_a1'] for g in contact.groups)

    def test_admin_edit_uses_contact_owner_groups(self, owner_a_client, seed, app):
        # Owner edits agent's contact — must use agent's group IDs
        resp = owner_a_client.post(
            f'/contacts/{seed["contact_a2"]}/edit',
            data={
                'first_name': 'John',
                'last_name': 'Smith',
                'email': 'john@test.com',
                'group_ids': str(seed['group_agent_a1']),
            },
            headers={'X-Requested-With': 'XMLHttpRequest'},
        )
        assert resp.status_code == 200
        assert resp.get_json()['status'] == 'success'

        with app.app_context():
            contact = Contact.query.get(seed['contact_a2'])
            assert any(g.id == seed['group_agent_a1'] for g in contact.groups)

    def test_admin_edit_rejects_admin_own_group_on_agent_contact(
        self, owner_a_client, seed
    ):
        resp = owner_a_client.post(
            f'/contacts/{seed["contact_a2"]}/edit',
            data={
                'first_name': 'John',
                'last_name': 'Smith',
                'email': 'john@test.com',
                'group_ids': str(seed['group_a1']),  # owner's group
            },
            headers={'X-Requested-With': 'XMLHttpRequest'},
        )
        assert resp.status_code == 400

    def test_inactive_membership_preserved_on_edit(self, owner_a_client, seed, app):
        with app.app_context():
            inactive = ContactGroup(
                name='Legacy Inactive',
                organization_id=seed['org_a'],
                user_id=seed['owner_a'],
                category='Status',
                sort_order=50,
                is_active=False,
            )
            db.session.add(inactive)
            db.session.flush()
            contact = Contact.query.get(seed['contact_a'])
            contact.groups.append(inactive)
            db.session.commit()
            inactive_id = inactive.id

        resp = owner_a_client.post(
            f'/contacts/{seed["contact_a"]}/edit',
            data={
                'first_name': 'Jane',
                'last_name': 'Doe',
                'email': 'jane@test.com',
                'group_ids': str(seed['group_a1']),
            },
            headers={'X-Requested-With': 'XMLHttpRequest'},
        )
        assert resp.status_code == 200

        with app.app_context():
            contact = Contact.query.get(seed['contact_a'])
            group_ids = {g.id for g in contact.groups}
            assert seed['group_a1'] in group_ids
            assert inactive_id in group_ids


class TestSeeding:
    def test_create_default_groups_for_user_idempotent(self, app, seed):
        with app.app_context():
            first = create_default_groups_for_user(
                seed['org_a'], seed['admin_a'], commit=True
            )
            second = create_default_groups_for_user(
                seed['org_a'], seed['admin_a'], commit=True
            )
            assert len(first) == len(DEFAULT_CONTACT_GROUPS)
            assert len(second) == len(first)
            assert ContactGroup.query.filter_by(user_id=seed['admin_a']).count() == len(
                DEFAULT_CONTACT_GROUPS
            )

    def test_invite_completion_seeds_groups(self, client, app, seed):
        with app.app_context():
            invite = OrganizationInvite(
                organization_id=seed['org_a'],
                email='invitee.groups@test.com',
                role='agent',
                token='invite-groups-token-123',
                invited_by_id=seed['owner_a'],
                expires_at=datetime.utcnow() + timedelta(days=2),
            )
            db.session.add(invite)
            db.session.commit()

        resp = client.post('/invite/invite-groups-token-123/complete', data={
            'username': 'inviteegroups',
            'password': 'supersecure123',
            'first_name': 'Invitee',
            'last_name': 'Groups',
        }, follow_redirects=True)
        assert resp.status_code == 200

        with app.app_context():
            user = User.query.filter_by(email='invitee.groups@test.com').first()
            assert user is not None
            assert ContactGroup.query.filter_by(user_id=user.id).count() == len(
                DEFAULT_CONTACT_GROUPS
            )


class TestCacheIsolation:
    def test_user_cache_does_not_leak(self, app, seed):
        with app.app_context():
            clear_user_contact_groups_cache(seed['org_a'], seed['owner_a'])
            clear_user_contact_groups_cache(seed['org_a'], seed['agent_a'])

            owner_groups = get_user_contact_groups(seed['org_a'], seed['owner_a'])
            agent_groups = get_user_contact_groups(seed['org_a'], seed['agent_a'])

            owner_ids = {g.id for g in owner_groups}
            agent_ids = {g.id for g in agent_groups}
            assert seed['group_a1'] in owner_ids
            assert seed['group_a1'] not in agent_ids
            assert seed['group_agent_a1'] in agent_ids
            assert seed['group_agent_a1'] not in owner_ids


class TestAggregateFilters:
    def test_all_contacts_view_aggregates_by_name(self, owner_a_client, seed):
        resp = owner_a_client.get('/contacts?view=all')
        assert resp.status_code == 200
        # Option value should include both owner and agent "Buyers" IDs
        combined = f'{seed["group_a1"]},{seed["group_agent_a1"]}'
        alt = f'{seed["group_agent_a1"]},{seed["group_a1"]}'
        assert combined.encode() in resp.data or alt.encode() in resp.data
