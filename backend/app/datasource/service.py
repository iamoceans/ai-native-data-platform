"""Datasource registration, network policy and connection tests (spec sections 9, 22)."""

from __future__ import annotations

import ipaddress
import uuid

from sqlalchemy.orm import Session

from app.auth.sessions import AuthContext
from app.config import Settings
from app.constants import SUPPORTED_PROVIDER_KINDS_V1, ErrorCode
from app.datasource.config_models import validate_connection_config
from app.datasource.secrets import SecretResolver
from app.errors import ApiError
from app.models.orm import Datasource
from app.providers.base import ConnectionHealth, ProviderCredentials, QueryProvider
from app.providers.registry import get_registry
from app.repositories import audit as audit_repo
from app.repositories import datasources as datasources_repo


def _is_allowlisted(host: str, settings: Settings) -> bool:
    target = host.strip().lower()
    if not target or "/" in target or "\\" in target or "@" in target:
        return False
    for entry in settings.source_host_allowlist_list:
        candidate = entry.strip().lower()
        if not candidate:
            continue
        if candidate == target:
            return True
        try:
            network = ipaddress.ip_network(candidate, strict=False)
        except ValueError:
            continue
        try:
            if ipaddress.ip_address(target) in network:
                return True
        except ValueError:
            continue
    return False


def validate_host(host: str, settings: Settings) -> None:
    """Reject metadata endpoints, link-local ranges and anything off the
    administrator allowlist (spec section 22)."""
    target = host.strip().lower()
    if target in {"0.0.0.0", "metadata.google.internal", "metadata", "169.254.169.254"}:
        raise ApiError(ErrorCode.FORBIDDEN, "host is not allowed by the datasource network policy")
    try:
        ip = ipaddress.ip_address(target)
        if ip.is_link_local or ip.is_multicast or ip.is_unspecified:
            raise ApiError(ErrorCode.FORBIDDEN, "host is not allowed by the datasource network policy")
    except ValueError:
        pass
    if not _is_allowlisted(target, settings):
        raise ApiError(
            ErrorCode.FORBIDDEN,
            "host is not in AIND_SOURCE_HOST_ALLOWLIST",
            details={"host": target},
        )


def ensure_kind_supported(kind: str) -> None:
    if kind not in SUPPORTED_PROVIDER_KINDS_V1:
        raise ApiError(ErrorCode.DATASOURCE_UNSUPPORTED, f"unknown datasource kind '{kind}'")


def build_provider(
    datasource: Datasource, credentials: ProviderCredentials, settings: Settings
) -> QueryProvider:
    ensure_kind_supported(datasource.kind)
    return get_registry().create(datasource.kind, datasource, credentials, settings)


def create_datasource(
    session: Session,
    *,
    actor: AuthContext,
    name: str,
    kind: str,
    connection_config: dict,
    secret_ref: str,
    enabled: bool,
    settings: Settings,
    trace_id: str,
) -> Datasource:
    ensure_kind_supported(kind)
    try:
        validated_config = validate_connection_config(kind, connection_config)
    except KeyError:
        raise ApiError(ErrorCode.DATASOURCE_UNSUPPORTED, f"no config model for kind '{kind}'")
    except Exception as exc:  # pydantic validation error already carries detail
        raise ApiError(ErrorCode.VALIDATION_ERROR, f"invalid connection_config: {exc}")
    validate_host(validated_config["host"], settings)
    resolver = SecretResolver(settings.secrets_dir)
    if not resolver.exists(secret_ref):
        raise ApiError(
            ErrorCode.DATASOURCE_UNAVAILABLE,
            f"secret '{secret_ref}' is not mounted; place {secret_ref}.json in the secrets directory",
        )
    if datasources_repo.get_datasource_by_name(session, name) is not None:
        raise ApiError(ErrorCode.CONFLICT, f"datasource '{name}' already exists")
    row = datasources_repo.create_datasource(
        session,
        name=name,
        kind=kind,
        connection_config=validated_config,
        secret_ref=secret_ref,
        capabilities={},
        enabled=enabled,
        created_by=actor.user.id,
    )
    datasources_repo.ensure_datasource_capacity(session, row.id, settings.per_datasource_concurrency)
    audit_repo.add_audit(
        session,
        actor_id=actor.user.id,
        action="datasource.create",
        resource_type="datasource",
        resource_id=str(row.id),
        trace_id=trace_id,
        outcome="success",
        details={"name": name, "kind": kind, "host": validated_config["host"]},
    )
    return row


def update_datasource(
    session: Session,
    *,
    actor: AuthContext,
    datasource: Datasource,
    version: int,
    patch: dict,
    settings: Settings,
    trace_id: str,
) -> Datasource:
    if int(datasource.version) != int(version):
        raise ApiError(
            ErrorCode.CONFLICT,
            "datasource was modified by someone else",
            details={"expected_version": datasource.version, "provided_version": version},
        )
    applied: dict = {}
    if patch.get("name") is not None and patch["name"] != datasource.name:
        if datasources_repo.get_datasource_by_name(session, patch["name"]) is not None:
            raise ApiError(ErrorCode.CONFLICT, f"datasource '{patch['name']}' already exists")
        applied["name"] = patch["name"]
    if patch.get("connection_config") is not None:
        try:
            validated = validate_connection_config(datasource.kind, patch["connection_config"])
        except Exception as exc:
            raise ApiError(ErrorCode.VALIDATION_ERROR, f"invalid connection_config: {exc}")
        validate_host(validated["host"], settings)
        applied["connection_config"] = validated
    if patch.get("secret_ref") is not None and patch["secret_ref"] != datasource.secret_ref:
        resolver = SecretResolver(settings.secrets_dir)
        if not resolver.exists(patch["secret_ref"]):
            raise ApiError(
                ErrorCode.DATASOURCE_UNAVAILABLE,
                f"secret '{patch['secret_ref']}' is not mounted",
            )
        applied["secret_ref"] = patch["secret_ref"]
    if patch.get("enabled") is not None:
        applied["enabled"] = patch["enabled"]
    if applied:
        datasources_repo.update_datasource(session, datasource, **applied)
    audit_repo.add_audit(
        session,
        actor_id=actor.user.id,
        action="datasource.update",
        resource_type="datasource",
        resource_id=str(datasource.id),
        trace_id=trace_id,
        outcome="success",
        details={"fields": sorted(applied)},
    )
    return datasource


def test_connection(
    session: Session,
    *,
    actor: AuthContext | None,
    datasource: Datasource,
    settings: Settings,
    trace_id: str,
) -> tuple[ConnectionHealth, dict]:
    resolver = SecretResolver(settings.secrets_dir)
    try:
        credentials = resolver.resolve(datasource.secret_ref)
    except ApiError as exc:
        health = ConnectionHealth(status="UNAVAILABLE", error={"code": exc.code, "message": exc.message})
        return health, {}
    provider = build_provider(datasource, credentials, settings)
    health = provider.test_connection()
    capabilities = provider.capabilities().as_dict() if health.status == "HEALTHY" else {}
    datasources_repo.update_datasource(
        session,
        datasource,
        health_status=health.status,
        capabilities=capabilities or datasource.capabilities,
    )
    if actor is not None:
        audit_repo.add_audit(
            session,
            actor_id=actor.user.id,
            action="datasource.test",
            resource_type="datasource",
            resource_id=str(datasource.id),
            trace_id=trace_id,
            outcome=health.status,
            details={"latency_ms": health.latency_ms},
        )
    return health, capabilities


def datasource_to_dict(row: Datasource) -> dict:
    return {
        "id": row.id,
        "name": row.name,
        "kind": row.kind,
        "connection_config": row.connection_config,
        "secret_ref": row.secret_ref,
        "capabilities": row.capabilities,
        "enabled": row.enabled,
        "health_status": row.health_status,
        "created_by": row.created_by,
        "created_at": row.created_at,
        "version": row.version,
    }
