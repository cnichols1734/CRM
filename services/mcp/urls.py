"""Canonical MCP and OAuth URLs for this deployment."""
from __future__ import annotations

from flask import current_app, request

from config import DEFAULT_APP_BASE_URL


def app_base_url() -> str:
    base = (
        current_app.config.get('APP_BASE_URL')
        or DEFAULT_APP_BASE_URL
    ).rstrip('/')
    if request and request.url_root and _is_local_request():
        return request.url_root.rstrip('/')
    return base


def _is_local_request() -> bool:
    host = (request.host or '').split(':', 1)[0].lower()
    return host in {'127.0.0.1', 'localhost'}


def mcp_resource_url(*, readonly: bool = False) -> str:
    suffix = '/mcp/readonly' if readonly else '/mcp'
    return app_base_url() + suffix


def authorization_server_issuer() -> str:
    return app_base_url()


def protected_resource_metadata_url(*, readonly: bool = False) -> str:
    path = '/mcp/readonly' if readonly else '/mcp'
    return f'{app_base_url()}/.well-known/oauth-protected-resource{path}'


def resource_matches(token_resource: str, requested: str) -> bool:
    return (token_resource or '').rstrip('/') == (requested or '').rstrip('/')
