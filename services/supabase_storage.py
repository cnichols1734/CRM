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

# Bucket name for contact files
BUCKET_NAME = 'contact-files'


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


def upload_file(contact_id: int, file_data: bytes, original_filename: str, content_type: str = None) -> dict:
    """
    Upload a file to Supabase Storage.
    
    Args:
        contact_id: The contact this file belongs to
        file_data: The file content as bytes
        original_filename: The original filename from the upload
        content_type: MIME type of the file (optional)
    
    Returns:
        dict with 'path', 'filename', 'size' keys on success
        
    Raises:
        Exception on upload failure
    """
    client = get_supabase_client()
    
    storage_path, unique_filename = generate_storage_path(contact_id, original_filename)
    
    # Set file options
    file_options = {}
    if content_type:
        file_options['content-type'] = content_type
    
    # Upload to Supabase Storage
    response = client.storage.from_(BUCKET_NAME).upload(
        path=storage_path,
        file=file_data,
        file_options=file_options
    )
    
    return {
        'path': storage_path,
        'filename': unique_filename,
        'size': len(file_data)
    }


def get_signed_url(storage_path: str, expires_in: int = 3600) -> str:
    """
    Generate a signed URL for private file access.
    
    Args:
        storage_path: The path to the file in storage
        expires_in: URL expiry time in seconds (default: 1 hour)
    
    Returns:
        Signed URL string
    """
    client = get_supabase_client()
    
    response = client.storage.from_(BUCKET_NAME).create_signed_url(
        path=storage_path,
        expires_in=expires_in
    )
    
    return response['signedURL']


def delete_file(storage_path: str) -> bool:
    """
    Delete a file from Supabase Storage.
    
    Args:
        storage_path: The path to the file in storage
    
    Returns:
        True on success, False on failure
    """
    client = get_supabase_client()
    
    try:
        client.storage.from_(BUCKET_NAME).remove([storage_path])
        return True
    except Exception as e:
        current_app.logger.error(f"Failed to delete file {storage_path}: {e}")
        return False


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
