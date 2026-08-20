"""Opaque token hashing and PKCE helpers (Authlib S256)."""
from __future__ import annotations

import hashlib
import secrets
from urllib.parse import urlparse

from authlib.oauth2.rfc7636 import create_s256_code_challenge


def hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def new_secret(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def s256_challenge(verifier: str) -> str:
    return create_s256_code_challenge(verifier)


def pkce_matches(verifier: str, challenge: str) -> bool:
    if not verifier or not challenge:
        return False
    return secrets.compare_digest(s256_challenge(verifier), challenge)


def client_dedupe_hash(client_name: str, redirect_uris: list[str]) -> str:
    joined = '|'.join([client_name.strip().lower(), *sorted(redirect_uris)])
    return hashlib.sha256(joined.encode('utf-8')).hexdigest()


def redirect_host(uri: str) -> str:
    try:
        return (urlparse(uri).hostname or '').lower()
    except Exception:
        return ''


def is_loopback_host(host: str) -> bool:
    return host in {'localhost', '127.0.0.1', '::1'}
