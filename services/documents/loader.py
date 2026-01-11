"""
Document Loader

Loads, validates, and caches document definitions from YAML files.
Validates all documents on startup and fails fast if any are invalid.
"""

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional, Set

import yaml

try:
    import jsonschema
except ImportError:
    jsonschema = None

from .types import DocumentDefinition, DocumentType
from .exceptions import ConfigurationError, ValidationError

logger = logging.getLogger(__name__)

# Paths
DOCUMENTS_DIR = Path(__file__).parent.parent.parent / 'documents'
SCHEMA_DIR = DOCUMENTS_DIR / 'schema'


class DocumentLoader:
    """
    Singleton loader for document definitions.
    
    Loads all YAML files from the documents/ directory on startup,
    validates them against the JSON schema, and caches them for
    fast lookup during request handling.
    
    Usage:
        # On app startup
        DocumentLoader.load_all()
        
        # During request handling
        definition = DocumentLoader.get('listing-agreement')
    """
    
    _definitions: Dict[str, DocumentDefinition] = {}
    _schemas: Dict[str, dict] = {}
    _validated: bool = False
    
    @classmethod
    def load_all(cls) -> None:
        """
        Load and validate all document definitions.
        
        Called at app startup. If any document fails validation,
        raises ConfigurationError with all errors listed.
        """
        cls._definitions.clear()
        errors = []
        
        # Load schema(s)
        cls._load_schemas()
        
        # Find all YAML files
        if not DOCUMENTS_DIR.exists():
            logger.warning(f"Documents directory not found: {DOCUMENTS_DIR}")
            return
        
        yaml_files = list(DOCUMENTS_DIR.glob('*.yml')) + list(DOCUMENTS_DIR.glob('*.yaml'))
        
        if not yaml_files:
            logger.warning(f"No document definitions found in {DOCUMENTS_DIR}")
            return
        
        # Load and validate each file
        for yaml_file in yaml_files:
            try:
                definition = cls._load_and_validate(yaml_file)
                
                # Check for duplicate slugs
                if definition.slug in cls._definitions:
                    errors.append(
                        f"{yaml_file.name}: Duplicate slug '{definition.slug}' "
                        f"(already defined in another file)"
                    )
                    continue
                
                cls._definitions[definition.slug] = definition
                logger.debug(f"Loaded document definition: {definition.slug}")
                
            except (ValidationError, yaml.YAMLError) as e:
                errors.append(f"{yaml_file.name}: {e}")
            except Exception as e:
                errors.append(f"{yaml_file.name}: Unexpected error - {e}")
        
        if errors:
            error_msg = "Document configuration errors:\n" + "\n".join(f"  - {e}" for e in errors)
            logger.error(error_msg)
            raise ConfigurationError(error_msg)
        
        cls._validated = True
        logger.info(f"Loaded {len(cls._definitions)} document definition(s)")
    
    @classmethod
    def _load_schemas(cls) -> None:
        """Load JSON schemas for validation."""
        cls._schemas.clear()
        
        if not SCHEMA_DIR.exists():
            logger.warning(f"Schema directory not found: {SCHEMA_DIR}")
            return
        
        for schema_file in SCHEMA_DIR.glob('v*.json'):
            try:
                version = schema_file.stem  # e.g., "v1.0"
                schema = json.loads(schema_file.read_text())
                cls._schemas[version] = schema
                logger.debug(f"Loaded schema: {version}")
            except Exception as e:
                logger.error(f"Failed to load schema {schema_file}: {e}")
    
    @classmethod
    def _get_schema(cls, version: str) -> dict:
        """Get schema for a specific version."""
        schema_key = f"v{version}"
        if schema_key not in cls._schemas:
            raise ValidationError(f"Unknown schema version: {version}")
        return cls._schemas[schema_key]
    
    @classmethod
    def _load_and_validate(cls, path: Path) -> DocumentDefinition:
        """Load a YAML file and validate it."""
        # Parse YAML
        raw = yaml.safe_load(path.read_text())
        
        if not raw:
            raise ValidationError("Empty document definition")
        
        # 1. Schema validation (if jsonschema available)
        schema_version = raw.get('schema_version', '1.0')
        if jsonschema and cls._schemas:
            try:
                schema = cls._get_schema(schema_version)
                jsonschema.validate(raw, schema)
            except jsonschema.ValidationError as e:
                raise ValidationError(f"Schema validation failed: {e.message}")
        
        # 2. Referential integrity checks
        cls._validate_references(raw)
        
        # 3. Additional business rule validation
        cls._validate_business_rules(raw)
        
        # 4. Convert to typed dataclass
        return DocumentDefinition.from_dict(raw)
    
    @classmethod
    def _validate_references(cls, raw: dict) -> None:
        """Validate that fields reference valid roles."""
        role_keys: Set[str] = {r['role_key'] for r in raw.get('roles', [])}
        
        for field in raw.get('fields', []):
            field_key = field.get('field_key', 'unknown')
            ref_role = field.get('role_key')
            
            if ref_role not in role_keys:
                raise ValidationError(
                    f"Field '{field_key}' references unknown role '{ref_role}'. "
                    f"Available roles: {sorted(role_keys)}"
                )
    
    @classmethod
    def _validate_business_rules(cls, raw: dict) -> None:
        """Validate business-specific rules."""
        doc_type = raw.get('type')
        
        # Form-driven documents must have form config
        if doc_type == 'form-driven' and 'form' not in raw:
            raise ValidationError(
                "Form-driven documents must have a 'form' section with template and partial"
            )
        
        # Check for unique role_keys
        role_keys = [r['role_key'] for r in raw.get('roles', [])]
        if len(role_keys) != len(set(role_keys)):
            duplicates = [k for k in role_keys if role_keys.count(k) > 1]
            raise ValidationError(f"Duplicate role_keys: {set(duplicates)}")
        
        # Check for unique field_keys
        field_keys = [f['field_key'] for f in raw.get('fields', [])]
        if len(field_keys) != len(set(field_keys)):
            duplicates = [k for k in field_keys if field_keys.count(k) > 1]
            raise ValidationError(f"Duplicate field_keys: {set(duplicates)}")
        
        # Validate source path syntax (bracket notation for arrays)
        for field in raw.get('fields', []):
            field_key = field.get('field_key', 'unknown')

            # Check single source
            source = field.get('source')
            if source and cls._has_invalid_array_syntax(source):
                raise ValidationError(
                    f"Field '{field_key}' has invalid source syntax: '{source}'. "
                    f"Use bracket notation for arrays (e.g., 'sellers[0]' not 'sellers.0')"
                )

            # Check combined field sources
            sources = field.get('sources')
            if sources:
                for src in sources:
                    if cls._has_invalid_array_syntax(src):
                        raise ValidationError(
                            f"Field '{field_key}' has invalid source syntax in sources: '{src}'. "
                            f"Use bracket notation for arrays (e.g., 'sellers[0]' not 'sellers.0')"
                        )

            # Validate combined field has template if it has sources
            if sources and not field.get('template'):
                raise ValidationError(
                    f"Field '{field_key}' has 'sources' but missing 'template'. "
                    f"Combined fields require both 'sources' and 'template'."
                )
        
        for role in raw.get('roles', []):
            for source_key in ['email_source', 'name_source']:
                source = role.get(source_key)
                if source and cls._has_invalid_array_syntax(source):
                    raise ValidationError(
                        f"Role '{role.get('role_key')}' has invalid {source_key} syntax: '{source}'. "
                        f"Use bracket notation for arrays (e.g., 'sellers[0]' not 'sellers.0')"
                    )
    
    @classmethod
    def _has_invalid_array_syntax(cls, source: str) -> bool:
        """Check if source uses dot notation for array indices (invalid)."""
        import re
        # Match patterns like .0. or .1. or .0 at end (but not [0])
        # This catches sellers.0.email but not sellers[0].email
        return bool(re.search(r'\.\d+(?:\.|$)', source))
    
    @classmethod
    def get(cls, slug: str) -> Optional[DocumentDefinition]:
        """
        Get a document definition by slug.
        
        Returns None if not found.
        """
        return cls._definitions.get(slug)
    
    @classmethod
    def get_or_raise(cls, slug: str) -> DocumentDefinition:
        """
        Get a document definition by slug, raising if not found.
        """
        definition = cls.get(slug)
        if not definition:
            raise ValidationError(f"Unknown document slug: {slug}")
        return definition
    
    @classmethod
    def all(cls) -> List[DocumentDefinition]:
        """Get all loaded document definitions."""
        return list(cls._definitions.values())
    
    @classmethod
    def all_slugs(cls) -> List[str]:
        """Get all loaded document slugs."""
        return list(cls._definitions.keys())
    
    @classmethod
    def get_by_type(cls, doc_type: DocumentType) -> List[DocumentDefinition]:
        """Get all documents of a specific type."""
        return [d for d in cls._definitions.values() if d.type == doc_type]
    
    @classmethod
    def get_form_driven(cls) -> List[DocumentDefinition]:
        """Get all form-driven documents."""
        return cls.get_by_type(DocumentType.FORM_DRIVEN)
    
    @classmethod
    def get_pdf_preview(cls) -> List[DocumentDefinition]:
        """Get all PDF-preview documents."""
        return cls.get_by_type(DocumentType.PDF_PREVIEW)
    
    @classmethod
    def get_sorted(cls) -> List[DocumentDefinition]:
        """Get all documents sorted by display order."""
        return sorted(cls._definitions.values(), key=lambda d: d.display.sort_order)
    
    @classmethod
    def is_loaded(cls) -> bool:
        """Check if documents have been loaded and validated."""
        return cls._validated
    
    @classmethod
    def clear(cls) -> None:
        """Clear all cached definitions. Mainly for testing."""
        cls._definitions.clear()
        cls._validated = False
    
    @classmethod
    def reload(cls) -> None:
        """Reload all document definitions. Used after saving new YAML."""
        cls.clear()
        try:
            cls.load_all()
        except ConfigurationError as e:
            logger.error(f"Failed to reload documents: {e}")
            raise
    
    @classmethod
    def validate_yaml_content(cls, yaml_content: str) -> List[str]:
        """
        Validate YAML content without saving.
        
        Args:
            yaml_content: Raw YAML string to validate
            
        Returns:
            List of validation error messages (empty if valid)
        """
        errors = []
        
        try:
            # Parse YAML
            raw = yaml.safe_load(yaml_content)
            
            if not raw:
                return ["Empty document definition"]
            
            # Schema validation
            schema_version = raw.get('schema_version', '1.0')
            if jsonschema and cls._schemas:
                try:
                    schema = cls._get_schema(schema_version)
                    jsonschema.validate(raw, schema)
                except jsonschema.ValidationError as e:
                    errors.append(f"Schema validation: {e.message}")
            
            # Referential integrity
            try:
                cls._validate_references(raw)
            except ValidationError as e:
                errors.append(str(e))
            
            # Business rules
            try:
                cls._validate_business_rules(raw)
            except ValidationError as e:
                errors.append(str(e))
                
        except yaml.YAMLError as e:
            errors.append(f"YAML syntax error: {e}")
        except Exception as e:
            errors.append(f"Unexpected error: {e}")
        
        return errors

