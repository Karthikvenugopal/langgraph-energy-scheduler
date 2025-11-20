"""Shared Google OAuth2 helpers for the Calendar and Fitness APIs.

A single consent flow requests every scope EnergyScheduler needs (Calendar read +
Fit activity/sleep/heart-rate read), so the user authorizes once. Credentials are
persisted to ``token.json`` and the OAuth client config is loaded from
``credentials.json`` (downloaded from the Google Cloud console).

All functions degrade gracefully: if ``token.json`` is missing the app simply runs
in demo mode, so nothing here raises just because the user has not authenticated.
"""

from __future__ import annotations

import os
from typing import Any, Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

# Every scope is requested together so the user sees one consent screen.
SCOPES: list[str] = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/fitness.activity.read",
    "https://www.googleapis.com/auth/fitness.sleep.read",
    "https://www.googleapis.com/auth/fitness.heart_rate.read",
]

# File locations (relative to the project root / current working directory).
TOKEN_FILE = "token.json"
CLIENT_SECRETS_FILE = "credentials.json"

# Where Google redirects back to after consent. Must match an "Authorized redirect
# URI" registered for the OAuth client in the Google Cloud console.
DEFAULT_REDIRECT_URI = os.getenv(
    "OAUTH_REDIRECT_URI", "http://localhost:8000/auth/callback"
)


def has_token() -> bool:
    """Return ``True`` if a stored OAuth token exists on disk.

    Used to decide between live Google data and demo mode without attempting a
    (potentially slow or failing) network refresh.
    """
    return os.path.exists(TOKEN_FILE)


def has_client_secrets() -> bool:
    """Return ``True`` if the OAuth client config (``credentials.json``) is present."""
    return os.path.exists(CLIENT_SECRETS_FILE)


def get_credentials() -> Optional[Credentials]:
    """Load and (if needed) refresh stored OAuth credentials.

    Returns:
        Valid :class:`Credentials` when ``token.json`` exists and can be loaded or
        refreshed; ``None`` when no token is stored or the token is unusable. Never
        raises — callers treat ``None`` as "not connected, use demo/synthetic data".
    """
    if not has_token():
        return None

    try:
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    except (ValueError, OSError):
        # Corrupt or unreadable token file — behave as if not connected.
        return None

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            save_credentials(creds)
        except Exception:  # noqa: BLE001 - refresh can fail many ways; fall back to demo
            return None

    return creds if creds and creds.valid else None


def save_credentials(creds: Credentials) -> None:
    """Persist credentials to ``token.json`` as JSON."""
    with open(TOKEN_FILE, "w", encoding="utf-8") as fh:
        fh.write(creds.to_json())


def get_google_service(api_name: str, version: str) -> Optional[Any]:
    """Build an authenticated Google API client for either Calendar or Fitness.

    Args:
        api_name: Google API name, e.g. ``"calendar"`` or ``"fitness"``.
        version: API version, e.g. ``"v3"`` (calendar) or ``"v1"`` (fitness).

    Returns:
        A ready-to-use service client, or ``None`` if the user is not authenticated
        (so service modules can fall back to demo/synthetic data).
    """
    creds = get_credentials()
    if creds is None:
        return None
    return build(api_name, version, credentials=creds, cache_discovery=False)


def build_flow(redirect_uri: Optional[str] = None) -> Flow:
    """Create an OAuth2 :class:`Flow` from ``credentials.json``.

    Args:
        redirect_uri: Callback URI; defaults to :data:`DEFAULT_REDIRECT_URI`.

    Returns:
        A configured :class:`~google_auth_oauthlib.flow.Flow` ready to produce an
        authorization URL or exchange an authorization code.

    Raises:
        FileNotFoundError: If ``credentials.json`` is missing — surface this to the
            user so they know to download their OAuth client config.
    """
    if not has_client_secrets():
        raise FileNotFoundError(
            "credentials.json not found. Download your OAuth client config from the "
            "Google Cloud console and place it in the project root."
        )
    flow = Flow.from_client_secrets_file(CLIENT_SECRETS_FILE, scopes=SCOPES)
    flow.redirect_uri = redirect_uri or DEFAULT_REDIRECT_URI
    return flow
