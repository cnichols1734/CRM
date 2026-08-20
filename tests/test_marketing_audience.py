"""Audience filters and the exclusion breakdown."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from models import Contact, ContactGroup, db
from services.marketing import audience as aud
from services.marketing.suppression import REASON_MANUAL, suppress

from marketing_helpers import enable_campaigns, load_org_user, make_contact


class TestAudienceEstimate:
    def test_matches_the_owner_s_contacts(self, app, seed):
        with app.app_context():
            org, owner = load_org_user(seed)
            jane = db.session.get(Contact, seed['contact_a'])
            estimate = aud.estimate(org.id, {'owners': [owner.id]}, owner)
            ids = {row.contact.id for row in estimate.sendable}
            assert jane.id in ids
            assert seed['contact_a2'] not in ids
            assert seed['contact_b'] not in ids

    def test_empty_filter_matches_nobody(self, app, seed):
        with app.app_context():
            org, owner = load_org_user(seed)
            estimate = aud.estimate(org.id, {}, owner)
            assert estimate.matched == 0
            assert estimate.sendable_count == 0

    def test_picked_contact_ids_build_the_list(self, app, seed):
        with app.app_context():
            org, owner = load_org_user(seed)
            extra = make_contact(
                org, owner, first='Pat', last='Lee',
                email='pat-lee@example.com',
            )
            jane = db.session.get(Contact, seed['contact_a'])
            estimate = aud.estimate(
                org.id, {'contact_ids': [jane.id, extra.id]}, owner,
            )
            ids = {row.contact.id for row in estimate.sendable}
            assert jane.id in ids
            assert extra.id in ids
            assert seed['contact_a2'] not in ids

    def test_filters_by_group(self, app, seed):
        with app.app_context():
            org, owner = load_org_user(seed)
            group = ContactGroup(
                name='Audience Buyers',
                organization_id=org.id,
                user_id=owner.id,
                category='general',
                sort_order=99,
                is_active=True,
            )
            db.session.add(group)
            contact = make_contact(
                org, owner, first='Group', last='Member',
                email='group-member@example.com',
            )
            contact.groups.append(group)
            db.session.commit()
            estimate = aud.estimate(org.id, {'groups': [group.id]}, owner)
            assert estimate.sendable_count == 1
            assert estimate.sendable[0].contact.id == contact.id

    def test_filters_by_zip_prefix(self, app, seed):
        with app.app_context():
            org, owner = load_org_user(seed)
            make_contact(
                org, owner, first='Zip', last='One',
                email='zip-one@example.com', zip_code='99901',
            )
            make_contact(
                org, owner, first='Zip', last='Two',
                email='zip-two@example.com', zip_code='88801',
            )
            estimate = aud.estimate(org.id, {'zips': ['999']}, owner)
            emails = {row.email for row in estimate.sendable}
            assert 'zip-one@example.com' in emails
            assert 'zip-two@example.com' not in emails

    def test_filters_by_city(self, app, seed):
        with app.app_context():
            org, owner = load_org_user(seed)
            make_contact(
                org, owner, first='City', last='Nb',
                email='city-nb@example.com', city='New Braunfels',
            )
            estimate = aud.estimate(org.id, {'cities': ['new braunfels']}, owner)
            assert any(row.email == 'city-nb@example.com' for row in estimate.sendable)

    def test_skips_opted_out(self, app, seed):
        with app.app_context():
            org, owner = load_org_user(seed)
            make_contact(
                org, owner, first='Out', last='Person',
                email='opted-out@example.com', marketing_consent='opted_out',
            )
            estimate = aud.estimate(org.id, {'owners': [owner.id]}, owner)
            emails = {row.email for row in estimate.sendable}
            assert 'opted-out@example.com' not in emails
            assert estimate.breakdown().get('opted_out', 0) >= 1

    def test_skips_suppressed(self, app, seed):
        with app.app_context():
            org, owner = load_org_user(seed)
            make_contact(
                org, owner, first='Supp', last='Person',
                email='suppressed@example.com',
            )
            suppress('suppressed@example.com', REASON_MANUAL, organization_id=org.id)
            estimate = aud.estimate(org.id, {'owners': [owner.id]}, owner)
            emails = {row.email for row in estimate.sendable}
            assert 'suppressed@example.com' not in emails
            assert estimate.breakdown().get('suppressed', 0) >= 1

    def test_require_consent_excludes_unknown(self, app, seed):
        with app.app_context():
            org, owner = load_org_user(seed)
            jane = db.session.get(Contact, seed['contact_a'])
            jane.marketing_consent = 'unknown'
            estimate = aud.estimate(
                org.id, {'owners': [owner.id], 'require_consent': True}, owner,
            )
            ids = {row.contact.id for row in estimate.sendable}
            assert jane.id not in ids
            assert estimate.breakdown().get('consent_required', 0) >= 1

    def test_agent_cannot_send_org_wide(self, app, seed):
        with app.app_context():
            org, agent = load_org_user(seed, user_key='agent_a')
            try:
                aud.estimate(org.id, {'whole_org': True}, agent)
            except aud.AudienceError as exc:
                assert 'owner or admin' in str(exc)
            else:
                raise AssertionError('expected AudienceError')

    def test_owner_can_send_org_wide(self, app, seed):
        with app.app_context():
            org, owner = load_org_user(seed)
            enable_campaigns(org)
            estimate = aud.estimate(org.id, {'whole_org': True}, owner)
            ids = {row.contact.id for row in estimate.sendable}
            assert seed['contact_a'] in ids
            assert seed['contact_a2'] in ids

    def test_duplicate_email_is_excluded_once(self, app, seed):
        with app.app_context():
            org, owner = load_org_user(seed)
            jane = db.session.get(Contact, seed['contact_a'])
            make_contact(
                org, owner, first='Jane', last='Copy',
                email=jane.email,
            )
            estimate = aud.estimate(org.id, {'owners': [owner.id]}, owner)
            emails = [row.email for row in estimate.sendable if row.email == jane.email]
            assert len(emails) == 1
            assert estimate.breakdown().get('duplicate_email', 0) >= 1
