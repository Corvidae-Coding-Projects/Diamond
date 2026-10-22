#!/usr/bin/env python3
"""Real process replacement and queued-handler version fencing."""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import time

from fixture import PASSWORD, app_id, identifier, write
from receiver import Receiver
from service import Client, ENV_NAME, Service, sha256

BASE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("_w8_versions_common", BASE.parent / "w5_independent/common.py")
previous = importlib.util.module_from_spec(spec)
spec.loader.exec_module(previous)
Receipt, quote, seed, wait_for = previous.Receipt, previous.quote, previous.seed, previous.wait_for


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--report", type=Path, default=BASE / "job_versions_results.json")
    options = parser.parse_args()
    root = Path(tempfile.mkdtemp(prefix="diamond-w8-job-versions-"))
    binary = root / "diamond"
    shutil.copy2(options.binary, binary)
    project = write(root / "project")
    service, receipt, receiver, fatal = Service(root, binary, project), Receipt(), None, None
    receipt.secret(PASSWORD)
    started = time.monotonic()

    def job(key):
        return service.sql_json("SELECT id,state,attempts,version FROM diamond_w7.jobs WHERE app=" + quote(app_id()) + " AND id=" + quote(key))[0]

    def submit(client, payload, key):
        result = client.rpc("submit", identifier("issue-red-one"), payload, key)
        if result[0] != 200:
            raise AssertionError(("actual version fixture submission rejected", result))
        return result[1]["value"]

    def client(contract):
        result = Client(service, contract["rpc_id"])
        result.login("alice", PASSWORD)
        return result

    try:
        receiver = Receiver()
        service.runtime["http"] = {"inventory": {"origin": receiver.origin, "network": "loopback", "paths": ["/v1/"], "methods": ["POST"]}}
        service.postgres()
        contract, _ = service.build()
        service.cli("project-init", project, "--postgres-env", ENV_NAME)
        service.files_cli(initialize=True)
        service.cli("project-admin-add", project, "--postgres-env", ENV_NAME, "--user", "alice", "--email", "alice@qualification.invalid", "--password-stdin", input=PASSWORD + "\n")
        seed(service)
        service.start_node("A")
        service.start_node("B")
        original = client(contract)
        ui_job = submit(original, "queued before browser edit", "ui-edit-key")
        ui_version = job(ui_job)["version"]
        service.stop_node("A")
        service.stop_node("B")
        ui = project.parent / "ui.dia"
        ui.write_text(ui.read_text().replace("Independent jobs, live events and recovery", "Revised browser copy, same handlers"))
        after_ui_contract, _ = service.build()
        service.start_node("A")
        service.start_node("B")
        after_ui = client(after_ui_contract)
        service.start_worker("one")
        service.start_worker("two")
        wait_for(lambda: job(ui_job)["state"] == "done", "same handler after browser-only edit", timeout=10)
        receipt.record("browser-edit-retains-durable-handler-version", job(ui_job)["version"] == ui_version and ui_job in receiver.snapshot()["effect_keys"],
            {"row": job(ui_job), "old_contract": contract["rpc_id"], "new_contract": after_ui_contract["rpc_id"]},
            "browser source replacement changes browser contract while existing current handler remains able to execute its queued payload")
        service.stop_worker("one")
        service.stop_worker("two")
        old_body_job = submit(after_ui, "queued before handler edit", "old-body-key")
        service.stop_node("A")
        service.stop_node("B")
        server = project.parent / "server.dia"
        body = server.read_text()
        old = 'host.secret("inventory"), payload)'
        if old not in body:
            raise AssertionError("handler body mutation did not find actual call")
        server.write_text(body.replace(old, 'host.secret("inventory"), payload + " revised")', 1))
        changed_contract, _ = service.build()
        service.start_node("A")
        service.start_node("B")
        current = client(changed_contract)
        service.start_worker("new-one")
        service.start_worker("new-two")
        new_body_job = submit(current, "new handler body", "new-body-key")
        wait_for(lambda: job(new_body_job)["state"] == "done", "current handler accepts newly versioned payload", timeout=10)
        time.sleep(.6)
        old_row, new_row = job(old_body_job), job(new_body_job)
        receipt.record("changed-handler-does-not-claim-old-version", old_row["state"] == "pending" and old_row["attempts"] == 0 and
            old_row["version"] != new_row["version"] and old_body_job not in receiver.snapshot()["effect_keys"] and
            new_body_job in receiver.snapshot()["effect_keys"],
            {"old": old_row, "new": new_row, "receiver": receiver.snapshot()},
            "current worker leaves incompatible queued implementation version untouched while processing newly checked work")
        cancelled = current.rpc("cancel", old_body_job, node="B")
        receipt.record("old-version-requires-explicit-cancellation", cancelled[0] == 200 and job(old_body_job)["state"] == "cancelled",
            {"rpc": cancelled, "row": job(old_body_job)}, "owner explicitly cancels old pending work rather than implicit replay under changed handler")
    except Exception as error:
        fatal = repr(error)
    finally:
        service.close()
        if receiver:
            receiver.close()
    report = receipt.report(service, sha256(binary), error=fatal, elapsed_seconds=time.monotonic() - started, temporary_root=str(root))
    options.report.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"status": report["status"], "observations": len(receipt.results), "error": fatal,
                      "root": str(root), "elapsed_seconds": report["elapsed_seconds"]}), flush=True)
    if report["status"] == "PASS":
        shutil.rmtree(root)
    return report["status"] != "PASS"


if __name__ == "__main__":
    raise SystemExit(main())
