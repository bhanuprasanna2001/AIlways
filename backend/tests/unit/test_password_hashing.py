"""Unit tests for password hashing utilities."""

from app.core.auth.security import hash_password, verify_password


class TestPasswordHashing:

    def test_hash_and_verify_correct_password(self):
        hashed = hash_password("Secret123")
        assert verify_password("Secret123", hashed)

    def test_wrong_password_fails_verification(self):
        hashed = hash_password("Secret123")
        assert not verify_password("Wrong123", hashed)

    def test_hash_is_not_plaintext(self):
        password = "Secret123"
        hashed = hash_password(password)
        assert hashed != password

    def test_same_password_produces_different_hashes(self):
        """Each hash should use a unique salt."""
        h1 = hash_password("Secret123")
        h2 = hash_password("Secret123")
        assert h1 != h2
