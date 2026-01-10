"""
Role Builder

Groups resolved fields by their DocuSeal role and builds
submitter objects ready for the DocuSeal API.
"""

import logging
from typing import Any, Dict, List, Optional

from .types import DocumentDefinition, ResolvedField, RoleDefinition, Submitter
from .field_resolver import FieldResolver

logger = logging.getLogger(__name__)


class RoleBuilder:
    """
    Builds DocuSeal submitter objects from document definitions and resolved fields.
    
    Takes:
        - Document definition (roles, fields)
        - Resolved fields (with values)
        - Context (for resolving role email/name)
    
    Returns:
        - List of Submitter objects ready for DocuSeal API
    """
    
    @classmethod
    def build(
        cls,
        definition: DocumentDefinition,
        resolved_fields: List[ResolvedField],
        context: Dict[str, Any]
    ) -> List[Submitter]:
        """
        Build submitter objects for all roles in the document.
        
        Args:
            definition: Document definition with role configs
            resolved_fields: List of resolved fields with values
            context: Data context for resolving role email/name
            
        Returns:
            List of Submitter objects, one per role (excluding optional roles with no data)
        """
        submitters = []
        
        # Group fields by role_key
        fields_by_role: Dict[str, List[ResolvedField]] = {}
        for field in resolved_fields:
            if field.role_key not in fields_by_role:
                fields_by_role[field.role_key] = []
            fields_by_role[field.role_key].append(field)
        
        # Build submitter for each role
        for role_def in definition.roles:
            submitter = cls._build_submitter(
                role_def=role_def,
                fields=fields_by_role.get(role_def.role_key, []),
                context=context
            )
            
            if submitter:
                submitters.append(submitter)
        
        logger.debug(f"Built {len(submitters)} submitter(s) for {definition.slug}")
        return submitters
    
    @classmethod
    def _build_submitter(
        cls,
        role_def: RoleDefinition,
        fields: List[ResolvedField],
        context: Dict[str, Any]
    ) -> Optional[Submitter]:
        """
        Build a single submitter from a role definition.
        
        Returns None if the role is optional and has no valid data.
        """
        # Resolve email and name from context
        email = FieldResolver.resolve_single(role_def.email_source, context)
        name = FieldResolver.resolve_single(role_def.name_source, context)
        
        # Skip optional roles with no email
        if role_def.optional and not email:
            logger.debug(f"Skipping optional role '{role_def.role_key}' (no email)")
            return None
        
        # Use placeholder for preview if no email but role is required
        if not email:
            logger.warning(f"No email for required role '{role_def.role_key}', using placeholder")
            email = "placeholder@preview.local"
            name = name or "Placeholder"
        
        # Build DocuSeal field format
        docuseal_fields = []
        for field in fields:
            field_dict = field.to_docuseal_format()
            if field_dict:  # Skip None (manual fields)
                docuseal_fields.append(field_dict)
        
        return Submitter(
            role=role_def.docuseal_role,
            email=email,
            name=name or email,
            fields=docuseal_fields
        )
    
    @classmethod
    def build_for_preview(
        cls,
        definition: DocumentDefinition,
        resolved_fields: List[ResolvedField],
        context: Dict[str, Any],
        preview_email: str = None
    ) -> List[Submitter]:
        """
        Build submitters for preview mode.
        
        In preview mode, all required roles get the preview email
        so we can generate a preview submission without sending emails.
        
        Args:
            definition: Document definition
            resolved_fields: Resolved fields with values
            context: Data context
            preview_email: Email to use for all submitters (optional)
            
        Returns:
            List of Submitter objects for preview
        """
        submitters = []
        
        # Group fields by role_key
        fields_by_role: Dict[str, List[ResolvedField]] = {}
        for field in resolved_fields:
            if field.role_key not in fields_by_role:
                fields_by_role[field.role_key] = []
            fields_by_role[field.role_key].append(field)
        
        # Build submitter for each role
        for role_def in definition.roles:
            # Resolve name for display
            name = FieldResolver.resolve_single(role_def.name_source, context)
            
            # For optional roles, check if we have data
            if role_def.optional:
                email = FieldResolver.resolve_single(role_def.email_source, context)
                if not email:
                    logger.debug(f"Skipping optional role '{role_def.role_key}' in preview")
                    continue
            
            # Build DocuSeal field format
            role_fields = fields_by_role.get(role_def.role_key, [])
            docuseal_fields = []
            for field in role_fields:
                field_dict = field.to_docuseal_format()
                if field_dict:
                    docuseal_fields.append(field_dict)
            
            # Use preview email or resolve from context
            if preview_email:
                use_email = preview_email
            else:
                use_email = FieldResolver.resolve_single(role_def.email_source, context)
                if not use_email:
                    use_email = f"preview-{role_def.role_key}@preview.local"
            
            submitters.append(Submitter(
                role=role_def.docuseal_role,
                email=use_email,
                name=name or role_def.docuseal_role,
                fields=docuseal_fields
            ))
        
        logger.debug(f"Built {len(submitters)} preview submitter(s) for {definition.slug}")
        return submitters
    
    @classmethod
    def build_for_send(
        cls,
        definition: DocumentDefinition,
        resolved_fields: List[ResolvedField],
        context: Dict[str, Any]
    ) -> List[Submitter]:
        """
        Build submitters for actual sending (emails will be sent).
        
        Unlike preview, this uses actual participant emails and
        excludes optional roles without valid email addresses.
        
        Args:
            definition: Document definition
            resolved_fields: Resolved fields with values
            context: Data context
            
        Returns:
            List of Submitter objects for sending
        """
        # For send, we use the standard build which already handles
        # optional roles correctly (skips them if no email)
        return cls.build(definition, resolved_fields, context)

