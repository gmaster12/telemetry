"""Encryption for tokens stored in SQLite. The key lives only in the environment."""
from __future__ import annotations

import os

from cryptography.fernet import Fernet, InvalidToken


class SecretBox:
    def __init__(self, key: str | None = None):
        key = key or os.environ.get("SECRET_KEY")
        if not key:
            raise RuntimeError(
                "SECRET_KEY is not set. Generate one with: "
                "python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
            )
        self.f = Fernet(key.encode())

    def seal(self, value: str | None) -> str | None:
        return None if not value else self.f.encrypt(value.encode()).decode()

    def open(self, value: str | None) -> str | None:
        if not value:
            return None
        try:
            return self.f.decrypt(value.encode()).decode()
        except InvalidToken:
            return None
