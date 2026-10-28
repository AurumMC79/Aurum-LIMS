"""Password hashing (Argon2id) and password policy checks."""

from __future__ import annotations

import secrets
import string

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_hasher = PasswordHasher()  # Argon2id with the library's RFC 9106 recommended parameters

# Hash of a random value, verified against when a username does not exist so a
# failed sign-in takes the same time whether or not the account exists.
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))

# Characters that are easy to tell apart when reading a temporary password aloud.
_TEMP_ALPHABET = "".join(c for c in string.ascii_letters + string.digits if c not in "0O1lI")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def verify_dummy(password: str) -> None:
    verify_password(_DUMMY_HASH, password)


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


def password_problems(password: str, *, min_length: int, username: str | None = None) -> list[str]:
    """Return the ways ``password`` breaks the policy (empty list = acceptable)."""
    problems = []
    if len(password) < min_length:
        problems.append(f"be at least {min_length} characters long")
    if not any(c.isalpha() for c in password):
        problems.append("contain at least one letter")
    if not any(c.isdigit() for c in password):
        problems.append("contain at least one number")
    if password != password.strip():
        problems.append("not start or end with a space")
    if username and username.lower() in password.lower():
        problems.append("not contain the username")
    return problems


def policy_description(min_length: int) -> str:
    return f"At least {min_length} characters, including a letter and a number. Must not contain your username."


def generate_temporary_password(length: int = 14) -> str:
    """A random password that satisfies the policy, for admins to hand out."""
    while True:
        candidate = "".join(secrets.choice(_TEMP_ALPHABET) for _ in range(length))
        if any(c.isalpha() for c in candidate) and any(c.isdigit() for c in candidate):
            return candidate
