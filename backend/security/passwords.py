from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    if not password:
        raise ValueError("Password must not be empty")
    return _hasher.hash(password)


def verify_password(password: str, encoded: str) -> bool:
    try:
        return _hasher.verify(encoded, password)
    except (VerifyMismatchError, VerificationError, TypeError):
        return False
