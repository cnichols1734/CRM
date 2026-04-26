"""
RQ job for AI document data extraction.

Downloads the PDF from Supabase Storage and runs the extraction pipeline
(PyMuPDF rendering + GPT vision) inside the worker process, keeping the
web workers free from memory-heavy rendering.
"""
import logging

from jobs.base import set_job_org_context

logger = logging.getLogger(__name__)


def extract_document_job(doc_id: int, org_id: int, _inline=False):
    """
    Fetch PDF from Supabase and extract structured field data via AI.

    Only doc_id and org_id are passed through the queue -- the PDF bytes
    are fetched directly from storage so nothing large transits Redis.

    When _inline=True the job runs inside a web request and skips
    db.session.remove() so the caller's session stays intact.
    """
    from models import db, TransactionDocument
    from services.supabase_storage import download_document
    from services.document_extractor import extract_document_data

    try:
        set_job_org_context(org_id)
        doc = TransactionDocument.query.get(doc_id)
        if not doc:
            logger.error(f"Document {doc_id} not found for extraction")
            return

        file_path = doc.source_file_path or doc.signed_file_path
        if not file_path:
            raise ValueError(
                f"Document {doc_id} has no source_file_path or signed_file_path "
                "-- cannot download for extraction"
            )

        file_data = download_document(file_path)
        extract_document_data(doc_id, org_id, file_data)

    except Exception as e:
        logger.error(f"Document extraction job failed for doc {doc_id}: {e}", exc_info=True)
        try:
            set_job_org_context(org_id)
            doc = TransactionDocument.query.get(doc_id)
            if doc:
                doc.extraction_status = 'failed'
                doc.extraction_error = str(e)[:500]
                db.session.commit()
        except Exception:
            logger.error(f"Failed to mark doc {doc_id} as failed", exc_info=True)
    finally:
        if not _inline:
            db.session.remove()
