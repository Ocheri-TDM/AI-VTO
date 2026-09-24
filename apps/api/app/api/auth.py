"""Minimal name/password authentication with opaque HttpOnly sessions."""

import hashlib
import secrets
from datetime import timedelta

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import delete, select

from app.database.models import AuthSession, User
from app.domain.models import utcnow

router = APIRouter(prefix="/api/auth", tags=["auth"])
hasher = PasswordHasher()
COOKIE = "research_session"


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=8, max_length=1024)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("Name required")
        return value


def normalized_name(value: str) -> str:
    return " ".join(value.strip().casefold().split())


def token_id(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def create_session(request: Request, response: Response, user: User):
    token = secrets.token_urlsafe(32)
    now = utcnow()
    expires = now + timedelta(days=request.app.state.settings.auth_session_days)
    async with request.app.state.sessions.begin() as db:
        db.add(AuthSession(id=token_id(token), user_id=user.id, created_at=now, expires_at=expires))
    response.set_cookie(
        COOKIE, token, httponly=True, secure=request.app.state.settings.auth_cookie_secure,
        samesite="lax", max_age=int((expires - now).total_seconds()), path="/",
    )
    return {"id": user.id, "name": user.display_name}


async def current_user(request: Request) -> User | None:
    token = request.cookies.get(COOKIE)
    if not token:
        return None
    async with request.app.state.sessions() as db:
        row = await db.scalar(
            select(User).join(AuthSession, AuthSession.user_id == User.id).where(
                AuthSession.id == token_id(token), AuthSession.expires_at > utcnow()
            )
        )
        return row


@router.post("/register", status_code=201)
async def register(body: Credentials, request: Request, response: Response):
    normalized = normalized_name(body.name)
    async with request.app.state.sessions.begin() as db:
        if await db.scalar(select(User.id).where(User.normalized_name == normalized)):
            raise HTTPException(409, "Name already exists")
        now = utcnow()
        user = User(
            display_name=body.name, normalized_name=normalized,
            password_hash=hasher.hash(body.password), created_at=now, updated_at=now,
        )
        db.add(user)
        await db.flush()
        user_id, display_name = user.id, user.display_name
    detached = User(id=user_id, display_name=display_name, normalized_name=normalized,
                    password_hash="", created_at=now, updated_at=now)
    return await create_session(request, response, detached)


@router.post("/login")
async def login(body: Credentials, request: Request, response: Response):
    async with request.app.state.sessions.begin() as db:
        user = await db.scalar(select(User).where(User.normalized_name == normalized_name(body.name)))
        if user is None:
            raise HTTPException(401, "Invalid credentials")
        try:
            hasher.verify(user.password_hash, body.password)
        except VerifyMismatchError:
            raise HTTPException(401, "Invalid credentials") from None
        if hasher.check_needs_rehash(user.password_hash):
            user.password_hash = hasher.hash(body.password)
        user.last_login_at, user.updated_at = utcnow(), utcnow()
        user_id, display_name = user.id, user.display_name
    detached = User(id=user_id, display_name=display_name, normalized_name=normalized_name(display_name),
                    password_hash="", created_at=utcnow(), updated_at=utcnow())
    return await create_session(request, response, detached)


@router.post("/logout", status_code=204)
async def logout(request: Request, response: Response):
    if token := request.cookies.get(COOKIE):
        async with request.app.state.sessions.begin() as db:
            await db.execute(delete(AuthSession).where(AuthSession.id == token_id(token)))
    response.delete_cookie(COOKIE, path="/")


@router.get("/me")
async def me(request: Request):
    user = await current_user(request)
    if user is None:
        raise HTTPException(401, "Authentication required")
    return {"id": user.id, "name": user.display_name}
