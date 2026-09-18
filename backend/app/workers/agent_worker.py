"""Agent worker process entry point for reconciliation and governed analyses."""

from __future__ import annotations

from app.workers.common import run_reconciler_loop


def main() -> None:
    run_reconciler_loop()


if __name__ == "__main__":
    main()
