from pwdlib import PasswordHash

# Algorithm: argon2
password_hasher = PasswordHash.recommended()


def hash_password(password: str) -> str:
    """Hash a plaintext password.

    Args:
        password (str): The plaintext password to hash.

    Returns:
        str: The hashed password.
    """
    return password_hasher.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plaintext password against a hashed password.

    Args:
        plain_password (str): The plaintext password to verify.
        hashed_password (str): The hashed password to verify against.

    Returns:
        bool: True if the passwords match, False otherwise.
    """
    return password_hasher.verify(plain_password, hashed_password)