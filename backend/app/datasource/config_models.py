"""Datasource connection configuration models (non-secret fields only).

Spec section 8.1: `connection_config` holds host/port/database/TLS/connect
timeout - never credentials. One model per engine so the API rejects fields
the provider does not support.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PostgresConnectionConfig(_Strict):
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(default=5432, ge=1, le=65535)
    database: str = Field(min_length=1, max_length=128)
    ssl_mode: Literal["disable", "prefer", "require", "verify-ca", "verify-full"] = "prefer"
    connect_timeout_seconds: int = Field(default=5, ge=1, le=30)


class MySQLConnectionConfig(_Strict):
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(default=3306, ge=1, le=65535)
    database: str = Field(min_length=1, max_length=128)
    connect_timeout_seconds: int = Field(default=5, ge=1, le=30)


class DorisConnectionConfig(_Strict):
    """Doris FE MySQL-protocol endpoint. Catalog is always ``internal`` in V1;
    external catalogs are rejected by the provider (spec section 7)."""

    host: str = Field(min_length=1, max_length=255)
    port: int = Field(default=9030, ge=1, le=65535)
    database: str = Field(min_length=1, max_length=128)
    connect_timeout_seconds: int = Field(default=5, ge=1, le=30)


CONFIG_MODELS = {
    "postgres": PostgresConnectionConfig,
    "mysql": MySQLConnectionConfig,
    "doris": DorisConnectionConfig,
}


def validate_connection_config(kind: str, payload: dict) -> dict:
    model = CONFIG_MODELS.get(kind)
    if model is None:
        raise KeyError(kind)
    return model.model_validate(payload).model_dump()
