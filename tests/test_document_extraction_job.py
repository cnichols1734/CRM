"""
Tests for the RQ document extraction pipeline.

Covers:
- post_upload_processing() enqueue failure is non-fatal (upload still succeeds)
- extract_document_job() fails fast when document has no storage path
- extract_document_job() marks extraction as 'failed' on download error
"""
import io
from unittest.mock import patch, MagicMock


class TestPostUploadEnqueue:
    """Verify that enqueue failure does not break the upload response."""

    def test_fulfill_succeeds_when_redis_unavailable(self, owner_a_client, seed):
        """
        Upload should return 200 even if Redis is down and enqueue raises.
        The document gets committed; extraction_status stays 'pending'.
        """
        pdf_bytes = b'%PDF-1.4 fake content'
        data = {
            'file': (io.BytesIO(pdf_bytes), 'listing.pdf'),
        }

        with patch('redis.Redis.from_url', side_effect=ConnectionError("Redis unavailable")), \
             patch('services.supabase_storage.get_supabase_client') as mock_sb:
            mock_client = MagicMock()
            mock_client.storage.from_.return_value.upload.return_value = None
            mock_sb.return_value = mock_client

            resp = owner_a_client.post(
                f'/transactions/{seed["tx_a"]}/documents/{seed["doc_a"]}/fulfill',
                data=data,
                content_type='multipart/form-data',
            )

        assert resp.status_code == 200
        result = resp.get_json()
        assert result['success'] is True

    def test_fulfill_enqueues_when_redis_available(self, owner_a_client, seed):
        """When Redis is available, enqueue is called with correct args."""
        pdf_bytes = b'%PDF-1.4 fake content'
        data = {
            'file': (io.BytesIO(pdf_bytes), 'listing.pdf'),
        }

        mock_queue_instance = MagicMock()

        with patch('rq.Queue', return_value=mock_queue_instance), \
             patch('redis.Redis.from_url', return_value=MagicMock()), \
             patch('services.supabase_storage.get_supabase_client') as mock_sb:
            mock_client = MagicMock()
            mock_client.storage.from_.return_value.upload.return_value = None
            mock_sb.return_value = mock_client

            resp = owner_a_client.post(
                f'/transactions/{seed["tx_a"]}/documents/{seed["doc_a"]}/fulfill',
                data=data,
                content_type='multipart/form-data',
            )

        assert resp.status_code == 200
        mock_queue_instance.enqueue.assert_called_once()
        call_kwargs = mock_queue_instance.enqueue.call_args
        assert call_kwargs.kwargs['doc_id'] == seed['doc_a']


class TestExtractDocumentJob:
    """Unit tests for the RQ job function itself."""

    def test_missing_storage_path_marks_failed(self, app, db, seed):
        """Document with no source_file_path or signed_file_path fails fast."""
        from models import TransactionDocument
        from jobs.document_extraction import extract_document_job

        with app.app_context():
            doc = db.session.get(TransactionDocument, seed['doc_a'])
            doc.source_file_path = None
            doc.signed_file_path = None
            doc.extraction_status = 'pending'
            doc.extraction_error = None
            db.session.flush()

            with patch('jobs.document_extraction.set_job_org_context'), \
                 patch('models.db.session.remove'):
                extract_document_job(doc_id=seed['doc_a'], org_id=seed['org_a'])

            db.session.expire_all()
            doc = db.session.get(TransactionDocument, seed['doc_a'])
            assert doc.extraction_status == 'failed'
            assert 'cannot download for extraction' in (doc.extraction_error or '').lower()

    def test_download_failure_marks_failed(self, app, db, seed):
        """If Supabase download raises, extraction is marked failed."""
        from models import TransactionDocument
        from jobs.document_extraction import extract_document_job

        with app.app_context():
            doc = db.session.get(TransactionDocument, seed['doc_a'])
            doc.source_file_path = 'documents/test/fake.pdf'
            doc.signed_file_path = None
            doc.extraction_status = 'pending'
            doc.extraction_error = None
            db.session.flush()

            with patch('jobs.document_extraction.set_job_org_context'), \
                 patch('models.db.session.remove'), \
                 patch('services.supabase_storage.download_document',
                       side_effect=Exception("Storage unavailable")):
                extract_document_job(doc_id=seed['doc_a'], org_id=seed['org_a'])

            db.session.expire_all()
            doc = db.session.get(TransactionDocument, seed['doc_a'])
            assert doc.extraction_status == 'failed'
            assert 'storage unavailable' in (doc.extraction_error or '').lower()

    def test_nonexistent_doc_does_not_raise(self, app, seed):
        """Job with a bad doc_id logs an error but does not raise."""
        from jobs.document_extraction import extract_document_job

        with app.app_context():
            with patch('jobs.document_extraction.set_job_org_context'), \
                 patch('models.db.session.remove'):
                extract_document_job(doc_id=999999, org_id=seed['org_a'])
