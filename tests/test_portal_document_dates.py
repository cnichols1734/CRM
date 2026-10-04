"""Document timestamp contract, using synthetic portal records."""
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
import unittest

from services.portal_service import _documents_block


class PortalDocumentDateTests(unittest.TestCase):
    def completed_document(self, signed_at):
        document = SimpleNamespace(
            id=17, template_name='Listing Agreement', status='signed',
            signed_at=signed_at, signed_file_path='fixture/signed.pdf',
            signatures=SimpleNamespace(all=lambda: []),
        )
        transaction = SimpleNamespace(documents=SimpleNamespace(all=lambda: [document]))
        participant = SimpleNamespace(display_email='fixture@example.com')
        return _documents_block(transaction, participant)['completed'][0]

    def test_naive_database_timestamp_is_utc_with_fractional_seconds(self):
        row = self.completed_document(datetime(2026, 10, 3, 14, 25, 36, 123456))
        self.assertEqual(row['signed_at'], '2026-10-03T14:25:36.123456Z')
        self.assertEqual(row['signed_on'], 'Oct 3')

    def test_aware_timestamp_preserves_instant_across_year_boundary(self):
        row = self.completed_document(datetime(
            2025, 12, 31, 23, 30, tzinfo=timezone(timedelta(hours=-6)),
        ))
        self.assertEqual(row['signed_at'], '2026-01-01T05:30:00Z')
        self.assertEqual(row['signed_on'], 'Dec 31')

    def test_missing_timestamp_stays_unknown(self):
        row = self.completed_document(None)
        self.assertIsNone(row['signed_at'])
        self.assertIsNone(row['signed_on'])
        self.assertTrue(row['can_view'])

    def test_date_only_value_does_not_invent_a_signing_instant(self):
        row = self.completed_document(date(2026, 10, 3))
        self.assertIsNone(row['signed_at'])
        self.assertEqual(row['signed_on'], 'Oct 3')


if __name__ == '__main__':
    unittest.main()
