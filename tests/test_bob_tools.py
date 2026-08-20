"""Tests for B.O.B.'s CRM tool layer.

The safety claims this feature rests on are all asserted here: tenant and
ownership isolation, that high-risk writes cannot apply without confirmation,
that identity can never come from the model, and that AI-created records emit
the same activation events as manual ones.

Run with: python -m pytest tests/test_bob_tools.py -v
"""
import os
import sys
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models import (
    ActivationEvent, BobAction, Contact, Interaction, Task, UserTodo, db,
)
from services.bob_tools import (
    CONFIRM_INTERACTIVE,
    CONFIRM_PRECLEARED,
    RISK_HIGH_WRITE,
    RISK_LOW_WRITE,
    RISK_READ,
    BobContext,
    confirm_action,
    dispatch,
    openai_tool_schemas,
    reject_action,
    undo_action,
)
from services.bob_tools.common import ToolError, due_datetime_utc
from services.bob_tools.registry import TOOLS, TOOLS_BY_NAME, sanitize_arguments


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_shared_caches():
    """Drop the process-wide lookup cache after each test in this module.

    ``services/cache_helpers`` memoizes live ORM instances for five minutes.
    Tests here open and close app contexts, which removes the SQLAlchemy session
    and leaves those cached instances detached, breaking unrelated tests that
    read them later.
    """
    yield
    from services.cache_helpers import _cache
    _cache.clear()


# Contexts are built from IDs rather than ORM objects so the fixtures need no
# app context of their own, which keeps them from tearing down the session.
@pytest.fixture()
def ctx_owner_a(seed):
    """Context for the Org A owner, who has org-admin visibility."""
    return BobContext(
        user_id=seed['owner_a'], organization_id=seed['org_a'],
        org_role='owner', is_org_admin=True,
    )


@pytest.fixture()
def ctx_agent_a(seed):
    """Context for a plain agent in Org A, who sees only their own records."""
    return BobContext(
        user_id=seed['agent_a'], organization_id=seed['org_a'],
        org_role='agent', is_org_admin=False,
    )


@pytest.fixture()
def ctx_owner_b(seed):
    """Context for a different tenant entirely."""
    return BobContext(
        user_id=seed['owner_b'], organization_id=seed['org_b'],
        org_role='owner', is_org_admin=True,
    )


def _unique(prefix):
    return f'{prefix}{datetime.utcnow().strftime("%H%M%S%f")}'


@pytest.fixture()
def scratch_contact(app, ctx_owner_a):
    """A throwaway contact with one task, owned by the Org A owner.

    Mutating tests use this instead of the shared seed rows. Handlers commit, so
    anything they change on seed data would leak into every later test module.
    """
    with app.app_context():
        created = dispatch('create_contact', {
            'first_name': 'Scratch', 'last_name': _unique('Case'),
            'email': f'{_unique("scratch")}@test.com',
            'city': 'Houston', 'state': 'TX',
            'group_names': ['Buyers'],
        }, ctx_owner_a)
        contact_id = created.data['contact']['contact_id']

        dispatch('create_task', {
            'contact_id': contact_id, 'subject': 'Scratch follow-up',
            'due_date': (ctx_owner_a.today() + timedelta(days=2)).isoformat(),
        }, ctx_owner_a)

    yield contact_id

    with app.app_context():
        contact = Contact.query.get(contact_id)
        if contact is not None:
            Task.query.filter_by(contact_id=contact_id).delete(
                synchronize_session=False,
            )
            Interaction.query.filter_by(contact_id=contact_id).delete(
                synchronize_session=False,
            )
            contact.groups = []
            db.session.flush()
            db.session.delete(contact)
            db.session.commit()


# ---------------------------------------------------------------------------
# Registry hygiene
# ---------------------------------------------------------------------------

class TestRegistryHygiene:
    """The schemas are product surface: the model reads them on every call."""

    def test_every_tool_is_well_formed(self):
        for tool in TOOLS:
            assert tool.risk in (RISK_READ, RISK_LOW_WRITE, RISK_HIGH_WRITE), tool.name
            assert tool.parameters['type'] == 'object', tool.name
            assert 'properties' in tool.parameters, tool.name
            assert callable(tool.handler), tool.name

    def test_descriptions_are_substantive(self):
        for tool in TOOLS:
            assert len(tool.description) > 80, (
                f'{tool.name} description is too thin to guide the model'
            )

    def test_every_parameter_is_documented(self):
        for tool in TOOLS:
            for param, spec in tool.parameters['properties'].items():
                assert 'type' in spec, f'{tool.name}.{param} has no type'
                if 'enum' not in spec:
                    assert spec.get('description'), (
                        f'{tool.name}.{param} has no description'
                    )

    def test_required_params_are_declared_properties(self):
        for tool in TOOLS:
            declared = set(tool.parameters['properties'])
            missing = set(tool.parameters.get('required', [])) - declared
            assert not missing, f'{tool.name} requires undeclared {missing}'

    def test_high_risk_tools_have_a_preview(self):
        for tool in TOOLS:
            if tool.risk == RISK_HIGH_WRITE:
                assert tool.preview is not None, (
                    f'{tool.name} needs a preview so the agent knows what they approve'
                )

    def test_no_tool_exposes_identity_parameters(self):
        """Identity comes from BobContext, never from the model."""
        forbidden = {'organization_id', 'user_id', 'assigned_to_id', 'created_by_id'}
        for tool in TOOLS:
            leaked = set(tool.parameters['properties']) & forbidden
            assert not leaked, f'{tool.name} exposes {leaked} to the model'

    def test_openai_schema_shape(self):
        schemas = openai_tool_schemas()
        assert len(schemas) == len(TOOLS)
        for schema in schemas:
            assert schema['type'] == 'function'
            assert set(schema['function']) == {'name', 'description', 'parameters'}


class TestArgumentSanitization:
    def test_identity_arguments_are_dropped(self):
        tool = TOOLS_BY_NAME['create_contact']
        clean = sanitize_arguments(tool, {
            'first_name': 'Sarah',
            'organization_id': 999,
            'user_id': 999,
            'created_by_id': 999,
        })
        assert clean == {'first_name': 'Sarah'}

    def test_undeclared_arguments_are_dropped(self):
        tool = TOOLS_BY_NAME['create_task']
        clean = sanitize_arguments(tool, {
            'contact_id': 5, 'subject': 'x', 'due_date': '2026-08-06',
            'is_admin': True, 'sql': 'DROP TABLE contact',
        })
        assert set(clean) == {'contact_id', 'subject', 'due_date'}

    def test_non_dict_arguments_become_empty(self):
        tool = TOOLS_BY_NAME['get_agenda']
        assert sanitize_arguments(tool, 'nonsense') == {}


# ---------------------------------------------------------------------------
# Context
# ---------------------------------------------------------------------------

class TestBobContext:
    def test_requires_an_organization(self, app):
        class Orphan:
            id = 1
            organization_id = None

        with pytest.raises(ValueError):
            BobContext.from_user(Orphan())

    def test_requires_a_persisted_user(self, app):
        with pytest.raises(ValueError):
            BobContext.from_user(None)

    def test_from_user_derives_admin_visibility(self, app, seed):
        from models import User

        with app.app_context():
            owner = BobContext.from_user(User.query.get(seed['owner_a']))
            agent = BobContext.from_user(User.query.get(seed['agent_a']))

        assert owner.is_org_admin is True
        assert owner.organization_id == seed['org_a']
        assert agent.is_org_admin is False
        assert agent.surface == 'bob_chat'

    def test_load_user_is_org_scoped(self, app, seed, ctx_agent_a):
        """A context pointed at the wrong org must not resolve a user."""
        with app.app_context():
            crossed = BobContext(
                user_id=seed['agent_a'],
                organization_id=seed['org_b'],
            )
            assert crossed.load_user() is None


# ---------------------------------------------------------------------------
# Tenant and ownership isolation
# ---------------------------------------------------------------------------

class TestIsolation:
    def test_cannot_read_another_orgs_contact(self, app, seed, ctx_owner_a):
        with app.app_context():
            result = dispatch('get_contact', {'contact_id': seed['contact_b']},
                              ctx_owner_a)
        assert result.ok is False
        assert 'contact_b' not in (result.error or '')

    def test_cannot_read_another_orgs_task(self, app, seed, ctx_owner_a):
        with app.app_context():
            result = dispatch('list_tasks', {'status': 'all'}, ctx_owner_a)
        assert result.ok is True
        subjects = [t['subject'] for t in result.data['tasks']]
        assert 'Task B Only' not in subjects

    def test_agent_cannot_read_another_agents_contact(self, app, seed, ctx_agent_a):
        with app.app_context():
            result = dispatch('get_contact', {'contact_id': seed['contact_a']},
                              ctx_agent_a)
        assert result.ok is False

    def test_org_admin_can_read_across_the_org(self, app, seed, ctx_owner_a):
        with app.app_context():
            result = dispatch('get_contact', {'contact_id': seed['contact_a2']},
                              ctx_owner_a)
        assert result.ok is True
        assert result.data['name'] == 'John Smith'

    def test_search_does_not_leak_across_orgs(self, app, seed, ctx_owner_b):
        with app.app_context():
            result = dispatch('search_contacts', {'query': 'Jane'}, ctx_owner_b)
        assert result.ok is True
        assert result.data['contacts'] == []

    def test_agent_only_sees_their_own_tasks(self, app, seed, ctx_agent_a):
        """A non-admin must never be shown a teammate's task, in any surface."""
        with app.app_context():
            result = dispatch('list_tasks', {'status': 'all'}, ctx_agent_a)

        assert result.ok is True
        subjects = [t['subject'] for t in result.data['tasks']]
        assert 'Follow up John' in subjects   # assigned to agent_a
        assert 'Call Jane' not in subjects    # assigned to owner_a
        assert 'Task B Only' not in subjects  # different org

    def test_agent_agenda_excludes_teammate_tasks(self, app, seed, ctx_agent_a):
        with app.app_context():
            result = dispatch('get_agenda', {}, ctx_agent_a)

        assert result.ok is True
        subjects = [
            task['subject']
            for bucket in ('overdue', 'due_today', 'upcoming')
            for task in result.data.get(bucket, [])
        ]
        assert 'Call Jane' not in subjects

    def test_agent_cannot_read_a_teammates_task_by_id(self, app, seed,
                                                      ctx_agent_a):
        """Guessing an id must not work either."""
        with app.app_context():
            result = dispatch('complete_task', {'task_id': seed['task_a']},
                              ctx_agent_a)
        assert result.ok is False

    def test_org_admin_still_sees_the_whole_org(self, app, seed, ctx_owner_a):
        with app.app_context():
            result = dispatch('list_tasks', {'status': 'all'}, ctx_owner_a)

        subjects = [t['subject'] for t in result.data['tasks']]
        assert 'Call Jane' in subjects
        assert 'Follow up John' in subjects

    def test_cannot_write_to_another_orgs_contact(self, app, seed, ctx_owner_b):
        with app.app_context():
            result = dispatch('log_interaction', {
                'contact_id': seed['contact_a'], 'type': 'call',
            }, ctx_owner_b)
        assert result.ok is False

        with app.app_context():
            assert Interaction.query.filter_by(
                contact_id=seed['contact_a'],
            ).count() == 0


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

class TestReadHandlers:
    def test_search_by_name(self, app, seed, ctx_owner_a):
        with app.app_context():
            result = dispatch('search_contacts', {'query': 'Jane'}, ctx_owner_a)
        assert result.ok is True
        assert any(c['name'] == 'Jane Doe' for c in result.data['contacts'])

    def test_search_by_phone_digits(self, app, seed, ctx_owner_a):
        with app.app_context():
            result = dispatch('search_contacts', {'query': '(555) 111-0000'},
                              ctx_owner_a)
        assert any(c['name'] == 'Jane Doe' for c in result.data['contacts'])

    def test_search_limit_is_clamped(self, app, seed, ctx_owner_a):
        with app.app_context():
            marker = _unique('ListCap')
            contacts = [
                Contact(
                    organization_id=ctx_owner_a.organization_id,
                    user_id=ctx_owner_a.user_id,
                    created_by_id=ctx_owner_a.user_id,
                    first_name=marker,
                    last_name=f'Person{i:02d}',
                )
                for i in range(55)
            ]
            db.session.add_all(contacts)
            db.session.commit()
            ids = [contact.id for contact in contacts]
            try:
                result = dispatch(
                    'search_contacts',
                    {'query': marker, 'limit': 9999},
                    ctx_owner_a,
                )
                assert result.data['total_matching'] == 55
                assert len(result.data['contacts']) == 50
                assert result.data['more_available'] is True
            finally:
                Contact.query.filter(Contact.id.in_(ids)).delete(
                    synchronize_session=False,
                )
                db.session.commit()

    def test_get_contact_includes_related_records(self, app, seed, ctx_owner_a):
        with app.app_context():
            result = dispatch('get_contact', {'contact_id': seed['contact_a']},
                              ctx_owner_a)
        assert 'recent_tasks' in result.data
        assert 'recent_interactions' in result.data

    def test_agenda_buckets_by_local_today(self, app, seed, ctx_owner_a):
        with app.app_context():
            result = dispatch('get_agenda', {}, ctx_owner_a)
        assert result.ok is True
        assert set(['overdue', 'due_today', 'upcoming']) <= set(result.data)
        assert result.data['timezone'] == ctx_owner_a.timezone

    def test_list_tasks_rejects_bad_status(self, app, seed, ctx_owner_a):
        with app.app_context():
            result = dispatch('list_tasks', {'status': 'whatever'}, ctx_owner_a)
        assert result.ok is False
        assert 'status must be one of' in result.error

    def test_list_task_types_reports_subtypes(self, app, seed, ctx_owner_a):
        with app.app_context():
            result = dispatch('list_task_types', {}, ctx_owner_a)
        names = {t['type'] for t in result.data['task_types']}
        assert 'Call' in names

    def test_reads_do_not_write_an_audit_row(self, app, seed, ctx_owner_a):
        with app.app_context():
            before = BobAction.query.count()
            dispatch('search_contacts', {'query': 'Jane'}, ctx_owner_a)
            assert BobAction.query.count() == before


# ---------------------------------------------------------------------------
# Low-risk writes
# ---------------------------------------------------------------------------

class TestCreateContact:
    def test_creates_and_audits(self, app, seed, ctx_owner_a):
        email = f'{_unique("new")}@test.com'
        with app.app_context():
            result = dispatch('create_contact', {
                'first_name': 'Sarah', 'last_name': 'Nguyen', 'email': email,
            }, ctx_owner_a)

            assert result.ok is True
            assert result.data['created'] is True
            assert result.undoable is True
            assert result.action_id is not None

            contact = Contact.query.filter_by(email=email).first()
            assert contact is not None
            assert contact.organization_id == ctx_owner_a.organization_id
            assert contact.user_id == ctx_owner_a.user_id
            assert contact.created_by_id == ctx_owner_a.user_id

            action = BobAction.query.get(result.action_id)
            assert action.status == BobAction.STATUS_EXECUTED
            assert action.tool_name == 'create_contact'
            assert action.surface == 'bob_chat'

    def test_emits_activation_events(self, app, seed, ctx_owner_a):
        email = f'{_unique("act")}@test.com'
        with app.app_context():
            before = ActivationEvent.query.filter_by(
                event=ActivationEvent.CONTACT_CREATED,
                user_id=ctx_owner_a.user_id,
            ).count()

            dispatch('create_contact', {
                'first_name': 'Activation', 'last_name': 'Case', 'email': email,
            }, ctx_owner_a)

            events = ActivationEvent.query.filter_by(
                event=ActivationEvent.CONTACT_CREATED,
                user_id=ctx_owner_a.user_id,
            ).all()
            assert len(events) == before + 1
            assert events[-1].event_data.get('source') == 'bob_chat'

    def test_duplicate_returns_existing_without_creating(self, app, seed, ctx_owner_a):
        with app.app_context():
            before = Contact.query.filter_by(
                organization_id=ctx_owner_a.organization_id,
            ).count()

            result = dispatch('create_contact', {
                'first_name': 'Jane', 'last_name': 'Doe', 'email': 'jane@test.com',
            }, ctx_owner_a)

            assert result.ok is True
            assert result.data['created'] is False
            assert result.data['reason'] == 'duplicate'
            assert Contact.query.filter_by(
                organization_id=ctx_owner_a.organization_id,
            ).count() == before

    def test_requires_a_first_name(self, app, seed, ctx_owner_a):
        with app.app_context():
            result = dispatch('create_contact', {'last_name': 'Nameless'},
                              ctx_owner_a)
        assert result.ok is False
        assert 'first name' in result.error.lower()

    def test_rejects_unusable_phone(self, app, seed, ctx_owner_a):
        with app.app_context():
            result = dispatch('create_contact', {
                'first_name': 'Bad', 'phone': '12',
            }, ctx_owner_a)
        assert result.ok is False
        assert 'phone' in result.error.lower()

    def test_rejects_malformed_email(self, app, seed, ctx_owner_a):
        with app.app_context():
            result = dispatch('create_contact', {
                'first_name': 'Bad', 'email': 'not-an-email',
            }, ctx_owner_a)
        assert result.ok is False

    def test_unknown_group_names_are_reported_not_created(self, app, seed, ctx_owner_a):
        email = f'{_unique("grp")}@test.com'
        with app.app_context():
            result = dispatch('create_contact', {
                'first_name': 'Group', 'last_name': 'Test', 'email': email,
                'group_names': ['Buyers', 'Nonexistent Group'],
            }, ctx_owner_a)

            assert result.ok is True
            assert result.data['groups_not_found'] == ['Nonexistent Group']
            contact = Contact.query.filter_by(email=email).first()
            assert [g.name for g in contact.groups] == ['Buyers']

    def test_undo_removes_the_contact(self, app, seed, ctx_owner_a):
        email = f'{_unique("undo")}@test.com'
        with app.app_context():
            result = dispatch('create_contact', {
                'first_name': 'Undo', 'last_name': 'Me', 'email': email,
            }, ctx_owner_a)
            contact_id = Contact.query.filter_by(email=email).first().id

            undone = undo_action(result.action_id, ctx_owner_a)
            assert undone.ok is True
            assert Contact.query.get(contact_id) is None
            assert BobAction.query.get(result.action_id).status == \
                BobAction.STATUS_UNDONE

    def test_undo_refuses_when_work_was_attached(self, app, seed, ctx_owner_a):
        email = f'{_unique("attached")}@test.com'
        with app.app_context():
            result = dispatch('create_contact', {
                'first_name': 'Has', 'last_name': 'Tasks', 'email': email,
            }, ctx_owner_a)
            contact = Contact.query.filter_by(email=email).first()

            dispatch('create_task', {
                'contact_id': contact.id, 'subject': 'Call them',
                'due_date': (datetime.utcnow() + timedelta(days=2)).strftime('%Y-%m-%d'),
            }, ctx_owner_a)

            undone = undo_action(result.action_id, ctx_owner_a)
            assert undone.ok is False
            assert Contact.query.get(contact.id) is not None

    def test_cannot_undo_another_users_action(self, app, seed, ctx_owner_a, ctx_owner_b):
        email = f'{_unique("cross")}@test.com'
        with app.app_context():
            result = dispatch('create_contact', {
                'first_name': 'Cross', 'last_name': 'Tenant', 'email': email,
            }, ctx_owner_a)

            undone = undo_action(result.action_id, ctx_owner_b)
            assert undone.ok is False
            assert Contact.query.filter_by(email=email).first() is not None


class TestCreateTask:
    def _tomorrow(self, ctx):
        return (ctx.today() + timedelta(days=1)).isoformat()

    def test_creates_against_a_contact(self, app, seed, ctx_owner_a):
        with app.app_context():
            result = dispatch('create_task', {
                'contact_id': seed['contact_a'],
                'subject': 'Call about Conroe',
                'due_date': self._tomorrow(ctx_owner_a),
                'type': 'Call', 'subtype': 'Follow Up',
                'priority': 'high',
            }, ctx_owner_a)

            assert result.ok is True
            task = Task.query.get(result.data['task']['task_id'])
            assert task.organization_id == ctx_owner_a.organization_id
            assert task.assigned_to_id == ctx_owner_a.user_id
            assert task.created_by_id == ctx_owner_a.user_id
            assert task.priority == 'high'

    def test_undated_task_lands_at_end_of_local_day(self, app, seed, ctx_owner_a):
        with app.app_context():
            result = dispatch('create_task', {
                'contact_id': seed['contact_a'],
                'subject': 'End of day check',
                'due_date': '2026-08-06',
            }, ctx_owner_a)

            task = Task.query.get(result.data['task']['task_id'])
            # 23:59:59 America/Chicago (CDT, UTC-5) is 04:59:59 UTC next day.
            assert task.due_date.hour == 4
            assert task.due_date.minute == 59
            assert task.due_date.date().isoformat() == '2026-08-07'
            assert task.scheduled_time is None

    def test_scheduled_time_is_stored_and_converted(self, app, seed, ctx_owner_a):
        with app.app_context():
            result = dispatch('create_task', {
                'contact_id': seed['contact_a'],
                'subject': 'Morning call',
                'due_date': '2026-08-06',
                'scheduled_time': '09:30',
            }, ctx_owner_a)

            task = Task.query.get(result.data['task']['task_id'])
            assert task.scheduled_time is not None
            assert task.due_date.hour == 14  # 09:30 CDT
            assert task.due_date.minute == 30

    def test_reported_due_date_is_the_agents_local_date(self, app, seed, ctx_owner_a):
        """The UTC value rolls to the next day; the agent must not see that."""
        with app.app_context():
            result = dispatch('create_task', {
                'contact_id': seed['contact_a'],
                'subject': 'Local date check',
                'due_date': '2026-08-06',
            }, ctx_owner_a)
        assert result.data['task']['due_date'] == '2026-08-06'

    def test_follow_up_emits_activation_events(self, app, seed, ctx_owner_a):
        with app.app_context():
            dispatch('create_task', {
                'contact_id': seed['contact_a'],
                'subject': 'Follow up properly',
                'due_date': self._tomorrow(ctx_owner_a),
                'type': 'Call', 'subtype': 'Follow Up',
            }, ctx_owner_a)

            created = ActivationEvent.query.filter_by(
                event=ActivationEvent.TASK_CREATED,
                user_id=ctx_owner_a.user_id,
            ).all()
            assert created
            assert created[-1].event_data.get('source') == 'bob_chat'

            follow_up = ActivationEvent.query.filter_by(
                event=ActivationEvent.FOLLOW_UP_CREATED,
                user_id=ctx_owner_a.user_id,
            ).first()
            assert follow_up is not None

    def test_requires_an_existing_contact(self, app, seed, ctx_owner_a):
        with app.app_context():
            result = dispatch('create_task', {
                'contact_id': 999999, 'subject': 'Ghost',
                'due_date': self._tomorrow(ctx_owner_a),
            }, ctx_owner_a)
        assert result.ok is False
        assert 'search_contacts' in result.error

    def test_rejects_malformed_due_date(self, app, seed, ctx_owner_a):
        with app.app_context():
            result = dispatch('create_task', {
                'contact_id': seed['contact_a'], 'subject': 'Bad date',
                'due_date': 'next Thursday',
            }, ctx_owner_a)
        assert result.ok is False
        assert 'YYYY-MM-DD' in result.error

    def test_rejects_bad_priority(self, app, seed, ctx_owner_a):
        with app.app_context():
            result = dispatch('create_task', {
                'contact_id': seed['contact_a'], 'subject': 'Bad priority',
                'due_date': self._tomorrow(ctx_owner_a), 'priority': 'urgent',
            }, ctx_owner_a)
        assert result.ok is False

    def test_unknown_type_falls_back_rather_than_failing(self, app, seed, ctx_owner_a):
        with app.app_context():
            result = dispatch('create_task', {
                'contact_id': seed['contact_a'], 'subject': 'Odd type',
                'due_date': self._tomorrow(ctx_owner_a),
                'type': 'Telepathy', 'subtype': 'Vibes',
            }, ctx_owner_a)
        assert result.ok is True


class TestCompleteTask:
    def test_completes_and_can_be_reopened(self, app, seed, ctx_owner_a):
        with app.app_context():
            created = dispatch('create_task', {
                'contact_id': seed['contact_a'], 'subject': 'Finish me',
                'due_date': (ctx_owner_a.today() + timedelta(days=1)).isoformat(),
            }, ctx_owner_a)
            task_id = created.data['task']['task_id']

            done = dispatch('complete_task', {
                'task_id': task_id, 'outcome': 'Left a voicemail',
            }, ctx_owner_a)
            assert done.ok is True

            task = Task.query.get(task_id)
            assert task.status == 'completed'
            assert task.completed_at is not None
            assert task.outcome == 'Left a voicemail'

            undone = undo_action(done.action_id, ctx_owner_a)
            assert undone.ok is True
            task = Task.query.get(task_id)
            assert task.status == 'pending'
            assert task.completed_at is None

    def test_emits_completion_activation_event(self, app, seed, ctx_owner_a):
        with app.app_context():
            created = dispatch('create_task', {
                'contact_id': seed['contact_a'], 'subject': 'Event check',
                'due_date': (ctx_owner_a.today() + timedelta(days=1)).isoformat(),
            }, ctx_owner_a)
            before = ActivationEvent.query.filter_by(
                event=ActivationEvent.TASK_COMPLETED,
                user_id=ctx_owner_a.user_id,
            ).count()

            dispatch('complete_task', {
                'task_id': created.data['task']['task_id'],
            }, ctx_owner_a)

            assert ActivationEvent.query.filter_by(
                event=ActivationEvent.TASK_COMPLETED,
                user_id=ctx_owner_a.user_id,
            ).count() == before + 1

    def test_already_completed_is_reported_not_repeated(self, app, seed, ctx_owner_a):
        with app.app_context():
            created = dispatch('create_task', {
                'contact_id': seed['contact_a'], 'subject': 'Twice',
                'due_date': (ctx_owner_a.today() + timedelta(days=1)).isoformat(),
            }, ctx_owner_a)
            task_id = created.data['task']['task_id']

            dispatch('complete_task', {'task_id': task_id}, ctx_owner_a)
            second = dispatch('complete_task', {'task_id': task_id}, ctx_owner_a)

            assert second.ok is True
            assert second.data['already_completed'] is True

    def test_cannot_complete_another_agents_task(self, app, seed, ctx_owner_b):
        with app.app_context():
            result = dispatch('complete_task', {'task_id': seed['task_a']},
                              ctx_owner_b)
            assert result.ok is False
            assert Task.query.get(seed['task_a']).status == 'pending'


class TestLogInteraction:
    def test_advances_last_contact_date(self, app, seed, ctx_owner_a):
        today = ctx_owner_a.today()
        with app.app_context():
            result = dispatch('log_interaction', {
                'contact_id': seed['contact_a'], 'type': 'call',
                'notes': 'Talked through the Conroe comps',
            }, ctx_owner_a)

            assert result.ok is True
            contact = Contact.query.get(seed['contact_a'])
            assert contact.last_phone_call_date == today
            assert contact.last_contact_date == today

    def test_rejects_future_dates(self, app, seed, ctx_owner_a):
        future = (ctx_owner_a.today() + timedelta(days=3)).isoformat()
        with app.app_context():
            result = dispatch('log_interaction', {
                'contact_id': seed['contact_a'], 'type': 'call', 'date': future,
            }, ctx_owner_a)
        assert result.ok is False
        assert 'future' in result.error.lower()

    def test_rejects_unknown_type(self, app, seed, ctx_owner_a):
        with app.app_context():
            result = dispatch('log_interaction', {
                'contact_id': seed['contact_a'], 'type': 'telepathy',
            }, ctx_owner_a)
        assert result.ok is False

    def test_undo_removes_the_entry(self, app, seed, ctx_owner_a):
        with app.app_context():
            result = dispatch('log_interaction', {
                'contact_id': seed['contact_a'],
                'type': 'text', 'notes': 'Quick check-in',
            }, ctx_owner_a)
            interaction_id = result.data['interaction_id']

            undone = undo_action(result.action_id, ctx_owner_a)
            assert undone.ok is True
            assert Interaction.query.get(interaction_id) is None


@pytest.fixture()
def located_contacts(app, seed):
    """Contacts with real addresses, since the seed rows have none.

    Three in Houston for the owner, one in Dallas, and one Houston contact owned
    by a different agent so scope behaviour is observable.
    """
    created = []
    with app.app_context():
        rows = [
            ('Hank', 'Houstonian', 'Houston', 'TX', '77002', seed['owner_a']),
            ('Hilda', 'Heights', 'Houston', 'TX', '77008', seed['owner_a']),
            ('Hugo', 'Hobby', 'Houston', 'TX', '77061', seed['owner_a']),
            ('Dana', 'Dallasite', 'Dallas', 'TX', '75201', seed['owner_a']),
            ('Theo', 'Teammate', 'Houston', 'TX', '77002', seed['agent_a']),
        ]
        for first, last, city, state, zip_code, owner_id in rows:
            contact = Contact(
                organization_id=seed['org_a'], user_id=owner_id,
                created_by_id=owner_id, first_name=first, last_name=last,
                email=f'{first.lower()}{_unique("")}@test.com',
                city=city, state=state, zip_code=zip_code,
                street_address='1 Test Way',
            )
            db.session.add(contact)
            created.append(contact)
        db.session.commit()
        ids = [c.id for c in created]

    yield ids

    with app.app_context():
        for contact in Contact.query.filter(Contact.id.in_(ids)).all():
            contact.groups = []
        db.session.flush()
        Contact.query.filter(Contact.id.in_(ids)).delete(synchronize_session=False)
        db.session.commit()


class TestContactSearchByLocation:
    """The reported bug: "how many contacts in Houston" answered zero."""

    def test_free_text_matches_city(self, app, located_contacts, ctx_owner_a):
        with app.app_context():
            result = dispatch('search_contacts', {'query': 'houston'}, ctx_owner_a)

        assert result.data['total_matching'] == 3
        assert result.summary.startswith('3 contact(s)')

    def test_free_text_matches_zip(self, app, located_contacts, ctx_owner_a):
        with app.app_context():
            result = dispatch('search_contacts', {'query': '77008'}, ctx_owner_a)
        assert result.data['total_matching'] == 1

    def test_city_filter_is_exact_about_location(self, app, located_contacts,
                                                ctx_owner_a):
        with app.app_context():
            result = dispatch('search_contacts', {'city': 'Dallas'}, ctx_owner_a)

        assert result.data['total_matching'] == 1
        assert result.data['contacts'][0]['name'] == 'Dana Dallasite'

    def test_total_is_the_real_count_not_the_page_size(self, app,
                                                     located_contacts,
                                                     ctx_owner_a):
        """Reporting len(contacts) here is what produced a wrong answer."""
        with app.app_context():
            result = dispatch('search_contacts', {
                'city': 'Houston', 'limit': 1,
            }, ctx_owner_a)

        assert result.data['total_matching'] == 3
        assert result.data['returned'] == 1
        assert result.data['more_available'] is True

    def test_zip_prefix_matches_a_range(self, app, located_contacts, ctx_owner_a):
        with app.app_context():
            result = dispatch('count_contacts', {'zip_code': '770'}, ctx_owner_a)
        assert result.data['total'] == 3

    def test_name_search_still_works(self, app, seed, ctx_owner_a):
        with app.app_context():
            result = dispatch('search_contacts', {'query': 'Jane'}, ctx_owner_a)
        assert result.data['total_matching'] >= 1


class TestCountContacts:
    def test_plain_total(self, app, located_contacts, ctx_owner_a):
        with app.app_context():
            result = dispatch('count_contacts', {'city': 'Houston'}, ctx_owner_a)

        assert result.ok is True
        assert result.data['total'] == 3

    def test_breakdown_by_city_is_sorted_by_count(self, app, located_contacts,
                                                 ctx_owner_a):
        with app.app_context():
            result = dispatch('count_contacts', {'group_by': 'city'}, ctx_owner_a)

        breakdown = result.data['breakdown']
        counts = [row['count'] for row in breakdown]
        assert counts == sorted(counts, reverse=True)

        houston = next(r for r in breakdown if r['city'] == 'Houston')
        assert houston['count'] == 3

    def test_breakdown_by_zip(self, app, located_contacts, ctx_owner_a):
        with app.app_context():
            result = dispatch('count_contacts', {
                'city': 'Houston', 'group_by': 'zip_code',
            }, ctx_owner_a)

        zips = {row['zip_code'] for row in result.data['breakdown']}
        assert zips == {'77002', '77008', '77061'}

    def test_zip_plus_four_collapses_into_its_base_zip(self, app, seed,
                                                      ctx_owner_a):
        with app.app_context():
            contact = Contact(
                organization_id=seed['org_a'], user_id=seed['owner_a'],
                created_by_id=seed['owner_a'], first_name='Zed', last_name='Plus',
                city='Houston', state='TX', zip_code='77002-1234',
            )
            db.session.add(contact)
            db.session.commit()
            contact_id = contact.id

        try:
            with app.app_context():
                result = dispatch('count_contacts', {
                    'city': 'Houston', 'group_by': 'zip_code',
                }, ctx_owner_a)

            zips = {row['zip_code'] for row in result.data['breakdown']}
            assert '77002' in zips
            assert '77002-1234' not in zips
        finally:
            with app.app_context():
                Contact.query.filter_by(id=contact_id).delete()
                db.session.commit()

    def test_breakdown_by_group(self, app, seed, ctx_owner_a):
        with app.app_context():
            result = dispatch('count_contacts', {'group_by': 'group'}, ctx_owner_a)

        assert result.ok is True
        assert any(row['group'] == 'Buyers' for row in result.data['breakdown'])

    def test_contacts_without_a_city_are_labelled(self, app, seed, ctx_owner_a):
        with app.app_context():
            result = dispatch('count_contacts', {'group_by': 'city'}, ctx_owner_a)

        labels = {row['city'] for row in result.data['breakdown']}
        assert '(not set)' in labels

    def test_group_name_filter(self, app, seed, ctx_owner_a):
        with app.app_context():
            result = dispatch('count_contacts', {'group_name': 'Buyers'},
                              ctx_owner_a)
        assert result.data['total'] >= 1

    def test_presence_and_group_assignment_filters(
            self, app, seed, ctx_owner_a):
        with app.app_context():
            marker = _unique('Presence')
            active_group = Contact.query.get(seed['contact_a']).groups[0]
            ungrouped_missing = Contact(
                organization_id=ctx_owner_a.organization_id,
                user_id=ctx_owner_a.user_id,
                created_by_id=ctx_owner_a.user_id,
                first_name=marker,
                last_name='Ungrouped',
                email=None,
                phone=None,
            )
            grouped_present = Contact(
                organization_id=ctx_owner_a.organization_id,
                user_id=ctx_owner_a.user_id,
                created_by_id=ctx_owner_a.user_id,
                first_name=marker,
                last_name='Present',
                email='present@test.com',
                phone='5551119999',
            )
            grouped_blank = Contact(
                organization_id=ctx_owner_a.organization_id,
                user_id=ctx_owner_a.user_id,
                created_by_id=ctx_owner_a.user_id,
                first_name=marker,
                last_name='Blank',
                email='blank@test.com',
                phone='   ',
            )
            grouped_present.groups.append(active_group)
            grouped_blank.groups.append(active_group)
            contacts = [ungrouped_missing, grouped_present, grouped_blank]
            db.session.add_all(contacts)
            db.session.commit()
            try:
                ungrouped = dispatch('count_contacts', {
                    'query': marker,
                    'group_status': 'unassigned',
                }, ctx_owner_a)
                assert ungrouped.data['total'] == 1

                missing_phone = dispatch('count_contacts', {
                    'query': marker,
                    'missing_fields': ['phone'],
                }, ctx_owner_a)
                assert missing_phone.data['total'] == 2

                missing_phone_and_email = dispatch('count_contacts', {
                    'query': marker,
                    'missing_fields': ['phone', 'email'],
                }, ctx_owner_a)
                assert missing_phone_and_email.data['total'] == 1

                present_phone = dispatch('search_contacts', {
                    'query': marker,
                    'present_fields': ['phone'],
                }, ctx_owner_a)
                assert present_phone.data['total_matching'] == 1
                assert present_phone.data['contacts'][0]['name'].endswith(
                    'Present'
                )
            finally:
                for contact in contacts:
                    contact.groups.clear()
                    db.session.delete(contact)
                db.session.commit()

    def test_unknown_group_by_is_refused(self, app, seed, ctx_owner_a):
        with app.app_context():
            result = dispatch('count_contacts', {'group_by': 'favourite_colour'},
                              ctx_owner_a)
        assert result.ok is False
        assert 'group_by' in result.error


class TestContactScope:
    """"My contacts" must not silently mean "the whole company"."""

    def test_defaults_to_the_agents_own_contacts(self, app, located_contacts,
                                                ctx_owner_a):
        with app.app_context():
            result = dispatch('count_contacts', {'city': 'Houston'}, ctx_owner_a)

        # Theo Teammate is also in Houston but belongs to another agent.
        assert result.data['total'] == 3
        assert result.data['scope'] == 'mine'

    def test_admin_can_ask_for_the_whole_org(self, app, located_contacts,
                                            ctx_owner_a):
        with app.app_context():
            result = dispatch('count_contacts', {
                'city': 'Houston', 'scope': 'organization',
            }, ctx_owner_a)

        assert result.data['total'] == 4
        assert result.data['scope'] == 'organization'

    def test_non_admin_asking_org_wide_is_told_it_was_narrowed(
            self, app, located_contacts, ctx_agent_a):
        with app.app_context():
            result = dispatch('count_contacts', {
                'city': 'Houston', 'scope': 'organization',
            }, ctx_agent_a)

        assert result.data['total'] == 1
        assert result.data['scope'] == 'mine'
        assert 'scope_note' in result.data

    def test_search_reports_its_scope(self, app, located_contacts, ctx_owner_a):
        with app.app_context():
            result = dispatch('search_contacts', {'city': 'Houston'}, ctx_owner_a)
        assert result.data['scope'] == 'mine'

    def test_invalid_scope_is_refused(self, app, seed, ctx_owner_a):
        with app.app_context():
            result = dispatch('count_contacts', {'scope': 'everyone'}, ctx_owner_a)
        assert result.ok is False

    def test_get_contact_keeps_full_admin_reach(self, app, seed, ctx_owner_a):
        """Looking up a known id is not a browse, so admins keep org reach."""
        with app.app_context():
            result = dispatch('get_contact', {'contact_id': seed['contact_a2']},
                              ctx_owner_a)
        assert result.ok is True


class TestAppendContactNote:
    """Appending is low risk on purpose: it never destroys existing notes."""

    def test_is_a_low_write_so_it_needs_no_confirmation(self):
        assert TOOLS_BY_NAME['append_contact_note'].risk == RISK_LOW_WRITE

    def test_appends_without_touching_existing_notes(self, app, scratch_contact,
                                                    ctx_owner_a):
        with app.app_context():
            Contact.query.get(scratch_contact).notes = 'Prefers morning calls.'
            db.session.commit()

            result = dispatch('append_contact_note', {
                'contact_id': scratch_contact, 'note': 'Wants a pool',
            }, ctx_owner_a)

            assert result.ok is True
            assert result.requires_confirmation is False
            notes = Contact.query.get(scratch_contact).notes
            assert notes.startswith('Prefers morning calls.')
            assert 'Wants a pool' in notes

    def test_stamps_the_line_with_the_local_date(self, app, scratch_contact,
                                                 ctx_owner_a):
        with app.app_context():
            result = dispatch('append_contact_note', {
                'contact_id': scratch_contact, 'note': 'Pre-approved',
            }, ctx_owner_a)

        stamp = ctx_owner_a.today().strftime('%b %d, %Y')
        assert result.data['note_added'] == f'[{stamp}] Pre-approved'

    def test_first_note_does_not_start_with_a_blank_line(self, app,
                                                        scratch_contact,
                                                        ctx_owner_a):
        with app.app_context():
            Contact.query.get(scratch_contact).notes = None
            db.session.commit()

            dispatch('append_contact_note', {
                'contact_id': scratch_contact, 'note': 'First',
            }, ctx_owner_a)

            assert Contact.query.get(scratch_contact).notes.startswith('[')

    def test_blank_note_is_refused(self, app, scratch_contact, ctx_owner_a):
        with app.app_context():
            result = dispatch('append_contact_note', {
                'contact_id': scratch_contact, 'note': '   ',
            }, ctx_owner_a)
        assert result.ok is False

    def test_refuses_once_notes_are_full(self, app, scratch_contact, ctx_owner_a):
        from services.bob_tools.common import MAX_CONTACT_NOTES

        with app.app_context():
            Contact.query.get(scratch_contact).notes = 'x' * MAX_CONTACT_NOTES
            db.session.commit()

            result = dispatch('append_contact_note', {
                'contact_id': scratch_contact, 'note': 'One more',
            }, ctx_owner_a)

            assert result.ok is False
            assert 'full' in result.error.lower()

    def test_cannot_annotate_another_tenants_contact(self, app, seed, ctx_owner_b):
        with app.app_context():
            result = dispatch('append_contact_note', {
                'contact_id': seed['contact_a'], 'note': 'Snooping',
            }, ctx_owner_b)
        assert result.ok is False

    def test_undo_restores_the_previous_body(self, app, scratch_contact,
                                            ctx_owner_a):
        with app.app_context():
            Contact.query.get(scratch_contact).notes = 'Original body.'
            db.session.commit()

            result = dispatch('append_contact_note', {
                'contact_id': scratch_contact, 'note': 'Added line',
            }, ctx_owner_a)

            undone = undo_action(result.action_id, ctx_owner_a)
            assert undone.ok is True
            assert Contact.query.get(scratch_contact).notes == 'Original body.'

    def test_undo_leaves_notes_alone_if_they_changed_since(self, app,
                                                          scratch_contact,
                                                          ctx_owner_a):
        """A later edit must not be clobbered by a stale undo."""
        with app.app_context():
            result = dispatch('append_contact_note', {
                'contact_id': scratch_contact, 'note': 'From BOB',
            }, ctx_owner_a)

            contact = Contact.query.get(scratch_contact)
            contact.notes = 'The agent rewrote this by hand.'
            db.session.commit()

            undone = undo_action(result.action_id, ctx_owner_a)
            assert undone.ok is False
            assert Contact.query.get(scratch_contact).notes == \
                'The agent rewrote this by hand.'


class TestPersonalTodos:
    @pytest.fixture(autouse=True)
    def _clean_up_todos(self, app):
        """Drop rows these tests create; the handlers commit.

        tests/test_user_todo.py works against the same users, so leftovers here
        would change its counts.
        """
        with app.app_context():
            before = {row.id for row in UserTodo.query.all()}
        yield
        with app.app_context():
            UserTodo.query.filter(~UserTodo.id.in_(before or {-1})).delete(
                synchronize_session=False,
            )
            db.session.commit()

    def test_add_then_list(self, app, ctx_owner_a):
        with app.app_context():
            added = dispatch('add_todo', {'text': 'Order more yard signs'},
                             ctx_owner_a)
            assert added.ok is True
            assert added.requires_confirmation is False

            listed = dispatch('list_todos', {}, ctx_owner_a)
            texts = [t['text'] for t in listed.data['todos']]
            assert 'Order more yard signs' in texts

    def test_blank_text_is_refused(self, app, ctx_owner_a):
        with app.app_context():
            result = dispatch('add_todo', {'text': '  '}, ctx_owner_a)
        assert result.ok is False

    def test_adding_the_same_item_twice_does_not_duplicate(self, app, ctx_owner_a):
        with app.app_context():
            dispatch('add_todo', {'text': 'Renew license'}, ctx_owner_a)
            again = dispatch('add_todo', {'text': 'renew license'}, ctx_owner_a)

            assert again.data['already_present'] is True
            listed = dispatch('list_todos', {}, ctx_owner_a)
            matches = [t for t in listed.data['todos']
                       if t['text'].lower() == 'renew license']
            assert len(matches) == 1

    def test_complete_by_id(self, app, ctx_owner_a):
        with app.app_context():
            added = dispatch('add_todo', {'text': 'Drop off lockbox'}, ctx_owner_a)
            todo_id = added.data['todo']['todo_id']

            done = dispatch('complete_todo', {'todo_id': todo_id}, ctx_owner_a)
            assert done.ok is True

            open_now = dispatch('list_todos', {}, ctx_owner_a)
            assert todo_id not in [t['todo_id'] for t in open_now.data['todos']]

            with_done = dispatch('list_todos', {'include_completed': True},
                                 ctx_owner_a)
            assert todo_id in [t['todo_id'] for t in with_done.data['completed']]

    def test_complete_by_partial_text(self, app, ctx_owner_a):
        with app.app_context():
            dispatch('add_todo', {'text': 'Pick up the lockbox from 12 Oak'},
                     ctx_owner_a)
            done = dispatch('complete_todo', {'text': 'lockbox'}, ctx_owner_a)

            assert done.ok is True
            assert done.data['todo']['completed'] is True

    def test_ambiguous_text_asks_instead_of_guessing(self, app, ctx_owner_a):
        with app.app_context():
            dispatch('add_todo', {'text': 'Call the title company'}, ctx_owner_a)
            dispatch('add_todo', {'text': 'Call the inspector'}, ctx_owner_a)

            result = dispatch('complete_todo', {'text': 'Call the'}, ctx_owner_a)

            assert result.ok is False
            assert 'match' in result.error.lower()

    def test_unmatched_text_is_an_error(self, app, ctx_owner_a):
        with app.app_context():
            result = dispatch('complete_todo', {'text': 'nothing like this'},
                              ctx_owner_a)
        assert result.ok is False

    def test_identifier_is_required(self, app, ctx_owner_a):
        with app.app_context():
            result = dispatch('complete_todo', {}, ctx_owner_a)
        assert result.ok is False

    def test_todos_are_private_to_each_user(self, app, ctx_owner_a, ctx_agent_a):
        """Same org, but a scratch list is personal: no admin widening."""
        with app.app_context():
            added = dispatch('add_todo', {'text': 'Owner private item'},
                             ctx_owner_a)
            todo_id = added.data['todo']['todo_id']

            visible = dispatch('list_todos', {'include_completed': True},
                               ctx_agent_a)
            assert todo_id not in [t['todo_id'] for t in visible.data['todos']]

            attempt = dispatch('complete_todo', {'todo_id': todo_id}, ctx_agent_a)
            assert attempt.ok is False

    def test_undo_add_removes_the_item(self, app, ctx_owner_a):
        with app.app_context():
            added = dispatch('add_todo', {'text': 'Temporary item'}, ctx_owner_a)
            todo_id = added.data['todo']['todo_id']

            undone = undo_action(added.action_id, ctx_owner_a)
            assert undone.ok is True
            assert UserTodo.query.get(todo_id) is None

    def test_undo_complete_reopens_the_item(self, app, ctx_owner_a):
        with app.app_context():
            added = dispatch('add_todo', {'text': 'Reopen me'}, ctx_owner_a)
            todo_id = added.data['todo']['todo_id']
            done = dispatch('complete_todo', {'todo_id': todo_id}, ctx_owner_a)

            undone = undo_action(done.action_id, ctx_owner_a)
            assert undone.ok is True
            assert UserTodo.query.get(todo_id).completed is False

    def test_completing_twice_is_reported_not_failed(self, app, ctx_owner_a):
        with app.app_context():
            added = dispatch('add_todo', {'text': 'Idempotent item'}, ctx_owner_a)
            todo_id = added.data['todo']['todo_id']

            dispatch('complete_todo', {'todo_id': todo_id}, ctx_owner_a)
            second = dispatch('complete_todo', {'todo_id': todo_id}, ctx_owner_a)

            assert second.ok is True
            assert second.data['already_completed'] is True


class TestGroupAssignment:
    def test_replaces_active_groups(self, app, scratch_contact, ctx_owner_a):
        with app.app_context():
            result = dispatch('set_contact_groups', {
                'contact_id': scratch_contact, 'group_names': ['Sellers'],
            }, ctx_owner_a)

            assert result.ok is True
            assert result.data['previous_groups'] == ['Buyers']
            contact = Contact.query.get(scratch_contact)
            assert [g.name for g in contact.groups] == ['Sellers']

    def test_empty_list_clears_groups(self, app, scratch_contact, ctx_owner_a):
        with app.app_context():
            result = dispatch('set_contact_groups', {
                'contact_id': scratch_contact, 'group_names': [],
            }, ctx_owner_a)

            assert result.ok is True
            assert Contact.query.get(scratch_contact).groups == []

    def test_reports_names_that_do_not_exist(self, app, scratch_contact, ctx_owner_a):
        with app.app_context():
            result = dispatch('set_contact_groups', {
                'contact_id': scratch_contact,
                'group_names': ['Buyers', 'Imaginary'],
            }, ctx_owner_a)
        assert result.data['groups_not_found'] == ['Imaginary']

    def test_cannot_borrow_another_users_group(self, app, scratch_contact,
                                              ctx_owner_a):
        """Group catalogs are per-user; Org B's 'Leads' must not resolve."""
        with app.app_context():
            result = dispatch('set_contact_groups', {
                'contact_id': scratch_contact, 'group_names': ['Leads'],
            }, ctx_owner_a)
        assert result.data['groups_not_found'] == ['Leads']


# ---------------------------------------------------------------------------
# High-risk writes: the confirmation gate
# ---------------------------------------------------------------------------

class TestConfirmationGate:
    def test_update_does_not_apply_until_confirmed(self, app, scratch_contact, ctx_owner_a):
        with app.app_context():
            original = Contact.query.get(scratch_contact).city

            result = dispatch('update_contact', {
                'contact_id': scratch_contact, 'fields': {'city': 'Katy'},
            }, ctx_owner_a)

            assert result.ok is True
            assert result.requires_confirmation is True
            assert result.action_id is not None
            assert Contact.query.get(scratch_contact).city == original

            action = BobAction.query.get(result.action_id)
            assert action.status == BobAction.STATUS_PENDING
            assert action.expires_at is not None

    def test_model_payload_states_nothing_was_applied(self, app, scratch_contact, ctx_owner_a):
        """The model must not be able to report a pending change as done."""
        with app.app_context():
            result = dispatch('update_contact', {
                'contact_id': scratch_contact, 'fields': {'city': 'Cypress'},
            }, ctx_owner_a)

        payload = result.for_model()
        assert payload['status'] == 'awaiting_confirmation'
        assert 'NOT applied' in payload['message']

    def test_preview_shows_the_diff(self, app, scratch_contact, ctx_owner_a):
        with app.app_context():
            result = dispatch('update_contact', {
                'contact_id': scratch_contact, 'fields': {'city': 'Spring'},
            }, ctx_owner_a)

        changes = result.data['preview']['changes']
        assert len(changes) == 1
        assert changes[0]['field'] == 'city'
        assert changes[0]['to'] == 'Spring'

    def test_confirming_applies_the_change(self, app, scratch_contact, ctx_owner_a):
        with app.app_context():
            result = dispatch('update_contact', {
                'contact_id': scratch_contact, 'fields': {'city': 'Tomball'},
            }, ctx_owner_a)

            confirmed = confirm_action(result.action_id, ctx_owner_a)
            assert confirmed.ok is True
            assert Contact.query.get(scratch_contact).city == 'Tomball'
            assert BobAction.query.get(result.action_id).status == \
                BobAction.STATUS_EXECUTED

    def test_rejecting_changes_nothing(self, app, scratch_contact, ctx_owner_a):
        with app.app_context():
            original = Contact.query.get(scratch_contact).state
            result = dispatch('update_contact', {
                'contact_id': scratch_contact, 'fields': {'state': 'CA'},
            }, ctx_owner_a)

            rejected = reject_action(result.action_id, ctx_owner_a)
            assert rejected.ok is True
            assert Contact.query.get(scratch_contact).state == original
            assert BobAction.query.get(result.action_id).status == \
                BobAction.STATUS_REJECTED

    def test_an_action_cannot_be_confirmed_twice(self, app, scratch_contact, ctx_owner_a):
        with app.app_context():
            result = dispatch('update_contact', {
                'contact_id': scratch_contact, 'fields': {'zip_code': '77070'},
            }, ctx_owner_a)

            assert confirm_action(result.action_id, ctx_owner_a).ok is True
            second = confirm_action(result.action_id, ctx_owner_a)
            assert second.ok is False
            assert 'already' in second.error

    def test_another_user_cannot_confirm_your_action(self, app, scratch_contact,
                                                    ctx_owner_a, ctx_owner_b):
        with app.app_context():
            original = Contact.query.get(scratch_contact).city
            result = dispatch('update_contact', {
                'contact_id': scratch_contact, 'fields': {'city': 'Hijacked'},
            }, ctx_owner_a)

            attempt = confirm_action(result.action_id, ctx_owner_b)
            assert attempt.ok is False
            assert Contact.query.get(scratch_contact).city == original

    def test_expired_confirmations_are_refused(self, app, scratch_contact, ctx_owner_a):
        with app.app_context():
            result = dispatch('update_contact', {
                'contact_id': scratch_contact, 'fields': {'city': 'Stale'},
            }, ctx_owner_a)

            action = BobAction.query.get(result.action_id)
            action.expires_at = datetime.utcnow() - timedelta(minutes=1)
            db.session.commit()

            attempt = confirm_action(result.action_id, ctx_owner_a)
            assert attempt.ok is False
            assert 'expired' in attempt.error.lower()
            assert Contact.query.get(scratch_contact).city != 'Stale'

    def test_permission_is_rechecked_at_confirm_time(self, app, ctx_owner_a):
        """Approving must not bypass validation against current state."""
        with app.app_context():
            created = dispatch('create_contact', {
                'first_name': 'Vanishing', 'last_name': 'Act',
                'email': f'{_unique("vanish")}@test.com',
            }, ctx_owner_a)
            contact_id = created.data['contact']['contact_id']

            pending = dispatch('update_contact', {
                'contact_id': contact_id, 'fields': {'city': 'Nowhere'},
            }, ctx_owner_a)

            db.session.delete(Contact.query.get(contact_id))
            db.session.commit()

            attempt = confirm_action(pending.action_id, ctx_owner_a)
            assert attempt.ok is False
            assert BobAction.query.get(pending.action_id).status == \
                BobAction.STATUS_FAILED

    def test_update_rejects_fields_outside_the_allowlist(self, app, scratch_contact, ctx_owner_a):
        with app.app_context():
            result = dispatch('update_contact', {
                'contact_id': scratch_contact,
                'fields': {'user_id': 999, 'organization_id': 999},
            }, ctx_owner_a)
        assert result.ok is False
        assert 'cannot be updated' in result.error

    def test_no_op_updates_are_refused(self, app, scratch_contact, ctx_owner_a):
        with app.app_context():
            contact = Contact.query.get(scratch_contact)
            result = dispatch('update_contact', {
                'contact_id': scratch_contact,
                'fields': {'first_name': contact.first_name},
            }, ctx_owner_a)
        assert result.ok is False
        assert 'already match' in result.error

    def test_confirmed_update_can_be_reverted(self, app, scratch_contact, ctx_owner_a):
        with app.app_context():
            before = Contact.query.get(scratch_contact).city
            pending = dispatch('update_contact', {
                'contact_id': scratch_contact, 'fields': {'city': 'Reverted'},
            }, ctx_owner_a)
            applied = confirm_action(pending.action_id, ctx_owner_a)
            assert Contact.query.get(scratch_contact).city == 'Reverted'

            undone = undo_action(applied.action_id, ctx_owner_a)
            assert undone.ok is True
            assert Contact.query.get(scratch_contact).city == before


class TestDeletes:
    def test_delete_contact_previews_the_cascade(self, app, scratch_contact, ctx_owner_a):
        with app.app_context():
            result = dispatch('delete_contact', {'contact_id': scratch_contact},
                              ctx_owner_a)

            assert result.requires_confirmation is True
            preview = result.data['preview']
            assert preview['irreversible'] is True
            assert preview['cascade']['tasks_deleted'] >= 1
            assert Contact.query.get(scratch_contact) is not None

    def test_confirmed_delete_removes_dependents(self, app, scratch_contact, ctx_owner_a):
        email = f'{_unique("del")}@test.com'
        with app.app_context():
            dispatch('create_contact', {
                'first_name': 'Delete', 'last_name': 'Me', 'email': email,
            }, ctx_owner_a)
            contact = Contact.query.filter_by(email=email).first()
            contact_id = contact.id

            dispatch('create_task', {
                'contact_id': contact_id, 'subject': 'Doomed task',
                'due_date': (ctx_owner_a.today() + timedelta(days=1)).isoformat(),
            }, ctx_owner_a)

            pending = dispatch('delete_contact', {'contact_id': contact_id},
                               ctx_owner_a)
            confirmed = confirm_action(pending.action_id, ctx_owner_a)

            assert confirmed.ok is True
            assert Contact.query.get(contact_id) is None
            assert Task.query.filter_by(contact_id=contact_id).count() == 0

    def test_delete_task_is_gated(self, app, scratch_contact, ctx_owner_a):
        with app.app_context():
            task = Task.query.filter_by(contact_id=scratch_contact).first()

            result = dispatch('delete_task', {'task_id': task.id}, ctx_owner_a)

            assert result.requires_confirmation is True
            assert result.data['preview']['irreversible'] is True
            assert Task.query.get(task.id) is not None

    def test_deletes_are_not_undoable(self, app, scratch_contact, ctx_owner_a):
        with app.app_context():
            created = dispatch('create_task', {
                'contact_id': scratch_contact, 'subject': 'Gone for good',
                'due_date': (ctx_owner_a.today() + timedelta(days=1)).isoformat(),
            }, ctx_owner_a)
            task_id = created.data['task']['task_id']

            pending = dispatch('delete_task', {'task_id': task_id}, ctx_owner_a)
            applied = confirm_action(pending.action_id, ctx_owner_a)

            assert applied.ok is True
            assert applied.undoable is False
            assert undo_action(applied.action_id, ctx_owner_a).ok is False

    def test_default_confirmation_is_interactive(self, app, scratch_contact, ctx_owner_a):
        import inspect
        signature = inspect.signature(dispatch)
        assert signature.parameters['confirmation'].default == CONFIRM_INTERACTIVE

        with app.app_context():
            original = Contact.query.get(scratch_contact).city
            result = dispatch('update_contact', {
                'contact_id': scratch_contact, 'fields': {'city': 'Katy'},
            }, ctx_owner_a)
            assert result.requires_confirmation is True
            assert Contact.query.get(scratch_contact).city == original

    def test_precleared_executes_high_write_and_audits(self, app, scratch_contact, ctx_owner_a):
        with app.app_context():
            result = dispatch(
                'update_contact',
                {'contact_id': scratch_contact, 'fields': {'city': 'Pearland'}},
                ctx_owner_a,
                confirmation=CONFIRM_PRECLEARED,
            )

            assert result.ok is True
            assert result.requires_confirmation is False
            assert Contact.query.get(scratch_contact).city == 'Pearland'
            assert result.data.get('preview') is not None

            action = BobAction.query.filter_by(
                tool_name='update_contact',
                user_id=ctx_owner_a.user_id,
                status=BobAction.STATUS_EXECUTED,
            ).order_by(BobAction.id.desc()).first()
            assert action is not None
            assert action.status == BobAction.STATUS_EXECUTED
            assert action.executed_at is not None

    def test_unknown_confirmation_falls_back_to_interactive(
        self, app, scratch_contact, ctx_owner_a,
    ):
        with app.app_context():
            original = Contact.query.get(scratch_contact).city
            result = dispatch(
                'update_contact',
                {'contact_id': scratch_contact, 'fields': {'city': 'Alvin'}},
                ctx_owner_a,
                confirmation='not-a-real-policy',
            )
            assert result.requires_confirmation is True
            assert Contact.query.get(scratch_contact).city == original


class TestUnknownTool:
    def test_unknown_tool_is_a_recoverable_error(self, app, seed, ctx_owner_a):
        with app.app_context():
            result = dispatch('drop_all_tables', {}, ctx_owner_a)
        assert result.ok is False
        assert 'no tool called' in result.error
        # The model is told what it may call instead of just failing.
        assert 'search_contacts' in result.error


# ---------------------------------------------------------------------------
# Tool loop
# ---------------------------------------------------------------------------

class _FakeFunction:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments


class _FakeToolCall:
    def __init__(self, call_id, name, arguments):
        self.id = call_id
        self.type = 'function'
        self.function = _FakeFunction(name, arguments)


class _FakeMessage:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class _FakeCompletion:
    def __init__(self, message):
        self.choices = [type('Choice', (), {'message': message})()]


class _FakeResponseCall:
    type = 'function_call'

    def __init__(self, call_id, name, arguments):
        self.call_id = call_id
        self.name = name
        self.arguments = arguments


class _FakeResponse:
    def __init__(self, *, text='', output=None):
        self.output_text = text
        self.output = output or []


class _FakeCompletions:
    """Replays a scripted sequence of model turns and records the calls."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self.script:
            return _FakeCompletion(_FakeMessage(content='Done.'))
        return _FakeCompletion(self.script.pop(0))


class _FakeResponses:
    """Replays Responses API turns and records native-file requests."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self.script:
            return _FakeResponse(text='Done.')
        item = self.script.pop(0)
        # Completions-era fixtures still use _FakeMessage; adapt for Responses.
        if isinstance(item, _FakeMessage):
            if item.tool_calls:
                output = [
                    _FakeResponseCall(
                        call.id, call.function.name, call.function.arguments,
                    )
                    for call in item.tool_calls
                ]
                return _FakeResponse(output=output)
            return _FakeResponse(text=item.content or '')
        return item


class _FakeClient:
    def __init__(self, script):
        self.completions = _FakeCompletions(script)
        self.chat = type('Chat', (), {'completions': self.completions})()
        self.responses = _FakeResponses(script)


@pytest.fixture()
def fake_openai(monkeypatch):
    """Swap the OpenAI client for a scripted stand-in."""
    from services import ai_service

    monkeypatch.setattr(ai_service.Config, 'OPENAI_API_KEY', 'test-key',
                        raising=False)

    created = {}

    def factory(script):
        client = _FakeClient(script)
        created['client'] = client
        monkeypatch.setattr(ai_service.openai, 'OpenAI',
                            lambda api_key=None: client)
        return client

    return factory


class TestToolLoop:
    def _run(self, messages=None, tools=None, execute=None, **kwargs):
        from services.ai_service import run_tool_conversation

        return list(run_tool_conversation(
            system_prompt='You are B.O.B.',
            messages=messages or [{'role': 'user', 'content': 'hi'}],
            tools=tools if tools is not None else openai_tool_schemas(),
            execute_tool=execute or (lambda name, args: ({'status': 'ok'}, {'ok': True})),
            **kwargs
        ))

    def test_plain_answer_streams_text(self, app, fake_openai):
        fake_openai([_FakeMessage(content='Here is your agenda.')])
        events = self._run()

        text = ''.join(p for e, p in events if e == 'text')
        assert text == 'Here is your agenda.'
        assert events[-1][0] == 'messages'

    def test_tool_call_then_answer(self, app, fake_openai):
        fake_openai([
            _FakeMessage(tool_calls=[
                _FakeToolCall('c1', 'search_contacts', '{"query": "Sarah"}'),
            ]),
            _FakeMessage(content='Found Sarah.'),
        ])
        calls = []

        def execute(name, args):
            calls.append((name, args))
            return {'status': 'ok', 'contacts': []}, {'ok': True, 'summary': 'Found'}

        events = self._run(execute=execute)
        kinds = [e for e, _ in events]

        assert calls == [('search_contacts', {'query': 'Sarah'})]
        assert kinds.index('tool_start') < kinds.index('tool_result')
        assert kinds.index('tool_result') < kinds.index('text')
        assert ''.join(p for e, p in events if e == 'text') == 'Found Sarah.'

    def test_tool_turns_are_in_the_transcript(self, app, fake_openai):
        fake_openai([
            _FakeMessage(tool_calls=[
                _FakeToolCall('c1', 'get_agenda', '{}'),
            ]),
            _FakeMessage(content='All clear.'),
        ])
        events = self._run()
        transcript = [p for e, p in events if e == 'messages'][0]

        roles = [m['role'] for m in transcript]
        assert 'tool' in roles
        assert roles[-1] == 'assistant'
        assert 'system' not in roles

    def test_malformed_arguments_become_an_empty_dict(self, app, fake_openai):
        fake_openai([
            _FakeMessage(tool_calls=[
                _FakeToolCall('c1', 'get_agenda', 'not json at all'),
            ]),
            _FakeMessage(content='Recovered.'),
        ])
        seen = []
        self._run(execute=lambda n, a: (seen.append(a) or ({'status': 'ok'}, {'ok': True})))
        assert seen == [{}]

    def test_round_cap_stops_a_runaway_model(self, app, fake_openai):
        client = fake_openai([
            _FakeMessage(tool_calls=[_FakeToolCall(f'c{i}', 'get_agenda', '{}')])
            for i in range(10)
        ])
        events = self._run(max_rounds=3)

        assert len(client.responses.calls) == 3
        text = ''.join(p for e, p in events if e == 'text')
        assert 'ran out of steps' in text

    def test_tools_are_withheld_on_the_final_round(self, app, fake_openai):
        client = fake_openai([
            _FakeMessage(tool_calls=[_FakeToolCall('c1', 'get_agenda', '{}')]),
            _FakeMessage(tool_calls=[_FakeToolCall('c2', 'get_agenda', '{}')]),
        ])
        self._run(max_rounds=2)

        assert 'tools' in client.responses.calls[0]
        assert 'tools' not in client.responses.calls[1]

    def test_reasoning_effort_is_low_on_ordinary_tool_turns(self, app, fake_openai):
        """Ordinary CRM/Telegram turns use Responses with low reasoning effort."""
        client = fake_openai([
            _FakeMessage(tool_calls=[_FakeToolCall('c1', 'get_agenda', '{}')]),
            _FakeMessage(content='All clear.'),
        ])
        self._run()

        assert client.responses.calls, 'no request was made'
        for call in client.responses.calls:
            assert call.get('reasoning') == {'effort': 'low'}, call.get('model')

    def test_pdf_uses_native_responses_file_input(self, app, fake_openai):
        client = fake_openai([
            _FakeResponse(text='All signature pages were reviewed.'),
        ])
        events = self._run(input_files=[{
            'filename': 'contract.pdf',
            'mime': 'application/pdf',
            'data': b'%PDF-1.4 multipage contract',
            'detail': 'high',
        }])

        assert not client.completions.calls
        request = client.responses.calls[0]
        user_content = request['input'][-1]['content']
        file_part = next(
            part for part in user_content if part['type'] == 'input_file'
        )
        assert file_part['filename'] == 'contract.pdf'
        assert file_part['detail'] == 'high'
        assert file_part['file_data'].startswith(
            'data:application/pdf;base64,'
        )
        assert request['reasoning'] == {'effort': 'medium'}
        assert request['tools'][0]['name'] == openai_tool_schemas()[0][
            'function'
        ]['name']
        assert ''.join(p for e, p in events if e == 'text') == (
            'All signature pages were reviewed.'
        )

    def test_native_file_responses_loop_returns_tool_output(
        self, app, fake_openai,
    ):
        client = fake_openai([
            _FakeResponse(output=[
                _FakeResponseCall(
                    'call-1', 'inspect_attachment', '{"operation":"summary"}',
                ),
            ]),
            _FakeResponse(text='The complete PDF is readable.'),
        ])
        seen = []

        events = self._run(
            input_files=[{
                'filename': 'contract.pdf',
                'mime': 'application/pdf',
                'data': b'%PDF-1.4',
            }],
            execute=lambda name, args: (
                seen.append((name, args))
                or ({'status': 'ok'}, {'ok': True})
            ),
        )

        assert seen == [('inspect_attachment', {'operation': 'summary'})]
        second_input = client.responses.calls[1]['input']
        assert any(
            item.get('type') == 'function_call_output'
            and item.get('call_id') == 'call-1'
            for item in second_input
            if isinstance(item, dict)
        )
        assert ''.join(p for e, p in events if e == 'text') == (
            'The complete PDF is readable.'
        )

    def test_missing_api_key_reports_an_error(self, app, monkeypatch):
        from services import ai_service
        monkeypatch.setattr(ai_service.Config, 'OPENAI_API_KEY', None,
                            raising=False)
        events = self._run()
        assert events[0][0] == 'error'


class TestToolLoopWithRealDispatch:
    """End to end through dispatch, so schema and handler stay in step."""

    def test_model_can_create_a_contact_and_report_it(self, app, seed,
                                                     ctx_owner_a, fake_openai):
        email = f'{_unique("loop")}@test.com'
        fake_openai([
            _FakeMessage(tool_calls=[
                _FakeToolCall('c1', 'create_contact',
                              '{"first_name": "Loop", "last_name": "Test", '
                              f'"email": "{email}"}}'),
            ]),
            _FakeMessage(content='Added Loop Test.'),
        ])

        from services.ai_service import run_tool_conversation

        with app.app_context():
            def execute(name, args):
                result = dispatch(name, args, ctx_owner_a)
                return result.for_model(), result.for_client()

            events = list(run_tool_conversation(
                system_prompt='You are B.O.B.',
                messages=[{'role': 'user', 'content': 'add Loop Test'}],
                tools=openai_tool_schemas(),
                execute_tool=execute,
            ))

            results = [p['result'] for e, p in events if e == 'tool_result']
            assert results[0]['ok'] is True
            assert Contact.query.filter_by(email=email).first() is not None

    def test_high_risk_call_reports_as_pending_not_done(self, app, seed,
                                                       ctx_owner_a, fake_openai):
        fake_openai([
            _FakeMessage(tool_calls=[
                _FakeToolCall('c1', 'update_contact',
                              '{"contact_id": %d, "fields": {"city": "Pending"}}'
                              % seed['contact_a']),
            ]),
            _FakeMessage(content='Waiting on your approval.'),
        ])

        from services.ai_service import run_tool_conversation

        with app.app_context():
            original = Contact.query.get(seed['contact_a']).city
            payloads = []

            def execute(name, args):
                result = dispatch(name, args, ctx_owner_a)
                payload = result.for_model()
                payloads.append(payload)
                return payload, result.for_client()

            events = list(run_tool_conversation(
                system_prompt='You are B.O.B.',
                messages=[{'role': 'user', 'content': 'set city'}],
                tools=openai_tool_schemas(),
                execute_tool=execute,
            ))

            assert payloads[0]['status'] == 'awaiting_confirmation'
            assert Contact.query.get(seed['contact_a']).city == original

            results = [p['result'] for e, p in events if e == 'tool_result']
            assert results[0]['requires_confirmation'] is True
            assert results[0]['action_id'] is not None
