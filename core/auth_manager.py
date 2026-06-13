import json
import os

_SESSION_FILE = 'session.json'

# Checked in order when looking for the auth bearer token
_TOKEN_KEYS = (
    'session_token',
    'kick_session',
    'laravel_session',
    'auth_token',
    'access_token',
    'token',
)


def save_session(cookies: dict):
    with open(_SESSION_FILE, 'w', encoding='utf-8') as f:
        json.dump(cookies, f)


def load_session() -> dict | None:
    if not os.path.exists(_SESSION_FILE):
        return None
    try:
        with open(_SESSION_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


def clear_session():
    if os.path.exists(_SESSION_FILE):
        os.remove(_SESSION_FILE)


def get_session_token() -> str | None:
    """
    Return the best available auth token from the saved session,
    regardless of which key it was stored under.
    """
    data = load_session()
    if not data:
        return None

    # Named candidates first
    for key in _TOKEN_KEYS:
        val = data.get(key)
        if val and isinstance(val, str) and len(val) > 20:
            return val

    # localStorage / sessionStorage prefixed copies
    for prefix in ('__ls_', '__ss_'):
        for key in _TOKEN_KEYS:
            val = data.get(f'{prefix}{key}')
            if val and isinstance(val, str) and len(val) > 20:
                return val

    # Any JWT-shaped value
    for val in data.values():
        if isinstance(val, str) and val.startswith('eyJ') and len(val) > 50:
            return val

    return None


def is_logged_in() -> bool:
    return get_session_token() is not None
