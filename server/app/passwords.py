import secrets

from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error, VerifyMismatchError

password_hasher = PasswordHasher(
    time_cost=3,
    memory_cost=32_768,
    parallelism=1,
    hash_len=32,
    salt_len=16,
)
_DUMMY_PASSWORD_HASH = password_hasher.hash(secrets.token_urlsafe(32))


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(password: str, encoded_hash: str | None) -> bool:
    try:
        password_hasher.verify(encoded_hash or _DUMMY_PASSWORD_HASH, password)
    except (VerifyMismatchError, Argon2Error, ValueError):
        return False
    return bool(encoded_hash)
