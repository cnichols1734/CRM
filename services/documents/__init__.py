"""
Document Generation System

A configuration-driven system for generating and managing documents.
Documents are defined in YAML files and processed through a pipeline
of resolution, role building, and DocuSeal integration.

Usage:
    from services.documents import DocumentLoader, FieldResolver, RoleBuilder, DocuSealClient
    
    # On app startup
    DocumentLoader.load_all()
    
    # When processing a document
    definition = DocumentLoader.get('listing-agreement')
    context = build_context(user, transaction, form_data)
    fields = FieldResolver.resolve(definition, context)
    submitters = RoleBuilder.build(definition, fields, context)
    result = DocuSealClient.create_submission(definition.docuseal_template_id, submitters)
"""

from .types import (
    DocumentType,
    DisplayConfig,
    FormConfig,
    RoleDefinition,
    FieldDefinition,
    DocumentDefinition,
    ResolvedField,
    Submitter
)

from .exceptions import (
    DocumentError,
    ConfigurationError,
    ValidationError,
    ResolutionError,
    DocuSealAPIError
)

from .loader import DocumentLoader
from .field_resolver import FieldResolver
from .role_builder import RoleBuilder
from .docuseal_client import DocuSealClient
from .transforms import TRANSFORMS, apply_transform, register_transform

__all__ = [
    # Types
    'DocumentType',
    'DisplayConfig',
    'FormConfig',
    'RoleDefinition',
    'FieldDefinition',
    'DocumentDefinition',
    'ResolvedField',
    'Submitter',
    
    # Exceptions
    'DocumentError',
    'ConfigurationError',
    'ValidationError',
    'ResolutionError',
    'DocuSealAPIError',
    
    # Services
    'DocumentLoader',
    'FieldResolver',
    'RoleBuilder',
    'DocuSealClient',
    
    # Transforms
    'TRANSFORMS',
    'apply_transform',
    'register_transform',
]
