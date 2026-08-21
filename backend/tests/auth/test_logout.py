"""Tests for POST /auth/logout"""

from httpx import AsyncClient


class TestLogoutSuccess:

    async def test_returns_success_message(self, client: AsyncClient, auth_cookies):
        resp = await client.post(
            "/auth/logout",
            cookies=auth_cookies,
            headers={"X-CSRF-Token": auth_cookies["csrf_token"]},
        )
        assert resp.status_code == 200
        assert resp.json()["message"] == "Logout successful"

    async def test_session_invalid_after_logout(self, client: AsyncClient, auth_cookies):
        """After logout the old session cookie must not grant access."""
        await client.post(
            "/auth/logout",
            cookies=auth_cookies,
            headers={"X-CSRF-Token": auth_cookies["csrf_token"]},
        )
        resp = await client.get(
            "/auth/me",
            cookies={"session_id": auth_cookies["session_id"]},
        )
        assert resp.status_code == 401


class TestLogoutRequiresAuth:

    async def test_no_cookies_at_all(self, client: AsyncClient):
        """Without any cookies the request must be rejected."""
        resp = await client.post("/auth/logout")
        # CSRF check runs first → 403, or auth check → 401; either is acceptable
        assert resp.status_code in (401, 403)


class TestLogoutCSRF:

    async def test_missing_csrf_header(self, client: AsyncClient, auth_cookies):
        """Session cookie present but no CSRF header → rejected."""
        resp = await client.post(
            "/auth/logout",
            cookies={"session_id": auth_cookies["session_id"]},
        )
        assert resp.status_code == 403

    async def test_wrong_csrf_header(self, client: AsyncClient, auth_cookies):
        """CSRF cookie and header values don't match → rejected."""
        resp = await client.post(
            "/auth/logout",
            cookies=auth_cookies,
            headers={"X-CSRF-Token": "wrong-token-value"},
        )
        assert resp.status_code == 403
