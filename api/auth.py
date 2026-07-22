import os
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Response
from jose import jwt
from pydantic import BaseModel
from dotenv import load_dotenv

from api.dependencies import get_current_user, SECRET_KEY, ALGORITHM

load_dotenv()

router = APIRouter()

ADMIN_USERID: str = os.getenv("ADMIN_USERID") or ""
ADMIN_PASSWORD: str = os.getenv("ADMIN_PASSWORD") or ""
ACCESS_TOKEN_EXPIRE_HOURS = 1  # 1 hour; use refresh tokens for longer sessions

if not ADMIN_USERID:
    raise ValueError("ADMIN_USERID missing — add it to .env")

if not ADMIN_PASSWORD:
    raise ValueError("ADMIN_PASSWORD missing — add it to .env")


class LoginRequest(BaseModel):
    username: str
    password: str


def _create_token(user_id: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
    return jwt.encode({"sub": user_id, "exp": expire}, SECRET_KEY, algorithm=ALGORITHM)


@router.post("/auth/login")
async def login(body: LoginRequest, response: Response):
    if body.username != ADMIN_USERID or body.password != ADMIN_PASSWORD:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    token = _create_token(ADMIN_USERID)

    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        max_age=ACCESS_TOKEN_EXPIRE_HOURS * 3600,
        samesite="lax",
        secure=os.getenv("SECURE_COOKIES", "true").lower() == "true",
    )

    return {"userId": ADMIN_USERID, "role": "SAD", "displayName": "Admin"}


@router.get("/auth/me")
async def get_me(current_user: dict = Depends(get_current_user)):
    return {
        "userId": current_user["user_id"],
        "role": "SAD",
        "displayName": "Admin",
    }


@router.post("/auth/logout")
async def logout(response: Response):
    response.delete_cookie("access_token")
    return {"message": "Logged out"}


@router.get("/admin/organizations")
async def list_organizations(current_user: dict = Depends(get_current_user)):
    """Return the single default organisation for this single-tenant deployment."""
    return [
        {
            "id": "default",
            "name": "ACT Angel AI",
            "slug": "act-angel-ai",
        }
    ]
