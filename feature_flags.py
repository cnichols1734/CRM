# Feature Flags Configuration
# Toggle features on/off by setting True/False
# Changes here deploy with git pull - no need to edit .env on server

FEATURE_FLAGS = {
    # Dashboard joke of the day - fetches from external joke APIs
    'SHOW_DASHBOARD_JOKE': True,
}


def is_enabled(flag_name: str) -> bool:
    """Check if a feature flag is enabled."""
    return FEATURE_FLAGS.get(flag_name, False)

