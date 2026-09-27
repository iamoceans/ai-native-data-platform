"""Management CLI: `python -m app.cli <command>`.

bootstrap      roles, global capacity, policy state and the initial admin
create-user    additional local users
set-password   rotate a password

Initial passwords are printed to stdout once and never logged.
"""

from __future__ import annotations

import argparse
import os
import secrets
import sys

from sqlalchemy import select

from app.auth.passwords import hash_password
from app.config import get_settings
from app.constants import RoleName
from app.db import session_scope
from app.models.orm import Role, User
from app.repositories import datasources as datasources_repo
from app.repositories import policy as policy_repo
from app.repositories import users as users_repo


def _ensure_role(session, name: str) -> Role:
    role = users_repo.get_role_by_name(session, name)
    if role is None:
        role = users_repo.create_role(session, name)
        print(f"created role: {name}")
    return role


def cmd_bootstrap(args: argparse.Namespace) -> int:
    settings = get_settings()
    from app.metrics.registry import load_metric_definitions, sync_metric_definitions

    with session_scope() as session:
        roles = {name: _ensure_role(session, name) for name in RoleName}
        policy_repo.get_revision(session)
        datasources_repo.ensure_global_capacity(session, settings.global_concurrency)
        definitions = load_metric_definitions(settings.metadata_dir)
        metric_report = sync_metric_definitions(session, definitions)
        print(
            f"metric definitions: {len(definitions)} loaded "
            f"({metric_report['created']} created, {metric_report['updated']} updated)"
        )

        username = args.admin_username
        admin = users_repo.get_user_by_username(session, username)
        generated = False
        password = args.admin_password or os.environ.get("AIND_BOOTSTRAP_ADMIN_PASSWORD")
        if admin is None:
            if not password:
                password = secrets.token_urlsafe(16)
                generated = True
            admin = users_repo.create_user(session, username, hash_password(password))
            users_repo.set_user_roles(
                session, admin.id, [roles[RoleName.ADMIN].id, roles[RoleName.ANALYST].id]
            )
            datasources_repo.ensure_user_capacity(session, admin.id, settings.per_user_concurrency)
            print(f"created admin user: {username}")
            if generated:
                print(f"initial password (shown once): {password}")
        else:
            if password:
                admin.password_hash = hash_password(password)
                print(f"rotated password for admin user: {username}")
            print(f"admin user already exists: {username}")
    print("bootstrap complete")
    return 0


def cmd_create_user(args: argparse.Namespace) -> int:
    settings = get_settings()
    with session_scope() as session:
        existing = users_repo.get_user_by_username(session, args.username)
        if existing is not None:
            print(f"user already exists: {args.username}", file=sys.stderr)
            return 1
        role_ids = []
        for name in args.roles or [RoleName.VIEWER]:
            role = _ensure_role(session, name)
            role_ids.append(role.id)
        user = users_repo.create_user(session, args.username, hash_password(args.password))
        users_repo.set_user_roles(session, user.id, role_ids)
        datasources_repo.ensure_user_capacity(session, user.id, settings.per_user_concurrency)
        print(f"created user: {args.username} roles={','.join(args.roles or [RoleName.VIEWER])}")
    return 0


def cmd_load_metrics(args: argparse.Namespace) -> int:
    """Validate and publish metadata/metrics/*.yaml into metric_definitions."""
    settings = get_settings()
    from app.metrics.registry import load_metric_definitions, sync_metric_definitions

    definitions = load_metric_definitions(settings.metadata_dir)
    with session_scope() as session:
        report = sync_metric_definitions(session, definitions)
    print(f"metrics: {len(definitions)} definitions ({report['created']} new, {report['updated']} updated)")
    return 0


def cmd_set_password(args: argparse.Namespace) -> int:
    with session_scope() as session:
        user = users_repo.get_user_by_username(session, args.username)
        if user is None:
            print(f"no such user: {args.username}", file=sys.stderr)
            return 1
        user.password_hash = hash_password(args.password)
        print(f"password updated for: {args.username}")
    return 0


def cmd_llm_check(args: argparse.Namespace) -> int:
    """Prove the configured model endpoint works, with one structured request.

    Prints what the analysis runner would use (provider, endpoint, model, key
    file), then makes a single planning-shaped call and reports the model id it
    answered with, the latency and the token usage. No key material is printed.
    """
    import time

    from app.agent.budget import AnalysisBudget
    from app.agent.llm import resolve_provider
    from app.agent.planner import PlanningSelection
    from app.config import get_settings

    settings = get_settings()
    print(f"provider: {settings.llm_provider}")
    print(f"base_url: {settings.llm_base_url}")
    print(f"model:    {settings.llm_model or '(unset)'}")
    print(f"key file: {settings.llm_api_key_file or '(unset)'}")
    print(f"format:   {settings.llm_response_format}")

    resolution = resolve_provider(settings)
    if resolution.warning:
        print(f"warning:  {resolution.warning}")
    if resolution.provider is None:
        print("no model call was made: the deterministic template path is active")
        return 0 if settings.llm_provider == "fake" else 1

    started = time.monotonic()
    try:
        generated = resolution.provider.generate_structured(
            messages=[
                {
                    "role": "system",
                    "content": "Reply with one breakdown dimension from the allowlist.",
                },
                {
                    "role": "user",
                    "content": '{"allowed_dimensions": ["country", "platform"]}',
                },
            ],
            schema=PlanningSelection,
            budget=AnalysisBudget.start(),
            max_output_tokens=100,
        )
    except Exception as exc:  # noqa: BLE001 - reported to the administrator
        print(f"FAILED after {time.monotonic() - started:.2f}s: {type(exc).__name__}: {str(exc)[:300]}")
        return 1
    latency = time.monotonic() - started
    print(
        f"ok: model_id={generated.model_id} latency={latency:.2f}s "
        f"tokens_in={generated.usage.input_tokens} tokens_out={generated.usage.output_tokens} "
        f"finish={generated.finish_reason}"
    )
    print(f"parsed value: {generated.value.model_dump(mode='json')}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="app.cli", description="AI-Native Data Platform CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    bootstrap = sub.add_parser("bootstrap", help="create roles, capacities and the initial admin")
    bootstrap.add_argument("--admin-username", default="admin")
    bootstrap.add_argument("--admin-password", default=None)
    bootstrap.set_defaults(func=cmd_bootstrap)

    create_user = sub.add_parser("create-user", help="create a local user")
    create_user.add_argument("--username", required=True)
    create_user.add_argument("--password", required=True)
    create_user.add_argument("--role", dest="roles", action="append", default=[])
    create_user.set_defaults(func=cmd_create_user)

    llm_check = sub.add_parser(
        "llm-check", help="verify the configured LLM endpoint with one structured call"
    )
    llm_check.set_defaults(func=cmd_llm_check)

    set_password = sub.add_parser("set-password", help="rotate a local password")
    set_password.add_argument("--username", required=True)
    set_password.add_argument("--password", required=True)
    set_password.set_defaults(func=cmd_set_password)

    load_metrics = sub.add_parser("load-metrics", help="validate and publish metric definitions")
    load_metrics.set_defaults(func=cmd_load_metrics)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
