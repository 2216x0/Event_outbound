#!/usr/bin/env python3
"""
Bridge Claude Code's login to the Anthropic SDK.

The discovery scripts (discover_events.py, discover_associations.py) talk to the
Anthropic API directly and need a credential. Rather than requiring a separate
ANTHROPIC_API_KEY, this pulls the OAuth access token that Claude Code already
stored at login and exposes it to the SDK via ANTHROPIC_AUTH_TOKEN (Bearer auth).

Usage:
    # as a library, at the top of a script or server startup:
    import claude_auth; claude_auth.ensure_token()

    # or from the shell, to export into your session:
    export ANTHROPIC_AUTH_TOKEN="$(python3 claude_auth.py --print)"

Precedence: if ANTHROPIC_API_KEY or ANTHROPIC_AUTH_TOKEN is already set in the
environment, that wins and Claude Code's token is not touched.
"""

import os
import sys
import json
import time
import subprocess
from pathlib import Path

_KEYCHAIN_SERVICE = "Claude Code-credentials"
_CRED_FILE = Path.home() / ".claude" / ".credentials.json"


def _from_keychain() -> dict | None:
    """macOS: read the Claude Code credential blob from the login keychain."""
    if sys.platform != "darwin":
        return None
    try:
        raw = subprocess.check_output(
            ["security", "find-generic-password", "-s", _KEYCHAIN_SERVICE, "-w"],
            stderr=subprocess.DEVNULL, text=True,
        )
        return json.loads(raw)
    except Exception:
        return None


def _from_file() -> dict | None:
    """Linux / fallback: read ~/.claude/.credentials.json."""
    try:
        return json.loads(_CRED_FILE.read_text())
    except Exception:
        return None


def get_token() -> str | None:
    """Return a currently-valid Claude Code OAuth access token, or None."""
    blob = _from_keychain() or _from_file()
    if not blob:
        return None
    oauth = blob.get("claudeAiOauth") or blob
    token = oauth.get("accessToken")
    if not token:
        return None
    # expiresAt is epoch milliseconds; treat a token expiring within 60s as stale.
    exp = oauth.get("expiresAt")
    if isinstance(exp, (int, float)) and exp / 1000.0 <= time.time() + 60:
        return None
    return token


def ensure_token() -> str | None:
    """
    Make a credential available to the Anthropic SDK for this process.

    No-op if ANTHROPIC_API_KEY or ANTHROPIC_AUTH_TOKEN is already set. Otherwise
    sets ANTHROPIC_AUTH_TOKEN from Claude Code's login. Returns the token used
    (or the existing env value), or None if nothing usable was found.
    """
    if os.environ.get("ANTHROPIC_API_KEY"):
        return os.environ["ANTHROPIC_API_KEY"]
    if os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return os.environ["ANTHROPIC_AUTH_TOKEN"]
    token = get_token()
    if token:
        os.environ["ANTHROPIC_AUTH_TOKEN"] = token
    return token


def main():
    token = get_token()
    if not token:
        sys.exit(
            "No usable Claude Code token found. Run `claude` and log in first "
            "(checked macOS Keychain and ~/.claude/.credentials.json). If your "
            "token just expired, open Claude Code once to refresh it."
        )
    # --print emits only the token (for `export ...=$(...)`); default is friendly.
    if "--print" in sys.argv:
        print(token)
    else:
        print(f"ANTHROPIC_AUTH_TOKEN={token}")


if __name__ == "__main__":
    main()
