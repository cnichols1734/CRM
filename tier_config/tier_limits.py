# config/tier_limits.py
"""
Tier configuration for multi-tenant SaaS.
Easy to modify defaults without code changes.
"""

TIER_DEFAULTS = {
    'free': {
        'max_users': 1,
        'max_contacts': 10000,
        'can_invite_users': False,
    },
    'pro': {
        'max_users': 25,  # Default, can be overridden per-org
        'max_contacts': None,  # Unlimited
        'can_invite_users': True,
    },
    'enterprise': {
        'max_users': 1000,
        'max_contacts': None,
        'can_invite_users': True,
    }
}


def get_tier_defaults(tier: str) -> dict:
    """Get the default limits for a tier."""
    return TIER_DEFAULTS.get(tier, TIER_DEFAULTS['free'])


def apply_tier_defaults(org, tier: str):
    """
    Apply tier defaults to an organization.
    
    Args:
        org: Organization model instance
        tier: Tier name ('free', 'pro', 'enterprise')
    """
    defaults = get_tier_defaults(tier)
    org.subscription_tier = tier
    org.max_users = defaults['max_users']
    org.max_contacts = defaults['max_contacts']
    org.can_invite_users = defaults['can_invite_users']
