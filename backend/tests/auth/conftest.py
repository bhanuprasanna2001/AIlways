import pytest

from app.db.models.user import User
from app.core.auth.security import hash_password


# ---------------------------------------------------------------------------
# User fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
async def registered_user(db_session):
    """A normal active user already in the database."""
    user = User(
        name="Test User",
        email="test@example.com",
        hashed_password=hash_password("ValidPass1"),
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest.fixture()
async def inactive_user(db_session):
    """A user whose account has been disabled."""
    user = User(
        name="Inactive User",
        email="inactive@example.com",
        hashed_password=hash_password("ValidPass1"),
        is_active=False,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

@pytest.fixture()
async def auth_cookies(client, registered_user):
    """Login the registered user and return session + CSRF cookies."""
    resp = await client.post("/auth/login", json={
        "email": "test@example.com",
        "password": "ValidPass1",
    })
    assert resp.status_code == 200
    return {
        "session_id": resp.cookies["session_id"],
        "csrf_token": resp.cookies["csrf_token"],
    }
