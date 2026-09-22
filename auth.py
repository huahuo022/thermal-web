#!/usr/bin/env python3
"""Credential and session handling for thermal-web (standard library only).

The browser logs in through a styled form and gets a signed session cookie.
HTTP basic auth is still accepted so that shell scripts keep working with
``curl -u``; it never sends a ``WWW-Authenticate`` challenge, so browsers do
not fall back to their own login dialog.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time

COOKIE_NAME = "thermal_session"
DEFAULT_SESSION_HOURS = 168  # one week
LOGIN_ATTEMPT_LIMIT = 5
LOGIN_ATTEMPT_WINDOW = 300  # seconds


def load_secret(path: str) -> bytes:
    """Read the cookie signing key, creating it on first use."""
    try:
        with open(path, "rb") as handle:
            key = handle.read().strip()
        if len(key) >= 32:
            return key
    except OSError:
        pass
    key = secrets.token_hex(32).encode()
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(key)
    os.chmod(path, 0o600)
    return key


def make_token(user: str, secret: bytes, ttl: int) -> str:
    payload = json.dumps({"u": user, "e": int(time.time()) + int(ttl)},
                         separators=(",", ":")).encode()
    body = base64.urlsafe_b64encode(payload).rstrip(b"=")
    signature = hmac.new(secret, body, hashlib.sha256).hexdigest().encode()
    return (body + b"." + signature).decode()


def parse_token(token: str, secret: bytes):
    """Return the user name for a valid, unexpired token, else ``None``."""
    try:
        body, _, signature = token.encode().partition(b".")
        if not body or not signature:
            return None
        expected = hmac.new(secret, body, hashlib.sha256).hexdigest().encode()
        if not hmac.compare_digest(signature, expected):
            return None
        padded = body + b"=" * (-len(body) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded).decode())
        if int(data.get("e", 0)) < time.time():
            return None
        return str(data.get("u") or "") or None
    except Exception:  # noqa: BLE001 - any malformed token is simply rejected
        return None


def read_cookie(header: str, name: str = COOKIE_NAME):
    for part in str(header or "").split(";"):
        key, _, value = part.strip().partition("=")
        if key == name:
            return value
    return None


def check_credentials(user: str, password: str, expected_user: str,
                      expected_password: str, expected_sha256: str = "") -> bool:
    """Constant time comparison; supports a plaintext or a sha256 password."""
    if not expected_password and not expected_sha256:
        return True  # authentication disabled
    if not hmac.compare_digest(str(user), str(expected_user)):
        return False
    if expected_sha256:
        digest = hashlib.sha256(str(password).encode()).hexdigest()
        return hmac.compare_digest(digest, str(expected_sha256).strip().lower())
    return hmac.compare_digest(str(password), str(expected_password))


def basic_credentials(header: str):
    """Extract ``(user, password)`` from an ``Authorization: Basic`` header."""
    if not str(header or "").startswith("Basic "):
        return None
    try:
        raw = base64.b64decode(header[6:]).decode("utf-8")
    except Exception:  # noqa: BLE001
        return None
    user, _, password = raw.partition(":")
    return user, password


class Attempts:
    """Very small in-memory throttle for failed logins."""

    def __init__(self, limit: int = LOGIN_ATTEMPT_LIMIT,
                 window: int = LOGIN_ATTEMPT_WINDOW):
        self.limit = limit
        self.window = window
        self._lock = threading.Lock()
        self._state = {}

    def blocked(self, key: str) -> bool:
        with self._lock:
            stamps = [t for t in self._state.get(key, []) if time.time() - t < self.window]
            self._state[key] = stamps
            return len(stamps) >= self.limit

    def fail(self, key: str):
        with self._lock:
            self._state.setdefault(key, []).append(time.time())

    def reset(self, key: str):
        with self._lock:
            self._state.pop(key, None)
