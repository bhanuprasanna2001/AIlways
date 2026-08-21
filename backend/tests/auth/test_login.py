"""Tests for POST /auth/login"""

from httpx import AsyncClient


class TestLoginSuccess:

    async def test_returns_success_message(self, client: AsyncClient, registered_user):
        resp = await client.post("/auth/login", json={
            "email": "test@example.com",
            "password": "ValidPass1",
        })
        assert resp.status_code == 200
        assert resp.json()["message"] == "Login successful"

    async def test_sets_session_cookie(self, client: AsyncClient, registered_user):
        resp = await client.post("/auth/login", json={
            "email": "test@example.com",
            "password": "ValidPass1",
        })
        assert "session_id" in resp.cookies

    async def test_sets_csrf_cookie(self, client: AsyncClient, registered_user):
        resp = await client.post("/auth/login", json={
            "email": "test@example.com",
            "password": "ValidPass1",
        })
        assert "csrf_token" in resp.cookies

    async def test_returns_user_info(self, client: AsyncClient, registered_user):
        resp = await client.post("/auth/login", json={
            "email": "test@example.com",
            "password": "ValidPass1",
        })
        user = resp.json()["user"]
        assert user["email"] == "test@example.com"
        assert user["name"] == "Test User"
        assert "id" in user

    async def test_no_password_in_response(self, client: AsyncClient, registered_user):
        resp = await client.post("/auth/login", json={
            "email": "test@example.com",
            "password": "ValidPass1",
        })
        assert resp.status_code == 200
        user_data = resp.json()["user"]
        assert "password" not in user_data
        assert "hashed_password" not in user_data

    async def test_email_is_case_insensitive(self, client: AsyncClient, registered_user):
        resp = await client.post("/auth/login", json={
            "email": "TEST@EXAMPLE.COM",
            "password": "ValidPass1",
        })
        assert resp.status_code == 200


class TestLoginFailure:

    async def test_wrong_password(self, client: AsyncClient, registered_user):
        resp = await client.post("/auth/login", json={
            "email": "test@example.com",
            "password": "WrongPass1",
        })
        assert resp.status_code == 401
        assert "invalid" in resp.json()["detail"].lower()

    async def test_nonexistent_email(self, client: AsyncClient):
        resp = await client.post("/auth/login", json={
            "email": "nobody@example.com",
            "password": "SomePass1",
        })
        assert resp.status_code == 401

    async def test_inactive_account_rejected(self, client: AsyncClient, inactive_user):
        resp = await client.post("/auth/login", json={
            "email": "inactive@example.com",
            "password": "ValidPass1",
        })
        assert resp.status_code == 403
        assert "disabled" in resp.json()["detail"].lower()

    async def test_wrong_password_same_error_as_wrong_email(self, client: AsyncClient, registered_user):
        """Wrong email and wrong password should return the same error to avoid user enumeration."""
        bad_email = await client.post("/auth/login", json={
            "email": "nobody@example.com",
            "password": "ValidPass1",
        })
        bad_pass = await client.post("/auth/login", json={
            "email": "test@example.com",
            "password": "WrongPass1",
        })
        assert bad_email.status_code == bad_pass.status_code
        assert bad_email.json()["detail"] == bad_pass.json()["detail"]
