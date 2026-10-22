#!/usr/bin/env python3
"""Exercise a live stale job holder across the real 30-second lease boundary."""
from __future__ import annotations

import argparse
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import sys
import tempfile
import threading
import time

BASE = Path(__file__).resolve().parent
PREVIOUS = BASE.parent / "w7_w8_independent"
sys.path.insert(0, str(PREVIOUS))
from fixture import PASSWORD, app_id, identifier, write  # noqa: E402
from service import Client, ENV_NAME, Service, sha256  # noqa: E402

spec = importlib.util.spec_from_file_location("_w9_stale_common", BASE.parent / "w5_independent/common.py")
previous = importlib.util.module_from_spec(spec)
spec.loader.exec_module(previous)
Receipt, quote, seed, wait_for = previous.Receipt, previous.quote, previous.seed, previous.wait_for


class HeldReceiver:
    """Real loopback HTTP destination with receiver-owned stable-key dedup."""

    def __init__(self):
        self.lock = threading.Lock()
        self.changed = threading.Condition(self.lock)
        self.release = threading.Event()
        self.attempts: list[dict] = []
        self.effects: dict[str, dict] = {}
        self.counts = Counter()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(length)
                key = self.path.removeprefix("/v1/dedup/") if self.path.startswith("/v1/dedup/") else ""
                with owner.changed:
                    owner.attempts.append(
                        {
                            "path": self.path,
                            "key": key,
                            "body_sha256": hashlib.sha256(raw).hexdigest(),
                            "bytes": len(raw),
                        }
                    )
                    owner.counts[self.path] += 1
                    if key:
                        owner.effects.setdefault(key, {"body_sha256": hashlib.sha256(raw).hexdigest()})
                    owner.changed.notify_all()
                owner.release.wait(60)
                body = json.dumps("accepted:" + key).encode()
                try:
                    self.send_response(200 if key else 422)
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

    def wait_attempts(self, count: int, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        with self.changed:
            while len(self.attempts) < count:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self.changed.wait(remaining)
            return True

    def snapshot(self) -> dict:
        with self.lock:
            return {
                "attempts": list(self.attempts),
                "attempt_count": len(self.attempts),
                "effect_keys": sorted(self.effects),
                "effects": len(self.effects),
                "counts": dict(self.counts),
            }

    def close(self):
        self.release.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--report", type=Path, default=BASE / "stale_holder_results.json")
    options = parser.parse_args()
    root = Path(tempfile.mkdtemp(prefix="diamond-w9-stale-holder-"))
    binary = root / "diamond"
    shutil.copy2(options.binary, binary)
    project = write(root / "project")
    service = Service(root, binary, project)
    receiver = None
    receipt = Receipt()
    receipt.secret(PASSWORD)
    fatal = None
    first_stopped = False
    started = time.monotonic()

    def row(job_id: str) -> dict:
        rows = service.sql_json(
            "SELECT id,state,attempts,fence,lease::text,(lease<clock_timestamp()) AS expired,outcome "
            "FROM diamond_w7.jobs WHERE app=" + quote(app_id()) + " AND id=" + quote(job_id)
        )
        if len(rows) != 1:
            raise AssertionError(("missing job", job_id, rows))
        return rows[0]

    try:
        receiver = HeldReceiver()
        service.runtime["http"] = {
            "inventory": {
                "origin": receiver.origin,
                "network": "loopback",
                "paths": ["/v1/"],
                "methods": ["POST"],
                "timeout_ms": 5000,
            }
        }
        service.postgres()
        contract, _build = service.build()
        service.cli("project-init", project, "--postgres-env", ENV_NAME)
        service.files_cli(initialize=True)
        service.cli(
            "project-admin-add",
            project,
            "--postgres-env",
            ENV_NAME,
            "--user",
            "alice",
            "--email",
            "alice@qualification.invalid",
            "--password-stdin",
            input=PASSWORD + "\n",
        )
        seed(service)
        service.start_node("A")
        alice = Client(service, contract["rpc_id"])
        alice.login("alice", PASSWORD)
        submitted = alice.rpc("submit", identifier("issue-red-one"), "stale holder payload", "stale-holder-key")
        if submitted[0] != 200:
            raise AssertionError(("submit rejected", submitted))
        job_id = submitted[1]["value"]
        service.start_worker("first")
        if not receiver.wait_attempts(1, 12):
            raise AssertionError("first worker did not reach held receiver")
        wait_for(lambda: row(job_id)["state"] == "running", "first claim visible", timeout=4)
        first_claim = row(job_id)
        first_pid = service.workers["first"].pid
        os.kill(first_pid, signal.SIGSTOP)
        first_stopped = True
        lease_wait_started = time.monotonic()
        wait_for(lambda: bool(row(job_id)["expired"]), "database-clock lease expiry", timeout=36)
        expired = row(job_id)
        service.start_worker("second")
        second_reached_before_release = receiver.wait_attempts(2, 3)
        before_release = row(job_id)
        receiver.release.set()
        os.kill(first_pid, signal.SIGCONT)
        first_stopped = False
        wait_for(lambda: row(job_id)["state"] == "done", "current fenced attempt completes", timeout=18)
        final = row(job_id)
        snapshot = receiver.snapshot()
        receipt.record(
            "live-stale-holder-lease-and-fence-race",
            first_claim["state"] == "running"
            and first_claim["attempts"] == 1
            and expired["expired"] is True
            and final["state"] == "done"
            and final["attempts"] == 2
            and final["fence"] >= 2
            and snapshot["attempt_count"] == 2
            and snapshot["effects"] == 1
            and snapshot["effect_keys"] == [job_id]
            and len({attempt["body_sha256"] for attempt in snapshot["attempts"]}) == 1,
            {
                "first_claim": first_claim,
                "expired": expired,
                "second_reached_receiver_before_first_release": second_reached_before_release,
                "before_release": before_release,
                "final": final,
                "receiver": snapshot,
                "lease_wait_seconds": time.monotonic() - lease_wait_started,
            },
            "after a process is stopped beyond its real lease, only the current fence completes; the stable key/body may repeat but receiver dedup records one business effect",
        )
        post_status = alice.rpc("status", job_id)
        parsed = json.loads(post_status[1]["value"]) if post_status[0] == 200 else {}
        receipt.record(
            "stale-holder-final-state-visible-through-owner-api",
            post_status[0] == 200 and parsed == {"state": "done", "attempts": 2, "outcome": "completed"},
            {"rpc": post_status, "parsed": parsed},
            "the owner-visible status reports the current two-attempt completion rather than an obsolete holder outcome",
        )
    except Exception as error:
        fatal = repr(error)
    finally:
        if receiver is not None:
            receiver.release.set()
        if first_stopped and "first" in service.workers:
            try:
                os.kill(service.workers["first"].pid, signal.SIGCONT)
            except ProcessLookupError:
                pass
        service.close()
        if receiver is not None:
            receiver.close()
    report = receipt.report(
        service,
        sha256(binary),
        error=fatal,
        elapsed_seconds=time.monotonic() - started,
        command=f"python3 verification/w9_independent/stale_holder.py --binary {options.binary}",
        oracle="ORACLES.md J1",
        injected_schedule="SIGSTOP first worker after receiver effect; wait actual DB lease expiry; start second; release receiver; SIGCONT first",
        temporary_root_removed=True,
    )
    options.report.parent.mkdir(parents=True, exist_ok=True)
    options.report.write_text(json.dumps(report, indent=2) + "\n")
    shutil.rmtree(root)
    print(json.dumps({"status": report["status"], "observations": len(receipt.results), "error": fatal, "elapsed_seconds": report["elapsed_seconds"]}))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
