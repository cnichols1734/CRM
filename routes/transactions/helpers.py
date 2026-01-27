# routes/transactions/helpers.py
"""
Shared helper functions for transaction routes.
"""

import logging
import requests
from datetime import datetime
from models import TransactionDocument
from services.supabase_storage import (
    upload_transaction_document,
    generate_transaction_storage_path
)

logger = logging.getLogger(__name__)


def build_prefill_data(transaction, participants):
    """Build prefill data from transaction and participants."""
    data = {
        # Property info
        'property_address': transaction.street_address or '',
        'property_city': transaction.city or '',
        'property_state': transaction.state or 'TX',
        'property_zip': transaction.zip_code or '',
        'property_county': transaction.county or '',
        'property_full_address': f"{transaction.street_address or ''}, {transaction.city or ''}, {transaction.state or 'TX'} {transaction.zip_code or ''}".strip(', '),
        
        # Broker info (Origen Realty defaults)
        'broker_name': 'Origen Realty',
        'broker_license': '',  # Can be set in config or org settings later
    }
    
    # Helper to get phone from participant (check contact first, then direct phone field)
    def get_phone(participant):
        if participant.contact_id and participant.contact:
            return participant.contact.phone or ''
        return participant.phone or ''
    
    # Add seller info (primary seller participant)
    seller = next((p for p in participants if p.role == 'seller' and p.is_primary), None)
    if seller:
        data['seller_name'] = seller.display_name
        data['seller_legal_name'] = seller.display_name  # Can be overwritten in form
        data['seller_email'] = seller.display_email or ''
        data['seller_phone'] = get_phone(seller)
        
        # If linked to a contact, get additional info
        if seller.contact:
            contact = seller.contact
            # Build mailing address if different from property
            if contact.street_address:
                mailing_parts = [contact.street_address]
                if contact.city:
                    mailing_parts.append(contact.city)
                if contact.state:
                    mailing_parts.append(contact.state)
                if contact.zip_code:
                    mailing_parts.append(contact.zip_code)
                data['seller_mailing_address'] = ', '.join(mailing_parts)
    
    # Add co-seller info
    co_sellers = [p for p in participants if p.role == 'co_seller']
    if co_sellers:
        co_seller = co_sellers[0]
        data['co_seller_name'] = co_seller.display_name
        data['co_seller_email'] = co_seller.display_email or ''
        data['co_seller_phone'] = get_phone(co_seller)
        
        # Combine names for legal name field if both exist
        if seller:
            data['seller_legal_name'] = f"{seller.display_name} and {co_seller.display_name}"
    
    # Add listing agent info
    agent = next((p for p in participants if p.role == 'listing_agent'), None)
    if agent:
        data['agent_name'] = agent.display_name
        data['agent_email'] = agent.display_email or ''
        data['agent_phone'] = get_phone(agent)
        
        # If linked to a user, get license info
        if agent.user:
            user = agent.user
            data['agent_license'] = user.license_number or ''
            data['licensed_supervisor'] = user.licensed_supervisor or ''
    
    # Add buyer's agent info if present
    buyers_agent = next((p for p in participants if p.role == 'buyers_agent'), None)
    if buyers_agent:
        data['buyers_agent_name'] = buyers_agent.display_name
        data['buyers_agent_email'] = buyers_agent.display_email or ''
        data['buyers_agent_phone'] = get_phone(buyers_agent)
        data['buyers_agent_company'] = buyers_agent.company or ''
    
    # Add title company info if present
    title_company = next((p for p in participants if p.role == 'title_company'), None)
    if title_company:
        data['title_company_name'] = title_company.display_name
        data['title_company_email'] = title_company.display_email or ''
        data['title_company_phone'] = get_phone(title_company)
    
    # Add intake data if available (with intake_ prefix)
    if transaction.intake_data:
        for key, value in transaction.intake_data.items():
            data[f'intake_{key}'] = value
    
    # Set defaults for listing agreement from intake data
    if transaction.intake_data:
        intake = transaction.intake_data
        
        # Map intake responses to listing agreement defaults
        if intake.get('has_hoa'):
            data['has_hoa'] = 'yes' if intake['has_hoa'] else 'no'
        if intake.get('special_districts'):
            data['has_special_districts'] = 'yes' if intake['special_districts'] else 'no'
        if intake.get('flood_hazard'):
            data['is_flood_hazard'] = 'yes' if intake['flood_hazard'] else 'no'
    
    # Build T-47.1 property description from listing agreement data
    data['t47_property_description'] = build_t47_property_description(transaction)
    
    # Set today's date for T-47.1 if not already set
    data['t47_date'] = datetime.now().strftime('%Y-%m-%d')
    
    return data


def build_t47_property_description(transaction):
    """
    Build the property description string for T-47.1 Affidavit from listing agreement data.
    
    Format: "Lot {lot}, Block {block}, {subdivision} Addition, City of {city}, 
             {county} County, TX known as {address}"
    
    Falls back to transaction data if listing agreement hasn't been filled yet.
    """
    # Try to get data from the listing agreement document first
    listing_agreement = TransactionDocument.query.filter_by(
        transaction_id=transaction.id,
        template_slug='listing-agreement'
    ).first()
    
    # Get field data from listing agreement if available
    la_data = listing_agreement.field_data if listing_agreement and listing_agreement.field_data else {}
    
    # Build components, preferring listing agreement data, falling back to transaction data
    lot = la_data.get('legal_lot', '')
    block = la_data.get('legal_block', '')
    subdivision = la_data.get('legal_subdivision', '')
    city = la_data.get('property_city', '') or transaction.city or ''
    county = la_data.get('property_county', '') or transaction.county or ''
    address = la_data.get('property_address', '') or transaction.street_address or ''
    
    # Build the description string
    parts = []
    
    if lot:
        parts.append(f"Lot {lot}")
    if block:
        parts.append(f"Block {block}")
    if subdivision:
        parts.append(f"{subdivision} Addition")
    if city:
        parts.append(f"City of {city}")
    if county:
        parts.append(f"{county} County, TX")
    if address:
        parts.append(f"known as {address}")
    
    # Join with commas
    if parts:
        return ', '.join(parts)
    
    # Fallback: just return full address if no legal description available
    if transaction.street_address:
        fallback_parts = [transaction.street_address]
        if transaction.city:
            fallback_parts.append(transaction.city)
        if transaction.state:
            fallback_parts.append(transaction.state)
        if transaction.zip_code:
            fallback_parts.append(transaction.zip_code)
        return ', '.join(fallback_parts)
    
    return ''


def download_and_store_signed_document(doc: TransactionDocument, documents: list) -> bool:
    """
    Download signed PDF from DocuSeal and store in Supabase.
    
    Called by the webhook handler when a document is completed.
    
    Args:
        doc: The TransactionDocument record
        documents: List of document dicts from webhook payload with 'url' and 'name'
    
    Returns:
        True if successfully stored, False otherwise
    """
    if not documents:
        logger.warning(f"No documents in webhook payload for doc {doc.id}")
        return False
    
    for doc_info in documents:
        url = doc_info.get('url')
        filename = doc_info.get('name', f'{doc.template_slug}_signed.pdf')
        
        if not url:
            logger.warning(f"No URL for document in webhook payload: {doc_info}")
            continue
        
        try:
            # Download PDF from DocuSeal
            logger.info(f"Downloading signed document from: {url[:50]}...")
            response = requests.get(url, timeout=60)
            response.raise_for_status()
            pdf_bytes = response.content
            
            if not pdf_bytes:
                logger.error(f"Empty response when downloading document for doc {doc.id}")
                continue
            
            # Upload to Supabase
            storage_path, _ = generate_transaction_storage_path(
                doc.transaction_id, doc.id, filename
            )
            
            result = upload_transaction_document(
                doc.transaction_id,
                doc.id,
                pdf_bytes,
                filename,
                'application/pdf'
            )
            
            # Update document record with storage info
            doc.signed_file_path = result['path']
            doc.signed_file_size = result['size']
            doc.signed_file_downloaded_at = datetime.utcnow()
            
            logger.info(f"Stored signed document for doc {doc.id}: {result['path']} ({result['size']} bytes)")
            return True
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to download signed document for doc {doc.id}: {e}")
            continue
        except Exception as e:
            logger.error(f"Failed to store signed document for doc {doc.id}: {e}")
            continue
    
    return False
