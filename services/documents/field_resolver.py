"""
Field Resolver

Resolves field values from data sources using source path expressions.
Supports dot notation for object properties and bracket notation for arrays.

Source path syntax:
    user.email              -> context['user'].email
    user.full_name          -> f"{context['user'].first_name} {context['user'].last_name}"
    transaction.property_address -> context['transaction'].property_address
    transaction.sellers[0].email -> context['transaction'].sellers[0].email
    form.list_price         -> context['form']['list_price']
    null                    -> None (manual entry)
"""

import logging
import re
from typing import Any, Dict, List, Optional

from .types import DocumentDefinition, FieldDefinition, ResolvedField
from .transforms import apply_transform
from .exceptions import ResolutionError

logger = logging.getLogger(__name__)


class FieldResolver:
    """
    Resolves field definitions to actual values using a data context.
    
    The context is a dict containing:
        - 'user': Current User object
        - 'transaction': Transaction object
        - 'form': Dict of form field values (from TransactionDocument.field_data)
    """
    
    # Pattern for bracket notation: name[index]
    BRACKET_PATTERN = re.compile(r'^([a-zA-Z_][a-zA-Z0-9_]*)\[(\d+)\]$')
    
    @classmethod
    def resolve(
        cls,
        definition: DocumentDefinition,
        context: Dict[str, Any]
    ) -> List[ResolvedField]:
        """
        Resolve all fields in a document definition.
        
        Args:
            definition: The document definition
            context: Dict with 'user', 'transaction', 'form' keys
            
        Returns:
            List of ResolvedField objects with values populated
        """
        resolved = []
        
        for field_def in definition.fields:
            try:
                resolved_field = cls.resolve_field(field_def, context)
                resolved.append(resolved_field)
            except ResolutionError as e:
                logger.warning(f"Failed to resolve field {field_def.field_key}: {e}")
                # Create a field with None value
                resolved.append(ResolvedField(
                    field_key=field_def.field_key,
                    docuseal_field=field_def.docuseal_field,
                    role_key=field_def.role_key,
                    value=None,
                    is_manual=field_def.source is None
                ))
        
        return resolved
    
    @classmethod
    def resolve_field(
        cls,
        field_def: FieldDefinition,
        context: Dict[str, Any]
    ) -> ResolvedField:
        """
        Resolve a single field definition.
        
        Args:
            field_def: The field definition
            context: Dict with data sources
            
        Returns:
            ResolvedField with value populated, or None value if condition not met
        """
        # Check condition if specified
        if field_def.condition_field and field_def.condition_equals is not None:
            condition_value = cls.resolve_path(field_def.condition_field, context)
            if str(condition_value) != str(field_def.condition_equals):
                # Condition not met - return field with None value (won't be sent)
                logger.debug(f"Condition not met for {field_def.field_key}: {condition_value} != {field_def.condition_equals}")
                return ResolvedField(
                    field_key=field_def.field_key,
                    docuseal_field=field_def.docuseal_field,
                    role_key=field_def.role_key,
                    value=None,
                    is_manual=False
                )
        
        # Manual entry fields have no source
        if field_def.source is None:
            return ResolvedField(
                field_key=field_def.field_key,
                docuseal_field=field_def.docuseal_field,
                role_key=field_def.role_key,
                value=None,
                is_manual=True
            )
        
        # Resolve the source path
        raw_value = cls.resolve_path(field_def.source, context)
        
        # Apply transform if specified
        transformed_value = apply_transform(raw_value, field_def.transform)
        
        return ResolvedField(
            field_key=field_def.field_key,
            docuseal_field=field_def.docuseal_field,
            role_key=field_def.role_key,
            value=transformed_value if transformed_value else None,
            is_manual=False
        )
    
    @classmethod
    def resolve_path(cls, source_path: str, context: Dict[str, Any]) -> Any:
        """
        Resolve a source path to a value.
        
        Args:
            source_path: Path like "user.email" or "transaction.sellers[0].name"
            context: Dict containing data sources
            
        Returns:
            The resolved value, or None if not found
        """
        if not source_path:
            return None
        
        # Split into parts, handling both dot notation and brackets
        parts = cls._parse_path(source_path)
        
        if not parts:
            return None
        
        # Get the root object from context
        root_key = parts[0]
        if root_key not in context:
            logger.debug(f"Root key '{root_key}' not in context")
            return None
        
        # Navigate through the path
        current = context[root_key]
        
        for part in parts[1:]:
            if current is None:
                return None
            
            current = cls._get_value(current, part)
        
        return current
    
    @classmethod
    def _parse_path(cls, path: str) -> List[str]:
        """
        Parse a source path into parts.
        
        Examples:
            "user.email" -> ["user", "email"]
            "transaction.sellers[0].name" -> ["transaction", "sellers[0]", "name"]
        """
        # Split by dots, but preserve bracket notation
        parts = []
        current = ""
        in_bracket = False
        
        for char in path:
            if char == '[':
                in_bracket = True
                current += char
            elif char == ']':
                in_bracket = False
                current += char
            elif char == '.' and not in_bracket:
                if current:
                    parts.append(current)
                current = ""
            else:
                current += char
        
        if current:
            parts.append(current)
        
        return parts
    
    @classmethod
    def _get_value(cls, obj: Any, part: str) -> Any:
        """
        Get a value from an object by property name or index.
        
        Handles:
            - Object attributes (getattr)
            - Dict keys
            - List/tuple indices via bracket notation
            - Special computed properties (full_name)
        """
        # Check for bracket notation: sellers[0]
        bracket_match = cls.BRACKET_PATTERN.match(part)
        if bracket_match:
            attr_name = bracket_match.group(1)
            index = int(bracket_match.group(2))
            
            # Get the list/collection first
            collection = cls._get_attr_or_key(obj, attr_name)
            if collection is None:
                return None
            
            # Access by index
            try:
                # Handle SQLAlchemy query objects
                if hasattr(collection, 'all'):
                    collection = collection.all()
                
                if isinstance(collection, (list, tuple)) and 0 <= index < len(collection):
                    return collection[index]
                return None
            except (IndexError, TypeError):
                return None
        
        # Handle special computed properties
        if part == 'full_name':
            return cls._get_full_name(obj)
        
        # Standard attribute/key access
        return cls._get_attr_or_key(obj, part)
    
    @classmethod
    def _get_attr_or_key(cls, obj: Any, key: str) -> Any:
        """Get a value by attribute or dict key."""
        # Try as dict key first (for form data)
        if isinstance(obj, dict):
            return obj.get(key)
        
        # Try as attribute
        if hasattr(obj, key):
            value = getattr(obj, key)
            # Handle callable relationships (SQLAlchemy)
            if callable(value) and not isinstance(value, type):
                try:
                    return value()
                except TypeError:
                    return value
            return value
        
        return None
    
    @classmethod
    def _get_full_name(cls, obj: Any) -> Optional[str]:
        """
        Compute full_name from first_name and last_name.
        
        This is a common computed property for User and Participant objects.
        """
        first = cls._get_attr_or_key(obj, 'first_name') or ''
        last = cls._get_attr_or_key(obj, 'last_name') or ''
        
        # Also try display_name for participants
        if not first and not last:
            display = cls._get_attr_or_key(obj, 'display_name')
            if display:
                return display
        
        full = f"{first} {last}".strip()
        return full if full else None
    
    @classmethod
    def resolve_single(cls, source_path: str, context: Dict[str, Any]) -> Any:
        """
        Convenience method to resolve a single source path.
        
        Useful for resolving role email/name sources.
        """
        return cls.resolve_path(source_path, context)

