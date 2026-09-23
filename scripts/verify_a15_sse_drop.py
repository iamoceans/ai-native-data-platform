#!/usr/bin/env python3
"""A15 live acceptance: drop an SSE client mid-stream and reconnect.

The in-process suite covers replay from a stored cursor; this script exercises
what actually happens when a client *disappears*: the TCP connection is reset
mid-stream (SO_LINGER 0, so the server sees an abort, not a graceful close), the
query keeps running with nobody listening, and a reconnect carrying
``Last-Event-ID`` has to resume exactly after the last delivered event - no gaps,
no duplicates, and the terminal event still delivered.

It runs against the deployed stack (uvicorn in the compose network) because a
real socket reset is the point of the test.

Usage:
  python scripts/verify_a15_sse_drop.py [--base-url http://127.0.0.1:8000]
      [--password <admin pw>] [--datasource source-postgres]
      [--host 127.0.0.1] [--port 8000]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import struct
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT, ROOT / "scripts"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from eval_agent import EvalError, ensure_datasource, open_authenticated_client  # noqa: E402

SQL = "SELECT COUNT(*) AS n FROM fixture_metrics"


def read_dotenv(name: str, default: str | None = None) -> str | None:
    if os.environ.get(name):
        return os.environ[name]
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith(f"{name}="):
                return line.split("=", 1)[1].strip()
    return default


def check(condition: bool, message: str) -> None:
    marker = "ok  " if condition else "FAIL"
    print(f"[{marker}] {message}", flush=True)
    if not condition:
        raise SystemExit(1)


def stream_socket(host: str, port: int, path: str, cookie: str, last_event_id: str | None):
    sock = socket.create_connection((host, port), timeout=20)
    headers = [
        f"GET {path} HTTP/1.1",
        f"Host: {host}:{port}",
        f"Cookie: {cookie}",
        "Accept: text/event-stream",
        "Connection: close",
    ]
    if last_event_id is not None:
        headers.append(f"Last-Event-ID: {last_event_id}")
    sock.sendall(("\r\n".join(headers) + "\r\n\r\n").encode())
    return sock


def read_until(sock: socket.socket, predicate, *, timeout: float = 30.0) -> str:
    """Read frames until predicate(buffer) is true; returns the raw text."""
    sock.settimeout(timeout)
    buffer = ""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            chunk = sock.recv(8192)
        except socket.timeout:
            break
        if not chunk:
            break
        buffer += chunk.decode("utf-8", errors="replace")
        if predicate(buffer):
            break
    return buffer


def reset_connection(sock: socket.socket) -> None:
    """Abort the connection: the peer gets an RST, not a FIN."""
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
    sock.close()


def parse_events(text: str) -> list[dict]:
    events: list[dict] = []
    current: dict = {}
    for line in text.split("\n"):
        line = line.rstrip("\r")
        if line.startswith("id: "):
            current["id"] = int(line[4:])
        elif line.startswith("event: "):
            current["event"] = line[7:]
        elif line == "" and current:
            events.append(current)
            current = {}
    return events


def main() -> int:
    parser = argparse.ArgumentParser(description="A15 live SSE drop/reconnect acceptance")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--username", default="admin")
    parser.add_argument("--password", default=read_dotenv("AIND_SMOKE_PASSWORD"))
    parser.add_argument("--datasource", default="source-postgres")
    args = parser.parse_args()
    if not args.password:
        raise SystemExit("admin password required (--password or AIND_SMOKE_PASSWORD)")

    client = open_authenticated_client(args.base_url, args.username, args.password)
    datasource_id = ensure_datasource(
        client,
        name=args.datasource,
        kind="postgres",
        config={
            "host": "source-postgres",
            "port": 5432,
            "database": "ainative_source",
            "ssl_mode": "disable",
            "connect_timeout_seconds": 5,
        },
        secret_ref="source-postgres",
        schema="public",
    )
    check(bool(datasource_id), f"datasource '{args.datasource}' is registered and reachable")
    cookie = "; ".join(f"{key}={value}" for key, value in client.cookies.items())

    submitted = client.post(
        "/api/v1/queries",
        json={"datasource_id": datasource_id, "sql": SQL, "limits": {"timeout_seconds": 60}},
        headers={"Idempotency-Key": f"a15-sse-{int(time.time())}"},
    )
    if submitted.status_code != 202:
        raise SystemExit(f"submit failed: HTTP {submitted.status_code} {submitted.text[:200]}")
    query_id = submitted.json()["query_id"]
    print(f"       query {query_id} submitted", flush=True)

    # 1. open the stream, take the first event, then abort the connection
    first = stream_socket(args.host, args.port, f"/api/v1/queries/{query_id}/events", cookie, None)
    # Match an SSE frame line, not the "x-request-id: " response header.
    def has_frame(buffer: str) -> bool:
        normalised = buffer.replace("\r\n", "\n")
        return "\nid: " in normalised or normalised.startswith("id: ")

    text = read_until(first, has_frame)
    first_events = parse_events(text)
    reset_connection(first)
    if not first_events:
        print("--- raw response from the first connection ---")
        print(text[:400].replace(chr(13), ""))
    check(bool(first_events), "the first connection delivered an event before the drop")
    last_seen = max(event["id"] for event in first_events)
    print(f"       connection reset after event id {last_seen}", flush=True)

    # 2. the query finishes with nobody listening
    deadline = time.monotonic() + 60
    status = ""
    while time.monotonic() < deadline:
        status = client.get(f"/api/v1/queries/{query_id}").json()["status"]
        if status in {"SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT", "LOST"}:
            break
        time.sleep(0.2)
    check(status == "SUCCEEDED", f"the query completed while the client was away (status={status})")

    # 3. reconnect with Last-Event-ID and resume
    second = stream_socket(
        args.host, args.port, f"/api/v1/queries/{query_id}/events", cookie, str(last_seen)
    )
    resumed_text = read_until(second, lambda buffer: "stream.end" in buffer, timeout=30)
    second.close()
    resumed = parse_events(resumed_text)
    # Only state events carry an id; the trailing stream.end frame does not.
    ids = [event["id"] for event in resumed if "id" in event]
    check(bool(ids), "the reconnect delivered the remaining events")
    check(min(ids) > last_seen, f"no event was re-delivered (first resumed id {min(ids)} > {last_seen})")
    check(
        ids == list(range(ids[0], ids[-1] + 1)),
        f"the resumed stream has no gaps ({len(ids)} events, ids {ids[0]}..{ids[-1]})",
    )
    check(
        any(event.get("event") == "query.finished" for event in resumed),
        "the terminal event was delivered after the reconnect",
    )
    delivered = [event["id"] for event in first_events]
    check(
        delivered + ids == list(range(delivered[0], ids[-1] + 1)),
        "the two connections together form one gapless sequence",
    )
    print("\na15-live: all checks passed (drop -> reconnect resumes with no gap or duplicate)")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except EvalError as exc:
        raise SystemExit(f"a15-live failed: {exc}")
