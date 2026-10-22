#!/usr/bin/env python3
"""Hold every ordinary request permit and verify deployment probes stay bounded."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import socket
import sys
import tempfile
import time

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[1]
W78 = BASE.parent / "w7_w8_independent"
sys.path.insert(0, str(W78))
from fixture import PASSWORD, write  # noqa: E402
from receiver import Receiver  # noqa: E402
from service import ENV_NAME, Service  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    options = parser.parse_args()
    root = Path(tempfile.mkdtemp(prefix="diamond-w9-health-starvation-"))
    binary = root / "diamond"
    shutil.copy2(options.binary, binary)
    project = write(root / "project")
    service = Service(root, binary, project)
    receiver = Receiver()
    service.runtime["http"] = {
        "inventory": {
            "origin": receiver.origin,
            "network": "loopback",
            "paths": ["/v1/"],
            "methods": ["POST"],
        }
    }
    held = []
    observations = []
    error = None
    started = time.monotonic()

    def record(name: str, okay: bool, observed, expected: str) -> None:
        observations.append(
            {"name": name, "status": "PASS" if okay else "FAIL", "observed": observed, "expected": expected}
        )

    try:
        service.postgres()
        service.build()
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
        service.start_node("A")
        request = (
            "POST /_diamond/login HTTP/1.1\r\n"
            f"Host: {service.authority}\r\n"
            f"Origin: {service.origin}\r\n"
            "Content-Type: application/json\r\n"
            "Content-Length: 16000\r\n"
            "Connection: close\r\n\r\n"
        ).encode()
        for _ in range(16):
            stream = socket.create_connection(("127.0.0.1", service.ports["A"]), timeout=2)
            stream.settimeout(5)
            stream.sendall(request)
            held.append(stream)
        time.sleep(0.15)
        probes = {}
        for path in ["live", "metrics", "ready"]:
            before = time.monotonic()
            status, _, raw = service.request("A", "/_diamond/health/" + path)
            probes[path] = {
                "status": status,
                "elapsed_ms": round((time.monotonic() - before) * 1000, 3),
                "body_sha256": hashlib.sha256(raw).hexdigest(),
            }
        record(
            "deployment-probes-have-separate-bounded-admission",
            all(value["status"] == 200 and value["elapsed_ms"] < 2000 for value in probes.values()),
            probes,
            "live, metrics and ready stay responsive while all 16 ordinary permits await bounded request bodies",
        )
        time.sleep(3.2)
        recovered = {path: service.request("A", "/_diamond/health/" + path)[0] for path in ["live", "metrics", "ready"]}
        record(
            "ordinary-body-timeout-releases-capacity",
            recovered == {"live": 200, "metrics": 200, "ready": 200},
            recovered,
            "after the three-second body deadline all deployment probes are healthy",
        )
    except Exception as failure:
        error = repr(failure)
    finally:
        for stream in held:
            try:
                stream.close()
            except OSError:
                pass
        service.close()
        receiver.close()
        report = {
            "status": "PASS" if error is None and not service.cleanup_errors and all(row["status"] == "PASS" for row in observations) else "FAIL",
            "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
            "error": error,
            "observations": observations,
            "shutdowns": service.shutdowns,
            "cleanup_errors": service.cleanup_errors,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "scope": "one real app node, 16 incomplete ordinary HTTP bodies, actual deployment probes and isolated PostgreSQL",
        }
        shutil.rmtree(root)
        report["temporary_root_removed"] = not root.exists()
        options.report.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"status": report["status"], "observations": len(observations), "error": error}))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
