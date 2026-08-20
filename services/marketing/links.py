"""Absolute URLs that go inside a sent email.

Built from configuration rather than the request, because most marketing mail is
composed by a worker with no request to build from, and a link that resolves to
localhost is a dead link in someone's inbox forever.
"""
from __future__ import annotations

from config import DEFAULT_APP_BASE_URL


def base_url() -> str:
    try:
        from flask import current_app
        configured = current_app.config.get('APP_BASE_URL')
    except RuntimeError:
        configured = None
    return (configured or DEFAULT_APP_BASE_URL).rstrip('/')


def unsubscribe_url(token: str) -> str:
    return f'{base_url()}/email/unsubscribe/{token}'
