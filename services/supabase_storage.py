"""
Supabase Storage Service for Contact Files

Handles file uploads, downloads, and deletions using Supabase Storage.
Files are stored privately and accessed via signed URLs.
"""

import os
import uuid
from datetime import datetime
from supabase import create_client, Client
from flask import current_app

# Supabase client singleton
_supabase_client: Client = None

# Bucket names
CONTACT_FILES_BUCKET = 'contact-files'
COMPANY_UPDATES_BUCKET = 'company-updates'
TRANSACTION_DOCUMENTS_BUCKET = 'transaction-documents'


def get_supabase_client() -> Client:
    """
    Get or create the Supabase client.
    Uses SUPABASE_URL and SUPABASE_KEY from environment.
    """
    global _supabase_client
    
    if _supabase_client is None:
        supabase_url = os.getenv('SUPABASE_URL')
        supabase_key = os.getenv('SUPABASE_KEY')
        
        if not supabase_url or not supabase_key:
            raise ValueError(
                "SUPABASE_URL and SUPABASE_KEY environment variables are required. "
                "Get these from your Supabase project settings."
            )
        
        _supabase_client = create_client(supabase_url, supabase_key)
    
    return _supabase_client


def generate_storage_path(contact_id: int, original_filename: str) -> tuple[str, str]:
    """
    Generate a unique storage path for a file.
    
    Returns:
        tuple: (storage_path, unique_filename)
    """
    # Get file extension
    ext = ''
    if '.' in original_filename:
        ext = '.' + original_filename.rsplit('.', 1)[1].lower()
    
    # Generate unique filename with UUID
    unique_filename = f"{uuid.uuid4().hex}{ext}"
    
    # Organize by contact_id for easy management
    storage_path = f"contacts/{contact_id}/{unique_filename}"
    
    return storage_path, unique_filename


def upload_file(bucket: str, storage_path: str, file_data: bytes, original_filename: str, content_type: str = None) -> dict:
    """
    Upload a file to a Supabase Storage bucket.
    
    Args:
        bucket: Target bucket name
        storage_path: Path within the bucket
        file_data: The file content as bytes
        original_filename: The original filename from the upload
        content_type: MIME type of the file (optional)
    
    Returns:
        dict with 'path', 'filename', 'size' keys on success
        
    Raises:
        Exception on upload failure
    """
    client = get_supabase_client()
    
    # Set file options
    file_options = {}
    if content_type:
        file_options['content-type'] = content_type
    
    client.storage.from_(bucket).upload(
        path=storage_path,
        file=file_data,
        file_options=file_options
    )
    
    return {
        'path': storage_path,
        'filename': storage_path.rsplit('/', 1)[-1],
        'size': len(file_data)
    }


def get_signed_url(bucket: str, storage_path: str, expires_in: int = 3600) -> str:
    """
    Generate a signed URL for private file access.
    
    Args:
        bucket: Bucket name containing the file
        storage_path: The path to the file in storage
        expires_in: URL expiry time in seconds (default: 1 hour)
    
    Returns:
        Signed URL string
    """
    client = get_supabase_client()
    
    response = client.storage.from_(bucket).create_signed_url(
        path=storage_path,
        expires_in=expires_in
    )
    
    return response['signedURL']


def delete_file(bucket: str, storage_path: str) -> bool:
    """
    Delete a file from Supabase Storage.
    
    Args:
        bucket: Bucket name containing the file
        storage_path: The path to the file in storage
    
    Returns:
        True on success, False on failure
    """
    client = get_supabase_client()
    
    try:
        client.storage.from_(bucket).remove([storage_path])
        return True
    except Exception as e:
        current_app.logger.error(f"Failed to delete file {storage_path}: {e}")
        return False


def generate_contact_storage_path(contact_id: int, original_filename: str) -> tuple[str, str]:
    """
    Generate a unique storage path for a contact file.
    
    Returns:
        tuple: (storage_path, unique_filename)
    """
    return generate_storage_path(contact_id, original_filename)


def upload_contact_file(contact_id: int, file_data: bytes, original_filename: str, content_type: str = None) -> dict:
    """Convenience wrapper for uploading contact files."""
    storage_path, unique_filename = generate_storage_path(contact_id, original_filename)
    return upload_file(CONTACT_FILES_BUCKET, storage_path, file_data, original_filename, content_type)


def get_contact_file_url(storage_path: str, expires_in: int = 3600) -> str:
    """Get a signed URL for a contact file."""
    return get_signed_url(CONTACT_FILES_BUCKET, storage_path, expires_in)


def delete_contact_file(storage_path: str) -> bool:
    """Delete a contact file from storage."""
    return delete_file(CONTACT_FILES_BUCKET, storage_path)


def generate_company_update_path(original_filename: str, folder: str = 'images') -> tuple[str, str]:
    """
    Generate a unique storage path for a company update image.
    
    Returns:
        tuple: (storage_path, unique_filename)
    """
    ext = ''
    if '.' in original_filename:
        ext = '.' + original_filename.rsplit('.', 1)[1].lower()
    
    unique_filename = f"{uuid.uuid4().hex}{ext}"
    storage_path = f"{folder}/{unique_filename}"
    return storage_path, unique_filename


def upload_company_update_image(file_data: bytes, original_filename: str, content_type: str = None) -> dict:
    """Upload a company update cover image to Supabase Storage."""
    storage_path, unique_filename = generate_company_update_path(original_filename)
    return upload_file(COMPANY_UPDATES_BUCKET, storage_path, file_data, original_filename, content_type)


def get_company_update_image_url(storage_path: str, expires_in: int = 3600) -> str:
    """Get a signed URL for a company update image."""
    return get_signed_url(COMPANY_UPDATES_BUCKET, storage_path, expires_in)


def delete_company_update_image(storage_path: str) -> bool:
    """Delete a company update image from storage."""
    return delete_file(COMPANY_UPDATES_BUCKET, storage_path)


def get_file_icon(file_extension: str) -> str:
    """
    Get Font Awesome icon class for a file type.
    
    Args:
        file_extension: File extension (without dot)
    
    Returns:
        Font Awesome icon class
    """
    icons = {
        'pdf': 'fa-file-pdf',
        'doc': 'fa-file-word',
        'docx': 'fa-file-word',
        'xls': 'fa-file-excel',
        'xlsx': 'fa-file-excel',
        'csv': 'fa-file-csv',
        'jpg': 'fa-file-image',
        'jpeg': 'fa-file-image',
        'png': 'fa-file-image',
        'gif': 'fa-file-image',
    }
    return icons.get(file_extension.lower(), 'fa-file')


def format_file_size(size_bytes: int) -> str:
    """
    Format file size in human-readable format.
    
    Args:
        size_bytes: Size in bytes
    
    Returns:
        Formatted string (e.g., "1.5 MB")
    """
    if not size_bytes:
        return 'Unknown'
    
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    
    return f"{size_bytes:.1f} TB"


# =============================================================================
# TRANSACTION DOCUMENT STORAGE
# =============================================================================
# Functions for storing signed documents from DocuSeal

def generate_transaction_storage_path(transaction_id: int, doc_id: int, original_filename: str) -> tuple[str, str]:
    """
    Generate a unique storage path for a signed transaction document.
    
    Args:
        transaction_id: The transaction ID
        doc_id: The TransactionDocument ID
        original_filename: Original filename from DocuSeal
    
    Returns:
        tuple: (storage_path, unique_filename)
    """
    # Get file extension (usually .pdf)
    ext = ''
    if '.' in original_filename:
        ext = '.' + original_filename.rsplit('.', 1)[1].lower()
    
    # Generate unique filename with doc ID for traceability
    unique_filename = f"{doc_id}_{uuid.uuid4().hex[:8]}{ext}"
    
    # Organize by transaction_id/signed/ for clarity
    storage_path = f"transactions/{transaction_id}/signed/{unique_filename}"
    
    return storage_path, unique_filename


def upload_transaction_document(
    transaction_id: int,
    doc_id: int,
    file_data: bytes,
    original_filename: str,
    content_type: str = 'application/pdf'
) -> dict:
    """
    Upload a signed transaction document to Supabase Storage.
    
    Args:
        transaction_id: The transaction ID
        doc_id: The TransactionDocument ID
        file_data: The PDF content as bytes
        original_filename: Original filename from DocuSeal
        content_type: MIME type (defaults to application/pdf)
    
    Returns:
        dict with 'path', 'filename', 'size' keys on success
    """
    storage_path, unique_filename = generate_transaction_storage_path(
        transaction_id, doc_id, original_filename
    )
    return upload_file(
        TRANSACTION_DOCUMENTS_BUCKET,
        storage_path,
        file_data,
        original_filename,
        content_type
    )


def get_transaction_document_url(storage_path: str, expires_in: int = 3600) -> str:
    """
    Get a signed URL for a transaction document.
    
    Args:
        storage_path: Path in Supabase storage
        expires_in: URL expiry time in seconds (default: 1 hour)
    
    Returns:
        Signed URL string for viewing/downloading the document
    """
    return get_signed_url(TRANSACTION_DOCUMENTS_BUCKET, storage_path, expires_in)


def delete_transaction_document(storage_path: str) -> bool:
    """
    Delete a transaction document from storage.
    
    Args:
        storage_path: Path to the file in storage
    
    Returns:
        True on success, False on failure
    """
    return delete_file(TRANSACTION_DOCUMENTS_BUCKET, storage_path)


# =============================================================================
# SCANNED DOCUMENT STORAGE
# =============================================================================
# Functions for storing scanned signed documents (physical signatures)

def generate_scanned_document_path(transaction_id: int, doc_id: int, original_filename: str) -> tuple[str, str]:
    """
    Generate a unique storage path for a scanned signed document.
    
    Args:
        transaction_id: The transaction ID
        doc_id: The TransactionDocument ID
        original_filename: Original filename from upload
    
    Returns:
        tuple: (storage_path, unique_filename)
    """
    # Get file extension (usually .pdf)
    ext = ''
    if '.' in original_filename:
        ext = '.' + original_filename.rsplit('.', 1)[1].lower()
    
    # Generate unique filename with doc ID for traceability
    unique_filename = f"{doc_id}_{uuid.uuid4().hex[:8]}{ext}"
    
    # Organize by transaction_id/scanned/ to separate from e-signed docs
    storage_path = f"transactions/{transaction_id}/scanned/{unique_filename}"
    
    return storage_path, unique_filename


def upload_scanned_document(
    transaction_id: int,
    doc_id: int,
    file_data: bytes,
    original_filename: str,
    content_type: str = 'application/pdf'
) -> dict:
    """
    Upload a scanned signed document to Supabase Storage.
    
    Used when agents get physical signatures and scan the signed document.
    
    Args:
        transaction_id: The transaction ID
        doc_id: The TransactionDocument ID
        file_data: The PDF/image content as bytes
        original_filename: Original filename from upload
        content_type: MIME type (defaults to application/pdf)
    
    Returns:
        dict with 'path', 'filename', 'size' keys on success
    """
    storage_path, unique_filename = generate_scanned_document_path(
        transaction_id, doc_id, original_filename
    )
    return upload_file(
        TRANSACTION_DOCUMENTS_BUCKET,
        storage_path,
        file_data,
        original_filename,
        content_type
    )
