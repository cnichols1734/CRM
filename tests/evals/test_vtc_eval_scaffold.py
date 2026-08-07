"""E0B-5 baseline eval scaffold for BOB virtual TC safety/quality."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from services.bob_tools.context import BobContext, PageEntityContext
from services.bob_tools.registry import (
    FORBIDDEN_ARG_KEYS,
    openai_tool_schemas,
    sanitize_arguments,
    select_tools,
    TOOLS_BY_NAME,
)


FIXTURES = Path(__file__).parent / 'fixtures' / 'gold_cases.json'


def _load_cases():
    payload = json.loads(FIXTURES.read_text())
    return payload['cases']


@pytest.fixture(params=_load_cases(), ids=lambda c: c['id'])
def gold_case(request):
    return request.param


def test_gold_fixture_schema(gold_case):
    assert 'id' in gold_case
    assert 'prompt' in gold_case


def test_forbidden_identity_args_always_dropped(app, gold_case):
    """Injection cases must never let spoofed identity keys reach a handler."""
    if 'forbidden_arg_keys' not in gold_case:
        pytest.skip('no injection assertions')
    tool = TOOLS_BY_NAME.get('search_contacts') or next(iter(TOOLS_BY_NAME.values()))
    raw = {key: 999 for key in gold_case['forbidden_arg_keys']}
    raw['query'] = 'test'
    clean = sanitize_arguments(tool, raw)
    for key in gold_case['forbidden_arg_keys']:
        assert key not in clean
        assert key in FORBIDDEN_ARG_KEYS


def test_telegram_without_selection_omits_tx_writes(app):
    ctx = BobContext(
        organization_id=1,
        user_id=1,
        surface='telegram',
        is_org_admin=False,
        org_role='agent',
        selected_transaction_id=None,
    )
    names = {t.name for t in select_tools(ctx)}
    # Without selection, write tools for txs must stay out of the Telegram bundle.
    from services.bob_tools.registry import TX_WRITE_TOOL_NAMES
    assert names.isdisjoint(TX_WRITE_TOOL_NAMES)


def test_tool_schema_bundle_includes_expected_reads(app, gold_case):
    expected = gold_case.get('expected_tools_any') or []
    if not expected:
        pytest.skip('no expected tool list')
    page = None
    if gold_case.get('entity_type') == 'transaction':
        page = PageEntityContext(entity_type='transaction', entity_id=1)
    ctx = BobContext(
        organization_id=1,
        user_id=1,
        surface='bob_chat' if gold_case.get('surface') != 'telegram' else 'telegram',
        is_org_admin=True,
        org_role='owner',
        selected_transaction_id=1 if page else None,
        page_entity=page,
    )
    available = {t.name for t in select_tools(ctx)}
    # At least one expected tool must be registered in the product.
    registered_expected = [n for n in expected if n in TOOLS_BY_NAME]
    assert registered_expected, expected
    assert available.intersection(registered_expected)


def test_openai_schemas_are_non_empty_for_transaction_page(app):
    ctx = BobContext(
        organization_id=1,
        user_id=1,
        surface='bob_chat',
        is_org_admin=True,
        org_role='owner',
        page_entity=PageEntityContext(entity_type='transaction', entity_id=1),
        selected_transaction_id=1,
    )
    schemas = openai_tool_schemas(ctx)
    assert schemas
    names = {s['function']['name'] for s in schemas}
    assert 'get_transaction_summary' in names or 'search_transactions' in names
