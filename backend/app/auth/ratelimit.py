"""Bounded single-process login rate limiter; see docs/security.md."""
from __future__ import annotations

import time
from collections import deque
from threading import Lock
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


class LoginRateLimiter:
    def __init__(self, max_attempts: int, window_seconds: int, max_keys: int = 10_000) -> None:
        if min(max_attempts, window_seconds, max_keys) < 1:
            raise ValueError("rate-limit bounds must be positive")
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self.max_keys = max_keys
        self._attempts: dict[str, deque[float]] = {}
        self._lock = Lock()
        self._next_cleanup = 0.0

    def _key(self, username: str, client: str) -> str:
        return f"{username.lower()}|{client}"

    def _trim(self, bucket: deque[float], now: float) -> None:
        while bucket and now - bucket[0] >= self.window_seconds:
            bucket.popleft()

    def _prune(self, now: float) -> None:
        if now < self._next_cleanup:
            return
        for key, bucket in list(self._attempts.items()):
            self._trim(bucket, now)
            if not bucket:
                del self._attempts[key]
        self._next_cleanup = now + min(self.window_seconds, 60)

    def check(self, username: str, client: str) -> bool:
        """Checks do not allocate; saturation denies new keys until expiry."""
        now = time.monotonic()
        key = self._key(username, client)
        with self._lock:
            self._prune(now)
            bucket = self._attempts.get(key)
            if bucket is None:
                return len(self._attempts) < self.max_keys
            self._trim(bucket, now)
            return len(bucket) < self.max_attempts

    def record_failure(self, username: str, client: str) -> None:
        now = time.monotonic()
        key = self._key(username, client)
        with self._lock:
            self._prune(now)
            bucket = self._attempts.get(key)
            if bucket is None:
                if len(self._attempts) >= self.max_keys:
                    return  # check() denies new keys while the bounded store is full.
                bucket = deque(maxlen=self.max_attempts)
                self._attempts[key] = bucket
            self._trim(bucket, now)
            bucket.append(now)

    def reset(self, username: str, client: str) -> None:
        with self._lock:
            self._attempts.pop(self._key(username, client), None)


class DatabaseLoginRateLimiter:
    """Failure windows shared by API processes, with bounded key allocation.

    check/record/reset must run in one transaction. Acquire this before user locks.
    Only first-use key allocation takes the capacity lock; existing keys only
    serialize the same username/client hash. Credentials never enter the table.
    """
    def __init__(self, session: Session, max_attempts: int, window_seconds: int, max_keys: int = 10_000):
        if min(max_attempts, window_seconds, max_keys) < 1:
            raise ValueError("rate-limit bounds must be positive")
        self.session, self.max_attempts, self.window_seconds, self.max_keys = session, max_attempts, window_seconds, max_keys
        self._rows = {}

    def _key(self, username: str, client: str) -> str:
        import hashlib
        return hashlib.sha256((username.lower() + "|" + client).encode()).hexdigest()

    def check(self, username: str, client: str) -> bool:
        from sqlalchemy import select, text, delete, func
        from datetime import timedelta
        from app.models.orm import AuthRateLimit
        key = self._key(username, client)
        # The resource class is independent of user/queue locks.
        row = self.session.get(AuthRateLimit, key)
        if row is None:
            self.session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended('auth-limit-capacity', 0))"))
        self.session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": "auth-limit:" + key})
        now = self.session.scalar(select(func.clock_timestamp()))
        row = self.session.get(AuthRateLimit, key, populate_existing=True)
        if row is None:
            self.session.execute(delete(AuthRateLimit).where(AuthRateLimit.expires_at <= now))
            if self.session.scalar(select(func.count()).select_from(AuthRateLimit)) >= self.max_keys:
                return False
            row = AuthRateLimit(key=key, failures=0, expires_at=now + timedelta(seconds=self.window_seconds))
            self.session.add(row)
        elif row.expires_at <= now:
            row.failures = 0
            row.expires_at = now + timedelta(seconds=self.window_seconds)
        self.session.flush()
        self._rows[key] = row
        return row.failures < self.max_attempts

    def record_failure(self, username: str, client: str) -> None:
        row = self._rows.get(self._key(username, client))
        if row is not None:
            row.failures += 1
            self.session.flush()

    def reset(self, username: str, client: str) -> None:
        row = self._rows.get(self._key(username, client))
        if row is not None:
            # Keep the allocated row until expiry: no delete/recreate race with
            # another replica holding its advisory key lock.
            row.failures = 0
            self.session.flush()
