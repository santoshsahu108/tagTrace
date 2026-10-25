"""Encryption at rest for per-user Google credentials.

Credentials (the former ``secrets.json`` contents) are serialised to JSON and
encrypted with Fernet (AES-128-CBC + HMAC-SHA256) using ``TAG_CREDENTIALS_KEY``.
Only the ciphertext ever reaches the database; plaintext lives in memory of
the process that needs it.
"""

from __future__ import annotations

import json

from cryptography.fernet import Fernet, InvalidToken

from config import get_settings


class CredentialKeyError(RuntimeError):
    """TAG_CREDENTIALS_KEY is missing or malformed."""


def generate_key() -> str:
    """A new random key suitable for TAG_CREDENTIALS_KEY."""
    return Fernet.generate_key().decode()


def _fernet() -> Fernet:
    key = get_settings().tag_credentials_key
    if not key:
        raise CredentialKeyError(
            "TAG_CREDENTIALS_KEY is not set. Generate one with "
            "`python3 collector.py --generate-key` and put it in .env."
        )
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError) as exc:
        raise CredentialKeyError("TAG_CREDENTIALS_KEY is not a valid Fernet key") from exc


def encrypt_credentials(data: dict) -> str:
    """Encrypt a credentials dict to an opaque string for storage."""
    return _fernet().encrypt(json.dumps(data).encode()).decode()


def decrypt_credentials(token: str) -> dict:
    """Decrypt a stored credentials string back to a dict."""
    try:
        return json.loads(_fernet().decrypt(token.encode()))
    except InvalidToken as exc:
        raise CredentialKeyError(
            "stored credentials can't be decrypted (TAG_CREDENTIALS_KEY changed?)"
        ) from exc
