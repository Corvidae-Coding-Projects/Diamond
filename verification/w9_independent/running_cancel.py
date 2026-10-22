#!/usr/bin/env python3
"""Reproduce cancellation racing a real in-flight external job."""
from __future__ import annotations

import argparse
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

import importlib.util

spec = importlib.util.spec_from_file_location("_w9_cancel_common", BASE.parent / "w5_independent/common.py")
previous = importlib.util.module_from_spec(spec)
spec.loader.exec_module(previous)
Receipt, quote, seed, wait_for = previous.Receipt, previous.quote, previous.seed, previous.wait_for


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--report", type=Path, default=BASE / "running_cancel_results.json")
    options = parser.parse_args()
    root = Path(tempfile.mkdtemp(prefix="diamond-w9-running-cancel-"))
    binary = root / "diamond"
    shutil.copy2(options.binary, binary)
    project = write(root / "project")
    service = Service(root, binary, project)
    receiver = None
    receipt = Receipt()
    receipt.secret(PASSWORD)
    fatal = None
    started = time.monotonic()

    def jobs(job_id: str):
        return service.sql_json(
            "SELECT id,state,attempts,fence,lease::text,outcome FROM diamond_w7.jobs "
            "WHERE app=" + quote(app_id()) + " AND id=" + quote(job_id)
        )

    try:
        receiver = Receiver()
        receiver.hold = True
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
        service.start_node("B")
        alice = Client(service, contract["rpc_id"])
        alice.login("alice", PASSWORD)
        parent = identifier("issue-red-one")
        submitted = alice.rpc("submit", parent, "held cancel payload", "running-cancel-key")
        if submitted[0] != 200:
            raise AssertionError(("submit rejected", submitted))
        job_id = submitted[1]["value"]
        service.start_worker("held")
        wait_for(lambda: receiver.recorded.is_set(), "external effect reached held receiver", timeout=12)
        wait_for(lambda: jobs(job_id) and jobs(job_id)[0]["state"] == "running", "job visibly running", timeout=4)
        before = jobs(job_id)[0]
        cancel_started = time.monotonic()
        cancelled = alice.rpc("cancel", job_id, node="B")
        cancel_seconds = time.monotonic() - cancel_started
        after = jobs(job_id)[0]
        receipt.record(
            "running-job-cancellation-fences-current-attempt",
            cancelled[0] == 200 and after["state"] == "cancelled" and after["fence"] > before["fence"],
            {
                "cancel_status": cancelled[0],
                "cancel_body": cancelled[1],
                "elapsed_seconds": cancel_seconds,
                "before": before,
                "after": after,
                "receiver": receiver.snapshot(),
            },
            "a current owner can cancel an in-flight job; the cancellation transaction commits a higher fence and cancelled state",
        )
        receiver.release.set()
        wait_for(
            lambda: jobs(job_id)[0]["state"] in {"done", "dead", "cancelled", "pending"},
            "worker reaches visible post-race state",
            timeout=12,
        )
        time.sleep(0.5)
        final = jobs(job_id)[0]
        receipt.record(
            "failed-or-successful-cancel-has-coherent-terminal-effect",
            (after["state"] == "cancelled" and final["state"] == "cancelled")
            or (cancelled[0] != 200 and final["state"] in {"done", "dead", "pending"}),
            {"cancel_status": cancelled[0], "state_after_cancel": after, "final": final, "receiver": receiver.snapshot()},
            "a committed cancellation remains inert; if cancellation itself was rejected, the original at-least-once attempt may continue",
        )
    except Exception as error:
        fatal = repr(error)
    finally:
        if receiver is not None:
            receiver.release.set()
        service.close()
        if receiver is not None:
            receiver.close()
    report = receipt.report(
        service,
        sha256(binary),
        error=fatal,
        elapsed_seconds=time.monotonic() - started,
        command=f"python3 verification/w9_independent/running_cancel.py --binary {options.binary}",
        oracle="ORACLES.md J2",
        temporary_root_removed=True,
    )
    options.report.parent.mkdir(parents=True, exist_ok=True)
    options.report.write_text(json.dumps(report, indent=2) + "\n")
    shutil.rmtree(root)
    print(json.dumps({"status": report["status"], "observations": len(receipt.results), "error": fatal}))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
