from __future__ import annotations

import logging
import time
from collections import deque
from typing import Annotated

from fastapi import Cookie, Depends, Header, HTTPException, Request, status

from hermes_mgmt.auth import bearer_matches, verify_session_token
from hermes_mgmt.config import Settings, get_settings

logger = logging.getLogger(__name__)

# per-IP: list of failure timestamps
_login_failures: dict[str, deque[float]] = {}
_RATE_WINDOW = 15 * 60  # 15 minutes
_RATE_MAX_FAILURES = 10


def get_settings_dep() -> Settings:
    return get_settings()


async def require_auth(
    authorization: Annotated[str | None, Header()] = None,
    session: Annotated[str | None, Cookie()] = None,
    settings: Settings = Depends(get_settings_dep),
) -> str:
    """Returns authenticated username or raises 401."""
    # Check Bearer API key first
    if bearer_matches(authorization, settings.mgmt_api_key):
        return "__api_key__"

    # Check session cookie
    if session:
        username = verify_session_token(session, settings.session_secret)
        if username:
            return username

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )


class RateLimiter:
    """In-memory per-IP rate limiter for login endpoint."""

    def __init__(
        self, max_failures: int = _RATE_MAX_FAILURES, window_seconds: float = _RATE_WINDOW
    ) -> None:
        self.max_failures = max_failures
        self.window_seconds = window_seconds
        self._store: dict[str, deque[float]] = {}

    def _client_ip(self, request: Request) -> str:
        forwarded_for = request.headers.get("X-Forwarded-For")
        if forwarded_for:
            return forwarded_for.split(",")[0].strip()
        if request.client:
            return request.client.host
        return "unknown"

    def is_blocked(self, request: Request) -> bool:
        ip = self._client_ip(request)
        now = time.time()
        if ip not in self._store:
            return False
        dq = self._store[ip]
        # Evict old entries
        while dq and now - dq[0] > self.window_seconds:
            dq.popleft()
        return len(dq) >= self.max_failures

    def record_failure(self, request: Request) -> None:
        ip = self._client_ip(request)
        now = time.time()
        if ip not in self._store:
            self._store[ip] = deque()
        dq = self._store[ip]
        while dq and now - dq[0] > self.window_seconds:
            dq.popleft()
        dq.append(now)

    def clear(self, request: Request) -> None:
        ip = self._client_ip(request)
        self._store.pop(ip, None)

    def cleanup(self) -> None:
        now = time.time()
        for ip in list(self._store.keys()):
            dq = self._store[ip]
            while dq and now - dq[0] > self.window_seconds:
                dq.popleft()
            if not dq:
                del self._store[ip]


_rate_limiter = RateLimiter()


def get_rate_limiter() -> RateLimiter:
    return _rate_limiter


async def rate_limit_login(request: Request) -> None:
    limiter = get_rate_limiter()
    if limiter.is_blocked(request):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed login attempts. Try again later.",
        )
