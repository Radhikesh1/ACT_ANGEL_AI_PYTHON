import os

from fastapi import HTTPException, Request
from jose import jwt, JWTError
from dotenv import load_dotenv

load_dotenv()

SECRET_KEY: str = os.getenv("SECRET_KEY") or "change-me-in-production"
ALGORITHM = "HS256"

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
    """Accept JWTs from any of the common cookie names used by Node backends."""
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
