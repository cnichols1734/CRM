# Feature Flags Configuration
# Toggle features on/off by setting True/False
# Changes here deploy with git pull - no need to edit .env on server

FEATURE_FLAGS = {
    # Dashboard joke of the day - fetches from external joke APIs
    'SHOW_DASHBOARD_JOKE': False,
    
    # Transaction Management - admin-only until ready for agents
    'TRANSACTIONS_ENABLED': False,
}


def is_enabled(flag_name: str) -> bool:
    """Check if a feature flag is enabled."""
    return FEATURE_FLAGS.get(flag_name, False)


def can_access_transactions(user) -> bool:
    """
    Check if user can access transactions module.
    Currently: feature flag must be enabled AND user must be admin.
    Later: can enable for specific agents via additional logic.
    """
    if not is_enabled('TRANSACTIONS_ENABLED'):
        return False
    if not user or not user.is_authenticated:
        return False
    return user.role == 'admin'

