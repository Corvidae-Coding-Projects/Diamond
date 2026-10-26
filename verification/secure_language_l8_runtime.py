#!/usr/bin/env python3
"""Adversarial final-artifact qualification for the assembled secure language profile."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import stat
import sys
import tempfile
import time


ROOT = Path(__file__).resolve().parents[1]
SUPPORT = ROOT / "verification" / "w7_w8_independent"
sys.path.insert(0, str(SUPPORT))

from receiver import Receiver  # noqa: E402
from service import Client, ENV_NAME, RUNTIME_NAME, Service, sha256  # noqa: E402


PASSWORD = "Synthetic L8 final qualification password 98!"
SECRET_ENV = "DIAMOND_L8_INVENTORY_SECRET"
SECRET = "synthetic-l8-final-bearer"


def value(outcome):
    if outcome[0] != 200 or not isinstance(outcome[1], dict) or "value" not in outcome[1]:
        raise AssertionError(("expected successful RPC", outcome))
    return outcome[1]["value"]


def wait_for(predicate, label: str, timeout: float = 12.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(0.025)
    raise AssertionError("deadline waiting for " + label)


def artifact_hashes(root: Path):
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*")) if path.is_file()
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, default=ROOT / "target/debug/diamond")
    parser.add_argument("--report", type=Path,
                        default=ROOT / "verification" / "secure_language_l8_runtime_results.json")
    options = parser.parse_args()
    workspace = ROOT / ".diamond"
    workspace.mkdir(mode=0o700, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="l8-final-runtime-", dir=workspace))
    binary, project_dir, mail_root = root / "diamond", root / "project", root / "mail"
    shutil.copy2(options.binary.resolve(), binary)
    shutil.copytree(ROOT / "examples" / "functions" / "authority", project_dir)
    mail_root.mkdir(mode=0o700)
    project = project_dir / "diamond.toml"
    manifest = project.read_text()
    api_anchor = """[http_api.operations.external_get]
function = "server.external_get"
auth = "token"
allow_unscoped = true
"""
    public_query = api_anchor + """

[http_api.operations.document_graph]
function = "server.document_graph"
auth = "public"
"""
    if manifest.count(api_anchor) != 1:
        raise AssertionError("authority HTTP API fixture changed unexpectedly")
    project.write_text(manifest.replace(api_anchor, public_query))
    service = Service(root, binary, project)
    receiver = None
    observations, fatal = [], None
    build_hashes = {}

    def record(name, okay, observed, expected):
        observations.append({"name": name, "status": "PASS" if okay else "FAIL",
                             "observed": observed, "expected": expected})
        if not okay:
            raise AssertionError((name, observed, expected))

    def workflow(identifier):
        rows = service.sql_json(
            "SELECT id,status,transitions,attempts,fence,outcome FROM "
            "diamond_workflows.executions WHERE id='" + identifier + "'")
        return rows[0] if rows else None

    def mail(identifier):
        rows = service.sql_json(
            "SELECT id,state,attempts,fence,outcome FROM diamond_mail.outbox WHERE id='" +
            identifier + "'")
        return rows[0] if rows else None

    try:
        receiver = Receiver()
        service.runtime["http"] = {"inventory": {"origin": receiver.origin,
            "network": "loopback", "paths": ["/v1/"], "methods": ["POST"],
            "credential_env": SECRET_ENV}}
        service.environment[SECRET_ENV] = SECRET
        service.runtime["mail"] = {"document_mail": {"root": str(mail_root),
                                                       "from": "noreply@example.invalid"}}
        service.postgres()
        contract, build = service.build()
        build_hashes = artifact_hashes(build)
        record("final-build-has-complete-security-contract",
               all(name in json.loads((build / "contract.json").read_text())
                   for name in ("mail", "workflows", "secret_inventory", "queries",
                                "file_operations", "events")),
               {"artifact_files": len(build_hashes), "contract_sha256":
                build_hashes.get("contract.json")},
               "one deterministic build publishes every new security primitive")

        tampered = root / "tampered-build"
        shutil.copytree(build, tampered)
        (tampered / "contract.json").write_text((tampered / "contract.json").read_text() + " ")
        rejected = service.cli("check", tampered, success=False)
        record("artifact-tamper-fails-closed", rejected.returncode != 0,
               {"exit": rejected.returncode},
               "a changed generated artifact cannot be loaded as a checked application")

        service.cli("project-init", project, "--postgres-env", ENV_NAME)
        service.files_cli(initialize=True)
        service.cli("project-mail-init", project, "--postgres-env", ENV_NAME,
                    "--runtime-env", RUNTIME_NAME)
        for username in ("alice", "bob"):
            service.cli("project-admin-add", project, "--postgres-env", ENV_NAME,
                        "--user", username, "--email", username + "@qualification.invalid",
                        "--password-stdin", input=PASSWORD + "\n")
        service.start_node("A")
        alice = Client(service, contract["rpc_id"])
        alice.login("alice", PASSWORD)

        parent = value(alice.rpc("create_document", "L8 adversarial parent"))
        valid_public = alice.api("document_graph", None, ("", "10"))
        excessive_public = alice.api("document_graph", None, ("", "11"))
        malformed_public = alice.api("document_graph", None, ("", "01"))
        record("query-plan-cannot-be-bypassed-through-public-api",
               valid_public == (200, {"api_version": 1, "value": []}) and
               excessive_public[0] == 400 and malformed_public[0] == 400,
               {"valid": valid_public, "excessive_status": excessive_public[0],
                "malformed_status": malformed_public[0]},
               "the public API shares the RPC query budget and canonical argument decoder")

        workflow_id = value(alice.rpc(
            "start_document_flow", parent, "recovered typed value", "l8-expired-workflow"))
        service.sql("UPDATE diamond_workflows.executions SET status='running',attempts=0,"
                    "fence=fence+1,lease=clock_timestamp()-INTERVAL '1 second',"
                    "due=clock_timestamp() WHERE id='" + workflow_id + "'")
        stale_workflow = workflow(workflow_id)
        service.start_worker("expired-workflow")
        wait_for(lambda: (row := workflow(workflow_id)) and row["status"] == "waiting" and row,
                 "recovered workflow timer")
        recovered_workflow = wait_for(
            lambda: (row := workflow(workflow_id)) and row["status"] == "completed" and row,
            "recovered workflow completion")
        service.stop_worker("expired-workflow")
        record("expired-workflow-lease-is-fenced-and-recovered",
               recovered_workflow["fence"] > stale_workflow["fence"] and
               recovered_workflow["transitions"] == 2 and
               recovered_workflow["outcome"] == "terminal:Completed",
               {"stale": stale_workflow, "recovered": recovered_workflow},
               "a crashed claim is reclaimed under a new fence and reaches its typed terminal state")

        mail_id = value(alice.rpc(
            "notify_document", parent, "alice", "lease recovery body", "l8-expired-mail"))
        service.sql("UPDATE diamond_mail.outbox SET state='running',attempts=0,fence=fence+1,"
                    "lease=clock_timestamp()-INTERVAL '1 second',due=clock_timestamp() WHERE id='" +
                    mail_id + "'")
        stale_mail = mail(mail_id)
        service.start_worker("expired-mail")
        recovered_mail = wait_for(
            lambda: (row := mail(mail_id)) and row["state"] == "delivered" and row,
            "expired mail recovery")
        service.stop_worker("expired-mail")
        delivered_path = mail_root / (mail_id + ".json")
        record("expired-mail-lease-reconciles-idempotently",
               recovered_mail["fence"] > stale_mail["fence"] and
               recovered_mail["outcome"] == "delivered" and delivered_path.is_file() and
               stat.S_IMODE(delivered_path.stat().st_mode) == 0o600,
               {"stale": stale_mail, "recovered": recovered_mail,
                "mode": oct(stat.S_IMODE(delivered_path.stat().st_mode))},
               "an expired mail claim is fenced, reauthorized, and emitted to one private file")

        poisoned_mail_id = value(alice.rpc(
            "notify_document", parent, "alice", "must not overwrite", "l8-poisoned-mail"))
        poisoned_path = mail_root / (poisoned_mail_id + ".json")
        poison = b"attacker-controlled-existing-delivery"
        poisoned_path.write_bytes(poison)
        poisoned_path.chmod(0o600)
        service.start_worker("poisoned-mail")
        poisoned_mail = wait_for(
            lambda: (row := mail(poisoned_mail_id)) and row["state"] == "dead" and row,
            "poisoned mail exhaustion")
        service.stop_worker("poisoned-mail")
        record("mail-collision-never-overwrites-existing-output",
               poisoned_mail["attempts"] == 3 and
               poisoned_mail["outcome"] == "denied_or_exhausted" and
               poisoned_path.read_bytes() == poison,
               {"row": poisoned_mail,
                "existing_sha256": hashlib.sha256(poisoned_path.read_bytes()).hexdigest()},
               "byte-mismatched delivery identity retries within its bound, dies, and preserves the file")

        malformed_id = value(alice.rpc(
            "start_document_flow", parent, "invalid state must fail", "l8-malformed-state"))
        malformed = json.dumps({"tag": "Impossible", "fields": []})
        service.sql("UPDATE diamond_workflows.executions SET state_value='" + malformed +
                    "' WHERE id='" + malformed_id + "'")
        service.start_worker("malformed-workflow")
        rejected_workflow = wait_for(
            lambda: (row := workflow(malformed_id)) and row["status"] == "dead" and row,
            "malformed workflow rejection")
        service.stop_worker("malformed-workflow")
        record("malformed-durable-state-fails-before-handler",
               rejected_workflow["transitions"] == 0 and
               rejected_workflow["outcome"] == "denied_or_exhausted",
               {"row": rejected_workflow},
               "strict typed state decoding rejects poisoned storage without a transition")

        worker_logs = "\n".join(path.read_text() for path in service.worker_logs.values()
                                  if path.exists())
        server_logs = "\n".join(Path(log.name).read_text() for log in service.logs)
        record("qualification-logs-redact-deployment-secrets",
               SECRET not in worker_logs and SECRET not in server_logs and
               PASSWORD not in worker_logs and PASSWORD not in server_logs,
               {"secret_present": SECRET in worker_logs or SECRET in server_logs,
                "password_present": PASSWORD in worker_logs or PASSWORD in server_logs},
               "failure and shutdown logs contain neither the injected secret nor test password")
    except Exception as error:
        fatal = repr(error)
    finally:
        if receiver is not None:
            receiver.close()
        service.close()
        report = {
            "status": "FAIL" if fatal or service.cleanup_errors or
                      any(item["status"] != "PASS" for item in observations) else "PASS",
            "binary_sha256": sha256(binary),
            "artifact_hashes": build_hashes,
            "observations": observations,
            "fatal": fatal,
            "shutdowns": service.shutdowns,
            "cleanup_errors": service.cleanup_errors,
            "scope": "Disposable loopback HTTP, owned PostgreSQL, real server/worker processes, private directory mail adapter, and checked build loader; no Internet effects.",
        }
        options.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        shutil.rmtree(root)
    print(json.dumps({"status": report["status"], "observations": len(observations),
                      "fatal": fatal}))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
