#!/usr/bin/env python3
"""Real adapter/runtime qualification for declarative secret injection and rotation."""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[1]
SUPPORT = ROOT / "verification" / "w7_w8_independent"
sys.path.insert(0, str(SUPPORT))
from service import Client, ENV_NAME, RUNTIME_NAME, Service, sha256  # noqa: E402

PASSWORD = "Synthetic L6 secret qualification password 96!"
SECRET_ENV = "DIAMOND_L6_INVENTORY_SECRET"
FIRST = "synthetic-l6-first-bearer"
SECOND = "synthetic-l6-rotated-bearer"


class Capture:
    def __init__(self):
        self.expected = FIRST
        self.lock = threading.Lock()
        self.records = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def reply(self):
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length)
                with owner.lock:
                    owner.records.append({"authorized": self.headers.get("Authorization") ==
                                          "Bearer " + owner.expected,
                                          "method": self.command, "path": self.path,
                                          "body": body.decode(errors="replace")})
                encoded = json.dumps("accepted").encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            do_GET = reply
            do_POST = reply

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.origin = "http://127.0.0.1:" + str(self.server.server_address[1])
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def snapshot(self):
        with self.lock:
            return list(self.records)

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def value(outcome):
    if outcome[0] != 200 or outcome[1].get("value") != "accepted":
        raise AssertionError(("expected accepted RPC", outcome))
    return outcome[1]["value"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, default=ROOT / "target/debug/diamond")
    parser.add_argument("--report", type=Path,
                        default=ROOT / "verification" / "secure_language_l6_runtime_results.json")
    options = parser.parse_args()
    workspace = ROOT / ".diamond"
    workspace.mkdir(mode=0o700, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="l6-secret-runtime-", dir=workspace))
    binary, project_dir, mail_root = root / "diamond", root / "project", root / "mail"
    shutil.copy2(options.binary.resolve(), binary)
    shutil.copytree(ROOT / "examples" / "functions" / "authority", project_dir)
    mail_root.mkdir(mode=0o700)
    project = project_dir / "diamond.toml"
    service = Service(root, binary, project)
    capture = Capture()
    observations, fatal = [], None

    def record(name, okay, observed, expected):
        observations.append({"name": name, "status": "PASS" if okay else "FAIL",
                             "observed": observed, "expected": expected})
        if not okay:
            raise AssertionError((name, observed, expected))

    try:
        service.runtime["http"] = {"inventory": {"origin": capture.origin,
            "network": "loopback", "paths": ["/v1/"], "methods": ["GET", "POST"],
            "credential_env": SECRET_ENV}}
        service.runtime["mail"] = {"document_mail": {"root": str(mail_root),
                                                       "from": "noreply@example.invalid"}}
        service.environment[SECRET_ENV] = FIRST
        service.postgres()
        contract, build = service.build()
        server_source = (project_dir / "server.dia").read_text()
        artifact = (build / "contract.json").read_text()
        record("build-inventory-excludes-deployment-secret", "inventory_bearer" in artifact and
               '"kind": "bearer"' in artifact and SECRET_ENV not in artifact and
               FIRST not in artifact and "host.secret" not in server_source,
               {"inventory": "inventory_bearer" in artifact,
                "environment_name_absent": SECRET_ENV not in artifact,
                "value_absent": FIRST not in artifact, "source_handle_absent": "host.secret" not in server_source},
               "artifact inventories kind/audience while source and artifacts contain no handle, environment name, or value")
        service.cli("project-init", project, "--postgres-env", ENV_NAME)
        service.files_cli(initialize=True)
        service.cli("project-mail-init", project, "--postgres-env", ENV_NAME,
                    "--runtime-env", RUNTIME_NAME)
        service.cli("project-admin-add", project, "--postgres-env", ENV_NAME, "--user", "alice",
                    "--email", "alice@qualification.invalid", "--password-stdin", input=PASSWORD + "\n")
        service.start_node("A")
        alice = Client(service, contract["rpc_id"])
        alice.login("alice", PASSWORD)
        value(alice.rpc("external_get", "/v1/read"))
        value(alice.rpc("external_post", "/v1/write", "bounded body"))
        first = capture.snapshot()
        record("adapter-injects-declared-bearer", len(first) == 2 and
               all(item["authorized"] for item in first) and
               [item["method"] for item in first] == ["GET", "POST"],
               {"requests": first},
               "native adapter injects the bound credential for both calls without a source-visible value")

        service.stop_node("A")
        capture.expected = SECOND
        service.environment[SECRET_ENV] = SECOND
        service.start_node("A")
        rotated = Client(service, contract["rpc_id"])
        rotated.login("alice", PASSWORD)
        value(rotated.rpc("external_get", "/v1/rotated"))
        after = capture.snapshot()
        record("restart-captures-rotated-generation", len(after) == 3 and after[-1]["authorized"],
               {"requests": len(after), "rotated_authorized": after[-1]["authorized"]},
               "a new runtime captures and uses the rotated secret without application-code changes")

        service.stop_node("A")
        service.environment.pop(SECRET_ENV)
        missing = service.cli("project-work", project, "--postgres-env", ENV_NAME,
                              "--runtime-env", RUNTIME_NAME, success=False, timeout=10)
        diagnostic = missing.stdout + missing.stderr
        record("missing-declared-secret-fails-closed-and-redacted", missing.returncode != 0 and
               FIRST not in diagnostic and SECOND not in diagnostic and SECRET_ENV not in diagnostic,
               {"exit": missing.returncode, "redacted": FIRST not in diagnostic and
                SECOND not in diagnostic and SECRET_ENV not in diagnostic},
               "worker startup denies missing deployment authority without disclosing value or environment name")
    except Exception as error:
        fatal = repr(error)
    finally:
        capture.close()
        service.close()
        report = {"status": "FAIL" if fatal or service.cleanup_errors or
                  any(item["status"] != "PASS" for item in observations) else "PASS",
                  "binary_sha256": sha256(binary), "observations": observations,
                  "fatal": fatal, "shutdowns": service.shutdowns,
                  "cleanup_errors": service.cleanup_errors,
                  "scope": "Disposable loopback receiver, owned PostgreSQL, and real server/worker startup; no Internet destination."}
        options.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        shutil.rmtree(root)
    print(json.dumps({"status": report["status"], "observations": len(observations), "fatal": fatal}))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
