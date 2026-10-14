"""Reject oversized HTTP bodies before parsing or database dependencies run."""
from __future__ import annotations

import uuid

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.constants import ErrorCode


class RequestBodyLimitMiddleware:
    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def _reject(self, scope: Scope, receive: Receive, send: Send, *, malformed: bool = False) -> None:
        request_id = str((scope.get("state") or {}).get("request_id") or uuid.uuid4())
        response = JSONResponse(
            {"error": {"code": str(ErrorCode.VALIDATION_ERROR if malformed else ErrorCode.REQUEST_TOO_LARGE),
                       "message": "invalid Content-Length" if malformed else "HTTP request body exceeds the configured limit",
                       "retryable": False, "details": {"max_bytes": self.max_bytes}},
             "request_id": request_id, "trace_id": request_id},
            status_code=422 if malformed else 413,
            headers={"X-Request-Id": request_id, "X-Trace-Id": request_id},
        )
        await response(scope, receive, send)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        lengths = [value for name, value in scope.get("headers", []) if name.lower() == b"content-length"]
        if lengths:
            try:
                if len(lengths) != 1 or not lengths[0].isdigit():
                    raise ValueError("invalid length")
                declared = int(lengths[0])
            except ValueError:
                await self._reject(scope, receive, send, malformed=True)
                return
            if declared > self.max_bytes:
                await self._reject(scope, receive, send)
                return
        buffered = bytearray()
        total = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            total += len(message.get("body", b""))
            if total > self.max_bytes:
                await self._reject(scope, receive, send)
                return
            buffered.extend(message.get("body", b""))
            if not message.get("more_body", False):
                break
        body = bytes(buffered)
        buffered.clear()
        replayed = False
        async def replay() -> Message:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()
        await self.app(scope, replay, send)
