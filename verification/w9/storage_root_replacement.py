#!/usr/bin/env python3
"""Replay replacement of a live shared-object root through real file APIs."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys
import tempfile

BASE = Path(__file__).resolve().parent
PREVIOUS = BASE.parent / "w7_w8_independent"
sys.path.insert(0, str(PREVIOUS))
from fixture import PASSWORD, identifier, write  # noqa: E402
from receiver import Receiver  # noqa: E402
from service import Client, ENV_NAME, Service, sha256  # noqa: E402

import importlib.util

spec = importlib.util.spec_from_file_location("_w9_storage_common", BASE.parent / "w5_independent/common.py")
common = importlib.util.module_from_spec(spec)
spec.loader.exec_module(common)

PREFIX = "/_diamond/files/issue_files/"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--report", type=Path, default=BASE / "storage_root_replacement_results.json")
    options = parser.parse_args()
    root = Path(tempfile.mkdtemp(prefix="diamond-w9-storage-root-"))
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
            "timeout_ms": 5000,
        }
    }
    observations = []
    fatal = None

    def record(name, okay, observed, expected):
        observations.append({"name": name, "status": "PASS" if okay else "FAIL", "observed": observed, "expected": expected})

    try:
        service.postgres()
        contract, _build = service.build()
        service.cli("project-init", project, "--postgres-env", ENV_NAME)
        service.files_cli(initialize=True)
        service.cli(
            "project-admin-add", project, "--postgres-env", ENV_NAME, "--user", "alice",
            "--email", "alice@qualification.invalid", "--password-stdin", input=PASSWORD + "\n",
        )
        common.seed(service)
        service.start_node("A")
        service.start_node("B")
        alice = Client(service, contract["rpc_id"])
        alice.login("alice", PASSWORD)
        original = service.objects.with_name("objects-original")
        service.objects.rename(original)
        service.objects.mkdir(mode=0o700)
        ready = service.request("A", "/_diamond/health/ready")
        record("replacement-makes-readiness-fail", ready[0] >= 400, {"status": ready[0]},
               "replacing the bound shared root makes readiness fail immediately")
        begin = alice.request(
            "A", PREFIX + "begin",
            {"parent": identifier("issue-red-one"), "filename": "replacement.bin", "length": "4", "media_type": "application/octet-stream"},
        )
        identifier_ = begin[1].get("id") if begin[0] == 200 else None
        ticket = begin[1].get("upload") if begin[0] == 200 else None
        put = service.transfer("B", alice, PREFIX + str(identifier_), b"W9!!", upload=ticket) if identifier_ else (0, {}, b"")
        finish = alice.request("A", PREFIX + str(identifier_) + "/finish", {}, {"X-Diamond-Upload": ticket}) if identifier_ else (0, None)
        replacement_files = sorted(path.name for path in service.objects.iterdir())
        record(
            "live-root-replacement-fails-closed",
            begin[0] >= 400 and put[0] == 0 and finish[0] == 0 and not replacement_files,
            {"begin": begin, "put_status": put[0], "finish": finish, "replacement_files": replacement_files},
            "no reservation, bytes, or ready metadata may be created through a server whose bound object root was replaced",
        )
    except Exception as exc:
        fatal = repr(exc)
    finally:
        service.close()
        receiver.close()
        shutdowns = list(service.shutdowns)
        cleanup_errors = list(service.cleanup_errors)
        shutil.rmtree(root)
    report = {
        "status": "PASS" if fatal is None and not cleanup_errors and all(r["status"] == "PASS" for r in observations) else "FAIL",
        "binary_sha256": sha256(options.binary),
        "error": fatal,
        "results": observations,
        "shutdowns": shutdowns,
        "cleanup_errors": cleanup_errors,
        "temporary_root_removed": not root.exists(),
        "oracle": "ORACLES.md F1/R1",
    }
    options.report.parent.mkdir(parents=True, exist_ok=True)
    options.report.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"status": report["status"], "observations": len(observations), "error": fatal}))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
