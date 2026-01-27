# services/document_registry.py
"""
Document Registry - Centralized configuration for document forms.

This registry defines UI configuration for each document type that has a
specialized form UI in the "Fill All Documents" flow.

Configuration includes:
- Partial template path for form fields
- Theme colors (for badges, accents, section borders)
- Icons (FontAwesome)
- Display order in combined fill view

TO ADD A NEW DOCUMENT:
1. Create the partial template:
   templates/transactions/partials/{slug}_fields.html

2. Add entry to DOCUMENT_REGISTRY below with:
   - slug matching the template_slug in TransactionDocument
   - partial_template path
   - color for theming (Tailwind color name)
   - icon (FontAwesome class)
   - sort_order for display ordering

3. Create YAML mapping for DocuSeal:
   docuseal_mappings/{slug}.yml

That's it! The Fill All Documents view will automatically include it.
"""

from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass
class DocumentConfig:
    """Configuration for a document type's UI and behavior."""
    
    slug: str                # Unique identifier (matches YAML filename and template_slug)
    name: str                # Display name for UI
    partial_template: str    # Path to form fields partial (relative to templates/)
    color: str               # Tailwind color name (orange, violet, blue, emerald, etc.)
    icon: str                # FontAwesome icon class (e.g., 'fa-file-contract')
    sort_order: int          # Display order in Fill All view (lower = first)
    
    @property
    def badge_bg_class(self) -> str:
        """Background class for badge (light variant)."""
        return f"bg-{self.color}-100"
    
    @property
    def badge_text_class(self) -> str:
        """Text class for badge."""
        return f"text-{self.color}-700"
    
    @property
    def badge_classes(self) -> str:
        """Combined badge classes for overview pills."""
        return f"bg-{self.color}-100 text-{self.color}-700"
    
    @property
    def gradient_class(self) -> str:
        """Gradient class for document header badges."""
        return f"from-{self.color}-500 to-{self.color}-600"
    
    @property
    def section_color_var(self) -> str:
        """CSS variable value for section accent color."""
        # Map Tailwind color names to hex values for CSS variables
        color_map = {
            'orange': '#f97316',
            'violet': '#8b5cf6',
            'blue': '#3b82f6',
            'emerald': '#10b981',
            'rose': '#f43f5e',
            'amber': '#f59e0b',
            'cyan': '#06b6d4',
            'indigo': '#6366f1',
            'teal': '#14b8a6',
            'pink': '#ec4899',
            'lime': '#84cc16',
            'sky': '#0ea5e9',
        }
        return color_map.get(self.color, '#64748b')  # Default to slate


# =============================================================================
# DOCUMENT REGISTRY
# =============================================================================
# Add new seller documents here. Each document with a specialized form UI
# needs an entry. Documents without entries will not appear in Fill All view.
#
# Color palette suggestions to keep documents visually distinct:
#   - orange: Listing Agreement (primary doc)
#   - violet: HOA Addendum
#   - emerald: Seller's Disclosure
#   - blue: Lead Paint Disclosure
#   - rose: Wire Fraud Warning
#   - amber: T-47 Affidavit
#   - cyan: Flood Hazard
#   - indigo: IABS
# =============================================================================

DOCUMENT_REGISTRY: Dict[str, DocumentConfig] = {
    'listing-agreement': DocumentConfig(
        slug='listing-agreement',
        name='Listing Agreement',
        partial_template='transactions/partials/listing_agreement_fields.html',
        color='orange',
        icon='fa-file-contract',
        sort_order=1,
    ),
    'hoa-addendum': DocumentConfig(
        slug='hoa-addendum',
        name='HOA Addendum',
        partial_template='transactions/partials/hoa_addendum_fields.html',
        color='violet',
        icon='fa-building',
        sort_order=2,
    ),
    'flood-hazard': DocumentConfig(
        slug='flood-hazard',
        name='Flood Hazard Information',
        partial_template='transactions/partials/flood_hazard_fields.html',
        color='cyan',
        icon='fa-water',
        sort_order=3,
    ),
    'seller-net-proceeds': DocumentConfig(
        slug='seller-net-proceeds',
        name="Seller's Estimated Net Proceeds",
        partial_template='transactions/partials/seller_net_proceeds_fields.html',
        color='emerald',
        icon='fa-calculator',
        sort_order=4,
    ),
    't47-affidavit': DocumentConfig(
        slug='t47-affidavit',
        name='T-47.1 Affidavit',
        partial_template='transactions/partials/t47_affidavit_fields.html',
        color='amber',
        icon='fa-file-signature',
        sort_order=5,
    ),
    # Future documents - uncomment and create partials/YAMLs as needed:
    #
    # 'sellers-disclosure': DocumentConfig(
    #     slug='sellers-disclosure',
    #     name="Seller's Disclosure",
    #     partial_template='transactions/partials/sellers_disclosure_fields.html',
    #     color='emerald',
    #     icon='fa-clipboard-list',
    #     sort_order=6,
    # ),
    # 'wire-fraud-warning': DocumentConfig(
    #     slug='wire-fraud-warning',
    #     name='Wire Fraud Warning',
    #     partial_template='transactions/partials/wire_fraud_fields.html',
    #     color='rose',
    #     icon='fa-exclamation-triangle',
    #     sort_order=7,
    # ),
}


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def get_document_config(slug: str) -> Optional[DocumentConfig]:
    """
    Get configuration for a document type by slug.
    
    Args:
        slug: Document template slug (e.g., 'listing-agreement')
        
    Returns:
        DocumentConfig if found, None otherwise
    """
    return DOCUMENT_REGISTRY.get(slug)


def get_specialized_slugs() -> List[str]:
    """
    Get list of document slugs that have specialized form UIs.
    
    Returns:
        List of slugs registered in DOCUMENT_REGISTRY
    """
    return list(DOCUMENT_REGISTRY.keys())


def get_configs_for_slugs(slugs: List[str]) -> Dict[str, DocumentConfig]:
    """
    Get configs for a list of slugs (useful for passing to templates).
    
    Args:
        slugs: List of document slugs to look up
        
    Returns:
        Dict mapping slug -> DocumentConfig for found documents
    """
    return {s: DOCUMENT_REGISTRY[s] for s in slugs if s in DOCUMENT_REGISTRY}


def get_all_configs() -> Dict[str, DocumentConfig]:
    """
    Get all document configurations.
    
    Returns:
        Complete DOCUMENT_REGISTRY dict
    """
    return DOCUMENT_REGISTRY


def get_sorted_configs() -> List[DocumentConfig]:
    """
    Get all document configs sorted by sort_order.
    
    Returns:
        List of DocumentConfig sorted by sort_order ascending
    """
    return sorted(DOCUMENT_REGISTRY.values(), key=lambda c: c.sort_order)


def is_specialized_document(slug: str) -> bool:
    """
    Check if a document slug has a specialized form UI.
    
    Args:
        slug: Document template slug
        
    Returns:
        True if document is in registry, False otherwise
    """
    return slug in DOCUMENT_REGISTRY


# =============================================================================
# PREVIEW-ONLY DOCUMENT REGISTRY
# =============================================================================
# Documents that auto-populate from user profile/system data and display as
# PDF previews (no form UI). These documents are shown in Fill All view after
# all form UI documents.
#
# These documents:
# - Have no user-fillable form fields
# - Auto-populate data from user profile (agent info, supervisor info)
# - Display as embedded PDF previews
# - Are automatically marked as 'filled' when generated
# =============================================================================

@dataclass
class PreviewDocumentConfig:
    """Configuration for a preview-only document type."""
    
    slug: str                   # Unique identifier (matches template_slug)
    name: str                   # Display name for UI
    docuseal_template_id: int   # DocuSeal template ID
    color: str                  # Tailwind color name for theming
    icon: str                   # FontAwesome icon class
    sort_order: int             # Display order (higher = after form docs)
    description: str = ''       # Optional description
    
    @property
    def badge_bg_class(self) -> str:
        """Background class for badge (light variant)."""
        return f"bg-{self.color}-100"
    
    @property
    def badge_text_class(self) -> str:
        """Text class for badge."""
        return f"text-{self.color}-700"
    
    @property
    def badge_classes(self) -> str:
        """Combined badge classes for overview pills."""
        return f"bg-{self.color}-100 text-{self.color}-700"
    
    @property
    def gradient_class(self) -> str:
        """Gradient class for document header badges."""
        return f"from-{self.color}-500 to-{self.color}-600"
    
    @property
    def section_color_var(self) -> str:
        """CSS variable value for section accent color."""
        color_map = {
            'orange': '#f97316',
            'violet': '#8b5cf6',
            'blue': '#3b82f6',
            'emerald': '#10b981',
            'rose': '#f43f5e',
            'amber': '#f59e0b',
            'cyan': '#06b6d4',
            'indigo': '#6366f1',
            'teal': '#14b8a6',
            'pink': '#ec4899',
            'lime': '#84cc16',
            'sky': '#0ea5e9',
        }
        return color_map.get(self.color, '#64748b')


PREVIEW_DOCUMENT_REGISTRY: Dict[str, PreviewDocumentConfig] = {
    'iabs': PreviewDocumentConfig(
        slug='iabs',
        name='Information About Brokerage Services',
        docuseal_template_id=2508644,
        color='indigo',
        icon='fa-handshake',
        sort_order=100,  # High number ensures it appears after form UI docs
        description='TXR-2501 Information About Brokerage Services'
    ),
    'lead-paint': PreviewDocumentConfig(
        slug='lead-paint',
        name='Lead-Based Paint Disclosure',
        docuseal_template_id=2530549,
        color='blue',
        icon='fa-paint-roller',
        sort_order=101,
        description='Addendum for Seller\'s Disclosure of Information on Lead-Based Paint'
    ),
    'wire-fraud-warning': PreviewDocumentConfig(
        slug='wire-fraud-warning',
        name='Wire Fraud Warning',
        docuseal_template_id=2661511,
        color='rose',
        icon='fa-exclamation-triangle',
        sort_order=102,
        description='Wire Fraud Warning for Sellers'
    ),
}


# =============================================================================
# PREVIEW DOCUMENT HELPER FUNCTIONS
# =============================================================================

def get_preview_config(slug: str) -> Optional[PreviewDocumentConfig]:
    """
    Get configuration for a preview-only document type by slug.
    
    Args:
        slug: Document template slug (e.g., 'iabs')
        
    Returns:
        PreviewDocumentConfig if found, None otherwise
    """
    return PREVIEW_DOCUMENT_REGISTRY.get(slug)


def get_preview_slugs() -> List[str]:
    """
    Get list of document slugs that are preview-only (no form UI).
    
    Returns:
        List of slugs registered in PREVIEW_DOCUMENT_REGISTRY
    """
    return list(PREVIEW_DOCUMENT_REGISTRY.keys())


def is_preview_document(slug: str) -> bool:
    """
    Check if a document slug is a preview-only document.
    
    Args:
        slug: Document template slug
        
    Returns:
        True if document is in preview registry, False otherwise
    """
    return slug in PREVIEW_DOCUMENT_REGISTRY


def get_preview_configs_for_slugs(slugs: List[str]) -> Dict[str, PreviewDocumentConfig]:
    """
    Get preview configs for a list of slugs.
    
    Args:
        slugs: List of document slugs to look up
        
    Returns:
        Dict mapping slug -> PreviewDocumentConfig for found documents
    """
    return {s: PREVIEW_DOCUMENT_REGISTRY[s] for s in slugs if s in PREVIEW_DOCUMENT_REGISTRY}


def get_all_preview_configs() -> Dict[str, PreviewDocumentConfig]:
    """
    Get all preview document configurations.
    
    Returns:
        Complete PREVIEW_DOCUMENT_REGISTRY dict
    """
    return PREVIEW_DOCUMENT_REGISTRY


def get_sorted_preview_configs() -> List[PreviewDocumentConfig]:
    """
    Get all preview document configs sorted by sort_order.
    
    Returns:
        List of PreviewDocumentConfig sorted by sort_order ascending
    """
    return sorted(PREVIEW_DOCUMENT_REGISTRY.values(), key=lambda c: c.sort_order)

