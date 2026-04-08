"""
Document data extraction service.

Uses GPT-4.1-mini vision to extract structured field data from uploaded
PDF documents. Each document type has a registered extraction schema
that defines which fields to extract and how to prompt the AI.

The extracted data is stored in TransactionDocument.field_data and used
to populate UI sections (e.g., LISTING INFO) without manual form entry.
"""

import base64
import logging

import fitz  # PyMuPDF

logger = logging.getLogger(__name__)

EXTRACTION_SCHEMAS = {
    'listing-agreement': {
        'fields': {
            'list_price': 'The listing/sales price of the property (digits only, no $ or commas)',
            'listing_start_date': 'The listing agreement start/beginning date (YYYY-MM-DD)',
            'listing_end_date': 'The listing agreement end/expiration date (YYYY-MM-DD)',
            'total_commission': 'Total commission percentage from Section 5A(1) (number only, no %)',
            'buyer_agent_percent': 'Buyer agent/other broker percentage share from Section 5A(2) (number only, no %)',
            'buyer_agent_flat': 'Buyer agent/other broker flat fee from Section 5A(2) (digits only, no $ or commas)',
            'listing_only_percent': 'Listing broker only fee percentage from Section 5B(1) (number only, no %)',
            'listing_only_flat': 'Listing broker only flat fee from Section 5B(1) (digits only, no $ or commas)',
            'protection_period_days': 'Number of days for the protection period from Section 5F (number only)',
            'financing_types': 'Comma-separated list of accepted financing types checked in Section 11C (e.g. "Conventional, VA, FHA, Cash"). Only include types that are explicitly checked/marked on the document.',
            'has_hoa': 'Whether the property is subject to a mandatory HOA from Section 2E. Return "yes" if "is" is checked, "no" if "is not" is checked, or null if neither is marked.',
            'special_provisions': 'The full text of any special provisions from Section 15. Return the exact text as written, or null if blank.',
        },
        'system_prompt': (
            "You are a precise document data extractor for Texas residential listing agreements. "
            "Extract ONLY the values explicitly written on the document. "
            "Do NOT invent or guess values. If a field is not filled in, blank, or not found, use null."
        ),
    },
}


def _render_pdf_to_images(file_data: bytes) -> list:
    """Render all PDF pages to base64-encoded PNG images."""
    images = []
    doc = fitz.open(stream=file_data, filetype="pdf")
    try:
        for page in doc:
            pix = page.get_pixmap(dpi=200)
            png_bytes = pix.tobytes("png")
            images.append(base64.b64encode(png_bytes).decode('ascii'))
    finally:
        doc.close()
    return images


def _build_extraction_prompt(schema: dict) -> str:
    """Build the user prompt with field definitions and format instructions."""
    lines = [
        "Extract the following fields from this document.",
        "Return ONLY a JSON object with these exact keys.",
        "If a field is not found or is blank, use null. Do NOT invent values.",
        "",
        "Fields to extract:",
    ]
    for key, description in schema['fields'].items():
        lines.append(f'  - "{key}": {description}')

    lines.extend([
        "",
        "Format rules:",
        "- Dates MUST be YYYY-MM-DD format",
        "- Currency/price values: digits only, no $ sign or commas (e.g. 450000)",
        "- Percentage values: number only, no % sign (e.g. 6)",
        "- Flat fee values: digits only, no $ sign or commas",
        "",
        "Return the JSON object now.",
    ])
    return "\n".join(lines)


def _set_rls(org_id: int):
    """Re-set RLS context. Must be called after every commit since SET LOCAL is transaction-scoped."""
    from jobs.base import set_job_org_context
    set_job_org_context(org_id)


def extract_document_data(doc_id: int, org_id: int, file_data: bytes):
    """
    Extract structured data from a document PDF and store in field_data.

    Runs inside a background thread with its own DB session and RLS context.
    The caller is responsible for setting up app context before calling.
    org_id is required to re-set RLS after each commit.
    """
    from models import db, TransactionDocument

    _set_rls(org_id)
    doc = TransactionDocument.query.get(doc_id)
    if not doc:
        logger.error(f"Document {doc_id} not found for extraction")
        return

    schema = EXTRACTION_SCHEMAS.get(doc.template_slug)
    if not schema:
        logger.warning(f"No extraction schema for template_slug={doc.template_slug}")
        return

    doc.extraction_status = 'processing'
    db.session.commit()

    try:
        _set_rls(org_id)

        images = _render_pdf_to_images(file_data)
        logger.info(f"Rendered {len(images)} pages for doc {doc_id}")

        from services.ai_service import generate_document_extraction

        result = generate_document_extraction(
            system_prompt=schema['system_prompt'],
            user_prompt=_build_extraction_prompt(schema),
            images=images,
        )

        logger.info(f"Raw extraction result for doc {doc_id}: {result}")

        doc.field_data = {key: result.get(key) for key in schema['fields'] if result.get(key) is not None}

        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(doc, 'field_data')

        doc.extraction_status = 'complete'
        doc.extraction_error = None
        db.session.commit()
        logger.info(f"Extraction complete for doc {doc_id}: {len(doc.field_data)} fields populated")

    except Exception as e:
        db.session.rollback()
        try:
            _set_rls(org_id)
            doc = TransactionDocument.query.get(doc_id)
            if doc:
                doc.extraction_status = 'failed'
                doc.extraction_error = str(e)[:500]
                db.session.commit()
        except Exception:
            logger.error(f"Failed to update extraction_status for doc {doc_id}", exc_info=True)

        logger.error(f"Document extraction failed for doc {doc_id}: {e}", exc_info=True)
