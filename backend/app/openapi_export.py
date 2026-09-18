"""OpenAPI export.

Usage:
  python -m app.openapi_export                 # stdout (UTF-8)
  python -m app.openapi_export --output FILE   # write UTF-8 file (portable)

Writing the file inside Python avoids PowerShell's UTF-16 redirection, which
would corrupt the JSON for the TypeScript generator.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.main import create_app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="export the OpenAPI document")
    parser.add_argument("--output", "-o", type=Path, default=None)
    args = parser.parse_args(argv)

    payload = create_app().openapi()
    text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
        print(f"wrote {args.output} ({len(text)} bytes)")
    else:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
