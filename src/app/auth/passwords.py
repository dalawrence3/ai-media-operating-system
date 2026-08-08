"""Argon2id password hashing via pwdlib.

Security invariants:
  - Argon2id is the only algorithm used. No bcrypt, MD5, or SHA-1.
  - Raw passwords are never logged, stored, or returned.
  - Verification is constant-time (pwdlib/Argon2 guarantee).
"""

from __future__ import annotations

from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher

_HASHER = PasswordHash((Argon2Hasher(),))


def hash_password(plaintext: str) -> str:
    """Return an Argon2id hash of `plaintext`. Never call with an empty string."""
    if not plaintext:
        raise ValueError("Password must not be empty")
    return _HASHER.hash(plaintext)


def verify_password(plaintext: str, hashed: str) -> bool:
    """Return True if `plaintext` matches the stored Argon2id `hashed` value.

    Returns False (not raises) on mismatch — callers translate to 401.
    """
    if not plaintext or not hashed:
        return False
    try:
        return _HASHER.verify(plaintext, hashed)
    except Exception:
        return False


def needs_rehash(hashed: str) -> bool:
    """Return True if the hash should be upgraded (e.g., Argon2 parameters changed)."""
    try:
        hasher = _HASHER.hashers[0]
        return hasher.check_needs_rehash(hashed)
    except Exception:
        return False
