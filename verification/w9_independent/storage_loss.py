#!/usr/bin/env python3
"""Exercise live shared-object corruption, loss, root replacement, and repair."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
import time

BASE = Path(__file__).resolve().parent
PREVIOUS = BASE.parent / "w7_w8_independent"
sys.path.insert(0, str(PREVIOUS))
from fixture import PASSWORD, app_id, identifier, write  # noqa: E402
from receiver import Receiver  # noqa: E402
from service import Client, ENV_NAME, Service, sha256  # noqa: E402

spec = importlib.util.spec_from_file_location(
    "_w9_storage_loss_common", BASE.parent / "w5_independent/common.py"
)
common = importlib.util.module_from_spec(spec)
spec.loader.exec_module(common)

PREFIX = "/_diamond/files/issue_files/"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument(
        "--report", type=Path, default=BASE / "storage_loss_results.json"
    )
    options = parser.parse_args()
    started = time.monotonic()
    root = Path(tempfile.mkdtemp(prefix="diamond-w9-storage-loss-"))
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
        observations.append(
            {
                "name": name,
                "status": "PASS" if okay else "FAIL",
                "observed": observed,
                "expected": expected,
            }
        )

    try:
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
        common.seed(service)
        service.start_node("A")
        service.start_node("B")
        alice = Client(service, contract["rpc_id"])
        alice.login("alice", PASSWORD)

        payload = b"independent storage loss \x00\xff " + bytes(range(256))
        begin = alice.request(
            "A",
            PREFIX + "begin",
            {
                "parent": identifier("issue-red-one"),
                "filename": "dependency-loss.bin",
                "length": str(len(payload)),
                "media_type": "application/octet-stream",
            },
        )
        if begin[0] != 200:
            raise AssertionError(("valid begin", begin))
        object_id = begin[1]["id"]
        upload = begin[1]["upload"]
        transfer = service.transfer(
            "B", alice, PREFIX + object_id, payload, upload=upload
        )
        finish = alice.request(
            "A",
            PREFIX + object_id + "/finish",
            {},
            {"X-Diamond-Upload": upload},
        )
        download_headers = {"Cookie": alice.cookie or ""}

        def download(node):
            return service.request(node, PREFIX + object_id, headers=download_headers)

        nominal = download("B")
        record(
            "published-object-cross-node-control",
            transfer[0] == 200 and finish[0] == 200 and nominal[0] == 200 and nominal[2] == payload,
            {
                "transfer": transfer[0],
                "finish": finish[0],
                "download": nominal[0],
                "bytes": len(nominal[2]),
                "sha256": hashlib.sha256(nominal[2]).hexdigest(),
            },
            "a real cross-node upload is finalized and read exactly before fault injection",
        )

        blob = service.objects / f"{app_id()}-{object_id}.blob"
        original = blob.read_bytes()
        blob.write_bytes(bytes([original[0] ^ 0xFF]) + original[1:])
        corrupt_a = download("A")
        corrupt_b = download("B")
        blob.write_bytes(original)
        repaired_corruption = download("A")
        record(
            "same-length-corruption-fails-closed-and-recovers",
            corrupt_a[0] >= 400
            and corrupt_b[0] >= 400
            and corrupt_a[2] != original
            and corrupt_b[2] != original
            and repaired_corruption[0] == 200
            and repaired_corruption[2] == original,
            {
                "corrupt_statuses": [corrupt_a[0], corrupt_b[0]],
                "corrupt_body_lengths": [len(corrupt_a[2]), len(corrupt_b[2])],
                "repaired_status": repaired_corruption[0],
                "repaired_sha256": hashlib.sha256(repaired_corruption[2]).hexdigest(),
            },
            "same-length corruption never streams, and restoring the recorded bytes restores service without restart",
        )

        blob.unlink()
        missing_a = download("A")
        missing_b = download("B")
        blob.write_bytes(original)
        repaired_missing = download("B")
        record(
            "missing-object-fails-closed-and-recovers",
            missing_a[0] >= 400
            and missing_b[0] >= 400
            and repaired_missing[0] == 200
            and repaired_missing[2] == original,
            {
                "missing_statuses": [missing_a[0], missing_b[0]],
                "repaired_status": repaired_missing[0],
                "repaired_sha256": hashlib.sha256(repaired_missing[2]).hexdigest(),
            },
            "missing owned bytes never fall back to database metadata or another path, and exact repair is live",
        )

        original_root = service.objects.with_name("objects-held")
        service.objects.rename(original_root)
        service.objects.mkdir(mode=0o700)
        replaced_ready = service.request("A", "/_diamond/health/ready")
        replaced_download = download("B")
        count_before = int(
            service.sql_json(
                "SELECT COUNT(*) AS count FROM diamond_w6.objects WHERE app='"
                + app_id()
                + "'"
            )[0]["count"]
        )
        replaced_begin = alice.request(
            "A",
            PREFIX + "begin",
            {
                "parent": identifier("issue-red-one"),
                "filename": "must-not-reserve.bin",
                "length": "1",
                "media_type": "application/octet-stream",
            },
        )
        count_after = int(
            service.sql_json(
                "SELECT COUNT(*) AS count FROM diamond_w6.objects WHERE app='"
                + app_id()
                + "'"
            )[0]["count"]
        )
        replacement_files = sorted(path.name for path in service.objects.iterdir())
        service.objects.rmdir()
        original_root.rename(service.objects)
        restored_ready = service.request("B", "/_diamond/health/ready")
        restored_download = download("A")
        record(
            "live-root-replacement-fails-closed-and-recovers",
            replaced_ready[0] != 200
            and replaced_download[0] >= 400
            and replaced_begin[0] >= 400
            and count_after == count_before
            and not replacement_files
            and restored_ready[0] == 200
            and restored_download[0] == 200
            and restored_download[2] == original,
            {
                "replacement_ready": replaced_ready[0],
                "replacement_download": replaced_download[0],
                "replacement_begin": replaced_begin[0],
                "row_counts": [count_before, count_after],
                "replacement_files": replacement_files,
                "restored_ready": restored_ready[0],
                "restored_download": restored_download[0],
                "restored_sha256": hashlib.sha256(restored_download[2]).hexdigest(),
            },
            "a replaced shared root grants no readiness, reads, reservations, or fallback bytes; restoring its identity recovers both live nodes",
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
        "status": "PASS"
        if fatal is None
        and not cleanup_errors
        and all(result["status"] == "PASS" for result in observations)
        else "FAIL",
        "binary_sha256": sha256(options.binary),
        "error": fatal,
        "results": observations,
        "shutdowns": shutdowns,
        "cleanup_errors": cleanup_errors,
        "http_requests": len(service.trace),
        "elapsed_seconds": time.monotonic() - started,
        "command": f"python3 verification/w9_independent/storage_loss.py --binary {options.binary}",
        "oracle": "ORACLES.md F1",
        "temporary_root_removed": not root.exists(),
    }
    options.report.parent.mkdir(parents=True, exist_ok=True)
    options.report.write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {
                "status": report["status"],
                "observations": len(observations),
                "error": fatal,
                "elapsed_seconds": report["elapsed_seconds"],
            }
        )
    )
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
