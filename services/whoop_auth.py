"""Whoop OAuth2 helpers.

Mirrors :mod:`services.google_auth`: a single authorize -> callback flow, with the
token persisted to ``token_whoop.json`` and auto-refreshed. Client credentials come
from env vars (``WHOOP_CLIENT_ID`` / ``WHOOP_CLIENT_SECRET``); register an app at
https://developer.whoop.com to obtain them.

Everything degrades gracefully: with no token the app falls back to another wearable
or synthetic energy, so nothing here raises just because Whoop isn't configured.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Optional
from urllib.parse import urlencode

import requests

AUTH_URL = "https://api.prod.whoop.com/oauth/oauth2/auth"
TOKEN_URL = "https://api.prod.whoop.com/oauth/oauth2/token"
API_BASE = "https://api.prod.whoop.com/developer/v2"

# ``offline`` is required to receive a refresh token.
SCOPES = ["read:recovery", "read:sleep", "read:cycles", "read:profile", "offline"]

TOKEN_FILE = "token_whoop.json"

DEFAULT_REDIRECT_URI = os.getenv(
    "WHOOP_REDIRECT_URI", "http://localhost:8000/auth/whoop/callback"
)


def client_id() -> Optional[str]:
    """Return the Whoop OAuth client id from the environment."""
    return os.getenv("WHOOP_CLIENT_ID")


def client_secret() -> Optional[str]:
    """Return the Whoop OAuth client secret from the environment."""
    return os.getenv("WHOOP_CLIENT_SECRET")


def has_credentials() -> bool:
    """Return ``True`` if both Whoop client id and secret are configured."""
    return bool(client_id() and client_secret())


def has_token() -> bool:
    """Return ``True`` if a stored Whoop token exists on disk."""
    return os.path.exists(TOKEN_FILE)


def _save_token(data: dict[str, Any]) -> None:
    """Persist a token response to ``token_whoop.json`` with an absolute expiry."""
    expires_in = int(data.get("expires_in", 3600))
    record = {
        "access_token": data.get("access_token"),
        "refresh_token": data.get("refresh_token"),
        "scope": data.get("scope"),
        "token_type": data.get("token_type", "bearer"),
        "expires_at": time.time() + expires_in - 60,  # refresh slightly early
    }
    with open(TOKEN_FILE, "w", encoding="utf-8") as fh:
        json.dump(record, fh)


def build_authorize_url(state: str, redirect_uri: Optional[str] = None) -> str:
    """Build the Whoop OAuth authorization URL.

    Args:
        state: An opaque CSRF/state value echoed back to the callback.
        redirect_uri: Callback URI; defaults to :data:`DEFAULT_REDIRECT_URI`.

    Returns:
        The full authorization URL to redirect the user to.
    """
    params = {
        "response_type": "code",
        "client_id": client_id() or "",
        "redirect_uri": redirect_uri or DEFAULT_REDIRECT_URI,
        "scope": " ".join(SCOPES),
        "state": state,
    }
    return f"{AUTH_URL}?{urlencode(params)}"


def exchange_code(code: str, redirect_uri: Optional[str] = None) -> None:
    """Exchange an authorization code for tokens and persist them.

    Args:
        code: The authorization code from the OAuth callback.
        redirect_uri: Must match the one used to start the flow.

    Raises:
        requests.HTTPError: If the token exchange fails (surfaced to the caller).
    """
    resp = requests.post(
        TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client_id(),
            "client_secret": client_secret(),
            "redirect_uri": redirect_uri or DEFAULT_REDIRECT_URI,
        },
        timeout=15,
    )
    resp.raise_for_status()
    _save_token(resp.json())


def _refresh(refresh_token: str) -> bool:
    """Refresh the access token using the stored refresh token.

    Returns:
        ``True`` if the refresh succeeded and a new token was saved.
    """
    try:
        resp = requests.post(
            TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": client_id(),
                "client_secret": client_secret(),
                "scope": "offline",
            },
            timeout=15,
        )
        resp.raise_for_status()
    except requests.RequestException:
        return False
    _save_token(resp.json())
    return True


def get_access_token() -> Optional[str]:
    """Return a valid Whoop access token, refreshing if needed.

    Returns:
        The access token string, or ``None`` if not connected or the token can't
        be loaded/refreshed (callers then fall back to another data source).
    """
    if not has_token():
        return None
    try:
        with open(TOKEN_FILE, encoding="utf-8") as fh:
            record = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None

    if record.get("expires_at", 0) <= time.time():
        refresh_token = record.get("refresh_token")
        if not refresh_token or not _refresh(refresh_token):
            return None
        try:
            with open(TOKEN_FILE, encoding="utf-8") as fh:
                record = json.load(fh)
        except (OSError, json.JSONDecodeError):
            return None

    return record.get("access_token")
