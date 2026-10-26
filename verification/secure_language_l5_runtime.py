#!/usr/bin/env python3
"""Real PostgreSQL/HTTP/worker qualification for typed durable workflows."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import shutil
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
SUPPORT = ROOT / "verification" / "w7_w8_independent"
sys.path.insert(0, str(SUPPORT))
from receiver import Receiver  # noqa: E402
from service import Client, ENV_NAME, RUNTIME_NAME, Service, sha256  # noqa: E402

PASSWORD = "Synthetic L5 workflow qualification password 95!"


def value(outcome):
    if outcome[0] != 200 or not isinstance(outcome[1], dict) or "value" not in outcome[1]:
        raise AssertionError(("expected successful RPC", outcome))
    return outcome[1]["value"]


def wait_for(predicate, label, timeout=12):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(0.025)
    raise AssertionError("deadline waiting for " + label)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, default=ROOT / "target/debug/diamond")
    parser.add_argument("--report", type=Path,
                        default=ROOT / "verification" / "secure_language_l5_runtime_results.json")
    options = parser.parse_args()
    workspace = ROOT / ".diamond"
    workspace.mkdir(mode=0o700, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="l5-workflow-runtime-", dir=workspace))
    binary = root / "diamond"
    project_dir = root / "project"
    mail_root = root / "mail"
    shutil.copy2(options.binary.resolve(), binary)
    shutil.copytree(ROOT / "examples" / "functions" / "authority", project_dir)
    mail_root.mkdir(mode=0o700)
    project = project_dir / "diamond.toml"
    service = Service(root, binary, project)
    receiver = None
    observations, fatal = [], None

    def record(name, okay, observed, expected):
        observations.append({"name": name, "status": "PASS" if okay else "FAIL",
                             "observed": observed, "expected": expected})
        if not okay:
            raise AssertionError((name, observed, expected))

    def execution(identifier):
        rows = service.sql_json("SELECT id,status,transitions,attempts,outcome,state_value,due<=clock_timestamp() AS due FROM diamond_workflows.executions WHERE id='" + identifier + "'")
        return rows[0] if rows else None

    try:
        receiver = Receiver()
        service.runtime["http"] = {"inventory": {"origin": receiver.origin, "network": "loopback",
                                                    "paths": ["/v1/"], "methods": ["POST"],
                                                    "credential_env": "DIAMOND_L5_INVENTORY"}}
        service.environment["DIAMOND_L5_INVENTORY"] = "synthetic-l5-bearer"
        service.runtime["mail"] = {"document_mail": {"root": str(mail_root),
                                                       "from": "noreply@example.invalid"}}
        service.postgres()
        contract, _ = service.build()
        service.cli("project-init", project, "--postgres-env", ENV_NAME)
        service.files_cli(initialize=True)
        service.cli("project-mail-init", project, "--postgres-env", ENV_NAME,
                    "--runtime-env", RUNTIME_NAME)
        for username in ("alice", "bob"):
            service.cli("project-admin-add", project, "--postgres-env", ENV_NAME, "--user", username,
                        "--email", username + "@qualification.invalid", "--password-stdin",
                        input=PASSWORD + "\n")
        service.start_node("A")
        alice = Client(service, contract["rpc_id"])
        alice_two = Client(service, contract["rpc_id"])
        bob = Client(service, contract["rpc_id"])
        for client, username in ((alice, "alice"), (alice_two, "alice"), (bob, "bob")):
            client.login(username, PASSWORD)

        parent = value(alice.rpc("create_document", "L5 durable workflow"))
        identifier = value(alice.rpc("start_document_flow", parent, "typed output", "flow-one"))
        replay = value(alice.rpc("start_document_flow", parent, "typed output", "flow-one"))
        changed = alice.rpc("start_document_flow", parent, "changed", "flow-one")
        record("content-bound-idempotent-submission", replay == identifier and changed[0] == 409,
               {"same_id": replay == identifier, "changed_status": changed[0]},
               "same owner/input/scope returns stable ID and changed reuse conflicts")

        service.start_worker("workflow-complete")
        waiting = wait_for(lambda: (row := execution(identifier)) and row["status"] == "waiting" and row,
                           "database-clock wait state")
        completed = wait_for(lambda: (row := execution(identifier)) and row["status"] == "completed" and row,
                             "terminal workflow state")
        service.stop_worker("workflow-complete")
        typed = alice.rpc("document_flow_state", identifier)
        status = json.loads(value(alice.rpc("document_flow_status", identifier)))
        record("typed-state-timer-and-terminal-output", waiting["transitions"] == 1 and
               completed["transitions"] == 2 and completed["outcome"] == "terminal:Completed" and
               typed[0] == 200 and typed[1]["value"] == {"tag": "Completed", "fields": ["typed output"]} and
               status["status"] == "completed",
               {"waiting": waiting, "completed": completed, "typed": typed, "status": status},
               "one typed transition waits on database time and the next exposes a typed terminal state")
        terminal_replay = value(alice.rpc("start_document_flow", parent, "typed output", "flow-one"))
        record("idempotency-survives-state-advance", terminal_replay == identifier,
               {"same_id": terminal_replay == identifier},
               "progress does not make an identical original submission conflict")

        cancelled_id = value(alice.rpc("start_document_flow", parent, "cancel me", "flow-cancel"))
        cancel_request = alice.rpc("cancel_document_flow", cancelled_id)
        service.start_worker("workflow-cancel")
        cancelled = wait_for(lambda: (row := execution(cancelled_id)) and row["status"] == "cancelled" and row,
                             "transactional compensation")
        service.stop_worker("workflow-cancel")
        record("fenced-cancel-and-compensation", cancel_request[0] == 200 and
               cancelled["outcome"] == "cancelled" and cancelled["transitions"] == 0,
               {"cancel_status": cancel_request[0], "execution": cancelled},
               "cancel request is fenced and reaches cancelled only after compensation commits")

        def start(client, key):
            return client.rpc("start_document_flow", parent, "one active per parent", key)
        with ThreadPoolExecutor(max_workers=2) as executor:
            concurrent = [future.result(timeout=10) for future in
                          [executor.submit(start, alice, "race-a"),
                           executor.submit(start, alice_two, "race-b")]]
        record("parent-concurrency-is-atomic", sorted(outcome[0] for outcome in concurrent) == [200, 409],
               {"statuses": sorted(outcome[0] for outcome in concurrent)},
               "advisory serialization admits exactly one active execution per binding and parent")
        winner = next(value(outcome) for outcome in concurrent if outcome[0] == 200)
        value(alice.rpc("cancel_document_flow", winner))
        service.start_worker("workflow-race-cleanup")
        wait_for(lambda: (row := execution(winner)) and row["status"] == "cancelled", "race winner cancel")
        service.stop_worker("workflow-race-cleanup")

        bob_parent = value(bob.rpc("create_document", "L5 current authority"))
        disabled_id = value(bob.rpc("start_document_flow", bob_parent, "must not run", "disabled-owner"))
        disabled = alice.account("disable", {"username": "bob", "disabled": True})
        service.start_worker("workflow-disabled")
        dead = wait_for(lambda: (row := execution(disabled_id)) and row["status"] == "dead" and row,
                        "disabled workflow owner")
        service.stop_worker("workflow-disabled")
        record("each-transition-reacquires-current-authority", disabled[0] == 200 and
               dead["outcome"] == "denied_or_exhausted" and dead["transitions"] == 0,
               {"disable_status": disabled[0], "execution": dead},
               "disabled owner cannot execute captured work or mutate its parent")
    except Exception as error:
        fatal = repr(error)
    finally:
        if receiver is not None:
            receiver.close()
        service.close()
        report = {"status": "FAIL" if fatal or service.cleanup_errors or
                  any(item["status"] != "PASS" for item in observations) else "PASS",
                  "binary_sha256": sha256(binary), "observations": observations,
                  "fatal": fatal, "shutdowns": service.shutdowns,
                  "cleanup_errors": service.cleanup_errors,
                  "scope": "Disposable loopback HTTP, owned PostgreSQL, real server and worker; no Internet effects."}
        options.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        shutil.rmtree(root)
    print(json.dumps({"status": report["status"], "observations": len(observations), "fatal": fatal}))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
