"""
Document System Exceptions

Custom exceptions for document configuration and processing errors.
"""


class DocumentError(Exception):
    """Base exception for all document system errors."""
    pass


class ConfigurationError(DocumentError):
    """
    Raised when document configuration is invalid.
    
    This includes YAML syntax errors, schema validation failures,
    and referential integrity issues (e.g., field references unknown role).
    """
    pass


class ValidationError(DocumentError):
    """
    Raised when a single document definition fails validation.
    
    Contains details about what specifically failed.
    """
    def __init__(self, message: str, document_slug: str = None, field: str = None):
        self.document_slug = document_slug
        self.field = field
        super().__init__(message)


class ResolutionError(DocumentError):
    """
    Raised when field resolution fails.
    
    This can happen when a source path is invalid or
    required data is missing.
    """
    def __init__(self, message: str, source_path: str = None):
        self.source_path = source_path
        super().__init__(message)


class DocuSealAPIError(DocumentError):
    """
    Raised when DocuSeal API calls fail.
    
    Wraps the underlying API error with context.
    """
    def __init__(self, message: str, status_code: int = None, response_body: str = None):
        self.status_code = status_code
        self.response_body = response_body
        super().__init__(message)

