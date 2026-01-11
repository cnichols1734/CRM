"""
Auto-Mapper Service v2

Intelligent field mapping between HTML forms and DocuSeal templates.
Uses domain-specific knowledge of real estate documents, semantic matching,
and pattern recognition for high-quality automatic mappings.
"""

import re
from typing import Any, Dict, List, Optional, Set, Tuple
from difflib import SequenceMatcher


class AutoMapper:
    """
    Intelligent field mapper for real estate document forms.

    Uses multiple strategies:
    - Semantic synonym matching (domain-specific)
    - Pattern-based matching (numbered fields, options)
    - Word-level analysis with weighted scoring
    - Type and transform inference
    - Role-aware matching
    """

    # Minimum confidence score to suggest a mapping
    MIN_CONFIDENCE = 35

    # ==========================================================================
    # DOMAIN-SPECIFIC SYNONYMS
    # ==========================================================================
    # Maps normalized terms to their synonyms/variations
    # When any of these terms appear, they can match each other

    SYNONYMS = {
        # Address fields
        'address': {'address', 'addr', 'street', 'street_address', 'property_address', 'location'},
        'street': {'street', 'street_address', 'address', 'property_address'},
        'city': {'city', 'town', 'municipality'},
        'state': {'state', 'st', 'province'},
        'zip': {'zip', 'zipcode', 'zip_code', 'postal', 'postal_code'},

        # Name fields
        'name': {'name', 'full_name', 'fullname', 'seller', 'buyer', 'client'},
        'seller': {'seller', 'seller_name', 'seller_1', 'primary_seller', 'owner'},
        'buyer': {'buyer', 'buyer_name', 'buyer_1', 'primary_buyer', 'purchaser'},

        # Contact fields
        'phone': {'phone', 'telephone', 'tel', 'mobile', 'cell', 'contact'},
        'email': {'email', 'e_mail', 'mail', 'contact'},

        # Date fields
        'date': {'date', 'dt', 'effective_date', 'closing_date', 'expiration'},
        'closing': {'closing', 'closing_date', 'close', 'settlement'},

        # Money/Fee fields
        'fee': {'fee', 'fees', 'cost', 'costs', 'price', 'amount', 'charge'},
        'price': {'price', 'amount', 'cost', 'fee', 'value', 'sales_price'},
        'amount': {'amount', 'amt', 'fee', 'cost', 'price', 'total'},
        'total': {'total', 'sum', 'subtotal', 'amount', 'net'},

        # Tax fields
        'tax': {'tax', 'taxes', 'property_tax', 'annual_tax'},
        'prorated': {'prorated', 'prorate', 'proration', 'pro_rated'},

        # HOA/Association fields
        'hoa': {'hoa', 'association', 'homeowners', 'owners_association'},
        'maintenance': {'maintenance', 'maint', 'hoa_fee', 'association_fee'},

        # Document/Option fields
        'option': {'option', 'opt', 'choice', 'selection'},
        'days': {'days', 'day', 'period', 'term'},

        # Signature fields
        'signature': {'signature', 'sign', 'sig', 'initials'},
        'initials': {'initials', 'initial', 'init'},

        # Financing fields
        'financing': {'financing', 'finance', 'loan', 'mortgage'},
        'conventional': {'conventional', 'conv'},
        'fha': {'fha', 'federal_housing'},
        'va': {'va', 'veterans'},
        'usda': {'usda', 'rural'},
        'cash': {'cash', 'all_cash'},

        # Repair fields
        'repairs': {'repairs', 'repair', 'fix', 'maintenance'},
        'lender': {'lender', 'bank', 'mortgage_company'},

        # Title/Escrow fields
        'title': {'title', 'title_company', 'title_policy'},
        'escrow': {'escrow', 'escrow_fee', 'closing'},
        'survey': {'survey', 'surveyor', 'plat'},

        # Misc
        'prepared': {'prepared', 'prepared_by', 'agent', 'broker'},
        'additional': {'additional', 'extra', 'other', 'custom'},
    }

    # ==========================================================================
    # EXACT MATCH PATTERNS
    # ==========================================================================
    # Direct field name -> DocuSeal field mappings for common patterns

    EXACT_MAPPINGS = {
        # Address
        'property_address': ['Street address', 'Address', 'Property Address', 'Property address'],
        'street_address': ['Street address', 'Address', 'Street Address'],

        # Names
        'seller_name': ['Seller', 'Seller 1', 'Seller Name', 'Seller name'],
        'buyer_name': ['Buyer', 'Buyer 1', 'Buyer Name'],
        'prepared_by': ['Prepared By', 'Prepared by', 'Agent', 'Agent Name'],

        # Dates
        'closing_date': ['Anticipated Closing Date', 'Closing Date', 'Close Date'],
        'effective_date': ['Effective Date', 'Contract Date'],

        # Financial
        'sales_price': ['Sales Price', 'Purchase Price', 'Sale Price'],
        'list_price': ['List Price', 'Listing Price'],
        'net_proceeds': ['Estimated Net Proceeds', 'Net Proceeds', 'Net'],
        'total_costs': ['Total Estimated Costs', 'Total Costs', 'Total'],
        'loan_payoff': ['Loan Payoff', 'Mortgage Payoff', 'Payoff'],

        # Fees
        'brokers_fee': ['Brokers Fee Calculated', 'Broker Fee', 'Commission'],
        'brokers_fee_percent': ['Brokers Percentage', 'Commission %', 'Broker %'],
        'escrow_fee': ['Escrow Fee', 'Escrow'],
        'title_policy': ['Title Policy Owner', 'Title Policy', 'Title Insurance'],
        'survey_fee': ['Survey Fee', 'Survey'],
        'recording_fees': ['Recording Fees', 'Recording Fee', 'Recording'],
        'wiring_fees': ['Wiring Fees', 'Wire Fee', 'Wire Transfer'],
        'courier_fees': ['Courier Fee', 'Courier Fees', 'Courier'],
        'attorneys_fees': ["Attorney's Fees", 'Attorney Fee', 'Legal Fees'],
        'tax_cert_fee': ['Tax Certificate Fee', 'Tax Cert Fee'],

        # Taxes
        'annual_property_taxes': ['Estimated Annual Property Taxes', 'Annual Taxes', 'Property Taxes'],
        'prorated_taxes': ['Taxes Amt', 'Prorated Taxes', 'Tax Proration'],

        # HOA
        'hoa_name': ['Association name & phone', 'HOA Name', 'Association Name'],
        'annual_maintenance_fees': ['Estimated Annual Maintenance Fees', 'Annual Maintenance', 'HOA Fees'],
        'condo_transfer_fee': ['Condo Transfer Fee', 'Transfer Fee'],

        # Repairs
        'repairs_buyer': ['Repairs Required by Buyer', 'Buyer Repairs'],
        'repairs_lender': ['Repairs Required by Lender', 'Lender Repairs'],
        'residential_service': ['Res Service Contract', 'Home Warranty', 'Service Contract'],

        # Prorations
        'prorated_days': ['Prorated For', 'Proration Days', 'Days'],
        'prorated_interest': ['Interest Assumptions', 'Prorated Interest'],
        'prorated_maintenance': ['Maintenance Fees', 'Prorated Maintenance'],

        # Refunds
        'unused_insurance': ['Unused Insurance', 'Insurance Refund'],
        'escrow_balance': ['Escrow Balance', 'Escrow Refund'],
        'total_refunds': ['Total Estimated Refunds', 'Total Refunds'],

        # Assessments
        'assessments': ['Assessments', 'Special Assessments'],
        'rents': ['Rents fee', 'Rents', 'Rental Income'],
        'seller_allowances': ['Nonallowables', 'Seller Allowances', 'Allowances'],
    }

    # ==========================================================================
    # PATTERN MATCHERS
    # ==========================================================================
    # Regex patterns for numbered/sequential fields

    NUMBERED_PATTERNS = [
        # "custom_label_1" -> "Additional Cost txt 1"
        (r'custom_label_(\d+)', r'Additional Cost txt {n}'),
        (r'custom_amount_(\d+)', r'Additional Cost \$ {n}'),

        # "option_1" -> "Option 1"
        (r'option_(\d+)', r'Option {n}'),

        # "seller_delivery_days" -> "1. Days after effective date"
        (r'seller_delivery_days', r'1\. Days after effective date'),
        (r'buyer_delivery_days', r'2\. Days after effective date'),

        # Financing options
        (r'financing_conventional', r'Conventional Option'),
        (r'financing_va', r'VA Option'),
        (r'financing_fha', r'FHA Option'),
        (r'financing_usda', r'USDA Option'),
        (r'financing_reverse', r'Reverse Mortgage Option'),
        (r'financing_assumption', r'Assumption Option'),
        (r'financing_owner', r'Owner Option'),
        (r'financing_cash', r'Cash Option'),
    ]

    # ==========================================================================
    # TRANSFORM INFERENCE
    # ==========================================================================
    # Field name patterns that suggest specific transforms

    TRANSFORM_PATTERNS = {
        'currency': [
            r'fee', r'fees', r'cost', r'costs', r'price', r'amount', r'amt',
            r'tax', r'taxes', r'payoff', r'proceeds', r'balance', r'refund',
            r'repair', r'allowance', r'assessment', r'rent', r'insurance',
            r'escrow', r'title', r'survey', r'courier', r'wiring', r'recording',
            r'total', r'net', r'gross', r'deduction', r'credit'
        ],
        'date_short': [
            r'date', r'closing', r'effective', r'expiration', r'term'
        ],
        'checkbox': [
            r'option', r'financing', r'require', r'pays', r'does', r'checkbox',
            r'selected', r'checked', r'approved'
        ],
        'phone': [
            r'phone', r'tel', r'mobile', r'cell', r'fax'
        ],
        'percent': [
            r'percent', r'percentage', r'rate', r'pct', r'\%'
        ],
    }

    # ==========================================================================
    # TYPE COMPATIBILITY
    # ==========================================================================

    TYPE_COMPATIBILITY = {
        'text': ['text', 'string', 'textarea'],
        'number': ['number', 'text', 'string'],
        'date': ['date', 'text', 'string'],
        'checkbox': ['checkbox', 'text'],
        'radio': ['radio', 'checkbox', 'text'],
        'select': ['select', 'text', 'string'],
        'textarea': ['text', 'textarea', 'string'],
    }

    # ==========================================================================
    # MAIN API
    # ==========================================================================

    @classmethod
    def auto_map(
        cls,
        html_fields: List[Dict[str, Any]],
        docuseal_fields: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Automatically map HTML fields to DocuSeal fields.

        Uses intelligent matching with domain-specific knowledge.

        Args:
            html_fields: List of HTML field definitions
            docuseal_fields: List of DocuSeal field definitions

        Returns:
            List of mapping suggestions with confidence scores
        """
        mappings = []
        used_docuseal_fields: Set[str] = set()

        # Build lookup structures for faster matching
        ds_by_normalized = cls._build_docuseal_lookup(docuseal_fields)

        # First pass: Try exact mappings (highest confidence)
        for html_field in html_fields:
            html_name = html_field.get('name', '')
            html_normalized = cls._normalize_name(html_name)

            # Check exact mappings first
            exact_match = cls._find_exact_mapping(html_normalized, docuseal_fields, used_docuseal_fields)
            if exact_match:
                exact_match['html_field'] = html_name
                exact_match['html_type'] = html_field.get('html_type', 'text')
                exact_match['suggested_transform'] = cls._infer_transform(html_name, exact_match.get('docuseal_type', 'text'))
                mappings.append(exact_match)
                used_docuseal_fields.add(exact_match['docuseal_field'])

        # Second pass: Pattern matching
        for html_field in html_fields:
            html_name = html_field.get('name', '')
            if any(m['html_field'] == html_name for m in mappings):
                continue

            pattern_match = cls._find_pattern_match(html_name, docuseal_fields, used_docuseal_fields)
            if pattern_match:
                pattern_match['html_field'] = html_name
                pattern_match['html_type'] = html_field.get('html_type', 'text')
                pattern_match['suggested_transform'] = cls._infer_transform(html_name, pattern_match.get('docuseal_type', 'text'))
                mappings.append(pattern_match)
                used_docuseal_fields.add(pattern_match['docuseal_field'])

        # Third pass: Semantic/fuzzy matching for remaining fields
        for html_field in html_fields:
            html_name = html_field.get('name', '')
            if any(m['html_field'] == html_name for m in mappings):
                continue

            fuzzy_match = cls._find_semantic_match(html_field, docuseal_fields, used_docuseal_fields)
            if fuzzy_match:
                mappings.append(fuzzy_match)
                used_docuseal_fields.add(fuzzy_match['docuseal_field'])

        # Sort by original HTML field order
        html_order = {f.get('name'): i for i, f in enumerate(html_fields)}
        mappings.sort(key=lambda m: html_order.get(m['html_field'], 999))

        return mappings

    # ==========================================================================
    # MATCHING STRATEGIES
    # ==========================================================================

    @classmethod
    def _find_exact_mapping(
        cls,
        html_normalized: str,
        docuseal_fields: List[Dict[str, Any]],
        used_fields: Set[str]
    ) -> Optional[Dict[str, Any]]:
        """Find exact mapping from predefined mappings."""
        if html_normalized in cls.EXACT_MAPPINGS:
            for ds_name_candidate in cls.EXACT_MAPPINGS[html_normalized]:
                for ds_field in docuseal_fields:
                    ds_name = ds_field.get('name', '')
                    if ds_name in used_fields:
                        continue
                    if ds_name.lower() == ds_name_candidate.lower():
                        return {
                            'docuseal_field': ds_name,
                            'docuseal_role': ds_field.get('role', 'Seller'),
                            'docuseal_type': ds_field.get('type', 'text'),
                            'confidence': 98,
                            'match_type': 'exact'
                        }
        return None

    @classmethod
    def _find_pattern_match(
        cls,
        html_name: str,
        docuseal_fields: List[Dict[str, Any]],
        used_fields: Set[str]
    ) -> Optional[Dict[str, Any]]:
        """Find match using regex patterns."""
        html_normalized = cls._normalize_name(html_name)

        for pattern, replacement in cls.NUMBERED_PATTERNS:
            match = re.match(pattern, html_normalized)
            if match:
                # Build expected DocuSeal field name
                if match.groups():
                    expected = replacement.replace('{n}', match.group(1))
                else:
                    expected = replacement

                # Search for this in DocuSeal fields
                for ds_field in docuseal_fields:
                    ds_name = ds_field.get('name', '')
                    if ds_name in used_fields:
                        continue

                    # Check if matches expected pattern
                    if re.search(re.escape(expected), ds_name, re.IGNORECASE):
                        return {
                            'docuseal_field': ds_name,
                            'docuseal_role': ds_field.get('role', 'Seller'),
                            'docuseal_type': ds_field.get('type', 'text'),
                            'confidence': 95,
                            'match_type': 'pattern'
                        }
        return None

    @classmethod
    def _find_semantic_match(
        cls,
        html_field: Dict[str, Any],
        docuseal_fields: List[Dict[str, Any]],
        used_fields: Set[str]
    ) -> Optional[Dict[str, Any]]:
        """Find best semantic/fuzzy match for an HTML field."""
        html_name = html_field.get('name', '')
        html_type = html_field.get('html_type', 'text')
        html_normalized = cls._normalize_name(html_name)
        html_words = set(html_normalized.split('_'))

        best_score = 0
        best_match = None

        for ds_field in docuseal_fields:
            ds_name = ds_field.get('name', '')
            if ds_name in used_fields:
                continue

            ds_type = ds_field.get('type', 'text')
            ds_role = ds_field.get('role', 'Seller')
            ds_normalized = cls._normalize_name(ds_name)
            ds_words = set(ds_normalized.split('_'))

            score = 0

            # 1. Exact normalized match (very high)
            if html_normalized == ds_normalized:
                score = 95

            else:
                # 2. Synonym matching (high value)
                synonym_score = cls._calculate_synonym_score(html_words, ds_words)
                score += synonym_score * 0.5  # Up to 50 points

                # 3. Word overlap (medium value)
                if html_words and ds_words:
                    overlap = len(html_words & ds_words)
                    total = len(html_words | ds_words)
                    word_score = (overlap / total) * 30
                    score += word_score

                # 4. Fuzzy string similarity (lower value)
                similarity = SequenceMatcher(None, html_normalized, ds_normalized).ratio()
                score += similarity * 15

                # 5. Type compatibility bonus
                if cls._types_compatible(html_type, ds_type):
                    score += 5

            if score > best_score and score >= cls.MIN_CONFIDENCE:
                best_score = score
                best_match = {
                    'html_field': html_name,
                    'html_type': html_type,
                    'docuseal_field': ds_name,
                    'docuseal_role': ds_role,
                    'docuseal_type': ds_type,
                    'confidence': min(int(score), 94),  # Cap below exact match
                    'suggested_transform': cls._infer_transform(html_name, ds_type),
                    'match_type': 'semantic'
                }

        return best_match

    @classmethod
    def _calculate_synonym_score(cls, html_words: Set[str], ds_words: Set[str]) -> int:
        """
        Calculate synonym-based match score.

        Checks if words from HTML field are synonyms of words in DocuSeal field.
        """
        score = 0
        matched_html = set()
        matched_ds = set()

        for hw in html_words:
            for dw in ds_words:
                # Direct match
                if hw == dw:
                    score += 25
                    matched_html.add(hw)
                    matched_ds.add(dw)
                    continue

                # Synonym match
                hw_synonyms = cls._get_synonyms(hw)
                dw_synonyms = cls._get_synonyms(dw)

                if hw in dw_synonyms or dw in hw_synonyms or (hw_synonyms & dw_synonyms):
                    score += 20
                    matched_html.add(hw)
                    matched_ds.add(dw)

        # Bonus for matching most words
        if html_words and matched_html:
            coverage = len(matched_html) / len(html_words)
            score += int(coverage * 20)

        return min(score, 100)

    @classmethod
    def _get_synonyms(cls, word: str) -> Set[str]:
        """Get all synonyms for a word."""
        synonyms = {word}
        for key, syn_set in cls.SYNONYMS.items():
            if word in syn_set or word == key:
                synonyms |= syn_set
                synonyms.add(key)
        return synonyms

    # ==========================================================================
    # TRANSFORM INFERENCE
    # ==========================================================================

    @classmethod
    def _infer_transform(cls, field_name: str, ds_type: str) -> Optional[str]:
        """Infer the best transform based on field name and type."""
        name_lower = field_name.lower()

        # Check each transform pattern
        for transform, patterns in cls.TRANSFORM_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, name_lower):
                    return transform

        # Type-based fallback
        if ds_type == 'date':
            return 'date_short'
        if ds_type == 'checkbox':
            return 'checkbox'

        return None

    # ==========================================================================
    # UTILITIES
    # ==========================================================================

    @classmethod
    def _normalize_name(cls, name: str) -> str:
        """Normalize a field name for comparison."""
        if not name:
            return ''

        # Lowercase and replace non-alphanumeric with underscores
        normalized = name.lower()
        normalized = re.sub(r'[^a-z0-9]', '_', normalized)
        normalized = re.sub(r'_+', '_', normalized)
        normalized = normalized.strip('_')

        # Remove common prefixes
        for prefix in ['field_', 'form_', 'input_', 'txt_']:
            if normalized.startswith(prefix):
                normalized = normalized[len(prefix):]

        # Remove common suffixes
        for suffix in ['_field', '_input', '_txt']:
            if normalized.endswith(suffix):
                normalized = normalized[:-len(suffix)]

        return normalized

    @classmethod
    def _build_docuseal_lookup(
        cls,
        docuseal_fields: List[Dict[str, Any]]
    ) -> Dict[str, Dict[str, Any]]:
        """Build a lookup dict of normalized DocuSeal field names."""
        lookup = {}
        for field in docuseal_fields:
            name = field.get('name', '')
            normalized = cls._normalize_name(name)
            lookup[normalized] = field
        return lookup

    @classmethod
    def _types_compatible(cls, html_type: str, ds_type: str) -> bool:
        """Check if HTML and DocuSeal field types are compatible."""
        html_type = html_type.lower()
        ds_type = ds_type.lower()

        if html_type == ds_type:
            return True

        compatible = cls.TYPE_COMPATIBILITY.get(html_type, ['text'])
        return ds_type in compatible

    # ==========================================================================
    # LEGACY API METHODS (for backward compatibility)
    # ==========================================================================

    @classmethod
    def suggest_role_key(cls, docuseal_role: str) -> str:
        """Suggest a role_key based on DocuSeal role name."""
        if not docuseal_role:
            return 'seller'

        role_key = docuseal_role.lower()
        role_key = re.sub(r'[^a-z0-9]', '_', role_key)
        role_key = re.sub(r'_+', '_', role_key)
        role_key = role_key.strip('_')

        return role_key

    @classmethod
    def suggest_source_path(cls, role_key: str, field_name: str) -> str:
        """Suggest a source path based on role and field name."""
        field_normalized = cls._normalize_name(field_name)

        # Check for computed properties
        if 'full_address' in field_normalized or 'property_address' in field_normalized:
            if 'city' in field_normalized or 'state' in field_normalized:
                return 'transaction.full_address'

        # Default to form data
        return f'form.{field_normalized}'
