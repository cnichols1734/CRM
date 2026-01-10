"""
YAML Generator Service

Generates schema-compliant YAML document definitions from mapping configurations.
"""

import yaml
from typing import Any, Dict, List, Optional
from datetime import datetime


class YamlGenerator:
    """
    Generates YAML document definitions for the new document system.
    
    Output format matches documents/schema/v1.0.json schema.
    """
    
    @classmethod
    def generate(cls, config: Dict[str, Any]) -> str:
        """
        Generate YAML content from a mapping configuration.
        
        Args:
            config: Mapping configuration dict containing:
                - slug: Document slug (e.g., 'listing-agreement')
                - name: Document display name
                - docuseal_template_id: DocuSeal template ID
                - type: 'form-driven' or 'pdf-preview'
                - display: {color, icon, sort_order}
                - form: {template, partial} (if form-driven)
                - roles: List of role configurations
                - fields: List of field mappings
        
        Returns:
            YAML string ready to save to file
        """
        # Build the document structure
        doc = cls._build_document_structure(config)
        
        # Generate YAML with custom formatting
        yaml_content = cls._format_yaml(doc, config)
        
        return yaml_content
    
    @classmethod
    def _build_document_structure(cls, config: Dict[str, Any]) -> Dict[str, Any]:
        """Build the document definition structure."""
        doc = {
            'schema_version': '1.0',
            'slug': config.get('slug', ''),
            'name': config.get('name', ''),
            'docuseal_template_id': config.get('docuseal_template_id', 0),
            'type': config.get('type', 'form-driven'),
            'display': config.get('display', {
                'color': '#6B7280',
                'icon': 'fas fa-file',
                'sort_order': 99
            }),
        }
        
        # Add form config if form-driven
        if doc['type'] == 'form-driven':
            doc['form'] = config.get('form', {
                'template': f"{config.get('slug', 'document')}_form.html",
                'partial': f"{config.get('slug', 'document')}_fields.html"
            })
        
        # Add roles
        doc['roles'] = cls._build_roles(config.get('roles', []))
        
        # Add fields
        doc['fields'] = cls._build_fields(config.get('fields', []))
        
        return doc
    
    @classmethod
    def _build_roles(cls, roles_config: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Build the roles list for the document."""
        roles = []
        
        for role in roles_config:
            role_def = {
                'role_key': role.get('role_key', ''),
                'docuseal_role': role.get('docuseal_role', ''),
                'email_source': role.get('email_source', 'transaction.primary_seller.display_email'),
                'name_source': role.get('name_source', 'transaction.primary_seller.display_name'),
            }
            
            # Add optional flags
            if role.get('optional'):
                role_def['optional'] = True
            if role.get('auto_complete'):
                role_def['auto_complete'] = True
            
            roles.append(role_def)
        
        return roles
    
    @classmethod
    def _build_fields(cls, fields_config: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Build the fields list for the document."""
        fields = []
        
        for field in fields_config:
            field_def = {
                'field_key': field.get('field_key', ''),
                'docuseal_field': field.get('docuseal_field', ''),
                'role_key': field.get('role_key', 'seller'),
            }
            
            # Add source (can be None for manual fields)
            source = field.get('source')
            if source:
                field_def['source'] = source
            else:
                field_def['source'] = None
            
            # Add optional transform
            if field.get('transform'):
                field_def['transform'] = field['transform']
            
            # Add conditional fields
            if field.get('condition_field'):
                field_def['condition_field'] = field['condition_field']
            if field.get('condition_equals'):
                field_def['condition_equals'] = field['condition_equals']
            
            fields.append(field_def)
        
        return fields
    
    @classmethod
    def _format_yaml(cls, doc: Dict[str, Any], config: Dict[str, Any]) -> str:
        """
        Format the document as YAML with comments and proper structure.
        
        Uses custom formatting for readability.
        """
        lines = []
        slug = config.get('slug', 'document')
        name = config.get('name', 'Document')
        
        # Header comment
        lines.append('# =============================================================================')
        lines.append(f'# {name.upper()}')
        lines.append('# =============================================================================')
        lines.append(f'# {name}')
        lines.append('#')
        lines.append(f'# Generated by Document Mapper v2 on {datetime.now().strftime("%Y-%m-%d %H:%M")}')
        lines.append(f'# Template ID: {doc["docuseal_template_id"]}')
        lines.append('# =============================================================================')
        lines.append('')
        
        # Schema and basic info
        lines.append(f'schema_version: "{doc["schema_version"]}"')
        lines.append(f'slug: {doc["slug"]}')
        lines.append(f'name: "{doc["name"]}"')
        lines.append(f'docuseal_template_id: {doc["docuseal_template_id"]}')
        lines.append(f'type: {doc["type"]}')
        lines.append('')
        
        # Display config
        lines.append('display:')
        lines.append(f'  color: "{doc["display"].get("color", "#6B7280")}"')
        lines.append(f'  icon: "{doc["display"].get("icon", "fas fa-file")}"')
        lines.append(f'  sort_order: {doc["display"].get("sort_order", 99)}')
        lines.append('')
        
        # Form config (if applicable)
        if 'form' in doc:
            lines.append('form:')
            lines.append(f'  template: {doc["form"].get("template", "")}')
            lines.append(f'  partial: {doc["form"].get("partial", "")}')
            lines.append('')
        
        # Roles section
        lines.append('# =============================================================================')
        lines.append('# ROLES')
        lines.append('# =============================================================================')
        lines.append('')
        lines.append('roles:')
        
        for role in doc.get('roles', []):
            lines.append(f'  - role_key: {role["role_key"]}')
            lines.append(f'    docuseal_role: "{role["docuseal_role"]}"')
            lines.append(f'    email_source: {role["email_source"]}')
            lines.append(f'    name_source: {role["name_source"]}')
            if role.get('optional'):
                lines.append('    optional: true')
            if role.get('auto_complete'):
                lines.append('    auto_complete: true')
            lines.append('')
        
        # Fields section
        lines.append('# =============================================================================')
        lines.append('# FIELDS')
        lines.append('# =============================================================================')
        lines.append('')
        lines.append('fields:')
        
        for field in doc.get('fields', []):
            lines.append(f'  - field_key: {field["field_key"]}')
            lines.append(f'    docuseal_field: "{field["docuseal_field"]}"')
            lines.append(f'    role_key: {field["role_key"]}')
            
            if field.get('source'):
                lines.append(f'    source: {field["source"]}')
            else:
                lines.append('    source: null')
            
            if field.get('transform'):
                lines.append(f'    transform: {field["transform"]}')
            
            if field.get('condition_field'):
                lines.append(f'    condition_field: {field["condition_field"]}')
            if field.get('condition_equals'):
                lines.append(f'    condition_equals: "{field["condition_equals"]}"')
            
            lines.append('')
        
        return '\n'.join(lines)
    
    @classmethod
    def generate_from_auto_map(
        cls,
        slug: str,
        name: str,
        template_id: int,
        mappings: List[Dict[str, Any]],
        roles: List[str],
        doc_type: str = 'form-driven',
        display_config: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Generate YAML from auto-mapping results.
        
        Convenience method that builds the config from auto-map output.
        """
        # Build roles from DocuSeal submitters
        roles_config = []
        for i, role_name in enumerate(roles):
            role_key = role_name.lower().replace(' ', '_')
            
            # Determine email/name sources based on role
            if 'seller' in role_key:
                if '2' in role_key or 'co' in role_key:
                    email_source = 'transaction.sellers[1].display_email'
                    name_source = 'transaction.sellers[1].display_name'
                    optional = True
                else:
                    email_source = 'transaction.primary_seller.display_email'
                    name_source = 'transaction.primary_seller.display_name'
                    optional = False
            elif 'buyer' in role_key:
                if '2' in role_key or 'co' in role_key:
                    email_source = 'transaction.buyers[1].display_email'
                    name_source = 'transaction.buyers[1].display_name'
                    optional = True
                else:
                    email_source = 'transaction.primary_buyer.display_email'
                    name_source = 'transaction.primary_buyer.display_name'
                    optional = False
            elif 'agent' in role_key:
                email_source = 'user.email'
                name_source = 'user.full_name'
                optional = False
            elif 'broker' in role_key:
                email_source = 'user.email'
                name_source = 'user.full_name'
                optional = False
            else:
                email_source = 'transaction.primary_seller.display_email'
                name_source = 'transaction.primary_seller.display_name'
                optional = i > 0
            
            roles_config.append({
                'role_key': role_key,
                'docuseal_role': role_name,
                'email_source': email_source,
                'name_source': name_source,
                'optional': optional,
                'auto_complete': 'agent' in role_key or 'broker' in role_key
            })
        
        # Build fields from mappings
        fields_config = []
        for mapping in mappings:
            field_key = mapping.get('html_field', '').replace('-', '_')
            role_key = mapping.get('docuseal_role', 'Seller').lower().replace(' ', '_')
            
            fields_config.append({
                'field_key': field_key,
                'docuseal_field': mapping.get('docuseal_field', ''),
                'role_key': role_key,
                'source': f"form.{field_key}",
                'transform': mapping.get('suggested_transform'),
                'condition_field': mapping.get('condition_field'),
                'condition_equals': mapping.get('condition_equals')
            })
        
        # Build config
        config = {
            'slug': slug,
            'name': name,
            'docuseal_template_id': template_id,
            'type': doc_type,
            'display': display_config or {
                'color': '#6B7280',
                'icon': 'fas fa-file',
                'sort_order': 99
            },
            'form': {
                'template': f'{slug.replace("-", "_")}_form.html',
                'partial': f'{slug.replace("-", "_")}_fields.html'
            },
            'roles': roles_config,
            'fields': fields_config
        }
        
        return cls.generate(config)

