from uuid import UUID
from sqlmodel import select
from sqlalchemy.ext.asyncio.session import AsyncSession
from fastapi import Depends, HTTPException, Request, WebSocket, status

from app.db import get_db
from app.db.models import User, Vault, VaultMember
from app.core.config import get_settings
from app.core.tools.redis import get_session, consume_ws_ticket
from app.core.logger import setup_logger

logger = setup_logger(__name__)

SETTINGS = get_settings()

SESSION_COOKIE_NAME = SETTINGS.SESSION_COOKIE_NAME
CSRF_COOKIE_NAME = SETTINGS.CSRF_COOKIE_NAME


async def get_current_user(request: Request, db: AsyncSession = Depends(get_db)) -> User:
    """Get the current authenticated user from the session cookie."""
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    if not session_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    user_id = await get_session(session_id)
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired or invalid")

    try:
        parsed_id = UUID(user_id)
    except (ValueError, AttributeError):
        logger.warning(f"Corrupt session data for session_id={session_id!r}")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session")

    result = await db.execute(select(User).where(User.id == parsed_id))
    user = result.scalars().first()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or invalid session")

    return user


async def require_csrf(request: Request):
    """Enforce CSRF protection on state-changing requests."""
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        csrf_token_cookie = request.cookies.get(CSRF_COOKIE_NAME)
        csrf_token_header = request.headers.get("X-CSRF-Token")
        if not csrf_token_cookie or not csrf_token_header or csrf_token_cookie != csrf_token_header:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="CSRF token missing or invalid")


# ---------------------------------------------------------------------------
# Vault authorization
# ---------------------------------------------------------------------------

ROLE_HIERARCHY = {"owner": 3, "editor": 2, "viewer": 1}


async def require_vault_member(
    vault_id: UUID,
    user: User,
    db: AsyncSession,
    min_role: str = "viewer",
) -> tuple[Vault, VaultMember]:
    """Verify the user is a vault member with at least ``min_role``."""
    result = await db.execute(
        select(Vault).where(Vault.id == vault_id, Vault.is_active == True, Vault.deleted_at == None)
    )
    vault = result.scalars().first()
    if not vault:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Vault not found")

    result = await db.execute(
        select(VaultMember).where(VaultMember.vault_id == vault_id, VaultMember.user_id == user.id)
    )
    member = result.scalars().first()
    if not member:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not a member of this vault")

    if ROLE_HIERARCHY.get(member.role, 0) < ROLE_HIERARCHY.get(min_role, 0):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")

    return vault, member


# ---------------------------------------------------------------------------
# WebSocket authentication
# ---------------------------------------------------------------------------

async def authenticate_websocket(
    websocket: WebSocket, db: AsyncSession,
) -> User | None:
    """Authenticate a WebSocket via ticket, query param, or cookie.

    Returns the authenticated User, or None (after closing the socket).
    """
    user_id: str | None = None

    # 1. One-time WS ticket (frontend flow)
    ticket = websocket.query_params.get("ticket")
    if ticket:
        user_id = await consume_ws_ticket(ticket)

    # 2. Session ID query param (Streamlit cross-origin)
    if not user_id:
        session_id = websocket.query_params.get("session_id")
        if session_id:
            user_id = await get_session(session_id)

    # 3. Session cookie (same-origin)
    if not user_id:
        session_id = websocket.cookies.get(SESSION_COOKIE_NAME)
        if session_id:
            user_id = await get_session(session_id)

    if not user_id:
        await websocket.close(code=4001, reason="Not authenticated")
        return None

    try:
        parsed_id = UUID(user_id)
    except (ValueError, AttributeError):
        await websocket.close(code=4001, reason="Invalid session")
        return None

    result = await db.execute(select(User).where(User.id == parsed_id))
    user = result.scalars().first()
    if not user:
        await websocket.close(code=4001, reason="User not found")
        return None

    return user

