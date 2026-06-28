"""
Sliding-window rate limiter backed by Redis.

Two layers:
  - per IP (always on, anonymous-safe): catches scrapers and runaways
  - per principal (when a JWT is present): enforces tier quotas

Uses INCR + EXPIRE which is atomic enough for a fixed 60-second window.
For 100 concurrent users this is plenty; if you grow to thousands and
need a true sliding window, swap to Redis sorted sets.
"""
import logging
import os
from typing import Optional

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.redis_client import get_redis
from app.services.auth import _decode, _extract_token, _auth_disabled

logger = logging.getLogger(__name__)

# Per-tier per-minute limits. Tune these once you have real traffic.
PLAN_LIMITS = {
    "anon":  60,    # IP-only, no JWT
    "free":  120,
    "basic": 600,
    "pro":   2000,
}

# Endpoints that should NEVER be rate limited
EXEMPT_PREFIXES = ("/health", "/docs", "/openapi.json", "/redoc", "/ws/")


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if any(path.startswith(p) for p in EXEMPT_PREFIXES):
            return await call_next(request)

        if os.getenv("DISABLE_RATE_LIMIT") == "1":
            return await call_next(request)

        # Resolve principal from Authorization header (cheap, no DB hit)
        principal_key = None
        plan = "anon"
        if not _auth_disabled():
            try:
                token = _extract_token(
                    request.headers.get("authorization"),
                    request.query_params.get("token"),
                )
                if token:
                    p = _decode(token)
                    principal_key = f"u:{p.id}"
                    plan = p.plan if p.plan in PLAN_LIMITS else "free"
            except Exception:
                # Bad token — let the route handler return 401, but rate
                # limit it as anonymous so brute-forcing tokens is bounded.
                principal_key = None

        ip = request.headers.get("x-forwarded-for", request.client.host if request.client else "unknown").split(",")[0].strip()
        ip_key = f"rl:ip:{ip}"
        principal_full = f"rl:{principal_key}" if principal_key else None

        try:
            redis = await get_redis()
            pipe = redis.pipeline()
            pipe.incr(ip_key)
            pipe.expire(ip_key, 60)
            if principal_full:
                pipe.incr(principal_full)
                pipe.expire(principal_full, 60)
            results = await pipe.execute()
        except Exception as exc:
            # Don't take down the service if Redis hiccups
            logger.warning("rate limit redis error: %s", exc)
            return await call_next(request)

        ip_count = results[0] or 0
        principal_count = results[2] if principal_full else 0
        ip_limit = PLAN_LIMITS["anon"] * 4  # IP cap is the broadest safety net
        plan_limit = PLAN_LIMITS[plan]

        if ip_count > ip_limit or (principal_full and principal_count > plan_limit):
            return JSONResponse(
                status_code=429,
                content={
                    "error": "rate_limited",
                    "limit_per_minute": plan_limit if principal_full else ip_limit,
                    "plan": plan,
                },
                headers={"Retry-After": "60"},
            )

        return await call_next(request)
