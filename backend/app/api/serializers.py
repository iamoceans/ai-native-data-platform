"""ORM -> API DTO serializers."""

from __future__ import annotations

import uuid

from app.models.orm import AuditLog, Permission, PermissionRequest, QueryJob, QueryResult, Role, SavedQuery, User


def query_to_detail(job: QueryJob, result: QueryResult | None) -> dict:
    return {
        "id": job.id,
        "datasource_id": job.datasource_id,
        "analysis_id": job.analysis_id,
        "status": job.status,
        "purpose": job.purpose,
        "sql": job.original_sql,
        "validated_sql": job.validated_sql,
        "parameters": job.bound_parameters or {},
        "sql_hash": job.sql_hash,
        "limits": job.limits or {},
        "policy_revision": int(job.policy_revision),
        "attempt": int(job.attempt),
        "error": job.error,
        "result": result_to_summary(result) if result is not None else None,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "cancel_requested_at": job.cancel_requested_at,
    }


def result_to_summary(result: QueryResult) -> dict:
    return {
        "id": result.id,
        "row_count": int(result.row_count),
        "byte_count": int(result.byte_count),
        "truncated": bool(result.truncated),
        "content_hash": result.content_hash,
        "created_at": result.created_at,
        "expires_at": result.expires_at,
    }


def user_to_response(user: User, roles: list[Role]) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "active": bool(user.active),
        "roles": [role_to_response(role) for role in roles],
        "created_at": user.created_at,
    }


def role_to_response(role: Role) -> dict:
    from app.constants import ROLE_CAPABILITIES

    return {
        "id": role.id,
        "name": role.name,
        "capabilities": sorted(ROLE_CAPABILITIES.get(role.name, frozenset())),
    }


def grant_to_response(permission: Permission) -> dict:
    return {
        "id": permission.id,
        "role_id": permission.role_id,
        "dataset_id": permission.dataset_id,
        "action": permission.action,
        "expires_at": permission.expires_at,
        "created_by": permission.created_by,
    }


def audit_to_response(row: AuditLog) -> dict:
    return {
        "id": int(row.id),
        "actor_id": row.actor_id,
        "action": row.action,
        "resource_type": row.resource_type,
        "resource_id": row.resource_id,
        "trace_id": row.trace_id,
        "outcome": row.outcome,
        "details": row.details or {},
        "created_at": row.created_at,
    }


def permission_request_to_response(row: PermissionRequest) -> dict:
    return {
        "id": row.id,
        "user_id": row.user_id,
        "dataset_id": row.dataset_id,
        "reason": row.reason,
        "status": row.status,
        "created_at": row.created_at,
    }


def saved_query_to_response(row: SavedQuery) -> dict:
    return {
        "id": row.id,
        "datasource_id": row.datasource_id,
        "title": row.title,
        "sql": row.sql_text,
        "created_at": row.created_at,
    }


def uuid_or_none(value: str | None) -> uuid.UUID | None:
    if not value:
        return None
    try:
        return uuid.UUID(value)
    except (ValueError, TypeError, AttributeError):
        return None
