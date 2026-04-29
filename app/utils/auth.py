"""Authentication utilities (PIN hashing, session tokens)."""

import secrets
from typing import Optional

import bcrypt


def hash_pin(pin: str) -> str:
    """Hash a PIN using bcrypt. Returns the hash as a UTF-8 string."""
    if not pin:
        return ""
    return bcrypt.hashpw(pin.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_pin(pin: str, pin_hash: Optional[str]) -> bool:
    """Verify a PIN against a stored hash."""
    if not pin_hash:
        # No PIN set - any PIN (or empty) is valid
        return True
    if not pin:
        return False
    try:
        return bcrypt.checkpw(pin.encode("utf-8"), pin_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def generate_session_token() -> str:
    """Generate a cryptographically secure session token."""
    return secrets.token_urlsafe(48)
