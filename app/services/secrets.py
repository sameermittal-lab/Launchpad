"""Encrypt/decrypt sensitive data at rest.

Uses Fernet symmetric encryption. The key is derived from a machine-local
key file so that exports (JSON/ZIP without the key file) cannot be decrypted
on another machine.
"""

from pathlib import Path
from typing import Optional

from cryptography.fernet import Fernet

from app.config import settings


_KEY_FILE = Path(".launchpad.key")


def _get_or_create_key() -> bytes:
    """Load the machine-local encryption key, creating it if missing."""
    key_path = settings.base_dir / _KEY_FILE
    if key_path.exists():
        return key_path.read_bytes()

    key = Fernet.generate_key()
    key_path.write_bytes(key)
    # On Unix, make the key file readable only by owner
    try:
        key_path.chmod(0o600)
    except (OSError, NotImplementedError):
        pass  # Windows may not support chmod
    return key


_fernet: Optional[Fernet] = None


def _cipher() -> Fernet:
    global _fernet
    if _fernet is None:
        _fernet = Fernet(_get_or_create_key())
    return _fernet


def encrypt(plaintext: str) -> str:
    """Encrypt a string. Returns base64-encoded ciphertext."""
    if not plaintext:
        return ""
    return _cipher().encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt(ciphertext: str) -> str:
    """Decrypt a base64-encoded ciphertext. Returns plaintext."""
    if not ciphertext:
        return ""
    return _cipher().decrypt(ciphertext.encode("utf-8")).decode("utf-8")
