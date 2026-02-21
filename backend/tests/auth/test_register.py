"""Tests for POST /auth/register"""

from httpx import AsyncClient


class TestRegisterSuccess:

    async def test_returns_success_message(self, client: AsyncClient):
        resp = await client.post("/auth/register", json={
            "name": "John Doe",
            "email": "john@example.com",
            "password": "StrongPass1",
        })
        assert resp.status_code == 200
        assert resp.json()["message"] == "User registered successfully"

    async def test_email_stored_as_lowercase(self, client: AsyncClient):
        """Registering with UPPER-case email should normalise it to lowercase."""
        resp = await client.post("/auth/register", json={
            "name": "John",
            "email": "JOHN@EXAMPLE.COM",
            "password": "StrongPass1",
        })
        assert resp.status_code == 200

        # Registering again with the lowercase variant must fail (duplicate)
        resp2 = await client.post("/auth/register", json={
            "name": "Jane",
            "email": "john@example.com",
            "password": "StrongPass1",
        })
        assert resp2.status_code == 400


class TestRegisterDuplicateEmail:

    async def test_duplicate_email_rejected(self, client: AsyncClient, registered_user):
        resp = await client.post("/auth/register", json={
            "name": "Another User",
            "email": "test@example.com",
            "password": "StrongPass1",
        })
        assert resp.status_code == 400
        assert "already registered" in resp.json()["detail"].lower()


class TestRegisterPasswordValidation:

    async def test_password_too_short(self, client: AsyncClient):
        resp = await client.post("/auth/register", json={
            "name": "John",
            "email": "john@example.com",
            "password": "Short1",
        })
        assert resp.status_code == 422

    async def test_password_missing_uppercase(self, client: AsyncClient):
        resp = await client.post("/auth/register", json={
            "name": "John",
            "email": "john@example.com",
            "password": "alllower1",
        })
        assert resp.status_code == 422

    async def test_password_missing_digit(self, client: AsyncClient):
        resp = await client.post("/auth/register", json={
            "name": "John",
            "email": "john@example.com",
            "password": "NoDigitHere",
        })
        assert resp.status_code == 422


class TestRegisterInputValidation:

    async def test_empty_name_rejected(self, client: AsyncClient):
        resp = await client.post("/auth/register", json={
            "name": "   ",
            "email": "john@example.com",
            "password": "StrongPass1",
        })
        assert resp.status_code == 422

    async def test_invalid_email_rejected(self, client: AsyncClient):
        resp = await client.post("/auth/register", json={
            "name": "John",
            "email": "not-an-email",
            "password": "StrongPass1",
        })
        assert resp.status_code == 422

    async def test_missing_fields_rejected(self, client: AsyncClient):
        resp = await client.post("/auth/register", json={})
        assert resp.status_code == 422
