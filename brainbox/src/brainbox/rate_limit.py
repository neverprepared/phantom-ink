"""Rate limiting middleware for API endpoints."""

from __future__ import annotations

from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from fastapi import Request, Response
from fastapi.responses import JSONResponse


def _rate_limit_key(request: Request) -> str:
    """Generate rate limit key from remote address."""
    return get_remote_address(request)


# Create limiter instance
limiter = Limiter(key_func=_rate_limit_key)


def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> Response:
    """Handle rate limit exceeded errors with clear message."""
    return JSONResponse(
        status_code=429,
        content={
            "error": "Rate limit exceeded",
            "detail": "Too many requests. Please try again later.",
            "retry_after": exc.retry_after if hasattr(exc, "retry_after") else None,
        },
        headers={"Retry-After": str(int(exc.retry_after))} if hasattr(exc, "retry_after") else {},
    )
