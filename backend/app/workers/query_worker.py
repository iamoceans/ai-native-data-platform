"""Query worker process entry point."""

from __future__ import annotations

from app.workers.common import run_query_worker


def main() -> None:
    run_query_worker()


if __name__ == "__main__":
    main()
