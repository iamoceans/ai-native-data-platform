"""Runtime singletons shared by API and workers."""

from __future__ import annotations

from functools import lru_cache

from app.config import get_settings
from app.results.store import ResultStore

_store: ResultStore | None = None


def get_result_store() -> ResultStore:
    global _store
    if _store is None:
        _store = ResultStore(get_settings().results_dir)
    return _store


def set_result_store(store: ResultStore | None) -> None:
    global _store
    _store = store
