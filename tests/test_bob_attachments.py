"""Tests for smart UI B.O.B. attachment handling."""
from __future__ import annotations

import io
import os
import sys
from datetime import datetime
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models import BobAction, Contact, Task, db
from services.bob_attachment_refs import (
    AttachmentRefError,
    make_attachment_ref,
    parse_attachment_ref,
    sha256_digest,
)
from services.bob_attachments import (
    INTENT_AMBIGUOUS,
    INTENT_ANALYZE,
    INTENT_CREATE,
    KIND_CSV,
    KIND_IMAGE,
    KIND_TXT,
    AttachmentParseError,
    classify_attachment_intent,
    classify_kind,
    parse_attachment,
    query_tabular,
)
from services.bob_tools import (
    AttachmentTurnContext,
    BobContext,
    confirm_action,
    dispatch,
    reject_action,
    undo_action,
)
from services.contact_import import (
    ContactImportError,
    execute_contact_import,
    map_contact_columns,
    parse_contact_rows,
    preview_contact_import,
)


@pytest.fixture(autouse=True)
def _reset_shared_caches():
    yield
    from services.cache_helpers import _cache
    _cache.clear()


@pytest.fixture()
def ctx_agent_a(seed):
    return BobContext(
        user_id=seed['agent_a'], organization_id=seed['org_a'],
        org_role='agent', is_org_admin=False,
    )


def _unique(prefix='x'):
    return f'{prefix}{datetime.utcnow().strftime("%H%M%S%f")}'


# ---------------------------------------------------------------------------
# Intent
# ---------------------------------------------------------------------------

class TestAttachmentIntent:
    def test_create_phrases(self):
        assert classify_attachment_intent(
            'Create this contact please', KIND_IMAGE,
        ) == INTENT_CREATE
        assert classify_attachment_intent(
            'Import these contacts', KIND_CSV,
        ) == INTENT_CREATE
        assert classify_attachment_intent(
            'Add them to my CRM', KIND_IMAGE,
        ) == INTENT_CREATE

    def test_analyze_phrases(self):
        assert classify_attachment_intent(
            'What does this card say?', KIND_IMAGE,
        ) == INTENT_ANALYZE
        assert classify_attachment_intent(
            'How many rows are in this spreadsheet?', KIND_CSV,
        ) == INTENT_ANALYZE

    def test_empty_rules(self):
        assert classify_attachment_intent(
            '', KIND_IMAGE, is_empty=False,
        ) == INTENT_ANALYZE
        assert classify_attachment_intent(
            '', KIND_CSV, is_empty=True,
        ) == INTENT_AMBIGUOUS


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

class TestParsers:
    def test_csv_parse_and_stats(self):
        data = (
            b'first_name,last_name,email,phone\n'
            b'Casey,Card,casey@example.com,8325550199\n'
            b'Jordan,Lee,jordan@example.com,\n'
        )
        parsed = parse_attachment(data, filename='people.csv', mime='text/csv')
        assert parsed.kind == KIND_CSV
        assert len(parsed.rows) == 2
        assert parsed.headers[0] == 'first_name'
        stats = parsed.stats
        assert stats['row_count'] == 2
        assert stats['missing_by_column']['phone'] == 1

    def test_csv_row_cap(self):
        header = 'first_name,last_name\n'
        body = ''.join(f'A{i},B{i}\n' for i in range(520))
        parsed = parse_attachment(
            (header + body).encode('utf-8'),
            filename='big.csv',
            mime='text/csv',
        )
        assert len(parsed.rows) == 500
        assert parsed.truncated is True

    def test_txt_parse(self):
        parsed = parse_attachment(
            b'Hello contact notes',
            filename='note.txt',
            mime='text/plain',
        )
        assert parsed.kind == KIND_TXT
        assert 'Hello contact notes' in parsed.text

    def test_xlsx_parse(self):
        from openpyxl import Workbook

        wb = Workbook()
        ws = wb.active
        ws.append(['First Name', 'Last Name', 'Email 1'])
        ws.append(['Casey', 'Card', 'casey@example.com'])
        buf = io.BytesIO()
        wb.save(buf)
        parsed = parse_attachment(
            buf.getvalue(),
            filename='people.xlsx',
            mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
        assert parsed.kind == 'xlsx'
        assert len(parsed.rows) == 1
        assert parsed.rows[0]['First Name'] == 'Casey'

    def test_doc_rejected(self):
        with pytest.raises(AttachmentParseError):
            parse_attachment(b'legacy', filename='old.doc', mime='application/msword')

    def test_query_tabular_filter(self):
        rows = [
            {'City': 'Houston', 'Name': 'A'},
            {'City': 'Dallas', 'Name': 'B'},
            {'City': 'Houston', 'Name': 'C'},
        ]
        result = query_tabular(
            rows,
            operation='count',
            filters=[{'column': 'City', 'op': 'eq', 'value': 'Houston'}],
        )
        assert result['count'] == 2


# ---------------------------------------------------------------------------
# Attachment refs
# ---------------------------------------------------------------------------

class TestAttachmentRefs:
    def test_make_and_parse(self, app, seed):
        with app.app_context():
            data = b'hello-bytes'
            digest = sha256_digest(data)
            token = make_attachment_ref(
                user_id=seed['agent_a'],
                organization_id=seed['org_a'],
                storage_path=f'user_{seed["agent_a"]}/abc.txt',
                mime='text/plain',
                size=len(data),
                filename='abc.txt',
                digest=digest,
            )
            meta = parse_attachment_ref(
                token,
                user_id=seed['agent_a'],
                organization_id=seed['org_a'],
            )
            assert meta.filename == 'abc.txt'
            assert meta.digest == digest

    def test_wrong_user_rejected(self, app, seed):
        with app.app_context():
            token = make_attachment_ref(
                user_id=seed['agent_a'],
                organization_id=seed['org_a'],
                storage_path=f'user_{seed["agent_a"]}/abc.txt',
                mime='text/plain',
                size=3,
                filename='abc.txt',
                digest=sha256_digest(b'abc'),
            )
            with pytest.raises(AttachmentRefError):
                parse_attachment_ref(
                    token,
                    user_id=seed['owner_b'],
                    organization_id=seed['org_b'],
                )

    def test_bad_prefix_rejected(self, app, seed):
        with app.app_context():
            with pytest.raises(AttachmentRefError):
                make_attachment_ref(
                    user_id=seed['agent_a'],
                    organization_id=seed['org_a'],
                    storage_path='other_user/abc.txt',
                    mime='text/plain',
                    size=3,
                    filename='abc.txt',
                    digest=sha256_digest(b'abc'),
                )


# ---------------------------------------------------------------------------
# Contact import service
# ---------------------------------------------------------------------------

class TestContactImport:
    def test_column_aliases(self):
        mapping = map_contact_columns([
            'First Name', 'Last Name', 'Email 1', 'Phone Number 1',
        ])
        assert mapping['First Name'] == 'first_name'
        assert mapping['Email 1'] == 'email'

    def test_preview_and_execute(self, app, seed, ctx_agent_a):
        with app.app_context():
            email = f'{_unique("imp")}@example.com'
            rows, meta = parse_contact_rows(
                (
                    'first_name,last_name,email,phone\n'
                    f'Casey,Card,{email},8325550199\n'
                    f'Casey,Card,{email},8325550199\n'
                ).encode('utf-8'),
                'people.csv',
                'text/csv',
                max_rows=500,
                use_ai_headers=False,
            )
            preview = preview_contact_import(
                rows,
                actor_user_id=ctx_agent_a.user_id,
                owner_user_id=ctx_agent_a.user_id,
                org_id=ctx_agent_a.organization_id,
                warnings=meta['warnings'],
                column_mapping=meta['column_mapping'],
            )
            assert preview.create_count == 1
            assert preview.duplicate_count == 1

            result = execute_contact_import(
                rows,
                actor_user_id=ctx_agent_a.user_id,
                owner_user_id=ctx_agent_a.user_id,
                org_id=ctx_agent_a.organization_id,
                source='bob_chat_attachment',
            )
            assert len(result.created) == 1
            contact = result.created[0]
            assert contact.email == email

            # Cleanup
            contact.groups.clear()
            db.session.delete(contact)
            db.session.commit()


# ---------------------------------------------------------------------------
# Tool policy + import confirm/undo
# ---------------------------------------------------------------------------

class TestAttachmentTools:
    def test_import_blocked_without_create_intent(self, app, ctx_agent_a):
        with app.app_context():
            ctx = ctx_agent_a.with_attachment(AttachmentTurnContext(
                intent=INTENT_ANALYZE,
                kind=KIND_CSV,
                filename='people.csv',
                mime='text/csv',
                attachment_ref='fake',
                allow_attachment_writes=False,
            ))
            result = dispatch('import_contacts', {}, ctx)
            assert result.ok is False
            assert 'blocked' in (result.error or '').lower()

    def test_import_candidates_confirm_and_undo(self, app, seed, ctx_agent_a):
        with app.app_context():
            email = f'{_unique("batch")}@example.com'
            candidates = [{
                'first_name': 'Casey',
                'last_name': 'Batch',
                'email': email,
                'phone': '(832) 555-0188',
                'street_address': None,
                'city': 'Houston',
                'state': 'TX',
                'zip_code': '77002',
                'notes': 'From photo',
                'group_name': None,
                'confidence': 'high',
            }, {
                'first_name': 'Jordan',
                'last_name': 'Batch',
                'email': f'{_unique("batch2")}@example.com',
                'phone': '(832) 555-0187',
                'street_address': None,
                'city': 'Houston',
                'state': 'TX',
                'zip_code': '77002',
                'notes': None,
                'group_name': None,
                'confidence': 'high',
            }]
            ctx = ctx_agent_a.with_attachment(AttachmentTurnContext(
                intent=INTENT_CREATE,
                kind=KIND_IMAGE,
                filename='card.jpg',
                mime='image/jpeg',
                attachment_ref='unused-because-candidates',
                allow_attachment_writes=True,
                candidate_count=2,
            ))
            pending = dispatch(
                'import_contacts',
                {'candidates': candidates},
                ctx,
            )
            assert pending.requires_confirmation is True
            assert pending.data['preview']['create_count'] == 2

            confirmed = confirm_action(pending.action_id, ctx_agent_a)
            assert confirmed.ok is True
            assert confirmed.undoable is True
            ids = (confirmed.data or {}).get('contact_ids') or []
            assert len(ids) == 2

            undone = undo_action(confirmed.action_id, ctx_agent_a)
            assert undone.ok is True
            remaining = Contact.query.filter(Contact.id.in_(ids)).count()
            assert remaining == 0

    def test_import_reject_creates_nothing(self, app, ctx_agent_a):
        with app.app_context():
            email = f'{_unique("rej")}@example.com'
            ctx = ctx_agent_a.with_attachment(AttachmentTurnContext(
                intent=INTENT_CREATE,
                kind=KIND_IMAGE,
                filename='card.jpg',
                mime='image/jpeg',
                attachment_ref='unused',
                allow_attachment_writes=True,
            ))
            pending = dispatch('import_contacts', {
                'candidates': [{
                    'first_name': 'Reject',
                    'last_name': 'Me',
                    'email': email,
                    'phone': '8325550111',
                    'confidence': 'high',
                }],
            }, ctx)
            rejected = reject_action(pending.action_id, ctx_agent_a)
            assert rejected.ok is True
            assert Contact.query.filter_by(email=email).first() is None

    def test_batch_undo_blocked_when_task_exists(self, app, seed, ctx_agent_a):
        with app.app_context():
            email = f'{_unique("tasky")}@example.com'
            ctx = ctx_agent_a.with_attachment(AttachmentTurnContext(
                intent=INTENT_CREATE,
                kind=KIND_IMAGE,
                filename='card.jpg',
                mime='image/jpeg',
                attachment_ref='unused',
                allow_attachment_writes=True,
            ))
            pending = dispatch('import_contacts', {
                'candidates': [{
                    'first_name': 'Tasky',
                    'last_name': 'Person',
                    'email': email,
                    'phone': '8325550122',
                    'confidence': 'high',
                }, {
                    'first_name': 'Tasky',
                    'last_name': 'Two',
                    'email': f'{_unique("tasky2")}@example.com',
                    'phone': '8325550123',
                    'confidence': 'high',
                }],
            }, ctx)
            confirmed = confirm_action(pending.action_id, ctx_agent_a)
            ids = confirmed.data['contact_ids']

            # Attach a task to one imported contact.
            from datetime import timedelta

            from models import TaskSubtype, TaskType
            ttype = TaskType.query.filter_by(
                organization_id=ctx_agent_a.organization_id,
            ).first()
            subtype = TaskSubtype.query.filter_by(task_type_id=ttype.id).first()
            task = Task(
                organization_id=ctx_agent_a.organization_id,
                contact_id=ids[0],
                assigned_to_id=ctx_agent_a.user_id,
                created_by_id=ctx_agent_a.user_id,
                type_id=ttype.id,
                subtype_id=subtype.id,
                subject='Follow up',
                status='pending',
                due_date=datetime.utcnow() + timedelta(days=1),
            )
            db.session.add(task)
            db.session.commit()

            undone = undo_action(confirmed.action_id, ctx_agent_a)
            assert undone.ok is False
            assert Contact.query.filter(Contact.id.in_(ids)).count() == 2

            # Cleanup
            db.session.delete(task)
            for cid in ids:
                contact = db.session.get(Contact, cid)
                if contact:
                    contact.groups.clear()
                    db.session.delete(contact)
            BobAction.query.filter_by(user_id=ctx_agent_a.user_id).delete()
            db.session.commit()
