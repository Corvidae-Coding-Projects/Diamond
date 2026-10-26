#!/usr/bin/env python3
"""Exercise the managed mail outbox through real CLI, PostgreSQL, HTTP, and worker processes."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
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


PASSWORD = "Synthetic L4 mail qualification password 94!"


def wait_for(predicate, label: str, timeout: float = 10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.025)
    raise AssertionError(f"deadline waiting for {label}")


def result_value(outcome):
    if outcome[0] != 200 or not isinstance(outcome[1], dict) or "value" not in outcome[1]:
        raise AssertionError(("expected successful RPC", outcome))
    return outcome[1]["value"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, default=ROOT / "target" / "debug" / "diamond")
    parser.add_argument("--report", type=Path,
                        default=ROOT / "verification" / "secure_language_l4_runtime_results.json")
    options = parser.parse_args()
    observations = []
    fatal = None
    workspace = ROOT / ".diamond"
    workspace.mkdir(mode=0o700, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="l4-mail-runtime-", dir=workspace))
    binary = root / "diamond"
    project_dir = root / "project"
    mail_root = root / "mail"
    shutil.copy2(options.binary.resolve(), binary)
    shutil.copytree(ROOT / "examples" / "functions" / "authority", project_dir)
    mail_root.mkdir(mode=0o700)
    project = project_dir / "diamond.toml"
    server = project_dir / "server.dia"
    source = server.read_text()
    original = """  read(row, by principal):
    principal.is_user(row.owner)
"""
    replacement = """  read(row, by principal):
    true
"""
    if source.count(original) != 1:
        raise AssertionError("authority fixture read policy changed unexpectedly")
    server.write_text(source.replace(original, replacement))
    service = Service(root, binary, project)
    receiver = None

    def record(name: str, okay: bool, observed, expected: str):
        observations.append({"name": name, "status": "PASS" if okay else "FAIL",
                             "observed": observed, "expected": expected})
        if not okay:
            raise AssertionError((name, observed, expected))

    def row(identifier: str):
        rows = service.sql_json(
            "SELECT id,state,attempts,outcome FROM diamond_mail.outbox WHERE id='" + identifier + "'")
        return rows[0] if rows else None

    def files():
        return sorted(path for path in mail_root.glob("*.json") if path.is_file())

    try:
        receiver = Receiver()
        service.runtime["http"] = {
            "inventory": {"origin": receiver.origin, "network": "loopback",
                          "paths": ["/v1/"], "methods": ["POST"],
                          "credential_env": "DIAMOND_L4_INVENTORY"}}
        service.environment["DIAMOND_L4_INVENTORY"] = "synthetic-l4-bearer"
        service.runtime["mail"] = {
            "document_mail": {"root": str(mail_root), "from": "noreply@example.invalid"}}
        service.postgres()
        contract, _build = service.build()
        service.cli("project-init", project, "--postgres-env", ENV_NAME)
        service.files_cli(initialize=True)
        initialized = service.cli("project-mail-init", project, "--postgres-env", ENV_NAME,
                                  "--runtime-env", RUNTIME_NAME)
        record("explicit-private-store-initialization", initialized.returncode == 0 and
               stat.S_IMODE(mail_root.stat().st_mode) == 0o700 and
               (mail_root / ".diamond-mail-store").is_file(),
               {"exit": initialized.returncode, "mode": oct(stat.S_IMODE(mail_root.stat().st_mode))},
               "explicit initialization binds one private mode-0700 adapter root")
        for username in ("alice", "bob"):
            service.cli("project-admin-add", project, "--postgres-env", ENV_NAME, "--user", username,
                        "--email", username + "@qualification.invalid", "--password-stdin",
                        input=PASSWORD + "\n")
        service.start_node("A")
        alice, bob = Client(service, contract["rpc_id"]), Client(service, contract["rpc_id"])
        alice.login("alice", PASSWORD)
        bob.login("bob", PASSWORD)
        parent = result_value(alice.rpc("create_document", "L4 real mail qualification"))

        hostile = "Literal <script>alert('mail')</script>\nBcc: attacker@example.invalid\tend"
        identifier = result_value(alice.rpc(
            "notify_document", parent, "bob", hostile, "stable-notice-one"))
        service.start_worker("mail-one")
        delivered = wait_for(lambda: (candidate := row(identifier)) and
                             candidate["state"] == "delivered" and candidate, "first delivery")
        service.stop_worker("mail-one")
        delivered_files = files()
        message = json.loads(delivered_files[0].read_text()) if len(delivered_files) == 1 else {}
        record("manifest-template-and-plain-text-delivery", len(delivered_files) == 1 and
               stat.S_IMODE(delivered_files[0].stat().st_mode) == 0o600 and
               message == {"profile": "diamond-mail-v1", "id": identifier,
                           "from": "noreply@example.invalid",
                           "recipient": "bob@qualification.invalid",
                           "subject": "Document notification", "text": hostile,
                           "template": "notice"},
               {"row": delivered, "files": len(delivered_files), "mode":
                oct(stat.S_IMODE(delivered_files[0].stat().st_mode)) if delivered_files else None,
                "message": message},
               "runtime owns headers and address resolution; hostile body remains literal plain text")

        replay = result_value(alice.rpc(
            "notify_document", parent, "bob", hostile, "stable-notice-one"))
        conflict = alice.rpc(
            "notify_document", parent, "bob", hostile + " changed", "stable-notice-one")
        record("stable-deduplication-is-content-bound", replay == identifier and conflict[0] == 409 and
               len(files()) == 1,
               {"same_id": replay == identifier, "changed_status": conflict[0], "files": len(files())},
               "same immutable submission reuses its ID; changed content cannot reuse the key")

        suppression = bob.rpc("set_mail_suppression", True)
        suppressed_id = result_value(alice.rpc(
            "notify_document", parent, "bob", "suppressed body", "stable-notice-two"))
        service.start_worker("mail-suppressed")
        suppressed = wait_for(lambda: (candidate := row(suppressed_id)) and
                              candidate["state"] == "suppressed" and candidate,
                              "suppressed delivery")
        service.stop_worker("mail-suppressed")
        record("recipient-suppression-prevents-output", result_value(suppression) == "suppressed" and
               suppressed["outcome"] == "recipient_suppressed" and len(files()) == 1,
               {"rpc": suppression[0], "row": suppressed, "files": len(files())},
               "current recipient preference is serialized with delivery and creates no message")

        result_value(bob.rpc("set_mail_suppression", False))
        disabled_id = result_value(alice.rpc(
            "notify_document", parent, "bob", "disabled recipient", "stable-notice-three"))
        disabled = alice.account("disable", {"username": "bob", "disabled": True})
        record("administrator-disabled-recipient", disabled[0] == 200, {"status": disabled[0]},
               "synthetic administrator disables recipient before worker claim")
        service.start_worker("mail-disabled")
        dead = wait_for(lambda: (candidate := row(disabled_id)) and
                        candidate["state"] == "dead" and candidate, "disabled recipient denial")
        service.stop_worker("mail-disabled")
        record("delivery-reauthorizes-current-account", dead["outcome"] == "denied_or_exhausted" and
               len(files()) == 1,
               {"row": dead, "files": len(files())},
               "disabled or unverified recipient fails closed at delivery with no output")
        enabled = alice.account("disable", {"username": "bob", "disabled": False})
        record("administrator-reenabled-recipient", enabled[0] == 200, {"status": enabled[0]},
               "synthetic recipient restored for subsequent independent checks")

        cancelled_id = result_value(alice.rpc(
            "notify_document", parent, "bob", "cancelled body", "stable-notice-four"))
        cancelled_rpc = alice.rpc("cancel_mail", cancelled_id)
        cancelled = row(cancelled_id)
        service.start_worker("mail-cancelled")
        time.sleep(0.75)
        service.stop_worker("mail-cancelled")
        record("cancel-before-claim-is-durable", cancelled_rpc[0] == 200 and
               json.loads(result_value(cancelled_rpc))["state"] == "cancelled" and
               cancelled["state"] == "cancelled" and len(files()) == 1,
               {"row": cancelled, "files": len(files())},
               "cancelled outbox entry cannot be claimed and creates no output")

        original_root = root / "mail-original"
        mail_root.rename(original_root)
        mail_root.mkdir(mode=0o700)
        replaced = service.cli("project-work", project, "--postgres-env", ENV_NAME,
                               "--runtime-env", RUNTIME_NAME, success=False, timeout=10)
        record("adapter-root-replacement-fails-closed", replaced.returncode != 0 and
               not any(mail_root.iterdir()),
               {"exit": replaced.returncode, "new_root_entries": len(list(mail_root.iterdir()))},
               "worker refuses an uninitialized or identity-mismatched replacement root")

    except Exception as error:  # retain a bounded, reproducible failure receipt
        fatal = repr(error)
    finally:
        if receiver is not None:
            receiver.close()
        service.close()
        report = {
            "status": "FAIL" if fatal or service.cleanup_errors or
                      any(item["status"] != "PASS" for item in observations) else "PASS",
            "binary_sha256": sha256(binary),
            "observations": observations,
            "fatal": fatal,
            "shutdowns": service.shutdowns,
            "cleanup_errors": service.cleanup_errors,
            "http_requests": len(service.trace),
            "scope": "Disposable loopback HTTP, owned PostgreSQL, worker, and private directory adapter; no SMTP or Internet delivery.",
        }
        options.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        shutil.rmtree(root)
    print(json.dumps({"status": report["status"], "observations": len(observations),
                      "fatal": fatal}))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
