"""GitHub App authentication and installation token management."""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Dict, Optional, Tuple

import httpx
import jwt

from app import config

logger = logging.getLogger(__name__)


class GitHubAuthError(Exception):
    """Raised when GitHub App authentication fails."""
    pass


# In-memory cache for installation tokens: {installation_id: (token, expires_at_timestamp)}
_TOKEN_CACHE: Dict[int, Tuple[str, float]] = {}


def load_private_key() -> str:
    """Loads RSA private key from environment variable or file path."""
    if config.GITHUB_PRIVATE_KEY and config.GITHUB_PRIVATE_KEY.strip():
        # Inline PEM string (converting literal \n to actual newlines if needed)
        key = config.GITHUB_PRIVATE_KEY.strip()
        if "\\n" in key and "-----BEGIN" in key:
            key = key.replace("\\n", "\n")
        return key

    if config.GITHUB_PRIVATE_KEY_PATH and config.GITHUB_PRIVATE_KEY_PATH.strip():
        key_path = Path(config.GITHUB_PRIVATE_KEY_PATH).expanduser()
        if not key_path.is_file():
            raise GitHubAuthError(f"GitHub App private key file not found at: {key_path}")
        try:
            return key_path.read_text(encoding="utf-8").strip()
        except Exception as e:
            raise GitHubAuthError(f"Failed to read private key file: {e}") from e

    raise GitHubAuthError(
        "GitHub App private key is not configured. Set GITHUB_PRIVATE_KEY or GITHUB_PRIVATE_KEY_PATH."
    )


def generate_app_jwt(app_id: Optional[str] = None, private_key_pem: Optional[str] = None) -> str:
    """
    Generates a signed JSON Web Token (JWT) for GitHub App authentication.
    Valid for 10 minutes with a 60-second backwards buffer for clock drift.
    """
    app_id_val = app_id or config.GITHUB_APP_ID
    if not app_id_val or not str(app_id_val).strip():
        raise GitHubAuthError("GITHUB_APP_ID is not configured.")

    pem_key = private_key_pem or load_private_key()

    now = int(time.time())
    payload = {
        # Issued at (60 seconds in the past to allow for clock drift)
        "iat": now - 60,
        # JWT expiration time (10 minute maximum for GitHub)
        "exp": now + (10 * 60),
        # GitHub App identifier
        "iss": str(app_id_val).strip(),
    }

    try:
        token = jwt.encode(payload, pem_key, algorithm="RS256")
        return token if isinstance(token, str) else token.decode("utf-8")
    except Exception as e:
        raise GitHubAuthError(f"Failed to sign GitHub App JWT: {e}") from e


def get_installation_access_token(
    installation_id: int,
    client: Optional[httpx.Client] = None
) -> str:
    """
    Exchanges an App JWT for an installation access token.
    Caches the token in-memory until near its expiration.
    """
    now = time.time()
    # Check cache first (with a 60-second expiration buffer)
    if installation_id in _TOKEN_CACHE:
        cached_token, expires_at = _TOKEN_CACHE[installation_id]
        if expires_at - now > 60:
            return cached_token

    app_jwt = generate_app_jwt()
    url = f"https://api.github.com/app/installations/{installation_id}/access_tokens"
    headers = {
        "Authorization": f"Bearer {app_jwt}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "AI-Code-Reviewer-Bot",
    }

    close_client = False
    if client is None:
        client = httpx.Client(timeout=15.0)
        close_client = True

    try:
        response = client.post(url, headers=headers)
        if response.status_code != 201:
            raise GitHubAuthError(
                f"Failed to obtain installation token (HTTP {response.status_code}): {response.text}"
            )
        data = response.json()
        token = data["token"]

        # Parse expiration or default to 50 minutes from now
        expires_at_ts = now + 3000
        _TOKEN_CACHE[installation_id] = (token, expires_at_ts)
        return token
    except Exception as e:
        if isinstance(e, GitHubAuthError):
            raise
        raise GitHubAuthError(f"Network error obtaining installation token: {e}") from e
    finally:
        if close_client:
            client.close()


def get_repo_installation_id(owner: str, repo: str, client: Optional[httpx.Client] = None) -> int:
    """Retrieves the GitHub App installation ID for a specific repository (used by CLI)."""
    app_jwt = generate_app_jwt()
    url = f"https://api.github.com/repos/{owner}/{repo}/installation"
    headers = {
        "Authorization": f"Bearer {app_jwt}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "AI-Code-Reviewer-Bot",
    }

    close_client = False
    if client is None:
        client = httpx.Client(timeout=15.0)
        close_client = True

    try:
        response = client.get(url, headers=headers)
        if response.status_code == 404:
            raise GitHubAuthError(f"GitHub App is not installed on repository '{owner}/{repo}'.")
        if response.status_code != 200:
            raise GitHubAuthError(
                f"Failed to fetch installation for '{owner}/{repo}' (HTTP {response.status_code}): {response.text}"
            )
        return response.json()["id"]
    finally:
        if close_client:
            client.close()

