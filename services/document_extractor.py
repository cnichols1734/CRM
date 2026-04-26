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
    'seller-offer-contract': {
        'fields': {
            'buyer_names': 'Buyer name or names as written in Paragraph 1 or signature blocks.',
            'buyer_agent_name': 'Buyer agent name if visible on the contract or signature/email blocks.',
            'buyer_agent_brokerage': 'Buyer agent brokerage/company if visible.',
            'offer_price': 'Total sales price from Paragraph 3C (digits only, no $ or commas).',
            'cash_down_payment': 'Cash portion/down payment from Paragraph 3A or financing terms (digits only, no $ or commas).',
            'financing_type': 'Financing type, such as cash, conventional, FHA, VA, seller financing, or other.',
            'earnest_money': 'Initial earnest money amount from Paragraph 5 (digits only, no $ or commas).',
            'additional_earnest_money': 'Additional earnest money amount, if any (digits only, no $ or commas).',
            'option_fee': 'Option fee amount, if any (digits only, no $ or commas).',
            'option_period_days': 'Number of option period days for unrestricted right to terminate.',
            'seller_concessions_amount': 'Seller concessions, seller contribution, or buyer expense amount paid by seller (digits only, no $ or commas).',
            'proposed_close_date': 'Closing date from Paragraph 9 (YYYY-MM-DD).',
            'possession_type': 'Possession terms, such as at closing/funding, temporary leaseback, or other.',
            'leaseback_days': 'Number of seller temporary leaseback days if shown.',
            'appraisal_contingency': 'Whether appraisal contingency or appraisal-related addendum/terms are present. Return true, false, or null.',
            'financing_contingency': 'Whether third party financing approval contingency exists. Return true, false, or null.',
            'sale_of_other_property_contingency': 'Whether sale of other property contingency/addendum is present. Return true, false, or null.',
            'inspection_or_repair_terms_summary': 'Short summary of any repair/inspection/as-is terms written in the offer.',
            'title_policy_payer': 'Who pays owner title policy: buyer, seller, split, or null.',
            'survey_payer': 'Who pays for a new survey if needed: buyer, seller, split, or null.',
            'hoa_resale_certificate_payer': 'Who pays HOA/resale certificate fees if stated: buyer, seller, split, or null.',
            'response_deadline_at': 'Offer acceptance deadline/respond-by date and time if written. Use ISO-like YYYY-MM-DDTHH:MM when time is available, otherwise YYYY-MM-DD.',
            'effective_date': 'Effective date if the contract is already executed (YYYY-MM-DD).',
            'title_company': 'Escrow/title company name if written.',
            'escrow_officer': 'Escrow officer name if written.',
            'survey_choice': 'Survey option selected or described.',
            'hoa_applicable': 'Whether HOA/POA addendum or HOA terms appear. Return true, false, or null.',
            'seller_disclosure_required': 'Whether Seller Disclosure Notice is required or referenced. Return true, false, or null.',
            'lead_based_paint_required': 'Whether lead-based paint disclosure/addendum is required or attached. Return true, false, or null.',
            'addenda': (
                'JSON object describing attached addenda and deadline-bearing terms. Include keys when present: '
                'third_party_financing_addendum, sale_of_other_property_addendum, seller_temporary_residential_lease, '
                'backup_addendum, hoa_addendum, lead_based_paint. Use nested simple key/value pairs.'
            ),
            'special_provisions': 'Exact special provisions text if present, or null.',
        },
        'system_prompt': (
            "You are a precise document data extractor for Texas residential purchase offers and TREC contracts. "
            "Extract ONLY values explicitly written on the document and attached addenda. "
            "Do NOT infer legal meaning, do NOT guess missing dates, and use null when a field is blank or not found."
        ),
    },
    'seller-counter-offer': {
        'fields': {},
        'system_prompt': (
            "You are a precise document data extractor for Texas real estate counter offers. "
            "Extract only explicit values and use null for missing fields."
        ),
    },
    'seller-accepted-contract': {
        'fields': {},
        'system_prompt': (
            "You are a precise document data extractor for executed Texas residential contracts. "
            "Extract only explicit values and use null for missing fields."
        ),
    },
    'seller-backup-addendum': {
        'fields': {
            'backup_position': 'Backup position number if shown.',
            'notice_trigger': 'Text describing when the backup contract becomes primary.',
            'option_period_start_rule': 'Text describing when the backup buyer option period starts.',
            'earnest_money_timing': 'Earnest money timing for backup buyer if stated.',
        },
        'system_prompt': (
            "You are a precise document data extractor for Texas Back-Up Addenda. "
            "Extract only explicit values and use null for missing fields."
        ),
    },
    'sellers-disclosure': {
        'fields': {
            'property_address': 'Property address from the notice.',
            'seller_names': 'Seller name or names shown on the notice.',
            'seller_signed_date': 'Seller signature date if shown (YYYY-MM-DD).',
            'buyer_received_date': 'Buyer acknowledgement or received date if shown (YYYY-MM-DD).',
            'seller_occupying_property': 'Whether seller is occupying the property. Return true, false, or null.',
            'seller_not_occupying_duration': 'Text describing how long since seller occupied the property, if shown.',
            'built_before_1978': 'Whether the property was built before 1978. Return true, false, or null.',
            'lead_based_paint_disclosed': 'Whether lead-based paint or hazards are disclosed. Return true, false, or null.',
            'known_defects_or_repairs': 'Concise summary of any known defects, malfunctions, repairs, or additional explanations written on the notice.',
            'roof_type': 'Roof type if written.',
            'roof_age': 'Roof age if written.',
            'flood_insurance_current': 'Whether current flood insurance coverage is marked yes. Return true, false, or null.',
            'previous_flooding': 'Whether any previous flooding/flood damage is disclosed. Return true, false, or null.',
            'flood_zone_summary': 'Floodplain/floodway/reservoir selection summary if marked.',
            'hoa_or_assessment_disclosed': 'Whether HOA, maintenance fees, or assessments are disclosed. Return true, false, or null.',
            'insurance_claims_disclosed': 'Whether non-flood damage insurance claims are disclosed. Return true, false, or null.',
            'utilities_summary': 'Provider names or utility notes listed near the end of the notice.',
        },
        'system_prompt': (
            "You are a precise document data extractor for Texas Seller's Disclosure Notices. "
            "Extract only what is explicitly marked or written. For checkbox questions, return true only when yes is clearly marked, "
            "false only when no is clearly marked, and null when unclear."
        ),
    },
    'hoa-addendum': {
        'fields': {
            'property_address': 'Property address from the addendum.',
            'association_name': 'Name of the property owners association.',
            'association_phone': 'Association phone number if shown.',
            'selected_subdivision_information_option': 'Selected paragraph A option number or text summary.',
            'subdivision_information_delivery_days': 'Number of days for delivery of subdivision information if written.',
            'buyer_termination_days_after_receipt': 'Number of days buyer may terminate after receiving subdivision information.',
            'updated_resale_certificate_required': 'Whether buyer requires an updated resale certificate. Return true, false, or null.',
            'updated_resale_certificate_delivery_days': 'Number of days for updated resale certificate delivery if written.',
            'transfer_fee_cap': 'Maximum buyer-paid association fees/deposits/reserves from Paragraph C (digits only, no $ or commas).',
            'title_company_info_payer': 'Who pays title company association information costs under Paragraph D: buyer, seller, split, or null.',
            'buyer_names': 'Buyer name or names if visible.',
            'seller_names': 'Seller name or names if visible.',
            'buyer_signed_date': 'Buyer signature date if shown (YYYY-MM-DD).',
            'seller_signed_date': 'Seller signature date if shown (YYYY-MM-DD).',
        },
        'system_prompt': (
            "You are a precise document data extractor for Texas HOA/Property Owners Association addenda. "
            "Extract only explicit values and selected checkboxes. Do not infer deadlines that are not filled in."
        ),
    },
    'pre-approval-or-proof-of-funds': {
        'fields': {
            'letter_type': 'Document type, such as mortgage pre-approval, pre-qualification, or proof of funds.',
            'letter_date': 'Date of the letter (YYYY-MM-DD).',
            'buyer_names': 'Borrower/buyer names approved in the letter.',
            'buyer_address': 'Buyer mailing address if shown.',
            'lender_name': 'Lender or bank name.',
            'loan_officer_name': 'Loan officer or contact person name.',
            'loan_officer_title': 'Loan officer title if shown.',
            'loan_officer_nmls': 'Loan officer NMLS ID if shown.',
            'loan_officer_phone': 'Loan officer phone if shown.',
            'loan_officer_email': 'Loan officer email if shown.',
            'pre_approved_amount': 'Pre-approved mortgage amount (digits only, no $ or commas).',
            'approximate_purchase_price': 'Approximate purchase price supported by the letter (digits only, no $ or commas).',
            'loan_amount': 'Loan amount shown in pre-approval details (digits only, no $ or commas).',
            'valid_until': 'Expiration or valid-until date (YYYY-MM-DD).',
            'conditions_summary': 'Concise summary of approval conditions listed in the letter.',
        },
        'system_prompt': (
            "You are a precise document data extractor for mortgage pre-approval, pre-qualification, and proof-of-funds letters. "
            "Extract only values explicitly visible in the letter and use null when a field is absent."
        ),
    },
    'third-party-financing-addendum': {
        'fields': {
            'property_address': 'Property address from the addendum.',
            'financing_type': 'Selected financing type: conventional, FHA, VA, USDA, Texas Veterans, reverse mortgage, other, or null.',
            'first_mortgage_amount': 'First mortgage principal amount (digits only, no $ or commas).',
            'second_mortgage_amount': 'Second mortgage principal amount if any (digits only, no $ or commas).',
            'loan_term_years': 'Loan term in years for selected financing.',
            'interest_rate_cap': 'Maximum interest rate percentage for selected financing (number only, no %).',
            'origination_charge_cap': 'Maximum origination charges percentage (number only, no %).',
            'other_lender_name': 'Lender name if other financing is selected.',
            'buyer_approval_required': 'Whether contract is subject to buyer obtaining buyer approval. Return true, false, or null.',
            'buyer_approval_days': 'Number of days after effective date for buyer approval termination right.',
            'property_approval_deadline_rule': 'Text summary of property approval/appraisal/insurability deadline rule.',
            'fha_va_appraisal_required': 'Whether FHA/VA required appraisal/value provision applies. Return true, false, or null.',
            'buyer_names': 'Buyer name or names if visible.',
            'seller_names': 'Seller name or names if visible.',
            'buyer_signed_date': 'Buyer signature date if shown (YYYY-MM-DD).',
            'seller_signed_date': 'Seller signature date if shown (YYYY-MM-DD).',
        },
        'system_prompt': (
            "You are a precise document data extractor for Texas Third Party Financing Addenda. "
            "Read checkboxes carefully and extract only filled-in values. Use null for blanks and unclear markings."
        ),
    },
}

# Reuse the same extraction fields for counter and accepted contract PDFs.
EXTRACTION_SCHEMAS['seller-counter-offer']['fields'] = EXTRACTION_SCHEMAS['seller-offer-contract']['fields']
EXTRACTION_SCHEMAS['seller-accepted-contract']['fields'] = EXTRACTION_SCHEMAS['seller-offer-contract']['fields']


def _render_pdf_to_images(file_data: bytes) -> list:
    """Render all PDF pages to base64-encoded PNG images."""
    images = []
    doc = fitz.open(stream=file_data, filetype="pdf")
    try:
        for page in doc:
            pix = page.get_pixmap(dpi=150)
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

        try:
            _set_rls(org_id)
            from services.seller_workflow import sync_offer_version_from_document
            sync_offer_version_from_document(doc_id)
            db.session.commit()
        except Exception:
            db.session.rollback()
            logger.error(f"Failed to sync extracted offer data for doc {doc_id}", exc_info=True)

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
