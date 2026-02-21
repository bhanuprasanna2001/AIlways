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
from app.core.tools.redis import store_session, get_session, delete_session
from app.core.auth.deps import get_current_user, require_csrf, SESSION_COOKIE_NAME, CSRF_COOKIE_NAME


router = APIRouter(prefix="/auth", tags=["auth"])
SETTINGS = get_settings()

_rate = Rate(5, Duration.MINUTE)
_limiter = Limiter(_rate)


def cookie_opts(http_only: bool = True):
    """Helper function to generate cookie options.

    Args:
        http_only (bool, optional): Whether the cookie should be HTTP-only. Defaults to True.

    Returns:
        dict: A dictionary of cookie options.
    """
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

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/register", dependencies=[Depends(RateLimiter(limiter=_limiter))], summary="Register a new user")
async def register(user_in: RegisterIn, db: AsyncSession = Depends(get_db)):
    """Register a new user.

    Args:
        user_in (RegisterIn): The user registration data.
        db (AsyncSession, optional): The database session. Defaults to Depends(get_db).

    Raises:
        HTTPException: If the email is already registered.

    Returns:
        dict: A success message.
    """
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
    """Login a user.

    Args:
        user_in (LoginIn): The user login data.
        response (Response): The FastAPI response object.
        db (AsyncSession, optional): The database session. Defaults to Depends(get_db).

    Raises:
        HTTPException: If the email or password is invalid.

    Returns:
        dict: A success message.
    """
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
    """Logout the current user.

    Args:
        request (Request): The FastAPI request object.
        response (Response): The FastAPI response object.
        current_user (User, optional): The currently authenticated user. Defaults to Depends(get_current_user).

    Returns:
        dict: A success message.
    """
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    if session_id:
        await delete_session(session_id)

    # Clear cookies
    response.delete_cookie(key=SESSION_COOKIE_NAME, path="/")
    response.delete_cookie(key=CSRF_COOKIE_NAME, path="/")

    return {"message": "Logout successful"}


@router.get("/me", summary="Get the current authenticated user's information")
async def get_me(current_user: User = Depends(get_current_user)):
    """Get the current authenticated user's information.

    Args:
        current_user (User, optional): The currently authenticated user. Defaults to Depends(get_current_user).

    Returns:
        dict: The current user's information.
    """
    return {
        "id": str(current_user.id),
        "name": current_user.name,
        "email": current_user.email,
    }