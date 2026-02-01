# feature_flags.py
"""
Per-organization feature flags based on subscription tier.
Features are controlled by:
1. Tier defaults (free, pro, enterprise)
2. Per-org overrides stored in Organization.feature_flags JSON
3. Platform admin orgs get everything
"""

from typing import Optional

# =============================================================================
# TIER FEATURE DEFINITIONS
# =============================================================================

TIER_FEATURES = {
    'free': {
        # Core CRM features - always available
        'CONTACTS': True,
        'TASKS': True,
        'USER_TODOS': True,
        'DASHBOARD': True,
        'TEAM_UPDATES': True,
        
        # Premium features - limited on free tier
        'AI_CHAT': True,  # Enabled with daily message limit (see tier_limits.py)
        'AI_DAILY_TODO': False,
        'AI_ACTION_PLAN': False,
        'TRANSACTIONS': False,
        'DOCUMENT_GENERATION': False,
        'MARKETING': False,
        
        # Fun features
        'SHOW_DASHBOARD_JOKE': False,
    },
    'pro': {
        'CONTACTS': True,
        'TASKS': True,
        'USER_TODOS': True,
        'DASHBOARD': True,
        'TEAM_UPDATES': True,
        'AI_CHAT': True,
        'AI_DAILY_TODO': True,
        'AI_ACTION_PLAN': True,
        'TRANSACTIONS': True,
        'DOCUMENT_GENERATION': True,
        'MARKETING': False,  # Still disabled for all
        'SHOW_DASHBOARD_JOKE': True,
    },
    'enterprise': {
        'CONTACTS': True,
        'TASKS': True,
        'USER_TODOS': True,
        'DASHBOARD': True,
        'TEAM_UPDATES': True,
        'AI_CHAT': True,
        'AI_DAILY_TODO': True,
        'AI_ACTION_PLAN': True,
        'TRANSACTIONS': True,
        'DOCUMENT_GENERATION': True,
        'MARKETING': True,  # Only enterprise has marketing
        'SHOW_DASHBOARD_JOKE': True,
    }
}

# Legacy global flags for backwards compatibility during migration
FEATURE_FLAGS = {
    'SHOW_DASHBOARD_JOKE': False,
    'TRANSACTIONS_ENABLED': True,
    'REPORTS_ADMIN_ONLY': True,  # When True, only admins/owners can access Reports
}


# =============================================================================
# FEATURE CHECK FUNCTIONS
# =============================================================================

def org_has_feature(feature_name: str, org=None) -> bool:
    """
    Check if organization has access to a feature.
    
    Args:
        feature_name: Name of the feature to check (e.g., 'AI_CHAT', 'TRANSACTIONS')
        org: Organization model instance (optional, uses current_user's org if None)
        
    Returns:
        True if the organization has access to this feature
    """
    from flask_login import current_user
    
    if org is None:
        if not current_user.is_authenticated:
            return False
        org = current_user.organization
    
    if not org:
        return False
    
    # Platform admin org (Origen) gets everything
    if org.is_platform_admin:
        return True
    
    # Check org-specific override first
    if org.feature_flags and feature_name in org.feature_flags:
        return org.feature_flags[feature_name]
    
    # Fall back to tier defaults
    tier = org.subscription_tier or 'free'
    return TIER_FEATURES.get(tier, TIER_FEATURES['free']).get(feature_name, False)


def get_org_features(org=None) -> dict:
    """
    Get all feature flags for an organization.
    
    Args:
        org: Organization model instance (optional, uses current_user's org if None)
        
    Returns:
        Dict of feature_name -> enabled boolean
    """
    from flask_login import current_user
    
    if org is None:
        if not current_user.is_authenticated:
            return TIER_FEATURES['free'].copy()
        org = current_user.organization
    
    if not org:
        return TIER_FEATURES['free'].copy()
    
    # Platform admin org gets everything enabled
    if org.is_platform_admin:
        return {k: True for k in TIER_FEATURES['enterprise'].keys()}
    
    # Start with tier defaults
    tier = org.subscription_tier or 'free'
    features = TIER_FEATURES.get(tier, TIER_FEATURES['free']).copy()
    
    # Apply org-specific overrides
    if org.feature_flags:
        for feature_name, enabled in org.feature_flags.items():
            features[feature_name] = enabled
    
    return features


# =============================================================================
# ROUTE PROTECTION DECORATOR
# =============================================================================

def feature_required(feature_name: str):
    """
    Decorator to require a feature flag for a route.
    Returns 403 or redirects to upgrade page if feature not available.
    
    Usage:
        @feature_required('AI_ACTION_PLAN')
        def action_plan():
            ...
    """
    from functools import wraps
    from flask import flash, redirect, url_for, abort
    from flask_login import current_user
    
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated:
                return redirect(url_for('auth.login'))
            
            if not org_has_feature(feature_name):
                flash(f'This feature requires a subscription upgrade.', 'warning')
                # Redirect to upgrade page or return 403
                try:
                    return redirect(url_for('org.upgrade'))
                except:
                    abort(403)
            
            return f(*args, **kwargs)
        return decorated_function
    return decorator


# =============================================================================
# LEGACY COMPATIBILITY FUNCTIONS
# =============================================================================

def is_enabled(flag_name: str) -> bool:
    """
    Legacy: Check if a feature flag is enabled globally.
    For backwards compatibility - new code should use org_has_feature().
    """
    return FEATURE_FLAGS.get(flag_name, False)


def can_access_transactions(user) -> bool:
    """
    Check if user can access transactions module.
    Now uses org-based feature flags.
    """
    if not user or not user.is_authenticated:
        return False
    
    # Check org has the feature
    org = user.organization
    if not org:
        return False
    
    # Check feature is enabled for this org
    if not org_has_feature('TRANSACTIONS', org):
        return False
    
    # Platform admin org: any admin/owner can access
    if org.is_platform_admin:
        return user.org_role in ('owner', 'admin') or user.role == 'admin'
    
    # Regular orgs: if they have the feature, all members can access
    return True


def can_access_ai_features(user) -> bool:
    """
    Check if user can access AI features (B.O.B., Daily Todo, Action Plan).
    """
    if not user or not user.is_authenticated:
        return False
    
    org = user.organization
    if not org:
        return False
    
    # Check if any AI feature is enabled
    return (
        org_has_feature('AI_CHAT', org) or
        org_has_feature('AI_DAILY_TODO', org) or
        org_has_feature('AI_ACTION_PLAN', org)
    )


def can_access_documents(user) -> bool:
    """
    Check if user can access document generation.
    """
    if not user or not user.is_authenticated:
        return False
    
    org = user.organization
    if not org:
        return False
    
    return org_has_feature('DOCUMENT_GENERATION', org)


def can_access_reports(user) -> bool:
    """
    Check if user can access the Reports module.
    If REPORTS_ADMIN_ONLY flag is True, only admins/owners can access.
    If flag is False, all authenticated users can access.
    """
    if not user or not user.is_authenticated:
        return False
    
    # Check if reports are admin-only
    if FEATURE_FLAGS.get('REPORTS_ADMIN_ONLY', True):
        # Only org admins/owners can access
        return user.org_role in ('owner', 'admin')
    
    # Reports available to all users
    return True


# =============================================================================
# TEMPLATE HELPERS
# =============================================================================

def get_feature_context():
    """
    Get feature flag context for templates.
    Use in templates: {% if features.AI_CHAT %}
    
    Returns:
        Dict of feature flags for the current user's org
    """
    return get_org_features()
