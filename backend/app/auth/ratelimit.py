"""In-memory login rate limiter (spec section 22).

Scope: attempts per (username, client ip) inside a moving window. Single API
process for M1; the deployment note in docs/security.md records that a shared
store is required once the API runs more than one replica.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from threading import Lock


class LoginRateLimiter:
    def __init__(self, max_attempts: int, window_seconds: int) -> None:
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self._attempts: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def _key(self, username: str, client: str) -> str:
        return f"{username.lower()}|{client}"

    def check(self, username: str, client: str) -> bool:
        """True when the caller may attempt another login."""
        now = time.monotonic()
        key = self._key(username, client)
        with self._lock:
            bucket = self._attempts[key]
            while bucket and now - bucket[0] > self.window_seconds:
                bucket.popleft()
            return len(bucket) < self.max_attempts

    def record_failure(self, username: str, client: str) -> None:
        now = time.monotonic()
        with self._lock:
            self._attempts[self._key(username, client)].append(now)

    def reset(self, username: str, client: str) -> None:
        with self._lock:
            self._attempts.pop(self._key(username, client), None)
