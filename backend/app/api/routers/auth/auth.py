import secrets
from sqlmodel import select
from fastapi_limiter.depends import RateLimiter
from sqlalchemy.ext.asyncio import AsyncSession
from pyrate_limiter import Duration, Rate, Limiter
from pydantic import BaseModel, EmailStr, field_validator
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from app.db import get_db
from app.db.models import User

from app.core.config import get_settings
from app.core.auth.security import hash_password, verify_password
from app.core.auth.deps import get_current_user, require_csrf, SESSION_COOKIE_NAME, CSRF_COOKIE_NAME
from app.core.tools.redis import store_session, delete_session, store_ws_ticket


router = APIRouter(prefix="/auth", tags=["auth"])
SETTINGS = get_settings()

_rate = Rate(5, Duration.MINUTE)
_limiter = Limiter(_rate)


def cookie_opts(http_only: bool = True):
    """Build standard cookie options dict for session/CSRF cookies."""
    return dict(
        httponly=http_only,
        secure=SETTINGS.COOKIE_SECURE,
        samesite="lax",
        max_age=SETTINGS.REDIS_SESSION_TTL_SECONDS,
        path="/",
    )
    

# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class RegisterIn(BaseModel):
    name: str
    email: EmailStr
    password: str

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        if not any(c.isupper() for c in v):
            raise ValueError("Password must contain at least one uppercase letter")
        if not any(c.isdigit() for c in v):
            raise ValueError("Password must contain at least one digit")
        return v

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Name cannot be empty")
        return v


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class UpdateMeIn(BaseModel):
    name: str | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.strip()
        if not v:
            raise ValueError("Name cannot be empty")
        return v

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/register", dependencies=[Depends(RateLimiter(limiter=_limiter))], summary="Register a new user")
async def register(user_in: RegisterIn, db: AsyncSession = Depends(get_db)):
    """Register a new user. Raises 400 if the email is already taken."""
    # Check if email is already registered
    email = user_in.email.lower()
    existing_user = await db.execute(
        select(User).where(User.email == email)
    )
    if existing_user.scalar():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email is already registered")

    # Create new user
    new_user = User(
        name=user_in.name,
        email=email,
        hashed_password=hash_password(user_in.password),
    )
    db.add(new_user)
    await db.commit()

    return {"message": "User registered successfully"}


@router.post("/login", dependencies=[Depends(RateLimiter(limiter=_limiter))], summary="Login a user")
async def login(user_in: LoginIn, response: Response, db: AsyncSession = Depends(get_db)):
    """Authenticate a user and set session + CSRF cookies."""
    # Find user by email
    result = await db.execute(
        select(User).where(User.email == user_in.email.lower())
    )
    user = result.scalars().first()
    if not user or not verify_password(user_in.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is disabled")

    # Create session
    session_id = secrets.token_urlsafe(32)
    csrf_token = secrets.token_urlsafe(32)
    await store_session(session_id, str(user.id))

    # Set cookies
    response.set_cookie(key=SESSION_COOKIE_NAME, value=session_id, **cookie_opts(http_only=True))
    response.set_cookie(key=CSRF_COOKIE_NAME, value=csrf_token, **cookie_opts(http_only=False))

    return {
        "message": "Login successful",
        "user": {
            "id": str(user.id),
            "name": user.name,
            "email": user.email,
        }
    }


@router.post("/logout", dependencies=[Depends(require_csrf)], summary="Logout the current user")
async def logout(request: Request, response: Response, current_user: User = Depends(get_current_user)):
    """Logout the current user and clear session cookies."""
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    if session_id:
        await delete_session(session_id)

    # Clear cookies
    response.delete_cookie(key=SESSION_COOKIE_NAME, path="/")
    response.delete_cookie(key=CSRF_COOKIE_NAME, path="/")

    return {"message": "Logout successful"}


@router.get("/me", summary="Get the current authenticated user's information")
async def get_me(current_user: User = Depends(get_current_user)):
    """Return the current authenticated user's profile."""
    return {
        "id": str(current_user.id),
        "name": current_user.name,
        "email": current_user.email,
    }


@router.patch("/me", dependencies=[Depends(require_csrf)], summary="Update current user profile")
async def update_me(
    body: UpdateMeIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update the current user's profile."""
    if body.name is not None:
        current_user.name = body.name
        db.add(current_user)
        await db.commit()
        await db.refresh(current_user)

    return {
        "id": str(current_user.id),
        "name": current_user.name,
        "email": current_user.email,
    }


@router.post(
    "/ws-ticket",
    dependencies=[Depends(require_csrf)],
    summary="Issue a one-time WebSocket authentication ticket",
)
async def issue_ws_ticket(
    current_user: User = Depends(get_current_user),
):
    """Create a short-lived, single-use ticket for WebSocket authentication.

    The browser cannot send cookies on a ``new WebSocket()`` call in
    all environments, so this endpoint issues a random ticket that the
    client passes as a query parameter.  The ticket is consumed
    atomically on first use and cannot be replayed.
    """
    ticket = secrets.token_hex(32)
    await store_ws_ticket(ticket, str(current_user.id))
    return {"ticket": ticket}