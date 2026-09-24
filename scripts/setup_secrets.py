#!/usr/bin/env python3
"""Generate local secrets for the core/full stacks.

Creates or completes:
  - .env                                   (from .env.example, random passwords;
                                            missing keys are appended, existing
                                            values are never overwritten)
  - infra/local-secrets/source-postgres.json   (M1 provider credential file)
  - infra/local-secrets/source-mysql.json      (M2 MySQL provider credentials)
  - infra/local-secrets/doris.json             (M2 Doris provider credentials)
  - infra/local-secrets/platform.json          (cursor signing secret)
  - infra/local-secrets/llm_api_key            (model API key; paste yours here)

Idempotent. Stdlib only.
"""

from __future__ import annotations

import argparse
import json
import re
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_EXAMPLE = ROOT / ".env.example"
ENV_FILE = ROOT / ".env"
SECRETS_DIR = ROOT / "infra" / "local-secrets"

GENERATED_MARKER = "__GENERATED__"
PASSWORD_KEYS = {
    "CONTROL_DB_PASSWORD",
    "SOURCE_DB_PASSWORD",
    "SOURCE_BOOTSTRAP_PASSWORD",
    "MYSQL_ADMIN_PASSWORD",
    "MYSQL_READER_PASSWORD",
    "DORIS_ADMIN_PASSWORD",
    "DORIS_READER_PASSWORD",
}


def random_password() -> str:
    return secrets.token_urlsafe(24)


def parse_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$", line)
        if match and not line.strip().startswith("#"):
            values[match.group(1)] = match.group(2).strip()
    return values


def write_env(force: bool) -> str:
    if not ENV_EXAMPLE.exists():
        raise SystemExit(f"missing template: {ENV_EXAMPLE}")
    template_lines = ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()

    if force or not ENV_FILE.exists():
        rendered: list[str] = []
        for line in template_lines:
            match = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*" + GENERATED_MARKER + r"\s*$", line)
            if match:
                rendered.append(f"{match.group(1)}={random_password()}")
            else:
                rendered.append(line)
        ENV_FILE.write_text("\n".join(rendered) + "\n", encoding="utf-8")
        return "wrote .env"

    # Complete an existing .env: append keys that exist in the template but not
    # in the file. Existing values are never modified.
    existing = parse_env(ENV_FILE)
    appended: list[str] = []
    for line in template_lines:
        match = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$", line)
        if not match or line.strip().startswith("#"):
            continue
        key, value = match.group(1), match.group(2).strip()
        if key in existing:
            continue
        if value == GENERATED_MARKER:
            value = random_password()
        appended.append(f"{key}={value}")
    if not appended:
        return "kept existing .env (no missing keys)"
    with ENV_FILE.open("a", encoding="utf-8") as handle:
        handle.write("\n# --- added by setup-secrets ---\n")
        handle.write("\n".join(appended) + "\n")
    return f"completed .env with {len(appended)} missing key(s)"


def write_platform_secret(env: dict[str, str], force: bool) -> str:
    SECRETS_DIR.mkdir(parents=True, exist_ok=True)
    path = SECRETS_DIR / "platform.json"
    if path.exists() and not force:
        return "kept existing platform.json"
    payload = {"description": "Platform signing secret (pagination cursors)", "cursor_secret": random_password()}
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return "wrote platform.json"


def write_provider_secret(filename: str, description: str, username: str, password: str | None, force: bool) -> str:
    SECRETS_DIR.mkdir(parents=True, exist_ok=True)
    path = SECRETS_DIR / filename
    if path.exists() and not force:
        return f"kept existing {filename}"
    if not password:
        raise SystemExit(f"password for {filename} missing in .env; run again with --force after checking .env")
    payload = {"description": description, "username": username, "password": password}
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return f"wrote {filename}"


LLM_KEY_PLACEHOLDER = """# Paste the model API key on the line below (one line, no quotes, no "Bearer").
#
# DeepSeek: https://platform.deepseek.com/api_keys  ->  the key looks like sk-...
# Matching settings live in .env (AIND_LLM_PROVIDER / BASE_URL / MODEL /
# API_KEY_FILE). This directory is mounted read-only at /run/secrets inside the
# containers and is git-ignored. After pasting, verify with: make llm-check
PASTE_DEEPSEEK_API_KEY_HERE
"""


def write_llm_key_placeholder(force: bool) -> str:
    """Create the model-key file if it is missing, and never clobber a real key."""
    SECRETS_DIR.mkdir(parents=True, exist_ok=True)
    path = SECRETS_DIR / "llm_api_key"
    if path.exists():
        content = path.read_text(encoding="utf-8", errors="replace")
        if "PASTE_DEEPSEEK_API_KEY_HERE" in content or not content.strip():
            path.write_text(LLM_KEY_PLACEHOLDER, encoding="utf-8")
            return "rewrote llm_api_key placeholder"
        return "kept existing llm_api_key (a key is present; delete the file to reset)"
    path.write_text(LLM_KEY_PLACEHOLDER, encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:  # pragma: no cover - Windows hosts without POSIX modes
        pass
    return "wrote llm_api_key placeholder"


def main() -> int:
    parser = argparse.ArgumentParser(description="create local secrets")
    parser.add_argument("--force", action="store_true", help="overwrite existing files")
    args = parser.parse_args()

    print(write_env(force=args.force))
    env = parse_env(ENV_FILE)
    print(write_platform_secret(env, force=args.force))
    print(write_llm_key_placeholder(args.force))
    print(
        write_provider_secret(
            "source-postgres.json",
            "Read-only account for the demo source PostgreSQL (provider credential file)",
            env.get("SOURCE_DB_USER", "source_reader"),
            env.get("SOURCE_DB_PASSWORD"),
            args.force,
        )
    )
    print(
        write_provider_secret(
            "source-mysql.json",
            "Read-only account for the demo source MySQL (provider credential file)",
            env.get("MYSQL_READER_USER", "source_reader"),
            env.get("MYSQL_READER_PASSWORD"),
            args.force,
        )
    )
    print(
        write_provider_secret(
            "doris.json",
            "SELECT-only account for the demo Doris FE (provider credential file)",
            env.get("DORIS_READER_USER", "doris_reader"),
            env.get("DORIS_READER_PASSWORD"),
            args.force,
        )
    )
    print("\nNext: make doctor, then make up-core (or up-full)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
