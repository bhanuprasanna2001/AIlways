"""Tests for GET /auth/me"""

from httpx import AsyncClient


class TestMeAuthenticated:

    async def test_returns_user_info(self, client: AsyncClient, auth_cookies):
        resp = await client.get(
            "/auth/me",
            cookies={"session_id": auth_cookies["session_id"]},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["email"] == "test@example.com"
        assert data["name"] == "Test User"
        assert "id" in data

    async def test_no_sensitive_fields(self, client: AsyncClient, auth_cookies):
        resp = await client.get(
            "/auth/me",
            cookies={"session_id": auth_cookies["session_id"]},
        )
        data = resp.json()
        assert "password" not in data
        assert "hashed_password" not in data


class TestMeUnauthenticated:

    async def test_no_cookie(self, client: AsyncClient):
        resp = await client.get("/auth/me")
        assert resp.status_code == 401

    async def test_invalid_session(self, client: AsyncClient):
        resp = await client.get(
            "/auth/me",
            cookies={"session_id": "totally-invalid-session"},
        )
        assert resp.status_code == 401
