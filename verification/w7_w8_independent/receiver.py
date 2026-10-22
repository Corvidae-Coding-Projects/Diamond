"""Controlled external system with explicit receiver-side deduplication.

Diamond still performs actual outbound HTTP. This server independently records
the received request and applies its own destination idempotency contract.
"""
from __future__ import annotations

from collections import Counter
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading


class Receiver:
    """Owned loopback destination that can withhold an effect acknowledgment."""

    def __init__(self, *, key_header="Idempotency-Key", key_field="key"):
        self.lock = threading.Lock()
        self.recorded = threading.Event()
        self.release = threading.Event()
        self.attempts = []
        self.effects = {}
        self.counts = Counter()
        self.hold = False
        self.fail = False
        self.key_header, self.key_field = key_header, key_field
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                if length > 65536:
                    self.send_error(413)
                    return
                raw = self.rfile.read(length)
                try:
                    payload = json.loads(raw)
                    if isinstance(payload, str):
                        payload = json.loads(payload)
                except (ValueError, UnicodeDecodeError):
                    payload = None
                key = self.headers.get(owner.key_header)
                if not key and isinstance(payload, dict):
                    key = payload.get(owner.key_field)
                if self.path.startswith("/v1/dedup/"):
                    key = self.path.removeprefix("/v1/dedup/")
                deduplicated = self.path in {"/dedup", "/hold"} or self.path.startswith("/v1/dedup/")
                with owner.lock:
                    owner.attempts.append({"path": self.path, "key": key,
                                           "body_sha256": hashlib.sha256(raw).hexdigest(),
                                           "bytes": len(raw)})
                    owner.counts[self.path] += 1
                    failing = owner.fail or self.path == "/fail"
                    holding = owner.hold or self.path == "/hold"
                    if deduplicated and isinstance(key, str) and key and not failing:
                        owner.effects.setdefault(key, {"payload": payload,
                                                       "body_sha256": hashlib.sha256(raw).hexdigest()})
                    count = owner.counts[self.path]
                owner.recorded.set()
                if holding:
                    owner.release.wait(20)
                status = 503 if failing else 200
                if deduplicated and not key:
                    status = 422
                body = json.dumps("unavailable" if status == 503 else
                                  "missing idempotency key" if status == 422 else
                                  f"accepted:{key}:{count}").encode()
                try:
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        self.origin = f"http://127.0.0.1:{self.port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def snapshot(self):
        with self.lock:
            return {"attempts": list(self.attempts), "effect_keys": sorted(self.effects),
                    "effects": len(self.effects), "counts": dict(self.counts)}

    def close(self):
        self.release.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
