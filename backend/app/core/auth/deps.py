from uuid import UUID
from sqlmodel import select, func
from sqlalchemy.ext.asyncio.session import AsyncSession
from fastapi import Cookie, Depends, HTTPException, Request, status

from app.db import get_db
from app.db.models import User, Vault, VaultMember
from app.core.config import get_settings
from app.core.tools.redis import get_session

SETTINGS = get_settings()

SESSION_COOKIE_NAME = SETTINGS.SESSION_COOKIE_NAME
CSRF_COOKIE_NAME = SETTINGS.CSRF_COOKIE_NAME


async def get_current_user(request: Request, db: AsyncSession = Depends(get_db)) -> User:
    """Dependency to get the current authenticated user based on the session cookie.

    Args:
        request: The incoming HTTP request.
        db: The database session.

    Returns:
        User: The authenticated user.
    """
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    if not session_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    user_id = await get_session(session_id)
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired or invalid")

    result = await db.execute(select(User).where(User.id == UUID(user_id)))
    user = result.scalars().first()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or invalid session")

    return user


async def require_csrf(request: Request):
    """Dependency to enforce CSRF protection on state-changing requests (POST, PUT, PATCH, DELETE).

    Args:
        request: The incoming HTTP request.
    
    Returns:
        None
    """
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
    """Verify the user is a member of the vault with at least the given role.

    Args:
        vault_id: The vault to check.
        user: The authenticated user.
        db: The database session.
        min_role: Minimum required role ('viewer', 'editor', or 'owner').

    Returns:
        tuple[Vault, VaultMember]: The vault and membership record.

    Raises:
        HTTPException: 404 if vault not found or soft-deleted, 403 if insufficient role.
    """
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

