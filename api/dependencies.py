import os

from fastapi import HTTPException, Request
from jose import jwt, JWTError
from dotenv import load_dotenv

load_dotenv()

SECRET_KEY: str | None = os.getenv("SECRET_KEY")
if not SECRET_KEY:
    raise RuntimeError("SECRET_KEY environment variable must be set")
ALGORITHM = "HS256"
INTERNAL_SECRET: str = os.getenv("ACTANGEL_INGEST_SECRET") or ""

# Cookie names used by common Node.js auth setups
_COOKIE_CANDIDATES = [
    "access_token",
    "token",
    "authToken",
    "auth_token",
    "jwt",
    "Authorization",
]


async def get_current_user(request: Request) -> dict:
    """Accept JWTs from any of the common cookie names used by Node backends,
    or allow internal WEB Node proxy calls via X-Internal-Secret header."""

    # Trusted internal calls from WEB Node bypass JWT auth
    internal = (
        request.headers.get("X-Internal-Secret")
        or request.headers.get("x-internal-secret")
    )
    if INTERNAL_SECRET and internal == INTERNAL_SECRET:
        return {"user_id": "internal", "role": "internal"}

    token: str | None = None
    for name in _COOKIE_CANDIDATES:
        token = request.cookies.get(name)
        if token:
            break

    # Also accept Bearer token in the Authorization header as a fallback
    if not token:
        auth_header = request.headers.get("Authorization") or request.headers.get("authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header[7:]

    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        # Accept "sub" (standard) or "userId" / "id" / "_id" (common Node.js JWT fields)
        user_id: str | None = (
            payload.get("sub")
            or payload.get("userId")
            or payload.get("id")
            or payload.get("_id")
        )
        if not user_id:
            raise HTTPException(status_code=401, detail="Invalid token")
        return {"user_id": str(user_id)}
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")


def get_org_id(request: Request) -> str | None:
    """Extract organization ID from the X-Org-Id header set by WEB Node proxy."""
    return (
        request.headers.get("X-Org-Id")
        or request.headers.get("x-org-id")
        or None
    )
