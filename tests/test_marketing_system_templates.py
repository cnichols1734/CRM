"""The starter library an org gets on day one.

These are the first thing an agent sees in Marketing and the reference examples
the AI studio learns the house style from, so they are held to the same rules as
anything an agent writes: valid blocks, clean linter, real merge fields.
"""
import pytest

from models import MarketingTemplate, Organization, db
from services.marketing import compliance, system_templates as st
from services.marketing import merge_fields as mf
from services.marketing.blocks import find_placeholders, validate_blocks
from services.marketing.render import preview
from services.marketing.shell import ShellContext


class TestDefinitions:
    def test_covers_the_six_sends_agents_actually_make(self):
        assert set(st.SYSTEM_TEMPLATE_KEYS) == {
            'check_in', 'open_house', 'market_update',
            'just_listed', 'just_sold', 'holiday',
        }

    def test_every_key_is_unique(self):
        assert len(set(st.SYSTEM_TEMPLATE_KEYS)) == len(st.SYSTEM_TEMPLATE_KEYS)

    def test_every_name_is_unique(self):
        # Seeding keys on name, so a duplicate would silently seed only one.
        names = [t['name'] for t in st.SYSTEM_TEMPLATES]
        assert len(set(names)) == len(names)

    def test_every_category_is_one_the_model_allows(self):
        for template in st.SYSTEM_TEMPLATES:
            assert template['category'] in MarketingTemplate.CATEGORIES

    @pytest.mark.parametrize('key', st.SYSTEM_TEMPLATE_KEYS)
    def test_blocks_validate(self, key):
        validate_blocks(st.definition(key)['blocks'])

    @pytest.mark.parametrize('key', st.SYSTEM_TEMPLATE_KEYS)
    def test_uses_only_real_merge_fields(self, key):
        spec = st.definition(key)
        text = '\n'.join([
            spec['subject'], spec['preheader'],
            *(str(v) for b in spec['blocks'] for v in b.values() if isinstance(v, str)),
        ])
        assert mf.unknown_keys(text) == set()

    @pytest.mark.parametrize('key', st.SYSTEM_TEMPLATE_KEYS)
    def test_ends_with_a_signature(self, key):
        assert st.definition(key)['blocks'][-1]['type'] == 'signature'

    @pytest.mark.parametrize('key', st.SYSTEM_TEMPLATE_KEYS)
    def test_starts_with_a_hero(self, key):
        # The library cards are scaled-down live emails. Without a hero the
        # check-in starter looked like a broken preview next to the others.
        assert st.definition(key)['blocks'][0]['type'] == 'hero'

    @pytest.mark.parametrize('key', st.SYSTEM_TEMPLATE_KEYS)
    def test_has_a_preheader(self, key):
        # Without one, the client pulls the first body line into the inbox
        # preview, which is usually "Hi John,".
        assert st.definition(key)['preheader']

    @pytest.mark.parametrize('key', st.SYSTEM_TEMPLATE_KEYS)
    def test_subject_fits_a_phone(self, key):
        assert len(st.definition(key)['subject']) <= 60

    def test_greets_the_reader_by_name_with_a_fallback(self):
        # A contact with no first name should read "Hi there," not "Hi ,".
        for template in st.SYSTEM_TEMPLATES:
            body = '\n'.join(
                b.get('text', '') for b in template['blocks']
                if b['type'] == 'paragraph'
            )
            assert '{{contact.first_name|' in body


class TestCompliance:
    def test_no_starter_trips_the_linter(self):
        # A template that trips the Fair Housing gate the first time an agent
        # opens it teaches them the gate is noise. Ours have to be clean.
        st.validate_all()

    @pytest.mark.parametrize('key', st.SYSTEM_TEMPLATE_KEYS)
    def test_scans_clean_field_by_field(self, key):
        spec = st.definition(key)
        blocks = validate_blocks(spec['blocks'])
        assert compliance.scan_blocks(blocks) == []
        assert compliance.scan_text(spec['subject']) == []


class TestPlaceholders:
    def test_the_no_ask_templates_need_no_filling_in(self):
        # Check-in is the one an agent should be able to send unedited.
        blocks = validate_blocks(st.definition('check_in')['blocks'])
        assert find_placeholders(blocks) == []

    @pytest.mark.parametrize('key', ['open_house', 'just_listed', 'just_sold'])
    def test_property_templates_flag_what_the_agent_must_supply(self, key):
        spec = st.definition(key)
        blocks = validate_blocks(spec['blocks'])
        found = find_placeholders(blocks, spec['subject'], spec['preheader'])
        assert found, 'a listing template with no placeholders is suspicious'
        assert any('[' in token for token in found)


class TestRendering:
    @pytest.mark.parametrize('key', st.SYSTEM_TEMPLATE_KEYS)
    def test_renders_without_leaking_merge_tokens(self, key):
        spec = st.definition(key)
        ctx = ShellContext(
            header_title='Origen Realty',
            agent_name='Suzie Harrington',
            brokerage_name='Origen Realty',
            brokerage_license='9003104',
            brokerage_address='1401 Common St, New Braunfels, TX 78130',
        )
        subject, html = preview(spec['blocks'], ctx, spec['subject'])
        assert '{{' not in subject
        assert '{{' not in html
        assert 'AgentFlow' not in html


@pytest.fixture()
def org_id(app, seed):
    """An org with no system templates yet."""
    with app.app_context():
        org = Organization.query.filter_by(slug='test-realty-a').first()
        MarketingTemplate.query.filter_by(
            organization_id=org.id, source='system',
        ).delete()
        db.session.commit()
        return org.id


class TestSeeding:
    def test_seeds_the_whole_library(self, app, org_id):
        with app.app_context():
            created = st.seed_for_org(org_id)
            assert len(created) == len(st.SYSTEM_TEMPLATES)

            rows = MarketingTemplate.query.filter_by(
                organization_id=org_id, source='system',
            ).all()
            assert {r.name for r in rows} == {t['name'] for t in st.SYSTEM_TEMPLATES}

    def test_seeded_templates_are_shared_and_sendable(self, app, org_id):
        with app.app_context():
            st.seed_for_org(org_id)
            rows = MarketingTemplate.query.filter_by(
                organization_id=org_id, source='system',
            ).all()
            for row in rows:
                assert row.visibility == 'org'
                assert row.is_sendable
                # No creator: nobody in the org authored these, and attributing
                # them to whoever triggered the seed would be a lie.
                assert row.created_by_id is None

    def test_running_twice_creates_nothing_new(self, app, org_id):
        with app.app_context():
            st.seed_for_org(org_id)
            assert st.seed_for_org(org_id) == []
            assert MarketingTemplate.query.filter_by(
                organization_id=org_id, source='system',
            ).count() == len(st.SYSTEM_TEMPLATES)

    def test_does_not_overwrite_a_saved_copy(self, app, org_id):
        with app.app_context():
            st.seed_for_org(org_id)
            copy = MarketingTemplate(
                organization_id=org_id,
                created_by_id=None,
                name='Just checking in',
                description='Mine',
                category='check_in',
                subject='My own subject',
                preheader='Mine',
                blocks=[{'type': 'paragraph', 'text': 'Keep this.'},
                        {'type': 'signature'}],
                visibility='private',
                status='ready',
                source='manual',
            )
            db.session.add(copy)
            db.session.commit()
            copy_id = copy.id

        with app.app_context():
            st.seed_for_org(org_id)
            assert db.session.get(
                MarketingTemplate, copy_id,
            ).subject == 'My own subject'

    def test_refreshes_a_stale_system_starter(self, app, org_id):
        with app.app_context():
            st.seed_for_org(org_id)
            row = MarketingTemplate.query.filter_by(
                organization_id=org_id, name='Just checking in', source='system',
            ).one()
            row.blocks = [
                {'type': 'paragraph', 'text': 'Old body without a hero.'},
                {'type': 'signature'},
            ]
            db.session.commit()
            template_id = row.id

        with app.app_context():
            assert st.seed_for_org(org_id) == []
            refreshed = db.session.get(MarketingTemplate, template_id)
            assert refreshed.blocks[0]['type'] == 'hero'

    def test_one_orgs_library_is_its_own(self, app, org_id, seed):
        with app.app_context():
            other = Organization.query.filter_by(slug='test-realty-b').first()
            st.seed_for_org(org_id)
            assert MarketingTemplate.query.filter_by(
                organization_id=other.id, source='system',
            ).count() == 0
