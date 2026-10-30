"""Security utilities: rate limiting, proxy inspection, and authentication."""
from __future__ import annotations

import collections
import logging
import secrets
import time
from typing import Dict, Optional

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app import config, database

logger = logging.getLogger(__name__)

# Basic Auth security scheme for Dashboard (auto_error=False so unconfigured auth allows direct access)
security_basic = HTTPBasic(auto_error=False)

# In-memory IP rate limiter: {ip_address: deque of timestamps}
_IP_REQUEST_LOGS: Dict[str, collections.deque] = collections.defaultdict(collections.deque)


def get_client_ip(request: Request) -> str:
    """
    Extracts the client IP address.

    Requirement:
    Uses the direct socket client address by default.
    Only honors X-Forwarded-For when TRUST_PROXY is explicitly enabled.
    """
    if config.TRUST_PROXY:
        forwarded_for = request.headers.get("X-Forwarded-For")
        if forwarded_for:
            # First IP in comma-separated chain is the client IP
            client_ip = forwarded_for.split(",")[0].strip()
            if client_ip:
                return client_ip

    if request.client and request.client.host:
        return request.client.host

    return "unknown"


def check_rate_limit(request: Request) -> None:
    """
    Applies per-IP sliding window rate limiting.
    Raises HTTP 429 if request limit per minute is exceeded.
    """
    client_ip = get_client_ip(request)
    now = time.time()
    one_minute_ago = now - 60.0

    timestamps = _IP_REQUEST_LOGS[client_ip]

    # Evict timestamps older than 1 minute
    while timestamps and timestamps[0] < one_minute_ago:
        timestamps.popleft()

    limit = config.RATE_LIMIT_PER_MINUTE
    if len(timestamps) >= limit:
        logger.warning("Rate limit exceeded for IP %s (%d reqs/min)", client_ip, len(timestamps))
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limit exceeded ({limit} requests/minute). Please wait a moment before retrying."
        )

    timestamps.append(now)


def check_daily_quota() -> None:
    """
    Enforces MAX_REVIEWS_PER_DAY across both the web app and GitHub bot.
    0 means unlimited.
    """
    max_quota = config.MAX_REVIEWS_PER_DAY
    if max_quota <= 0:
        return

    today_count = database.get_reviews_count_today()
    if today_count >= max_quota:
        logger.warning("Daily review quota reached (%d / %d reviews today)", today_count, max_quota)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Daily review quota reached ({max_quota} reviews/day). Quota resets at 00:00 UTC."
        )


def verify_access_token(request: Request) -> None:
    """
    Verifies optional ACCESS_TOKEN for protected API endpoints.
    Accepts token via X-API-Key or Authorization: Bearer <token>.
    """
    required_token = config.ACCESS_TOKEN
    if not required_token or not required_token.strip():
        return  # No access token configured; public access permitted

    token = request.headers.get("X-API-Key")
    if not token:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header.split("Bearer ", 1)[1].strip()

    if not token or not secrets.compare_digest(token, required_token.strip()):
        logger.warning("Unauthorized access attempt to %s from %s", request.url.path, get_client_ip(request))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing access token. Provide a valid token in X-API-Key or Authorization header.",
            headers={"WWW-Authenticate": "Bearer"},
        )


def verify_dashboard_auth(
    credentials: Optional[HTTPBasicCredentials] = Depends(security_basic)
) -> None:
    """
    Protects /dashboard using Basic Auth if DASHBOARD_PASSWORD is configured.
    """
    required_password = config.DASHBOARD_PASSWORD
    if not required_password or not required_password.strip():
        return  # No dashboard password set; public viewing permitted

    if not credentials or not secrets.compare_digest(credentials.password, required_password.strip()):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid dashboard credentials",
            headers={"WWW-Authenticate": "Basic"},
        )


def clear_rate_limit_cache() -> None:
    """Clears the in-memory rate limit logs (used in tests)."""
    _IP_REQUEST_LOGS.clear()

