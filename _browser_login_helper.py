"""
Standalone script spawned as a subprocess by the main app.
Opens kick.com/login in a native WebView2 window.

Detection strategy
------------------
Rather than looking for a specific cookie name, we watch the URL.
When the browser lands on a kick.com page that is NOT a login/OAuth/SSO
page, we assume the user is now authenticated and harvest everything:
  1. All cookies via pywebview's get_cookies()
  2. Non-HttpOnly cookies via document.cookie (JS)
  3. All localStorage keys
Then we pick the best auth token candidate and print the full cookie
dict as JSON to stdout so the parent process can save it.

Exit codes
----------
  0 – success, JSON printed to stdout
  1 – window closed without a successful login
  2 – pywebview not installed
"""
import json
import sys
import threading
import time
import re

try:
    import webview
except ImportError:
    print('ERROR: pywebview is not installed', file=sys.stderr)
    sys.exit(2)

# ── URL patterns ──────────────────────────────────────────────────────────────

# If the current URL matches any of these we are still mid-auth — keep waiting
_STILL_AUTHING = re.compile(
    r'accounts\.google\.|appleid\.apple\.|idmsa\.apple\.|'
    r'kick\.com/login|kick\.com/register|kick\.com/auth|'
    r'/oauth|/sso|/callback|/authorize',
    re.IGNORECASE,
)

# Once we land on kick.com and none of the above match, we're in
_KICK_DOMAIN = re.compile(r'https?://(www\.)?kick\.com', re.IGNORECASE)

# Token names to promote to 'session_token' key, in priority order
_TOKEN_KEYS = (
    'session_token',
    'kick_session',
    'laravel_session',
    'auth_token',
    'access_token',
    'token',
)

# ── globals ───────────────────────────────────────────────────────────────────

_done   = threading.Event()
_result: dict = {}

# ── helpers ───────────────────────────────────────────────────────────────────

def _is_logged_in_url(url: str) -> bool:
    if not url:
        return False
    if not _KICK_DOMAIN.match(url):
        return False
    if _STILL_AUTHING.search(url):
        return False
    return True


def _harvest(window) -> dict:
    """Collect every auth artifact we can find from the webview."""
    bag: dict[str, str] = {}

    # 1. pywebview get_cookies() — works with HttpOnly on WebView2
    try:
        for c in (window.get_cookies() or []):
            if isinstance(c, dict):
                name, val = c.get('name', ''), c.get('value', '')
            else:
                name, val = getattr(c, 'name', ''), getattr(c, 'value', '')
            if name and val:
                bag[name] = val
    except Exception:
        pass

    # 2. document.cookie (non-HttpOnly only, but a useful fallback)
    try:
        raw = window.evaluate_js('document.cookie') or ''
        for part in raw.split(';'):
            part = part.strip()
            if '=' in part:
                k, v = part.split('=', 1)
                bag.setdefault(k.strip(), v.strip())
    except Exception:
        pass

    # 3. localStorage — Kick may store the token here after OAuth
    try:
        js = (
            '(function(){'
            '  var o={};'
            '  for(var i=0;i<localStorage.length;i++){'
            '    var k=localStorage.key(i);'
            '    o[k]=localStorage.getItem(k);'
            '  }'
            '  return JSON.stringify(o);'
            '})()'
        )
        raw_ls = window.evaluate_js(js)
        if raw_ls:
            ls = json.loads(raw_ls)
            for k, v in ls.items():
                if v and isinstance(v, str) and len(v) > 10:
                    bag[f'__ls_{k}'] = v
    except Exception:
        pass

    # 4. sessionStorage — same idea
    try:
        js = (
            '(function(){'
            '  var o={};'
            '  for(var i=0;i<sessionStorage.length;i++){'
            '    var k=sessionStorage.key(i);'
            '    o[k]=sessionStorage.getItem(k);'
            '  }'
            '  return JSON.stringify(o);'
            '})()'
        )
        raw_ss = window.evaluate_js(js)
        if raw_ss:
            ss = json.loads(raw_ss)
            for k, v in ss.items():
                if v and isinstance(v, str) and len(v) > 10:
                    bag[f'__ss_{k}'] = v
    except Exception:
        pass

    return bag


def _promote_token(bag: dict) -> dict | None:
    """Return the bag with 'session_token' set to the best candidate, or None."""
    if not bag:
        return None

    # Direct name match
    for key in _TOKEN_KEYS:
        if bag.get(key) and len(bag[key]) > 20:
            bag['session_token'] = bag[key]
            return bag

    # localStorage / sessionStorage versions of the same keys
    for prefix in ('__ls_', '__ss_'):
        for key in _TOKEN_KEYS:
            full = f'{prefix}{key}'
            if bag.get(full) and len(bag[full]) > 20:
                bag['session_token'] = bag[full]
                return bag

    # Any JWT-shaped value (starts with eyJ)
    for val in bag.values():
        if isinstance(val, str) and val.startswith('eyJ') and len(val) > 50:
            bag['session_token'] = val
            return bag

    # Any suspiciously long value that isn't XSRF
    for key, val in bag.items():
        if (
            isinstance(val, str)
            and len(val) > 40
            and 'xsrf' not in key.lower()
            and 'csrf' not in key.lower()
        ):
            bag['session_token'] = val
            return bag

    return None


# ── polling thread ────────────────────────────────────────────────────────────

def _poll(window: webview.Window):
    last_url = ''
    # Give the page a moment to finish any initial redirect
    time.sleep(1.5)

    while not _done.is_set():
        time.sleep(0.8)
        try:
            url = window.get_current_url() or ''

            if url == last_url:
                continue
            last_url = url

            if not _is_logged_in_url(url):
                continue

            # Give cookies a moment to settle after the redirect
            time.sleep(1.5)

            bag = _harvest(window)
            result = _promote_token(bag)

            if result:
                global _result
                _result = result
                _done.set()
                window.destroy()
        except Exception:
            pass


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    window = webview.create_window(
        'Sign in to Kick.com',
        'https://kick.com/login',
        width=520,
        height=720,
        resizable=True,
    )

    def on_start():
        t = threading.Thread(target=_poll, args=(window,), daemon=True)
        t.start()

    try:
        webview.start(on_start, gui='edgechromium')
    except Exception:
        webview.start(on_start)

    if _done.is_set() and _result:
        print(json.dumps(_result))
        sys.stdout.flush()
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == '__main__':
    main()
