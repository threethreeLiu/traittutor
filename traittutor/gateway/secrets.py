"""Encrypted-at-rest provider secret helper for the internal gateway."""

from __future__ import annotations

import base64
import hashlib
import os

from cryptography.fernet import Fernet, InvalidToken


class GatewaySecretError(RuntimeError):
    pass


def _fernet() -> Fernet:
    raw = os.environ.get("TRAITTUTOR_GATEWAY_MASTER_KEY", "").strip()
    if not raw:
        raise GatewaySecretError(
            "TRAITTUTOR_GATEWAY_MASTER_KEY is required for hosted provider secrets."
        )
    try:
        return Fernet(raw.encode())
    except ValueError:
        derived = base64.urlsafe_b64encode(hashlib.sha256(raw.encode()).digest())
        return Fernet(derived)


def encrypt_secret(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt_secret(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise GatewaySecretError(
            "Gateway secret cannot be decrypted with the configured master key."
        ) from exc
