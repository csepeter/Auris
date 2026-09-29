"""Request hardening for the local Auris web server.

Auris listens on loopback, but any web page open in the same browser can still
send requests to it. These checks block cross-site writes (CSRF), DNS
rebinding through foreign Host headers, and keep stored API keys out of
responses.
"""

from __future__ import annotations

import os
import re
from urllib.parse import urlsplit

from flask import jsonify, request

SECRET_SETTING_KEYS = ('llm_api_key', 'openai_api_key', 'abs_api_token', 'api_token')
SECRET_MASK = '••••••••'
_UNSAFE_METHODS = frozenset({'POST', 'PUT', 'PATCH', 'DELETE'})
_LOOPBACK_HOSTS = frozenset({'127.0.0.1', 'localhost', '::1', '[::1]'})
_HF_REPO_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}/[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$')


def allowed_hosts() -> set[str]:
    extra = os.environ.get('AURIS_ALLOWED_HOSTS', '')
    hosts = set(_LOOPBACK_HOSTS)
    hosts.update(h.strip().lower() for h in extra.split(',') if h.strip())
    return hosts


def _hostname(value: str) -> str:
    value = (value or '').strip().lower()
    if value.startswith('['):
        end = value.find(']')
        return value[: end + 1] if end > 0 else value
    return value.rsplit(':', 1)[0] if value.count(':') == 1 else value


def _origin_matches(origin: str) -> bool:
    try:
        parts = urlsplit(origin)
    except ValueError:
        return False
    if parts.scheme not in ('http', 'https'):
        return False
    return parts.netloc.lower() == request.host.lower()


def check_request():
    """Return an error response for a request that must not be served."""
    host = _hostname(request.host)
    hosts = allowed_hosts()
    if host not in hosts and '*' not in hosts:
        return jsonify({'error': 'Ismeretlen Host fejléc; a kérés elutasítva.'}), 403
    if request.method not in _UNSAFE_METHODS:
        return None
    fetch_site = (request.headers.get('Sec-Fetch-Site') or '').lower()
    if fetch_site in ('cross-site', 'same-site'):
        return jsonify({'error': 'Más webhelyről érkező módosító kérés elutasítva.'}), 403
    origin = request.headers.get('Origin')
    if origin and origin != 'null' and not _origin_matches(origin):
        return jsonify({'error': 'Más webhelyről érkező módosító kérés elutasítva.'}), 403
    if origin == 'null':
        return jsonify({'error': 'Ismeretlen eredetű módosító kérés elutasítva.'}), 403
    if not origin:
        referer = request.headers.get('Referer')
        if referer and not _origin_matches(referer):
            return jsonify({'error': 'Más webhelyről érkező módosító kérés elutasítva.'}), 403
    return None


def install(app) -> None:
    """Run the security check before every other request hook."""
    funcs = app.before_request_funcs.setdefault(None, [])
    funcs.insert(0, check_request)


def mask_secrets(settings: dict) -> dict:
    masked = dict(settings)
    for key in SECRET_SETTING_KEYS:
        if masked.get(key):
            masked[key] = SECRET_MASK
    return masked


def drop_masked_secrets(updates: dict) -> dict:
    """Ignore secret fields that still carry the display mask."""
    return {
        key: value for key, value in updates.items()
        if not (key in SECRET_SETTING_KEYS and value == SECRET_MASK)
    }


def resolve_secret(value, stored: str) -> str:
    value = str(value or '')
    if not value or value == SECRET_MASK:
        return str(stored or '')
    return value


def valid_hf_repo(repo: str) -> bool:
    return bool(_HF_REPO_RE.fullmatch(str(repo or '').strip()))
