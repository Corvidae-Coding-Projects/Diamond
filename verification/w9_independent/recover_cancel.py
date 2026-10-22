#!/usr/bin/env python3
"""Exercise encrypted restore followed by explicit job recovery cancellation."""
from __future__ import annotations

import argparse
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
from service import Client, ENV_NAME, RUNTIME_NAME, Service, sha256  # noqa: E402

spec = importlib.util.spec_from_file_location("_w9_recover_common", BASE.parent / "w5_independent/common.py")
previous = importlib.util.module_from_spec(spec)
spec.loader.exec_module(previous)
Receipt, quote, seed, wait_for = previous.Receipt, previous.quote, previous.seed, previous.wait_for

KEY_ENV = "DIAMOND_W9_CANCEL_BACKUP_KEY"
KEY = "W9 disposable cancel recovery passphrase 91!"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--report", type=Path, default=BASE / "recover_cancel_results.json")
    options = parser.parse_args()
    root = Path(tempfile.mkdtemp(prefix="diamond-w9-recover-cancel-"))
    binary = root / "diamond"
    shutil.copy2(options.binary, binary)
    project = write(root / "project")
    source_root, restored_root = root / "source", root / "restored"
    source_root.mkdir(mode=0o700)
    restored_root.mkdir(mode=0o700)
    source = Service(source_root, binary, project)
    restored = Service(restored_root, binary, project)
    services = [source, restored]
    receiver = None
    receipt = Receipt()
    receipt.secret(PASSWORD)
    receipt.secret(KEY)
    fatal = None
    started = time.monotonic()

    def cli(service: Service, command: str, *args, success=True):
        runtime = [] if command == "project-recover" else ["--runtime-env", RUNTIME_NAME]
        return service.cli(
            command,
            project,
            "--postgres-env",
            ENV_NAME,
            *runtime,
            *args,
            success=success,
            timeout=120,
        )

    def row(service: Service, job_id: str) -> dict:
        rows = service.sql_json(
            "SELECT id,state,attempts,fence,lease::text,outcome FROM diamond_w7.jobs WHERE app="
            + quote(app_id())
            + " AND id="
            + quote(job_id)
        )
        if len(rows) != 1:
            raise AssertionError(("missing restored job", job_id, rows))
        return rows[0]

    try:
        receiver = Receiver()
        receiver.hold = True
        for service in services:
            service.environment[KEY_ENV] = KEY
            service.runtime["http"] = {
                "inventory": {
                    "origin": receiver.origin,
                    "network": "loopback",
                    "paths": ["/v1/"],
                    "methods": ["POST"],
                    "timeout_ms": 5000,
                }
            }
        source.postgres()
        contract, _build = source.build()
        source.cli("project-init", project, "--postgres-env", ENV_NAME)
        source.files_cli(initialize=True)
        source.cli(
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
        seed(source)
        source.start_node("A")
        alice = Client(source, contract["rpc_id"])
        alice.login("alice", PASSWORD)
        parent = identifier("issue-red-one")
        running_result = alice.rpc("submit", parent, "pre-snapshot running", "recover-cancel-running")
        if running_result[0] != 200:
            raise AssertionError(("running submit rejected", running_result))
        running = running_result[1]["value"]
        source.start_worker("victim")
        wait_for(lambda: receiver.recorded.is_set(), "running job reaches held receiver", timeout=12)
        wait_for(lambda: row(source, running)["state"] == "running", "running row visible", timeout=4)
        pending_result = alice.rpc("submit", parent, "pre-snapshot pending", "recover-cancel-pending")
        if pending_result[0] != 200:
            raise AssertionError(("pending submit rejected", pending_result))
        pending = pending_result[1]["value"]
        before = {running: row(source, running), pending: row(source, pending)}
        source.stop_worker("victim", kill=True)
        receiver.release.set()
        source.stop_node("A")
        backup = cli(source, "project-backup", "--out", root / "backup", "--backup-key-env", KEY_ENV)
        restored.postgres()
        restore = cli(restored, "project-restore", "--from", root / "backup", "--backup-key-env", KEY_ENV)
        restored_before = {running: row(restored, running), pending: row(restored, pending)}
        paused_before = restored.sql("SELECT paused FROM diamond_w7.apps WHERE app=" + quote(app_id()))
        attempts_before = len(receiver.snapshot()["attempts"])
        recover = cli(restored, "project-recover", "--policy", "cancel")
        cancelled = {running: row(restored, running), pending: row(restored, pending)}
        paused_after = restored.sql("SELECT paused FROM diamond_w7.apps WHERE app=" + quote(app_id()))
        receipt.record(
            "encrypted-restore-explicit-cancel-fences-pending-and-running",
            backup.returncode == 0
            and restore.returncode == 0
            and recover.returncode == 0
            and before[running]["state"] == "running"
            and before[pending]["state"] == "pending"
            and restored_before[running]["state"] == "running"
            and restored_before[pending]["state"] == "pending"
            and paused_before == "t"
            and paused_after == "f"
            and all(item["state"] == "cancelled" and item["outcome"] == "restore_cancelled" for item in cancelled.values())
            and cancelled[running]["fence"] == restored_before[running]["fence"] + 1
            and cancelled[pending]["fence"] == restored_before[pending]["fence"] + 1,
            {
                "source_before_snapshot": before,
                "restored_before_recovery": restored_before,
                "cancelled": cancelled,
                "paused_before": paused_before,
                "paused_after": paused_after,
                "backup": json.loads(backup.stdout),
                "restore": json.loads(restore.stdout),
            },
            "a real encrypted restore is paused with pending/running rows; the named cancel policy increments each fence, records restore_cancelled, and explicitly unpauses",
        )
        restored.start_worker("one")
        restored.start_worker("two")
        time.sleep(1.5)
        after_workers = {running: row(restored, running), pending: row(restored, pending)}
        attempts_after = len(receiver.snapshot()["attempts"])
        restored.start_node("A")
        fresh = Client(restored, contract["rpc_id"])
        fresh.login("alice", PASSWORD)
        visible = {}
        for job_id in [running, pending]:
            outcome = fresh.rpc("status", job_id)
            visible[job_id] = {
                "status": outcome[0],
                "value": json.loads(outcome[1]["value"]) if outcome[0] == 200 else outcome[1],
            }
        receipt.record(
            "cancelled-restored-jobs-remain-inert-and-visible",
            after_workers == cancelled
            and attempts_after == attempts_before
            and all(item["status"] == 200 and item["value"]["state"] == "cancelled" and item["value"]["outcome"] == "restore_cancelled" for item in visible.values()),
            {
                "after_workers": after_workers,
                "receiver_attempts_before": attempts_before,
                "receiver_attempts_after": attempts_after,
                "visible": visible,
            },
            "two actual workers dispatch no cancelled recovery work and a fresh restored login observes the explicit terminal outcome",
        )
    except Exception as error:
        fatal = repr(error)
    finally:
        if receiver is not None:
            receiver.release.set()
        for service in services:
            service.close()
        if receiver is not None:
            receiver.close()
    source.shutdowns.extend(restored.shutdowns)
    source.cleanup_errors.extend(restored.cleanup_errors)
    report = receipt.report(
        source,
        sha256(binary),
        error=fatal,
        elapsed_seconds=time.monotonic() - started,
        command=f"python3 verification/w9_independent/recover_cancel.py --binary {options.binary}",
        oracle="ORACLES.md J2 D2",
        temporary_root_removed=True,
    )
    options.report.parent.mkdir(parents=True, exist_ok=True)
    options.report.write_text(json.dumps(report, indent=2) + "\n")
    shutil.rmtree(root)
    print(json.dumps({"status": report["status"], "observations": len(receipt.results), "error": fatal, "elapsed_seconds": report["elapsed_seconds"]}))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
