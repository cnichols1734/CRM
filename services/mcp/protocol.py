"""JSON-RPC MCP methods for Streamable HTTP."""
from __future__ import annotations

from services.mcp.adapter import build_context, call_mcp_tool, mcp_tool_catalog
from services.mcp.instructions import SERVER_INSTRUCTIONS
from services.mcp.prompts import get_prompt, list_prompts
from services.mcp.rate_limit import consume_call
from services.mcp.scopes import SCOPE_READ, scope_for_tool
from services.mcp.tokens import VerifiedToken

SUPPORTED_PROTOCOL_VERSIONS = ('2025-06-18', '2025-11-25')
LATEST_PROTOCOL_VERSION = '2025-11-25'
SERVER_NAME = 'AgentFlow'
SERVER_VERSION = '1.0.0'


def negotiate_protocol_version(requested: str | None) -> str:
    if requested in SUPPORTED_PROTOCOL_VERSIONS:
        return requested
    return LATEST_PROTOCOL_VERSION


def handle_rpc(message: dict, verified: VerifiedToken) -> dict | None:
    """Return a JSON-RPC response, or None for notifications (HTTP 202)."""
    if not isinstance(message, dict) or message.get('jsonrpc') != '2.0':
        return _rpc_error(None, -32600, 'Invalid Request')

    rpc_id = message.get('id', _MISSING)
    method = message.get('method')
    params = message.get('params') or {}
    if not isinstance(params, dict):
        params = {}

    if rpc_id is _MISSING:
        return None

    if method == 'initialize':
        version = negotiate_protocol_version(params.get('protocolVersion'))
        return _rpc_result(rpc_id, {
            'protocolVersion': version,
            'capabilities': {
                'tools': {'listChanged': False},
                'prompts': {'listChanged': False},
            },
            'serverInfo': {'name': SERVER_NAME, 'version': SERVER_VERSION},
            'instructions': SERVER_INSTRUCTIONS,
        })

    if method == 'ping':
        return _rpc_result(rpc_id, {})

    scopes = _effective_scopes(verified)

    if method == 'tools/list':
        ctx = build_context(verified.user)
        return _rpc_result(rpc_id, {'tools': mcp_tool_catalog(ctx, scopes)})

    if method == 'tools/call':
        name = params.get('name')
        if not name:
            return _rpc_error(rpc_id, -32602, 'Missing tool name')
        allowed, reason = consume_call(
            verified.grant, is_read=scope_for_tool(name) == SCOPE_READ,
        )
        if not allowed:
            return _rpc_error(rpc_id, -32000, reason)
        ctx = build_context(verified.user)
        result = call_mcp_tool(name, params.get('arguments') or {}, ctx, scopes)
        return _rpc_result(rpc_id, result)

    if method == 'prompts/list':
        return _rpc_result(rpc_id, {'prompts': list_prompts()})

    if method == 'prompts/get':
        prompt = get_prompt(params.get('name') or '', params.get('arguments') or {})
        if prompt is None:
            return _rpc_error(rpc_id, -32602, 'Unknown prompt')
        return _rpc_result(rpc_id, prompt)

    return _rpc_error(rpc_id, -32601, f'Method not found: {method}')


def _effective_scopes(verified: VerifiedToken) -> list[str]:
    scopes = list(verified.scopes or [])
    if (verified.resource or '').rstrip('/').endswith('/readonly'):
        return [SCOPE_READ] if SCOPE_READ in scopes else []
    return scopes


_MISSING = object()


def _rpc_result(rpc_id, result: dict) -> dict:
    return {'jsonrpc': '2.0', 'id': rpc_id, 'result': result}


def _rpc_error(rpc_id, code: int, message: str) -> dict:
    return {'jsonrpc': '2.0', 'id': rpc_id, 'error': {'code': code, 'message': message}}
