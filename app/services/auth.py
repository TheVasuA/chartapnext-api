"""
Plan + auth enforcement for the FastAPI surface.

The Next.js frontend mints short-lived HS256 JWTs (issuer=chartap-next,
audience=chartap-api) carrying the user's effective `plan` and `role`.
We verify them with a shared secret (`BACKEND_JWT_SECRET`, falling back
to `NEXTAUTH_SECRET` for single-secret deployments).

Public, light endpoints (e.g. /coins, /signals/) stay anonymous. Heavy
endpoints (per-symbol strategy runs, scalping cold scan, websocket
subscriptions) use one of the dependency factories below to require
a minimum plan tier.

Set DISABLE_AUTH=1 in env to bypass enforcement for local development.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from fastapi import Depends, HTTPException, Header, Query, WebSocket, status
from jose import JWTError, jwt

ISS = "chartap-next"
AUD = "chartap-api"
ALGO = "HS256"

# free < basic < pro
PLAN_RANK = {"free": 0, "basic": 1, "pro": 2}


def _secret() -> str:
    return os.getenv("BACKEND_JWT_SECRET") or os.getenv("NEXTAUTH_SECRET") or ""


def _auth_disabled() -> bool:
    return os.getenv("DISABLE_AUTH") == "1"


@dataclass
class Principal:
    id: str
    plan: str
    role: str
    email: Optional[str] = None


def _decode(token: str) -> Principal:
    secret = _secret()
    if not secret:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="server misconfigured: missing BACKEND_JWT_SECRET",
        )
    try:
        payload = jwt.decode(
            token,
            secret,
            algorithms=[ALGO],
            audience=AUD,
            issuer=ISS,
        )
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"invalid token: {exc}",
        )
    return Principal(
        id=payload.get("sub", ""),
        plan=str(payload.get("plan", "free")),
        role=str(payload.get("role", "user")),
        email=payload.get("email"),
    )


def _extract_token(authorization: Optional[str], token_query: Optional[str]) -> Optional[str]:
    if authorization:
        if authorization.lower().startswith("bearer "):
            return authorization[7:].strip()
        return authorization.strip()
    return token_query


def get_principal_optional(
    authorization: Optional[str] = Header(default=None),
    token: Optional[str] = Query(default=None, alias="token"),
) -> Optional[Principal]:
    """Return the principal if a valid token is provided, else None."""
    if _auth_disabled():
        return Principal(id="dev", plan="pro", role="admin", email="dev@local")
    raw = _extract_token(authorization, token)
    if not raw:
        return None
    return _decode(raw)


def get_principal(
    authorization: Optional[str] = Header(default=None),
    token: Optional[str] = Query(default=None, alias="token"),
) -> Principal:
    """Strict — 401 if no/invalid token."""
    if _auth_disabled():
        return Principal(id="dev", plan="pro", role="admin", email="dev@local")
    raw = _extract_token(authorization, token)
    if not raw:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing token",
        )
    return _decode(raw)


def require_plan(min_plan: str):
    """
    FastAPI dependency factory:
        @router.get("/")
        async def x(p = Depends(require_plan('basic'))): ...
    """
    if min_plan not in PLAN_RANK:
        raise ValueError(f"unknown plan: {min_plan}")

    def _dep(principal: Principal = Depends(get_principal)) -> Principal:
        if PLAN_RANK[principal.plan] < PLAN_RANK[min_plan]:
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail=f"plan_required:{min_plan}",
            )
        return principal

    return _dep


# ─────────────── WebSocket helpers ───────────────

async def ws_principal(websocket: WebSocket) -> Principal:
    """
    Verify a WebSocket caller's plan. The frontend appends ?token=... when
    connecting. We close the socket cleanly on failure.
    """
    if _auth_disabled():
        return Principal(id="dev", plan="pro", role="admin", email="dev@local")

    raw = (
        websocket.query_params.get("token")
        or websocket.headers.get("authorization")
    )
    if raw and raw.lower().startswith("bearer "):
        raw = raw[7:].strip()
    if not raw:
        await websocket.close(code=4401)
        raise HTTPException(401, "missing token")
    try:
        return _decode(raw)
    except HTTPException as exc:
        await websocket.close(code=4401)
        raise exc


async def ws_require_plan(websocket: WebSocket, min_plan: str) -> Principal:
    p = await ws_principal(websocket)
    if PLAN_RANK[p.plan] < PLAN_RANK[min_plan]:
        await websocket.close(code=4402)
        raise HTTPException(402, f"plan_required:{min_plan}")
    return p
