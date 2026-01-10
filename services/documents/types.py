"""
Document System Type Definitions

Dataclasses representing document definitions loaded from YAML.
These are immutable after loading and validated on startup.
"""

from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from enum import Enum


class DocumentType(Enum):
    """Types of documents supported by the system."""
    FORM_DRIVEN = "form-driven"
    PDF_PREVIEW = "pdf-preview"


@dataclass(frozen=True)
class DisplayConfig:
    """Display/UI configuration for a document."""
    color: str
    icon: str
    sort_order: int


@dataclass(frozen=True)
class FormConfig:
    """Form configuration for form-driven documents."""
    template: str  # Full template path (e.g., listing_agreement_form.html)
    partial: str   # Partial template path (e.g., listing_agreement_fields.html)


@dataclass(frozen=True)
class RoleDefinition:
    """
    A DocuSeal submitter role definition.
    
    Attributes:
        role_key: Stable internal identifier (snake_case)
        docuseal_role: Exact role name in DocuSeal template
        email_source: Source path for email (e.g., "user.email")
        name_source: Source path for display name (e.g., "user.full_name")
        optional: If True, skip this role when source resolves to None
    """
    role_key: str
    docuseal_role: str
    email_source: str
    name_source: str
    optional: bool = False


@dataclass(frozen=True)
class FieldDefinition:
    """
    A field mapping between data sources and DocuSeal fields.
    
    Attributes:
        field_key: Stable internal identifier (snake_case)
        docuseal_field: Exact field name in DocuSeal template
        role_key: Which role this field belongs to
        source: Data source path (e.g., "user.email", "form.list_price", null for manual)
        transform: Optional transform function name (e.g., "currency", "date")
    """
    field_key: str
    docuseal_field: str
    role_key: str
    source: Optional[str] = None  # None means manual entry in DocuSeal
    transform: Optional[str] = None


@dataclass(frozen=True)
class DocumentDefinition:
    """
    Complete definition of a document loaded from YAML.
    
    This is the primary data structure that drives the entire
    document generation system. One YAML file = one DocumentDefinition.
    """
    schema_version: str
    slug: str
    name: str
    docuseal_template_id: int
    type: DocumentType
    display: DisplayConfig
    roles: List[RoleDefinition]
    fields: List[FieldDefinition]
    form: Optional[FormConfig] = None
    
    @property
    def is_form_driven(self) -> bool:
        """Check if this document uses a custom form UI."""
        return self.type == DocumentType.FORM_DRIVEN
    
    @property
    def is_pdf_preview(self) -> bool:
        """Check if this document uses direct PDF preview."""
        return self.type == DocumentType.PDF_PREVIEW
    
    def get_role(self, role_key: str) -> Optional[RoleDefinition]:
        """Get a role definition by its key."""
        return next((r for r in self.roles if r.role_key == role_key), None)
    
    def get_fields_for_role(self, role_key: str) -> List[FieldDefinition]:
        """Get all fields that belong to a specific role."""
        return [f for f in self.fields if f.role_key == role_key]
    
    def get_role_keys(self) -> List[str]:
        """Get all role keys defined in this document."""
        return [r.role_key for r in self.roles]
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'DocumentDefinition':
        """
        Create a DocumentDefinition from a parsed YAML dict.
        
        This handles the conversion from raw dict to typed dataclasses.
        """
        # Parse display config
        display_data = data.get('display', {})
        display = DisplayConfig(
            color=display_data.get('color', '#6B7280'),
            icon=display_data.get('icon', 'fas fa-file'),
            sort_order=display_data.get('sort_order', 999)
        )
        
        # Parse form config (optional, only for form-driven)
        form = None
        if 'form' in data:
            form_data = data['form']
            form = FormConfig(
                template=form_data.get('template', ''),
                partial=form_data.get('partial', '')
            )
        
        # Parse roles
        roles = []
        for role_data in data.get('roles', []):
            roles.append(RoleDefinition(
                role_key=role_data['role_key'],
                docuseal_role=role_data['docuseal_role'],
                email_source=role_data['email_source'],
                name_source=role_data['name_source'],
                optional=role_data.get('optional', False)
            ))
        
        # Parse fields
        fields = []
        for field_data in data.get('fields', []):
            fields.append(FieldDefinition(
                field_key=field_data['field_key'],
                docuseal_field=field_data['docuseal_field'],
                role_key=field_data['role_key'],
                source=field_data.get('source'),  # Can be None
                transform=field_data.get('transform')
            ))
        
        return cls(
            schema_version=data['schema_version'],
            slug=data['slug'],
            name=data['name'],
            docuseal_template_id=data['docuseal_template_id'],
            type=DocumentType(data['type']),
            display=display,
            form=form,
            roles=tuple(roles),  # Convert to tuple for immutability
            fields=tuple(fields)
        )


@dataclass
class ResolvedField:
    """
    A field with its value resolved from the data context.
    
    This is the intermediate representation between the definition
    and the final DocuSeal API payload.
    """
    field_key: str
    docuseal_field: str
    role_key: str
    value: Optional[str]  # Resolved and transformed value
    is_manual: bool = False  # True if source was null (manual entry)
    
    def to_docuseal_format(self) -> Dict[str, Any]:
        """Convert to DocuSeal API field format."""
        if self.is_manual or self.value is None:
            return None  # Don't include manual/empty fields
        return {
            'name': self.docuseal_field,
            'default_value': str(self.value)
        }


@dataclass
class Submitter:
    """
    A DocuSeal submitter ready for API submission.
    
    This is the final representation sent to DocuSeal.
    """
    role: str  # DocuSeal role name
    email: str
    name: str
    fields: List[Dict[str, Any]]  # List of {name, default_value}
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to DocuSeal API format."""
        return {
            'role': self.role,
            'email': self.email,
            'name': self.name,
            'fields': self.fields
        }

