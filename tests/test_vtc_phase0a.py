"""Phase 0A safety freeze regressions for virtual TC foundation."""

from unittest.mock import MagicMock, patch

import pytest

from feature_flags import GLOBAL_FEATURE_OVERRIDES, org_has_feature
from routes.ai_chat import get_rate_limit_message
from tier_config.tier_limits import get_tier_defaults


def test_document_generation_globally_frozen():
    assert GLOBAL_FEATURE_OVERRIDES.get('DOCUMENT_GENERATION') is False
    org = MagicMock()
    org.is_platform_admin = True
    org.subscription_tier = 'enterprise'
    org.feature_flags = {'DOCUMENT_GENERATION': True}
    assert org_has_feature('DOCUMENT_GENERATION', org) is False


def test_rate_limit_message_matches_tier_limits():
    free_limit = get_tier_defaults('free')['daily_ai_chat_messages']
    assert free_limit == 25
    with patch('routes.ai_chat.get_daily_message_limit', return_value=free_limit):
        message = get_rate_limit_message()
    assert f'{free_limit} messages' in message
    assert '10 messages' not in message


def test_extraction_auto_apply_defaults_off(app):
    assert app.config.get('EXTRACTION_AUTO_APPLY') is False


def test_extraction_skips_sync_when_auto_apply_disabled(app):
    """Auto-apply gate defaults off and source must not call sync unconditionally."""
    from services import document_extractor as extractor

    with app.app_context():
        assert app.config.get('EXTRACTION_AUTO_APPLY') is False

    source = open(extractor.__file__).read()
    assert 'EXTRACTION_AUTO_APPLY' in source
    assert 'sync_offer_version_from_document' in source
    assert 'if auto_apply:' in source


def test_transaction_auth_break_glass_and_creator():
    from services.transaction_auth import can_view_transaction, can_edit_transaction

    tx = MagicMock()
    tx.organization_id = 1
    tx.created_by_id = 10
    tx.id = 99

    owner = MagicMock()
    owner.is_authenticated = True
    owner.organization_id = 1
    owner.id = 2
    owner.org_role = 'owner'

    decision = can_view_transaction(tx, owner)
    assert decision.allowed

    agent = MagicMock()
    agent.is_authenticated = True
    agent.organization_id = 1
    agent.id = 10
    agent.org_role = 'agent'
    assert can_edit_transaction(tx, agent).allowed

    stranger = MagicMock()
    stranger.is_authenticated = True
    stranger.organization_id = 1
    stranger.id = 99
    stranger.org_role = 'agent'
    with patch('services.transaction_auth.get_assignment', return_value=None):
        assert can_view_transaction(tx, stranger).allowed is False


def test_generation_freeze_does_not_redirect_to_unverified_tx(app, seed, owner_a_client):
    """Cross-org preview must 404 — never 302/403 that discloses a foreign id."""
    foreign_tx = seed['tx_b']
    resp = owner_a_client.get(
        f'/transactions/{foreign_tx}/documents/preview-all',
        follow_redirects=False,
    )
    assert resp.status_code == 404
    location = resp.headers.get('Location') or ''
    assert f'/transactions/{foreign_tx}' not in location
