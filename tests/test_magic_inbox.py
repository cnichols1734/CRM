"""
Magic Inbox unit + integration tests.

Covers:
- Address provisioning, slug normalization, plus-alias parsing
- Undo token round-trip + tamper resistance
- SendGrid payload normalizer (text bodies, HTML stripping, vCards, CSVs,
  truncation, image classification)
- Contact extraction orchestrator: dedupe, group resolution from plus alias,
  free-tier limits, self-reference suppression, rejected-when-empty
- /inbox UI route (logged-in render)
- Webhook end-to-end via Flask test client (AI mocked)

Heavy paths (real OpenAI, real SendGrid) are mocked. All DB writes happen
against the shared SQLite test database from `tests/conftest.py`.
"""
from __future__ import annotations

import io
import json
from email.message import EmailMessage
from unittest.mock import patch

import pytest
from sqlalchemy import inspect
from werkzeug.exceptions import RequestEntityTooLarge

from models import (
    Contact,
    ContactGroup,
    InboundMessage,
    User,
    db,
)
from services.inbound_payload import (
    MAX_CSV_ROWS,
    NormalizedInbound,
    normalize_sendgrid_payload,
)
from services.inbox_provisioning import (
    build_address,
    generate_token,
    get_inbox_domain,
    parse_recipient,
    provision_inbox_address,
    rotate_inbox_address,
    slugify_for_inbox,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _user(seed, key='owner_a') -> User:
    return User.query.get(seed[key])


def _ensure_inbox(seed, key='owner_a') -> User:
    """Provision an inbox for the seed user if missing."""
    u = _user(seed, key)
    if not u.inbox_address:
        provision_inbox_address(u)
    return u


@pytest.fixture(autouse=True)
def _scrub_inbox_writes(app, seed):
    """The orchestrator commits via ``db.session.commit()``, so the
    project-wide ``_rollback_after_test`` fixture in ``conftest.py`` cannot
    undo what these tests create. Without an explicit scrub, ``Contact`` and
    ``InboundMessage`` rows would leak across tests, breaking unique
    constraints and the seed-state assumptions of unrelated test files
    (e.g. ``test_market_insights``, ``test_admin``)."""
    yield
    seed_contact_ids = {seed[k] for k in seed
                        if isinstance(k, str) and k.startswith('contact_')}
    with app.app_context():
        db.session.rollback()
        tables = set(inspect(db.engine).get_table_names())
        if 'inbound_messages' in tables:
            InboundMessage.query.delete(synchronize_session=False)
        if 'contact' in tables:
            Contact.query.filter(
                ~Contact.id.in_(seed_contact_ids)
            ).delete(synchronize_session=False)
        db.session.commit()


# ---------------------------------------------------------------------------
# Slug + token + address shape
# ---------------------------------------------------------------------------

class TestSlugAndToken:
    def test_slugify_basic(self):
        assert slugify_for_inbox('Chris', 'Nichols') == 'chris.nichols'

    def test_slugify_strips_punctuation(self):
        # Each run of non-alphanumeric collapses into one '.'.
        assert slugify_for_inbox("Mary-Jane", "O'Brien") == 'mary.jane.o.brien'

    def test_slugify_unicode_collapses_to_ascii(self):
        assert slugify_for_inbox('Renée', 'Zellweger') == 'renee.zellweger'

    def test_slugify_empty_uses_fallback(self):
        assert slugify_for_inbox('', '', fallback='alice@x.com') == 'alice.x.com'

    def test_slugify_completely_empty_yields_user(self):
        assert slugify_for_inbox(None, None) == 'user'

    def test_slugify_reserved_appends_user(self):
        # 'admin' is in RESERVED_SLUGS — must not collide with system mailbox.
        out = slugify_for_inbox('admin', '')
        assert out.endswith('.user')

    def test_token_alphabet_and_length(self):
        for _ in range(50):
            t = generate_token()
            assert len(t) == 8
            assert all(c in 'abcdefghjkmnpqrstuvwxyz23456789' for c in t)
            # Visually-ambiguous chars excluded.
            assert '0' not in t and '1' not in t and 'o' not in t and 'l' not in t


# ---------------------------------------------------------------------------
# Provisioning
# ---------------------------------------------------------------------------

@pytest.mark.usefixtures('app')
class TestProvisioning:
    def test_provisioning_is_idempotent(self, app, seed):
        with app.app_context():
            u = _user(seed, 'owner_a')
            u.inbox_address = None
            u.inbox_token = None
            db.session.commit()

            provision_inbox_address(u)
            first_address = u.inbox_address

            provision_inbox_address(u)
            assert u.inbox_address == first_address

    def test_address_format(self, app, seed):
        with app.app_context():
            u = _ensure_inbox(seed, 'agent_a')
            slug, _, domain = u.inbox_address.partition('@')
            assert domain == get_inbox_domain()
            assert '-' in slug
            local_slug, _, token = slug.rpartition('-')
            assert local_slug
            assert len(token) == 8

    def test_two_users_get_distinct_tokens(self, app, seed):
        with app.app_context():
            a = _ensure_inbox(seed, 'owner_a')
            b = _ensure_inbox(seed, 'agent_a')
            assert a.inbox_token != b.inbox_token
            assert a.inbox_address != b.inbox_address

    def test_rotate_changes_token(self, app, seed):
        with app.app_context():
            u = _ensure_inbox(seed, 'owner_b')
            old_token = u.inbox_token
            old_address = u.inbox_address

            rotate_inbox_address(u)
            assert u.inbox_token != old_token
            assert u.inbox_address != old_address


# ---------------------------------------------------------------------------
# parse_recipient
# ---------------------------------------------------------------------------

class TestParseRecipient:
    def test_parses_valid_address(self):
        addr = build_address('chris.nichols', 'abcd2345')
        slug, token, alias = parse_recipient(addr)
        assert slug == 'chris.nichols'
        assert token == 'abcd2345'
        assert alias is None

    def test_parses_plus_alias(self):
        addr = f'chris.nichols-abcd2345+leads@{get_inbox_domain()}'
        slug, token, alias = parse_recipient(addr)
        assert slug == 'chris.nichols'
        assert token == 'abcd2345'
        assert alias == 'leads'

    def test_rejects_wrong_domain(self):
        addr = 'chris.nichols-abcd2345@example.com'
        assert parse_recipient(addr) == (None, None, None)

    def test_rejects_no_dash(self):
        addr = f'chrisnichols@{get_inbox_domain()}'
        assert parse_recipient(addr) == (None, None, None)

    def test_rejects_short_token(self):
        addr = f'chris.nichols-short@{get_inbox_domain()}'
        assert parse_recipient(addr) == (None, None, None)

    def test_rejects_invalid_token_chars(self):
        addr = f'chris.nichols-AAAA1111@{get_inbox_domain()}'
        # Uppercase 'A' is in alphabet (lowercase only); '1' is not.
        slug, token, alias = parse_recipient(addr)
        assert slug is None and token is None

    def test_lowercases_address(self):
        addr = f'Chris.Nichols-Abcd2345@{get_inbox_domain().upper()}'
        slug, token, _ = parse_recipient(addr)
        assert slug == 'chris.nichols'
        assert token == 'abcd2345'


# ---------------------------------------------------------------------------
# Undo token
# ---------------------------------------------------------------------------

@pytest.mark.usefixtures('app')
class TestUndoToken:
    def test_round_trip(self, app):
        from services.sendgrid_outbound import (
            make_undo_token, parse_undo_token, verify_undo_token,
        )
        with app.app_context():
            tok = make_undo_token(42)
            assert verify_undo_token(tok) == 42
            assert parse_undo_token(tok) == {'inbound_id': 42}

    def test_contact_specific_token(self, app):
        from services.sendgrid_outbound import (
            make_undo_token, parse_undo_token, verify_undo_token,
        )
        with app.app_context():
            tok = make_undo_token(42, contact_id=7)
            assert verify_undo_token(tok) == 42
            assert parse_undo_token(tok) == {
                'inbound_id': 42,
                'contact_id': 7,
            }

    def test_tampered_token_rejected(self, app):
        from services.sendgrid_outbound import (
            make_undo_token, verify_undo_token,
        )
        with app.app_context():
            tok = make_undo_token(42)
            assert verify_undo_token(tok + 'x') is None

    def test_garbage_token_rejected(self, app):
        from services.sendgrid_outbound import verify_undo_token
        with app.app_context():
            assert verify_undo_token('not-a-token') is None
            assert verify_undo_token('') is None


class TestSendGridTemplateData:
    def test_account_welcome_uses_dynamic_template_when_configured(
            self, app, seed):
        from services.sendgrid_outbound import send_account_welcome

        with app.app_context(), app.test_request_context('/'):
            user = _ensure_inbox(seed, 'owner_a')
            with patch('services.sendgrid_outbound._send_template',
                       return_value=True) as send_template, \
                    patch('services.sendgrid_outbound._send_html') as send_html:
                assert send_account_welcome(user) is True

            send_template.assert_called_once()
            _, template_id, data = send_template.call_args.args[:3]
            assert template_id == 'd-8ca289d2b7fa4778a8c4b3d10992aab5'
            assert data['first_name'] == user.first_name
            assert data['inbox_address'] == user.inbox_address
            assert data['vcard_url'].endswith('/inbox/vcard')
            assert data['dashboard_url'].endswith('/dashboard')
            assert data['contacts_url'].endswith('/contacts')
            assert data['inbox_url'].endswith('/inbox')
            send_html.assert_not_called()

    def test_welcome_uses_dynamic_template_when_configured(self, app, seed):
        from services.sendgrid_outbound import send_inbox_welcome

        with app.app_context(), app.test_request_context('/'):
            user = _ensure_inbox(seed, 'owner_a')
            with patch('services.sendgrid_outbound._send_template',
                       return_value=True) as send_template, \
                    patch('services.sendgrid_outbound._send_html') as send_html:
                assert send_inbox_welcome(user) is True

            send_template.assert_called_once()
            _, template_id, data = send_template.call_args.args[:3]
            assert template_id == 'd-d89070c074554464a728867471e173e1'
            assert data['inbox_address'] == user.inbox_address
            assert data['inbound_domain'] == get_inbox_domain()
            assert data['vcard_url'].endswith('/inbox/vcard')
            assert data['inbox_url'].endswith('/inbox')
            send_html.assert_not_called()

    def test_receipt_data_has_per_contact_view_and_undo_urls(
            self, app, seed):
        from services.sendgrid_outbound import _receipt_template_data

        with app.app_context(), app.test_request_context('/'):
            user = _ensure_inbox(seed, 'owner_a')
            c1 = Contact(
                user_id=user.id,
                organization_id=user.organization_id,
                created_by_id=user.id,
                first_name='Janet',
                last_name='Hill',
                email='janet.hill@example.com',
            )
            c2 = Contact(
                user_id=user.id,
                organization_id=user.organization_id,
                created_by_id=user.id,
                first_name='Daniel',
                last_name='Reyes',
                email='dreyes@example.com',
            )
            db.session.add_all([c1, c2])
            db.session.flush()

            data = _receipt_template_data(
                [c1, c2],
                inbound_id=99,
                subject='Saved 2 contacts to your CRM',
                inbound_recipient=user.inbox_address,
                sender_email=user.email,
                source_kind='csv',
                source_subject='Weekend leads',
                skipped_count=1,
            )

            assert data['count_label'] == '2 new contacts'
            assert data['saved_pronoun'] == 'them'
            assert data['source_kind'] == 'CSV'
            assert data['skipped_label'] == '1 entry'
            assert data['contacts_url'].endswith('/contacts')
            assert data['inbox_url'].endswith('/inbox')
            assert len(data['contacts']) == 2
            for row in data['contacts']:
                assert row['view_url'].endswith(f"/contact/{row['id']}")
                assert '/inbox/undo/' in row['undo_url']


# ---------------------------------------------------------------------------
# Payload normalizer
# ---------------------------------------------------------------------------

class _FakeFile:
    """Minimal stand-in for werkzeug FileStorage."""

    def __init__(self, filename, mimetype, data):
        self.filename = filename
        self.mimetype = mimetype
        self._data = data

    def read(self):
        return self._data


class TestNormalizer:
    def test_empty_payload(self):
        bundle = normalize_sendgrid_payload({}, {})
        assert isinstance(bundle, NormalizedInbound)
        assert bundle.cleaned_text == ''
        assert bundle.image_blocks == []
        assert bundle.source_kind == 'text'

    def test_text_only(self):
        form = {
            'subject': 'Quick intro',
            'from': 'Sarah Chen <sarah@example.com>',
            'text': 'Adding a new client. 555-123-9999',
        }
        bundle = normalize_sendgrid_payload(form, {})
        assert 'SUBJECT: Quick intro' in bundle.cleaned_text
        assert 'FROM: Sarah Chen' in bundle.cleaned_text
        assert '555-123-9999' in bundle.cleaned_text
        assert bundle.image_blocks == []
        assert bundle.source_kind == 'text'

    def test_html_stripped_when_no_text(self):
        form = {
            'subject': 'X',
            'html': '<p>Hello <b>Sarah</b></p><div>email: sarah@x.com</div>',
        }
        bundle = normalize_sendgrid_payload(form, {})
        # Tags gone, content kept, whitespace squashed.
        assert '<p>' not in bundle.cleaned_text
        assert 'Hello Sarah' in bundle.cleaned_text
        assert 'sarah@x.com' in bundle.cleaned_text

    def test_vcard_attachment_passthrough(self):
        vcf = (b'BEGIN:VCARD\r\nVERSION:3.0\r\n'
               b'FN:Sarah Chen\r\nEMAIL:sarah@example.com\r\nEND:VCARD\r\n')
        files = {'attachment1': _FakeFile('sarah.vcf', 'text/vcard', vcf)}
        bundle = normalize_sendgrid_payload({}, files)
        assert 'BEGIN:VCARD' in bundle.cleaned_text
        assert 'sarah@example.com' in bundle.cleaned_text
        assert bundle.source_kind == 'vcard'

    def test_csv_truncated_above_limit(self):
        rows = ['name,email']
        rows += [f'Person {i},p{i}@x.com' for i in range(MAX_CSV_ROWS + 50)]
        csv_bytes = '\n'.join(rows).encode('utf-8')
        files = {'attachment1': _FakeFile('big.csv', 'text/csv', csv_bytes)}
        bundle = normalize_sendgrid_payload({}, files)
        assert bundle.over_limit_csv is True
        assert bundle.skipped_csv_rows >= 50
        assert 'TRUNCATED' in bundle.cleaned_text
        assert bundle.source_kind == 'csv'

    def test_raw_email_field_csv_attachment(self):
        csv_bytes = b'name,email,phone\nJames Aikens,james@example.com,6038121777\n'
        msg = EmailMessage()
        msg['Subject'] = 'CSV contacts'
        msg.set_content('Import these contacts.')
        msg.add_attachment(
            csv_bytes,
            maintype='text',
            subtype='csv',
            filename='contacts.csv',
        )

        bundle = normalize_sendgrid_payload({'email': msg.as_string()}, {})
        assert 'ATTACHMENT CSV (contacts.csv)' in bundle.cleaned_text
        assert 'James Aikens' in bundle.cleaned_text
        assert 'james@example.com' in bundle.cleaned_text
        assert bundle.source_kind == 'csv'

    def test_unknown_attachment_type_skipped(self):
        files = {'attachment1': _FakeFile(
            'mystery.bin', 'application/octet-stream', b'\x00\x01\x02')}
        bundle = normalize_sendgrid_payload({}, files)
        assert bundle.cleaned_text == ''
        assert bundle.image_blocks == []

    def test_image_attachment_classified(self):
        try:
            from PIL import Image
        except ImportError:
            pytest.skip('Pillow not installed')

        # Build a real 64x64 PNG so the normalizer can decode it.
        img = Image.new('RGB', (64, 64), color=(220, 80, 80))
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        files = {'attachment1': _FakeFile(
            'card.png', 'image/png', buf.getvalue())}
        bundle = normalize_sendgrid_payload({}, files)
        assert len(bundle.image_blocks) == 1
        assert bundle.source_kind == 'image'

    def test_raw_email_field_image_classified(self):
        try:
            from PIL import Image
        except ImportError:
            pytest.skip('Pillow not installed')

        img = Image.new('RGB', (64, 64), color=(80, 120, 220))
        buf = io.BytesIO()
        img.save(buf, format='PNG')

        msg = EmailMessage()
        msg['Subject'] = 'Contact screenshot'
        msg.set_content('See attached contact card.')
        msg.add_attachment(
            buf.getvalue(),
            maintype='image',
            subtype='png',
            filename='contact-card.png',
        )

        bundle = normalize_sendgrid_payload({'email': msg.as_string()}, {})
        assert 'See attached contact card' in bundle.cleaned_text
        assert len(bundle.image_blocks) == 1
        assert bundle.source_kind == 'image'

    def test_extra_images_above_limit_skipped(self):
        try:
            from PIL import Image
        except ImportError:
            pytest.skip('Pillow not installed')

        img = Image.new('RGB', (32, 32), color='blue')
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        png = buf.getvalue()

        files = {f'attachment{i}': _FakeFile(f'i{i}.png', 'image/png', png)
                 for i in range(1, 8)}
        bundle = normalize_sendgrid_payload({}, files)
        assert len(bundle.image_blocks) == 5
        assert bundle.skipped_images == 2


# ---------------------------------------------------------------------------
# Orchestrator (process_inbound) — AI is mocked
# ---------------------------------------------------------------------------

@pytest.mark.usefixtures('app')
class TestOrchestrator:
    def _bundle(self, text='SUBJECT: hi', images=None,
                kind='text', plus_alias=None):
        return NormalizedInbound(
            cleaned_text=text,
            image_blocks=images or [],
            source_kind=kind,
            plus_alias=plus_alias,
        )

    def _new_message(self, user, plus_alias=None, sender_email=None):
        m = InboundMessage(
            organization_id=user.organization_id,
            user_id=user.id,
            recipient_address=user.inbox_address,
            sender_email=sender_email or user.email,
            subject='Test',
            plus_alias=plus_alias,
            source_kind='text',
            status='received',
        )
        db.session.add(m)
        db.session.commit()
        return m

    def test_creates_new_contact(self, app, seed):
        from services.contact_extraction import process_inbound

        with app.app_context():
            user = _ensure_inbox(seed, 'owner_a')
            msg = self._new_message(user)
            ai_payload = {
                'contacts': [{
                    'first_name': 'Sarah', 'last_name': 'Chen',
                    'email': 'sarah.chen@example.com', 'phone': '555-123-9999',
                    'street_address': None, 'city': None,
                    'state': None, 'zip_code': None,
                    'notes': 'Met at open house', 'confidence': 'high',
                }],
                '_meta': {'model': 'gpt-5.4-nano',
                          'tokens_in': 100, 'tokens_out': 30},
            }
            with patch('services.contact_extraction.generate_contact_extraction',
                       return_value=ai_payload):
                result = process_inbound(user, msg, self._bundle())

            assert result['status'] == 'processed'
            assert len(result['created_contacts']) == 1
            c = result['created_contacts'][0]
            assert c.first_name == 'Sarah'
            assert c.email == 'sarah.chen@example.com'
            db.session.refresh(msg)
            assert msg.status == 'processed'
            assert msg.created_contact_ids == [c.id]
            assert msg.ai_model == 'gpt-5.4-nano'
            assert msg.ai_tokens_in == 100
            # Cost should be a positive Decimal.
            assert msg.ai_cost_cents is not None
            assert float(msg.ai_cost_cents) > 0

    def test_receipt_sent_to_owner_when_forwarded_by_someone_else(
            self, app, seed):
        from services.contact_extraction import process_inbound

        with app.app_context():
            user = _ensure_inbox(seed, 'owner_a')
            msg = self._new_message(
                user, sender_email='outside.sender@example.com')
            ai_payload = {
                'contacts': [{
                    'first_name': 'Forwarded', 'last_name': 'Lead',
                    'email': 'forwarded.lead@example.com',
                    'phone': '555-321-5555',
                    'street_address': None, 'city': None,
                    'state': None, 'zip_code': None,
                    'notes': 'Forwarded by someone else',
                    'confidence': 'high',
                }],
                '_meta': {'model': 'gpt-5.4-nano',
                          'tokens_in': 100, 'tokens_out': 30},
            }
            with patch('services.contact_extraction.generate_contact_extraction',
                       return_value=ai_payload), \
                    patch('services.contact_extraction.send_inbox_receipt',
                          return_value=True) as send_receipt:
                result = process_inbound(user, msg, self._bundle())

            assert result['status'] == 'processed'
            send_receipt.assert_called_once()
            receipt_user = send_receipt.call_args.args[0]
            assert receipt_user.email == user.email
            assert send_receipt.call_args.kwargs['sender_email'] == (
                'outside.sender@example.com')

    def test_dedupes_against_existing_email(self, app, seed):
        from services.contact_extraction import process_inbound

        with app.app_context():
            user = _ensure_inbox(seed, 'owner_a')
            existing = Contact.query.get(seed['contact_a'])  # jane@test.com
            msg = self._new_message(user)
            ai_payload = {
                'contacts': [{
                    'first_name': 'Jane', 'last_name': 'Doe',
                    'email': existing.email, 'phone': None,
                    'street_address': None, 'city': None,
                    'state': None, 'zip_code': None,
                    'notes': None, 'confidence': 'high',
                }],
                '_meta': {'model': 'gpt-5.4-nano',
                          'tokens_in': 10, 'tokens_out': 5},
            }
            with patch('services.contact_extraction.generate_contact_extraction',
                       return_value=ai_payload):
                result = process_inbound(user, msg, self._bundle())

            assert result['created_contacts'] == []
            db.session.refresh(msg)
            assert msg.status == 'rejected'
            assert 'dupes=1' in (msg.error_message or '')

    def test_drops_self_reference(self, app, seed):
        from services.contact_extraction import process_inbound

        with app.app_context():
            user = _ensure_inbox(seed, 'owner_a')
            msg = self._new_message(user)
            ai_payload = {
                'contacts': [{
                    'first_name': 'Alice', 'last_name': 'Owner',
                    'email': user.email, 'phone': None,
                    'street_address': None, 'city': None,
                    'state': None, 'zip_code': None,
                    'notes': None, 'confidence': 'high',
                }],
                '_meta': {'model': 'gpt-5.4-nano',
                          'tokens_in': 10, 'tokens_out': 5},
            }
            with patch('services.contact_extraction.generate_contact_extraction',
                       return_value=ai_payload):
                result = process_inbound(user, msg, self._bundle())

            assert result['created_contacts'] == []
            db.session.refresh(msg)
            assert msg.status == 'rejected'

    def test_resolves_group_via_plus_alias(self, app, seed):
        """Self-contained: provision our own group rather than relying on the
        seeded `Buyers` group, which other test files (e.g. test_admin) may
        have renamed in earlier tests in the same session."""
        from services.contact_extraction import process_inbound

        with app.app_context():
            user = _ensure_inbox(seed, 'owner_a')
            target = ContactGroup(
                name='InboxAliasGroup',
                organization_id=user.organization_id,
                category='custom',
                sort_order=99,
            )
            db.session.add(target)
            db.session.commit()

            try:
                msg = self._new_message(user, plus_alias='inboxaliasgroup')
                ai_payload = {
                    'contacts': [{
                        'first_name': 'New', 'last_name': 'Lead',
                        'email': 'newlead@example.com', 'phone': None,
                        'street_address': None, 'city': None,
                        'state': None, 'zip_code': None,
                        'notes': None, 'confidence': 'high',
                    }],
                    '_meta': {'model': 'gpt-5.4-nano',
                              'tokens_in': 10, 'tokens_out': 5},
                }
                with patch(
                    'services.contact_extraction.generate_contact_extraction',
                    return_value=ai_payload,
                ):
                    result = process_inbound(
                        user, msg,
                        self._bundle(plus_alias='inboxaliasgroup'))

                assert len(result['created_contacts']) == 1
                c = result['created_contacts'][0]
                assert target in c.groups
            finally:
                db.session.delete(target)
                db.session.commit()

    def test_rejects_empty_payload(self, app, seed):
        from services.contact_extraction import process_inbound

        with app.app_context():
            user = _ensure_inbox(seed, 'owner_a')
            msg = self._new_message(user)
            bundle = NormalizedInbound(cleaned_text='', image_blocks=[])
            result = process_inbound(user, msg, bundle)
            assert result['status'] == 'rejected'
            db.session.refresh(msg)
            assert msg.status == 'rejected'

    def test_low_confidence_no_signal_dropped(self, app, seed):
        from services.contact_extraction import process_inbound

        with app.app_context():
            user = _ensure_inbox(seed, 'owner_a')
            msg = self._new_message(user)
            ai_payload = {
                'contacts': [{
                    'first_name': 'Maybe', 'last_name': 'Someone',
                    'email': None, 'phone': None,
                    'street_address': None, 'city': None,
                    'state': None, 'zip_code': None,
                    'notes': None, 'confidence': 'low',
                }],
                '_meta': {'model': 'gpt-5.4-nano',
                          'tokens_in': 10, 'tokens_out': 5},
            }
            with patch('services.contact_extraction.generate_contact_extraction',
                       return_value=ai_payload):
                result = process_inbound(user, msg, self._bundle())
            assert result['created_contacts'] == []


# ---------------------------------------------------------------------------
# /inbox UI
# ---------------------------------------------------------------------------

@pytest.mark.usefixtures('app')
class TestInboxRoute:
    def test_logged_out_redirects(self, client):
        rv = client.get('/inbox', follow_redirects=False)
        assert rv.status_code in (301, 302)

    def test_logged_in_renders_address(self, app, seed, owner_a_client):
        with app.app_context():
            _ensure_inbox(seed, 'owner_a')
            user = _user(seed, 'owner_a')
            address = user.inbox_address

        rv = owner_a_client.get('/inbox')
        assert rv.status_code == 200
        body = rv.data.decode('utf-8')
        assert address in body
        assert 'Magic Inbox' in body

    def test_qr_payload_is_contact_vcard(self, app, seed, owner_a_client):
        with app.app_context():
            _ensure_inbox(seed, 'owner_a')
            user = _user(seed, 'owner_a')
            address = user.inbox_address

        captured = {}

        def _capture_qr(payload):
            captured['payload'] = payload
            return ''

        with patch('routes.inbound_email._qr_svg_markup',
                   side_effect=_capture_qr):
            rv = owner_a_client.get('/inbox')

        assert rv.status_code == 200
        assert captured['payload'].startswith('BEGIN:VCARD')
        assert 'FN:Origen Inbox' in captured['payload']
        assert f'EMAIL;TYPE=INTERNET;TYPE=PREF:{address}' in captured['payload']
        assert not captured['payload'].startswith('mailto:')

    def test_vcard_download(self, app, seed, owner_a_client):
        with app.app_context():
            _ensure_inbox(seed, 'owner_a')
        rv = owner_a_client.get('/inbox/vcard')
        assert rv.status_code == 200
        assert rv.mimetype == 'text/vcard'
        body = rv.data.decode('utf-8')
        assert 'BEGIN:VCARD' in body
        assert 'Origen Inbox' in body

    def test_dismiss_onboarding_persists(self, app, seed, owner_a_client):
        with app.app_context():
            user = _user(seed, 'owner_a')
            user.has_seen_inbox_onboarding = False
            db.session.commit()

        rv = owner_a_client.post('/inbox/dismiss-onboarding',
                                 follow_redirects=False)
        assert rv.status_code in (200, 204, 302)

        with app.app_context():
            user = _user(seed, 'owner_a')
            assert user.has_seen_inbox_onboarding is True


@pytest.mark.usefixtures('app')
class TestUndoRoute:
    def _make_inbound_with_contacts(self, seed):
        user = _ensure_inbox(seed, 'owner_a')
        c1 = Contact(
            user_id=user.id,
            organization_id=user.organization_id,
            created_by_id=user.id,
            first_name='Undo',
            last_name='One',
            email='undo.one@example.com',
        )
        c2 = Contact(
            user_id=user.id,
            organization_id=user.organization_id,
            created_by_id=user.id,
            first_name='Keep',
            last_name='Two',
            email='keep.two@example.com',
        )
        db.session.add_all([c1, c2])
        db.session.flush()
        msg = InboundMessage(
            organization_id=user.organization_id,
            user_id=user.id,
            recipient_address=user.inbox_address,
            sender_email=user.email,
            subject='Undo route test',
            source_kind='text',
            status='processed',
            created_contact_ids=[c1.id, c2.id],
        )
        db.session.add(msg)
        db.session.commit()
        return msg.id, c1.id, c2.id

    def test_contact_specific_undo_only_removes_one_contact(
            self, app, seed, client):
        from services.sendgrid_outbound import make_undo_token

        with app.app_context():
            inbound_id, remove_id, keep_id = self._make_inbound_with_contacts(
                seed)
            token = make_undo_token(inbound_id, contact_id=remove_id)

        rv = client.get(f'/inbox/undo/{token}', follow_redirects=False)
        assert rv.status_code in (301, 302)

        with app.app_context():
            msg = InboundMessage.query.get(inbound_id)
            assert Contact.query.get(remove_id) is None
            assert Contact.query.get(keep_id) is not None
            assert msg.status == 'processed'
            assert msg.created_contact_ids == [keep_id]

    def test_batch_undo_still_removes_all_contacts(self, app, seed, client):
        from services.sendgrid_outbound import make_undo_token

        with app.app_context():
            inbound_id, remove_id, keep_id = self._make_inbound_with_contacts(
                seed)
            token = make_undo_token(inbound_id)

        rv = client.get(f'/inbox/undo/{token}', follow_redirects=False)
        assert rv.status_code in (301, 302)

        with app.app_context():
            msg = InboundMessage.query.get(inbound_id)
            assert Contact.query.get(remove_id) is None
            assert Contact.query.get(keep_id) is None
            assert msg.status == 'rejected'
            assert msg.created_contact_ids == []


# ---------------------------------------------------------------------------
# Webhook end-to-end (signature off, AI mocked)
# ---------------------------------------------------------------------------

@pytest.mark.usefixtures('app')
class TestWebhook:
    @pytest.fixture(autouse=True)
    def _no_inbox_secrets(self, monkeypatch):
        """These tests exercise non-auth behavior. Ensure the shared-secret
        and public-key env vars are unset so the webhook accepts requests
        even when the developer ran tests after `source .env`."""
        monkeypatch.delenv('SENDGRID_INBOUND_WEBHOOK_SECRET', raising=False)
        monkeypatch.delenv('SENDGRID_INBOUND_PUBLIC_KEY', raising=False)

    def _post(self, client, recipient, **extra):
        form = {
            'to': recipient,
            'from': 'sender@example.com',
            'subject': 'Forwarded contact',
            'text': 'Hi! Adding Sarah Chen, sarah@example.com, 555-123-9999.',
            'envelope': json.dumps({'to': [recipient],
                                    'from': 'sender@example.com'}),
            'spam_score': '0.0',
        }
        form.update(extra)
        return client.post('/webhooks/sendgrid/inbound-parse', data=form)

    def test_unknown_recipient_returns_200(self, client):
        rv = self._post(
            client, f'unknown-aaaa2345@{get_inbox_domain()}')
        assert rv.status_code == 200

    def test_high_spam_score_dropped(self, app, seed, client):
        with app.app_context():
            user = _ensure_inbox(seed, 'agent_a')
            recipient = user.inbox_address

        rv = self._post(client, recipient, spam_score='9.5')
        assert rv.status_code == 200
        with app.app_context():
            # Webhook should still record nothing AI-processed for spam drops.
            count = (InboundMessage.query
                     .filter_by(user_id=seed['agent_a'])
                     .count())
            # Spam is dropped before persisting any row.
            assert count == 0

    def test_happy_path_creates_contact(self, app, seed, client):
        from services.ai_service import INBOX_PRIMARY_MODEL  # noqa: F401

        with app.app_context():
            user = _ensure_inbox(seed, 'agent_a')
            recipient = user.inbox_address

        ai_payload = {
            'contacts': [{
                'first_name': 'Sarah', 'last_name': 'Chen',
                'email': 'sarah.webhook@example.com', 'phone': '555-321-7777',
                'street_address': None, 'city': None,
                'state': None, 'zip_code': None,
                'notes': 'From webhook test', 'confidence': 'high',
            }],
            '_meta': {'model': 'gpt-5.4-nano',
                      'tokens_in': 100, 'tokens_out': 25},
        }

        with patch('services.contact_extraction.generate_contact_extraction',
                   return_value=ai_payload):
            rv = self._post(client, recipient)

        assert rv.status_code == 200
        with app.app_context():
            created = (Contact.query
                       .filter_by(email='sarah.webhook@example.com')
                       .all())
            assert len(created) == 1
            inbound = (InboundMessage.query
                       .filter_by(user_id=seed['agent_a'])
                       .order_by(InboundMessage.id.desc())
                       .first())
            assert inbound is not None
            assert inbound.status == 'processed'
            assert created[0].id in (inbound.created_contact_ids or [])

    def test_ai_failure_marks_failed_but_returns_200(self, app, seed, client):
        with app.app_context():
            user = _ensure_inbox(seed, 'agent_a')
            recipient = user.inbox_address

        with patch('services.contact_extraction.generate_contact_extraction',
                   side_effect=RuntimeError('boom')):
            rv = self._post(client, recipient)

        assert rv.status_code == 200
        with app.app_context():
            inbound = (InboundMessage.query
                       .filter_by(user_id=seed['agent_a'])
                       .order_by(InboundMessage.id.desc())
                       .first())
            assert inbound is not None
            assert inbound.status == 'failed'
            assert 'boom' in (inbound.error_message or '')

    def test_oversized_payload_returns_200(self, client):
        with patch('routes.inbound_email._resolve_recipient',
                   side_effect=RequestEntityTooLarge()):
            rv = self._post(
                client, f'big-file-aaaa2345@{get_inbox_domain()}')

        assert rv.status_code == 200


# ---------------------------------------------------------------------------
# Webhook authentication (shared secret + public key fallbacks)
# ---------------------------------------------------------------------------

class TestWebhookAuth:
    """Shared-secret auth is the realistic path for SendGrid Inbound Parse,
    which doesn't natively sign requests. We accept the secret either as a
    ``?secret=`` query param (what SendGrid configures) or an
    ``X-Inbound-Secret`` header (for reverse proxies)."""

    SECRET = 'super-secret-token-abc123'

    def _post(self, client, recipient, *, url_secret=None, header_secret=None):
        url = '/webhooks/sendgrid/inbound-parse'
        if url_secret is not None:
            url = f'{url}?secret={url_secret}'
        headers = {}
        if header_secret is not None:
            headers['X-Inbound-Secret'] = header_secret
        form = {
            'to': recipient,
            'from': 'sender@example.com',
            'subject': 'Auth probe',
            'text': 'Sarah Chen, sarah@example.com, 555-123-9999.',
            'envelope': json.dumps({'to': [recipient],
                                    'from': 'sender@example.com'}),
            'spam_score': '0.0',
        }
        return client.post(url, data=form, headers=headers)

    def test_missing_secret_when_required_drops_silently(
            self, app, seed, client, monkeypatch):
        """When the secret is configured but the request omits it, we still
        return 200 (SendGrid won't retry on 4xx anyway) but the message must
        not be persisted or processed."""
        monkeypatch.setenv('SENDGRID_INBOUND_WEBHOOK_SECRET', self.SECRET)
        with app.app_context():
            user = _ensure_inbox(seed, 'agent_a')
            recipient = user.inbox_address
            before = InboundMessage.query.filter_by(
                user_id=seed['agent_a']).count()

        with patch('services.contact_extraction.generate_contact_extraction'
                   ) as ai:
            rv = self._post(client, recipient)

        assert rv.status_code == 200
        ai.assert_not_called()
        with app.app_context():
            after = InboundMessage.query.filter_by(
                user_id=seed['agent_a']).count()
            assert after == before, (
                'Auth-failed webhook hit must not persist an InboundMessage')

    def test_wrong_secret_dropped(self, app, seed, client, monkeypatch):
        monkeypatch.setenv('SENDGRID_INBOUND_WEBHOOK_SECRET', self.SECRET)
        with app.app_context():
            user = _ensure_inbox(seed, 'agent_a')
            recipient = user.inbox_address
            before = InboundMessage.query.filter_by(
                user_id=seed['agent_a']).count()

        with patch('services.contact_extraction.generate_contact_extraction'
                   ) as ai:
            rv = self._post(client, recipient, url_secret='not-the-secret')

        assert rv.status_code == 200
        ai.assert_not_called()
        with app.app_context():
            after = InboundMessage.query.filter_by(
                user_id=seed['agent_a']).count()
            assert after == before

    def test_secret_via_query_param_accepted(
            self, app, seed, client, monkeypatch):
        monkeypatch.setenv('SENDGRID_INBOUND_WEBHOOK_SECRET', self.SECRET)
        with app.app_context():
            user = _ensure_inbox(seed, 'agent_a')
            recipient = user.inbox_address

        ai_payload = {
            'contacts': [{
                'first_name': 'Auth', 'last_name': 'OkQuery',
                'email': 'auth.okquery@example.com', 'phone': '555-000-1111',
                'street_address': None, 'city': None, 'state': None,
                'zip_code': None, 'notes': '', 'confidence': 'high',
            }],
            '_meta': {'model': 'gpt-5.4-nano',
                      'tokens_in': 50, 'tokens_out': 10},
        }
        with patch('services.contact_extraction.generate_contact_extraction',
                   return_value=ai_payload):
            rv = self._post(client, recipient, url_secret=self.SECRET)

        assert rv.status_code == 200
        with app.app_context():
            assert Contact.query.filter_by(
                email='auth.okquery@example.com').count() == 1

    def test_secret_via_header_accepted(
            self, app, seed, client, monkeypatch):
        """Lets a reverse proxy strip the ?secret= from the URL and forward it
        as a header instead."""
        monkeypatch.setenv('SENDGRID_INBOUND_WEBHOOK_SECRET', self.SECRET)
        with app.app_context():
            user = _ensure_inbox(seed, 'agent_a')
            recipient = user.inbox_address

        ai_payload = {
            'contacts': [{
                'first_name': 'Auth', 'last_name': 'OkHeader',
                'email': 'auth.okheader@example.com', 'phone': '555-000-2222',
                'street_address': None, 'city': None, 'state': None,
                'zip_code': None, 'notes': '', 'confidence': 'high',
            }],
            '_meta': {'model': 'gpt-5.4-nano',
                      'tokens_in': 50, 'tokens_out': 10},
        }
        with patch('services.contact_extraction.generate_contact_extraction',
                   return_value=ai_payload):
            rv = self._post(client, recipient, header_secret=self.SECRET)

        assert rv.status_code == 200
        with app.app_context():
            assert Contact.query.filter_by(
                email='auth.okheader@example.com').count() == 1

    def test_no_secret_configured_accepts_unverified(
            self, app, seed, client, monkeypatch):
        """Backwards compat: in dev with neither env var set, requests are
        accepted with a warning so devs can iterate without ngrok."""
        monkeypatch.delenv('SENDGRID_INBOUND_WEBHOOK_SECRET', raising=False)
        monkeypatch.delenv('SENDGRID_INBOUND_PUBLIC_KEY', raising=False)
        with app.app_context():
            user = _ensure_inbox(seed, 'agent_a')
            recipient = user.inbox_address

        ai_payload = {
            'contacts': [{
                'first_name': 'Dev', 'last_name': 'Noauth',
                'email': 'dev.noauth@example.com', 'phone': '555-000-3333',
                'street_address': None, 'city': None, 'state': None,
                'zip_code': None, 'notes': '', 'confidence': 'high',
            }],
            '_meta': {'model': 'gpt-5.4-nano',
                      'tokens_in': 50, 'tokens_out': 10},
        }
        with patch('services.contact_extraction.generate_contact_extraction',
                   return_value=ai_payload):
            rv = self._post(client, recipient)

        assert rv.status_code == 200
        with app.app_context():
            assert Contact.query.filter_by(
                email='dev.noauth@example.com').count() == 1
