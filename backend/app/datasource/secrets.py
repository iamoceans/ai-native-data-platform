"""SecretResolver (spec section 8.1).

Maps a pre-registered logical ``secret_ref`` to a fixed mounted file. User
input can never select a path, environment variable or arbitrary file: the
reference must match a strict pattern and resolves inside the secrets
directory only.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from app.constants import ErrorCode
from app.errors import ApiError
from app.providers.base import ProviderCredentials

_REF_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,63}$")


class SecretResolver:
    def __init__(self, base_dir: Path) -> None:
        self._base = base_dir.resolve()

    @property
    def base_dir(self) -> Path:
        return self._base

    def _path_for(self, secret_ref: str) -> Path:
        if not _REF_PATTERN.match(secret_ref or ""):
            raise ApiError(
                ErrorCode.VALIDATION_ERROR,
                "secret_ref must match ^[A-Za-z0-9][A-Za-z0-9._-]{2,63}$",
            )
        if any(sep in secret_ref for sep in ("/", "\\", "..")):
            raise ApiError(ErrorCode.VALIDATION_ERROR, "secret_ref must be a logical name")
        candidate = (self._base / f"{secret_ref}.json").resolve()
        if self._base != candidate.parent:
            raise ApiError(ErrorCode.FORBIDDEN, "secret_ref escapes the secrets directory")
        return candidate

    def exists(self, secret_ref: str) -> bool:
        try:
            return self._path_for(secret_ref).is_file()
        except ApiError:
            return False

    def resolve(self, secret_ref: str) -> ProviderCredentials:
        path = self._path_for(secret_ref)
        if not path.is_file():
            raise ApiError(
                ErrorCode.DATASOURCE_UNAVAILABLE,
                f"secret '{secret_ref}' is not mounted on this host",
                details={"secret_ref": secret_ref},
            )
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ApiError(
                ErrorCode.DATASOURCE_UNAVAILABLE,
                f"secret '{secret_ref}' is unreadable or malformed",
                details={"reason": type(exc).__name__},
            ) from exc
        username = payload.get("username")
        password = payload.get("password")
        if not isinstance(username, str) or not isinstance(password, str):
            raise ApiError(
                ErrorCode.DATASOURCE_UNAVAILABLE,
                f"secret '{secret_ref}' must contain string username and password",
            )
        return ProviderCredentials(username=username, password=password)
