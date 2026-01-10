"""
Auto-Mapper Service

Automatically maps HTML form fields to DocuSeal template fields using
fuzzy string matching and type inference.
"""

import re
from typing import Any, Dict, List, Optional, Tuple
from difflib import SequenceMatcher


class AutoMapper:
    """
    Automatic field mapping between HTML forms and DocuSeal templates.
    
    Uses multiple strategies:
    - Exact name matching (highest priority)
    - Fuzzy string matching (Levenshtein-style)
    - Word overlap scoring
    - Type compatibility
    """
    
    # Minimum confidence score to suggest a mapping
    MIN_CONFIDENCE = 40
    
    # Type compatibility mappings
    TYPE_COMPATIBILITY = {
        'text': ['text', 'string'],
        'number': ['number', 'text'],
        'date': ['date', 'text'],
        'checkbox': ['checkbox', 'text'],
        'radio': ['radio', 'checkbox', 'text'],
        'select': ['select', 'text'],
        'textarea': ['text', 'textarea'],
    }
    
    # Common field name variations to normalize
    NAME_VARIATIONS = {
        'address': ['addr', 'street', 'property_address', 'street_address'],
        'phone': ['tel', 'telephone', 'mobile', 'cell'],
        'email': ['mail', 'e-mail'],
        'name': ['full_name', 'fullname'],
        'date': ['dt', 'effective_date', 'closing_date'],
        'fee': ['fees', 'cost', 'price', 'amount'],
        'seller': ['seller_1', 'primary_seller'],
        'buyer': ['buyer_1', 'primary_buyer'],
    }
    
    @classmethod
    def auto_map(
        cls,
        html_fields: List[Dict[str, Any]],
        docuseal_fields: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Automatically map HTML fields to DocuSeal fields.
        
        Args:
            html_fields: List of HTML field definitions
                         [{'name': 'property_address', 'type': 'text', ...}, ...]
            docuseal_fields: List of DocuSeal field definitions
                             [{'name': 'Property Address', 'type': 'text', 'role': 'Seller'}, ...]
        
        Returns:
            List of mapping suggestions with confidence scores
            [{'html_field': 'property_address', 
              'docuseal_field': 'Property Address',
              'docuseal_role': 'Seller',
              'confidence': 95,
              'suggested_transform': None}, ...]
        """
        mappings = []
        used_docuseal_fields = set()
        
        # Sort HTML fields by specificity (longer names first)
        sorted_html = sorted(html_fields, key=lambda f: len(f.get('name', '')), reverse=True)
        
        for html_field in sorted_html:
            best_match = cls._find_best_match(html_field, docuseal_fields, used_docuseal_fields)
            
            if best_match:
                mappings.append(best_match)
                used_docuseal_fields.add(best_match['docuseal_field'])
        
        # Sort by original HTML field order
        html_order = {f.get('name'): i for i, f in enumerate(html_fields)}
        mappings.sort(key=lambda m: html_order.get(m['html_field'], 999))
        
        return mappings
    
    @classmethod
    def _find_best_match(
        cls,
        html_field: Dict[str, Any],
        docuseal_fields: List[Dict[str, Any]],
        used_fields: set
    ) -> Optional[Dict[str, Any]]:
        """Find the best matching DocuSeal field for an HTML field."""
        html_name = html_field.get('name', '')
        html_type = html_field.get('html_type', 'text')
        
        best_score = 0
        best_match = None
        
        for ds_field in docuseal_fields:
            ds_name = ds_field.get('name', '')
            ds_type = ds_field.get('type', 'text')
            ds_role = ds_field.get('role', 'Seller')
            
            # Skip already used fields
            if ds_name in used_fields:
                continue
            
            # Calculate match score
            score = cls._calculate_match_score(html_name, html_type, ds_name, ds_type)
            
            if score > best_score and score >= cls.MIN_CONFIDENCE:
                best_score = score
                best_match = {
                    'html_field': html_name,
                    'docuseal_field': ds_name,
                    'docuseal_role': ds_role,
                    'confidence': score,
                    'suggested_transform': cls._suggest_transform(html_type, ds_type),
                    'html_type': html_type,
                    'docuseal_type': ds_type
                }
        
        return best_match
    
    @classmethod
    def _calculate_match_score(
        cls,
        html_name: str,
        html_type: str,
        ds_name: str,
        ds_type: str
    ) -> int:
        """
        Calculate a match score between HTML and DocuSeal fields.
        
        Score components:
        - Exact match: 100 points
        - Fuzzy similarity: up to 60 points
        - Word overlap: up to 30 points  
        - Type compatibility: 10 points
        
        Returns:
            Score from 0-100
        """
        score = 0
        
        # Normalize names for comparison
        html_normalized = cls._normalize_name(html_name)
        ds_normalized = cls._normalize_name(ds_name)
        
        # Exact match (highest priority)
        if html_normalized == ds_normalized:
            return 100
        
        # Fuzzy string similarity (SequenceMatcher)
        similarity = SequenceMatcher(None, html_normalized, ds_normalized).ratio()
        score += int(similarity * 60)
        
        # Word overlap scoring
        html_words = set(html_normalized.split('_'))
        ds_words = set(ds_normalized.split('_'))
        
        if html_words and ds_words:
            overlap = len(html_words & ds_words)
            max_words = max(len(html_words), len(ds_words))
            word_score = (overlap / max_words) * 30
            score += int(word_score)
        
        # Type compatibility bonus
        if cls._types_compatible(html_type, ds_type):
            score += 10
        
        return min(score, 99)  # Cap at 99 (100 is reserved for exact match)
    
    @classmethod
    def _normalize_name(cls, name: str) -> str:
        """
        Normalize a field name for comparison.
        
        - Convert to lowercase
        - Replace spaces with underscores
        - Remove common prefixes/suffixes
        - Handle common variations
        """
        if not name:
            return ''
        
        # Lowercase and replace spaces/special chars
        normalized = name.lower()
        normalized = re.sub(r'[^a-z0-9]', '_', normalized)
        normalized = re.sub(r'_+', '_', normalized)
        normalized = normalized.strip('_')
        
        # Remove common prefixes
        prefixes = ['field_', 'form_', 'input_']
        for prefix in prefixes:
            if normalized.startswith(prefix):
                normalized = normalized[len(prefix):]
        
        # Remove common suffixes
        suffixes = ['_field', '_input', '_1', '_2']
        for suffix in suffixes:
            if normalized.endswith(suffix):
                normalized = normalized[:-len(suffix)]
        
        return normalized
    
    @classmethod
    def _types_compatible(cls, html_type: str, ds_type: str) -> bool:
        """Check if HTML and DocuSeal field types are compatible."""
        html_type = html_type.lower()
        ds_type = ds_type.lower()
        
        # Same type is always compatible
        if html_type == ds_type:
            return True
        
        # Check compatibility map
        compatible = cls.TYPE_COMPATIBILITY.get(html_type, ['text'])
        return ds_type in compatible
    
    @classmethod
    def _suggest_transform(cls, html_type: str, ds_type: str) -> Optional[str]:
        """Suggest a transform based on field types."""
        html_type = html_type.lower()
        ds_type = ds_type.lower()
        
        # Currency fields
        if 'money' in html_type or 'currency' in html_type or 'fee' in ds_type or 'price' in ds_type:
            return 'currency'
        
        # Date fields
        if html_type == 'date' or ds_type == 'date':
            return 'date_short'
        
        # Checkbox fields
        if html_type == 'checkbox' or ds_type == 'checkbox':
            return 'checkbox'
        
        return None
    
    @classmethod
    def suggest_role_key(cls, docuseal_role: str) -> str:
        """
        Suggest a role_key based on DocuSeal role name.
        
        Examples:
            "Seller" -> "seller"
            "Seller 2" -> "seller_2"
            "Listing Agent" -> "listing_agent"
        """
        if not docuseal_role:
            return 'seller'
        
        # Normalize to snake_case
        role_key = docuseal_role.lower()
        role_key = re.sub(r'[^a-z0-9]', '_', role_key)
        role_key = re.sub(r'_+', '_', role_key)
        role_key = role_key.strip('_')
        
        return role_key
    
    @classmethod
    def suggest_source_path(cls, role_key: str, field_name: str) -> str:
        """
        Suggest a source path based on role and field name.
        
        Returns paths like:
        - "form.property_address"
        - "transaction.full_address"
        - "user.email"
        """
        field_normalized = cls._normalize_name(field_name)
        
        # Check for computed properties
        if 'full_address' in field_normalized or 'property_address' in field_normalized:
            if 'city' in field_normalized or 'state' in field_normalized:
                return 'transaction.full_address'
        
        # Default to form data
        return f'form.{field_normalized}'

