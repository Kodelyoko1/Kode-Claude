#!/usr/bin/env python3
"""
iMessage relay listener for a Wholesale Omniverse Mac bridge.

Run on a Mac that's signed into iCloud with Messages.app open. This HTTP
server accepts POST /send requests from the Linux side (this repo) and
shells out to osascript to deliver an actual iMessage.

Usage:
    export IMESSAGE_SECRET="<a long random string, also set on the Linux side>"
    python3 imessage_listener.py              # listens on :8787
    python3 imessage_listener.py --port 9090  # custom port

POST /send
    Headers: X-Auth: <IMESSAGE_SECRET>
    Body:    {"to": "+12073854041", "message": "deal closed $12,500"}
    Returns: {"status": "sent"} or {"status": "failed", "error": "..."}

GET /health
    Returns: {"ok": true, "ts": "..."}

stdlib only — no Flask, no aiohttp. Safe to drop on a fresh Mac.
"""
from __future__ import annotations

import argparse
import http.server
import json
import os
import socketserver
import subprocess
import sys
from datetime import datetime
from pathlib import Path

SCRIPT = Path(__file__).parent / "imessage_send.applescript"


def _send_imessage(to: str, message: str) -> dict:
    """Shell to osascript. Returns {status, ...}."""
    if not SCRIPT.exists():
        return {"status": "failed", "error": f"missing applescript at {SCRIPT}"}
    try:
        r = subprocess.run(
            ["osascript", str(SCRIPT), to, message],
            capture_output=True, text=True, timeout=15,
        )
        if r.returncode == 0:
            return {"status": "sent", "stdout": r.stdout.strip()}
        return {"status": "failed",
                "error": (r.stderr or r.stdout).strip()[:300]}
    except subprocess.TimeoutExpired:
        return {"status": "failed", "error": "osascript timeout"}
    except FileNotFoundError:
        return {"status": "failed", "error": "osascript not on PATH (Linux box?)"}


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "WO-iMessage-Relay/1.0"

    def _reply(self, code: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        sys.stderr.write(f"[{datetime.now():%H:%M:%S}] {fmt % args}\n")

    def do_GET(self):
        if self.path == "/health":
            self._reply(200, {"ok": True, "ts": datetime.now().isoformat()})
            return
        self._reply(404, {"error": "not_found"})

    def do_POST(self):
        if self.path != "/send":
            self._reply(404, {"error": "not_found"})
            return
        secret = os.environ.get("IMESSAGE_SECRET", "")
        if secret and self.headers.get("X-Auth", "") != secret:
            self._reply(401, {"error": "bad_secret"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length).decode() or "{}")
        except (ValueError, json.JSONDecodeError) as e:
            self._reply(400, {"error": f"bad_json: {e}"})
            return
        to = (body.get("to") or "").strip()
        message = body.get("message") or ""
        if not to or not message:
            self._reply(400, {"error": "to+message required"})
            return
        result = _send_imessage(to, message)
        self._reply(200 if result["status"] == "sent" else 502, result)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=8787)
    p.add_argument("--host", default="0.0.0.0")
    args = p.parse_args()

    if not os.environ.get("IMESSAGE_SECRET"):
        print("WARNING: IMESSAGE_SECRET not set — listener will accept any caller",
              file=sys.stderr)

    with socketserver.ThreadingTCPServer((args.host, args.port), Handler) as srv:
        print(f"iMessage relay listening on http://{args.host}:{args.port}", file=sys.stderr)
        print(f"AppleScript: {SCRIPT}", file=sys.stderr)
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            print("\nshutting down", file=sys.stderr)


if __name__ == "__main__":
    main()
