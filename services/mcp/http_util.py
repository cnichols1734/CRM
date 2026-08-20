"""Shared HTTP helpers for MCP and OAuth discovery."""
from __future__ import annotations

from flask import Response, make_response, request

from services.mcp.urls import protected_resource_metadata_url


CORS_HEADERS = {
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
    'Access-Control-Allow-Headers': (
        'Authorization, Content-Type, MCP-Protocol-Version'
    ),
    'Access-Control-Max-Age': '86400',
}


def with_cors(response: Response) -> Response:
    for key, value in CORS_HEADERS.items():
        response.headers.setdefault(key, value)
    return response


def json_response(payload, status: int = 200) -> Response:
    response = make_response(payload, status)
    response.mimetype = 'application/json'
    return with_cors(response)


def empty_response(status: int) -> Response:
    response = make_response('', status)
    return with_cors(response)


def bearer_token() -> str:
    header = request.headers.get('Authorization') or ''
    if header.lower().startswith('bearer '):
        return header[7:].strip()
    return ''


def www_authenticate(*, readonly: bool = False) -> str:
    metadata = protected_resource_metadata_url(readonly=readonly)
    return f'Bearer realm="AgentFlow", resource_metadata="{metadata}"'
