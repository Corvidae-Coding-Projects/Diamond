#!/usr/bin/env python3
"""Real W8 validation, replacement and isolated encrypted recovery drill."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil
import socket
import subprocess
import tempfile
import time

from fixture import PASSWORD, app_id, identifier, table, write
from receiver import Receiver
from service import Client, ENV_NAME, RUNTIME_NAME, Service, free_port, sha256
from wire import WebSocket

BASE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("_w8_previous_common", BASE.parent / "w5_independent/common.py")
previous = importlib.util.module_from_spec(spec)
spec.loader.exec_module(previous)
Receipt, quote, seed, wait_for = previous.Receipt, previous.quote, previous.seed, previous.wait_for
KEY_ENV = "DIAMOND_W8_SYNTHETIC_BACKUP_KEY"
KEY = "W8 disposable backup passphrase only 87!"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--report", type=Path, default=BASE / "deployment_results.json")
    options = parser.parse_args()
    start = time.monotonic()
    root = Path(tempfile.mkdtemp(prefix="diamond-w8-independent-"))
    binary = root / "diamond"
    shutil.copy2(options.binary, binary)
    project = write(root / "project")
    with project.open("a") as manifest:
        manifest.write('''
[http_api]
version = 1
[http_api.operations.read]
function = "server.read"
auth = "token"
''')
    receipt = Receipt()
    receipt.secret(PASSWORD)
    receipt.secret(KEY)
    source_root, restored_root = root / "source", root / "restored"
    source_root.mkdir(mode=0o700)
    restored_root.mkdir(mode=0o700)
    source, restored = Service(source_root, binary, project), Service(restored_root, binary, project)
    services = [source, restored]
    receiver, ws, fatal = None, None, None
    source_hashes = {path.name: sha256(path) for path in sorted(BASE.glob("*.py"))}

    def cli(service, operation, *arguments, success=True):
        native = [] if operation == "project-recover" else ["--runtime-env", RUNTIME_NAME]
        return service.cli(operation, project, "--postgres-env", ENV_NAME, *native,
                           *arguments, success=success, timeout=120)

    def check(service, origin="https://app.example.invalid", success=True):
        return cli(service, "project-deploy-check", "--origin", origin,
                   "--identity-mailbox", service.mailbox, success=success)

    def value(result):
        if result[0] != 200 or "value" not in result[1]:
            raise AssertionError(("actual RPC rejected", result))
        return result[1]["value"]

    def inventory(service):
        return {"issues": service.sql_json("SELECT * FROM " + table("Issues") + " ORDER BY id"),
                "users": service.sql_json("SELECT username,email,verified,disabled,administrator FROM diamond_identity.users WHERE app=" + quote(app_id()) + " ORDER BY username"),
                "files": service.sql_json("SELECT id,parent,owner,state,length,sha256 FROM diamond_w6.objects WHERE app=" + quote(app_id()) + " ORDER BY id"),
                "jobs": service.sql_json("SELECT id,binding,parent,owner,payload,state,attempts FROM diamond_w7.jobs WHERE app=" + quote(app_id()) + " ORDER BY id"),
                "objects": {path.name: {"bytes": path.stat().st_size, "sha256": sha256(path)}
                            for path in service.objects.iterdir() if path.is_file()}}

    try:
        receiver = Receiver()
        for service in services:
            service.environment[KEY_ENV] = KEY
            service.runtime["http"] = {"inventory": {"origin": receiver.origin, "network": "loopback",
                "paths": ["/v1/"], "methods": ["POST"], "timeout_ms": 5000}}
        source.postgres()
        contract, build = source.build()
        source.cli("project-init", project, "--postgres-env", ENV_NAME)
        source.files_cli(initialize=True)
        source.cli("project-admin-add", project, "--postgres-env", ENV_NAME, "--user", "alice",
            "--email", "alice@qualification.invalid", "--password-stdin", input=PASSWORD + "\n")
        seed(source)
        accepted = check(source)
        accepted_value = json.loads(accepted.stdout)
        receipt.record("deployment-capabilities-without-launch", accepted_value.get("valid") is True and
            "Jobs" in accepted_value.get("capabilities", []) and not source.nodes and not source.workers,
            accepted_value, "validator accepts complete explicit authority and creates no listener/worker")
        for origin in ["http://app.example.invalid", "https://app.example.invalid/path", "https://user:pass@app.example.invalid", "https://app.example.invalid?query=1"]:
            rejected = check(source, origin, success=False)
            receipt.record("unsafe-origin-" + origin, rejected.returncode != 0,
                {"exit": rejected.returncode, "stderr": rejected.stderr.strip()}, "unsafe/noncanonical deployment origin is rejected")
        old_runtime = source.runtime["http"]
        source.runtime["http"] = {}
        rejected = check(source, success=False)
        receipt.record("missing-declared-native-grant", rejected.returncode != 0,
            {"exit": rejected.returncode, "stderr": rejected.stderr.strip()}, "declared outbound capability without native grant fails validation")
        source.runtime["http"] = old_runtime
        mismatch_root = root / "mismatched-project"
        shutil.copytree(project.parent, mismatch_root)
        mismatch_manifest = mismatch_root / "diamond.toml"
        mismatch_manifest.write_text(mismatch_manifest.read_text().replace("max_attempts = 3", "max_attempts = 2"))
        mismatch_project = mismatch_root / "diamond.toml"
        incompatible = source.cli("project-deploy-check", mismatch_project, "--postgres-env", ENV_NAME,
            "--runtime-env", RUNTIME_NAME, "--origin", "https://app.example.invalid",
            "--identity-mailbox", source.mailbox, success=False)
        refused_start = source.cli("serve", mismatch_project, "--postgres-env", ENV_NAME,
            "--runtime-env", RUNTIME_NAME, "--origin", source.origin,
            "--listen", "127.0.0.1:" + str(free_port()), "--identity-mailbox", source.mailbox, success=False)
        receipt.record("mismatched-contract-validation-and-startup-refuse", incompatible.returncode != 0 and refused_start.returncode != 0,
            {"validation_exit": incompatible.returncode, "validation_stderr": incompatible.stderr.strip(),
             "startup_exit": refused_start.returncode, "startup_stderr": refused_start.stderr.strip()},
            "changed durable handler declaration cannot validate or start against another current registry schema")
        source.start_node("A")
        source.start_node("B")
        alice = Client(source, contract["rpc_id"])
        alice.login("alice", PASSWORD)
        for node in ["A", "B"]:
            observed = {name: source.request(node, "/_diamond/health/" + name)
                        for name in ["live", "ready", "metrics"]}
            decoded = {name: {"status": result[0], "body": json.loads(result[2])} for name, result in observed.items()}
            receipt.record("live-readiness-redacted-metrics-" + node,
                all(item["status"] == 200 for item in decoded.values()) and
                decoded["ready"]["body"].get("contract") == contract["rpc_id"] and
                set(decoded["metrics"]["body"]) == {"requests", "active_requests", "active_sockets", "draining"},
                decoded, "both actual nodes report authoritative readiness and only aggregate redacted counters")
        page = source.request("A", "/")[2].decode()
        assets = sorted(set(re.findall(r'(?:src|href)="(/assets/[^" ]+)"', page)))
        shared_assets = []
        for path in assets:
            first, second = source.request("A", path), source.request("B", path)
            shared_assets.append({"path": path, "A": first[0], "B": second[0],
                                  "same": first[2] == second[2], "sha256": hashlib.sha256(first[2]).hexdigest()})
        receipt.record("shared-hashed-assets", bool(shared_assets) and all(item["A"] == 200 and item["B"] == 200 and item["same"] for item in shared_assets),
            shared_assets, "both nodes serve the same build's content-addressed browser assets")
        source.start_worker("one")
        source.start_worker("two")
        processed = value(alice.rpc("submit", identifier("issue-red-one"), "pre-backup completed work", "before-backup"))
        wait_for(lambda: source.sql("SELECT state FROM diamond_w7.jobs WHERE id=" + quote(processed)) == "done",
                 "pre-backup actual workers complete", timeout=15)
        ws = WebSocket("127.0.0.1", source.ports["A"], "/_diamond/events/issue_changes/" + identifier("issue-red-one") + "/0",
                       authority=source.authority, origin=source.origin, cookie=alice.cookie)
        with ThreadPoolExecutor(max_workers=1) as pool:
            closing = pool.submit(source.stop_node, "A")
            surviving = alice.rpc("read", {"tag": "issue", "value": identifier("issue-red-one")}, node="B")
            closing.result(timeout=10)
        try:
            closed = ws.receive()
            closed_ok = closed.get("kind") == "close"
        except EOFError:
            closed, closed_ok = {"closed": True}, True
        receipt.record("drain-with-live-socket-and-surviving-node", surviving[0] == 200 and closed_ok,
            {"surviving_rpc": surviving, "socket": closed, "shutdown": source.shutdowns[-1]},
            "SIGINT drains owned connection and exits cleanly while other server continues authenticated reads")
        ws.close()
        source.start_node("A")
        source.stop_worker("one", kill=True)
        after_worker_kill = value(alice.rpc("submit", identifier("issue-red-one"), "surviving worker effect", "surviving-worker"))
        wait_for(lambda: source.sql("SELECT state FROM diamond_w7.jobs WHERE id=" + quote(after_worker_kill)) == "done",
                 "remaining worker processes work after peer death", timeout=15)
        source.start_worker("replacement")
        receipt.record("replace-server-and-worker-with-current-artifact", source.request("A", "/_diamond/health/ready")[0] == 200 and
            after_worker_kill in receiver.snapshot()["effect_keys"] and all(p.poll() is None for p in source.workers.values()),
            {"workers_alive": len(source.workers), "job": after_worker_kill, "receiver": receiver.snapshot()},
            "current-version node replacement and surviving worker preserve shared correctness")

        body = b"W8 real restore attachment exact bytes\n" + bytes(range(256)) * 512
        file_hash = hashlib.sha256(body).hexdigest()
        begin = alice.request("A", "/_diamond/files/issue_files/begin", {"parent": identifier("issue-red-one"),
            "filename": "w8-synthetic.bin", "length": str(len(body)), "media_type": "application/octet-stream"})
        if begin[0] != 200:
            raise AssertionError(("real attachment reservation rejected", begin))
        file_id, upload = begin[1]["id"], begin[1]["upload"]
        receipt.secret(upload)
        sent = source.transfer("B", alice, "/_diamond/files/issue_files/" + file_id, body, upload=upload)
        finished = alice.request("A", "/_diamond/files/issue_files/" + file_id + "/finish", {}, {"X-Diamond-Upload": upload})
        if sent[0] != 200 or finished[0] != 200:
            raise AssertionError(("real attachment finalization failed", sent[0], finished))
        credential = alice.account("create_token", {"label": "W8 backup recovery synthetic credential", "operations": ["read"],
            "scopes": {"workspace": [identifier("red")]}, "lifetime_seconds": "3600"})
        bearer = value(credential)
        receipt.secret(bearer)
        if alice.api("read", bearer, [{"tag": "issue", "value": identifier("issue-red-one")}])[0] != 200:
            raise AssertionError("new real API credential did not work before snapshot")
        active_rejection = cli(source, "project-backup", "--out", root / "forbidden-active-backup",
                               "--backup-key-env", KEY_ENV, success=False)
        receipt.record("backup-refuses-active-instances", active_rejection.returncode != 0 and not (root / "forbidden-active-backup").exists(),
            {"exit": active_rejection.returncode, "stderr": active_rejection.stderr.strip()},
            "exclusive snapshot guard refuses running application without creating output")
        for name in list(source.workers):
            source.stop_worker(name)
        pending = value(alice.rpc("submit", identifier("issue-red-one"), "pending durable recovery effect", "pending-recovery"))
        old_cookie = alice.cookie
        for name in list(source.nodes):
            source.stop_node(name)
        expected = inventory(source)
        snapshot_start_wall = time.time()
        backup = cli(source, "project-backup", "--out", root / "backup", "--backup-key-env", KEY_ENV)
        snapshot_end_wall = time.time()
        names = sorted(path.name for path in (root / "backup").iterdir())
        encrypted = all(path.is_file() and path.suffix == ".gpg" and
                        (path.stat().st_mode & 0o777) == 0o600 and body not in path.read_bytes()
                        for path in (root / "backup").iterdir())
        receipt.record("quiesced-encrypted-private-snapshot", encrypted and "manifest.json.gpg" in names and
            "database.dump.gpg" in names and not (root / "backup/plaintext-work").exists(),
            {"result": json.loads(backup.stdout), "files": names, "expected_inventory": expected},
            "quiesced snapshot consists of private encrypted artifacts with plaintext work removed")
        source.start_node("A")
        post_snapshot = alice.rpc("edit", {"tag": "issue", "value": identifier("issue-red-one")}, {"rank": "77"})
        if post_snapshot[0] != 200:
            raise AssertionError(("post-snapshot data-loss boundary edit failed", post_snapshot))
        source.stop_node("A")
        source_after_snapshot = inventory(source)
        restored.postgres()
        def empty_restore_target():
            count = restored.sql("SELECT count(*) FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname NOT LIKE 'pg_toast%' AND n.nspname NOT LIKE 'pg_temp%'")
            return count == "0" and not list(restored.objects.iterdir())
        restored.environment[KEY_ENV] = "Wrong disposable backup passphrase 87!"
        bad_key = cli(restored, "project-restore", "--from", root / "backup", "--backup-key-env", KEY_ENV, success=False)
        receipt.record("wrong-backup-key-preserves-empty-target", bad_key.returncode != 0 and empty_restore_target(),
            {"exit": bad_key.returncode, "stderr": bad_key.stderr.strip(), "target_empty": empty_restore_target()},
            "wrong decryption authority fails before creating database/object state")
        restored.environment[KEY_ENV] = KEY
        shutil.copytree(root / "backup", root / "tampered-backup")
        tampered = root / "tampered-backup/database.dump.gpg"
        changed = bytearray(tampered.read_bytes())
        changed[-1] ^= 1
        tampered.write_bytes(changed)
        bad_snapshot = cli(restored, "project-restore", "--from", root / "tampered-backup", "--backup-key-env", KEY_ENV, success=False)
        receipt.record("tampered-backup-preserves-empty-target", bad_snapshot.returncode != 0 and empty_restore_target(),
            {"exit": bad_snapshot.returncode, "stderr": bad_snapshot.stderr.strip(), "target_empty": empty_restore_target()},
            "modified encrypted data is rejected before database restore or object publication")
        restore_start = time.monotonic()
        restore = cli(restored, "project-restore", "--from", root / "backup", "--backup-key-env", KEY_ENV)
        restored_inventory = inventory(restored)
        restore_report = json.loads(restore.stdout)
        credential_counts = restored.sql_json("SELECT (SELECT count(*) FROM diamond_identity.sessions) AS sessions," +
            "(SELECT count(*) FROM diamond_identity.credentials) AS credentials,(SELECT count(*) FROM diamond_identity.challenges) AS challenges")
        paused = restored.sql("SELECT paused FROM diamond_w7.apps WHERE app=" + quote(app_id()))
        receipt.record("isolated-restore-manifest-bytes-and-recovery-policy", restored_inventory == expected and paused == "t" and
            all(count == 0 for count in credential_counts[0].values()) and restore_report.get("jobs_paused") is True,
            {"expected": expected, "actual": restored_inventory, "paused": paused,
             "credential_counts": credential_counts, "restore": restore_report,
             "source_changed_after_snapshot": source_after_snapshot["issues"] != expected["issues"],
             "restore_seconds": time.monotonic() - restore_start,
             "snapshot_duration_seconds": snapshot_end_wall - snapshot_start_wall,
             "snapshot_to_restore_seconds": time.time() - snapshot_end_wall},
            "different empty DB/object roots restore exact snapshot inventory; later source edit is absent; sessions/tokens/challenges revoked and jobs paused")
        restored.start_node("A")
        restored.start_node("B")
        stale = Client(restored, contract["rpc_id"])
        stale.cookie = old_cookie
        stale.csrf = alice.csrf
        stale_session = stale.rpc("read", {"tag": "issue", "value": identifier("issue-red-one")})
        fresh = Client(restored, contract["rpc_id"])
        fresh.login("alice", PASSWORD)
        forbidden_bearer = fresh.api("read", bearer, [{"tag": "issue", "value": identifier("issue-red-one")}], node="B")
        downloaded = restored.request("B", "/_diamond/files/issue_files/" + file_id, headers={"Cookie": fresh.cookie})
        receipt.record("restored-real-account-file-and-revoked-credentials", stale_session[0] == 401 and forbidden_bearer[0] == 401 and
            downloaded[0] == 200 and downloaded[2] == body and hashlib.sha256(downloaded[2]).hexdigest() == file_hash,
            {"old_session": stale_session[0], "old_api": forbidden_bearer[0], "file_status": downloaded[0],
             "bytes": len(downloaded[2]), "sha256": hashlib.sha256(downloaded[2]).hexdigest()},
            "password-backed account can sign in anew; old session/API deny; other node downloads independently verified exact attachment bytes")
        before = len(receiver.snapshot()["attempts"])
        restored.start_worker("paused")
        time.sleep(1)
        paused_state = restored.sql("SELECT state FROM diamond_w7.jobs WHERE id=" + quote(pending))
        receipt.record("restore-never-implicitly-replays-pending-work", paused_state == "pending" and len(receiver.snapshot()["attempts"]) == before,
            {"state": paused_state, "new_receiver_attempts": len(receiver.snapshot()["attempts"]) - before},
            "even a started worker cannot claim restored work before explicit recovery policy")
        restored.stop_worker("paused")
        active_recover = cli(restored, "project-recover", "--policy", "resume", success=False)
        receipt.record("recovery-refuses-active-instances", active_recover.returncode != 0 and
            restored.sql("SELECT paused FROM diamond_w7.apps WHERE app=" + quote(app_id())) == "t",
            {"exit": active_recover.returncode, "stderr": active_recover.stderr.strip()},
            "maintenance/recovery cannot race running application instances or implicitly unpause jobs")
        for name in list(restored.nodes):
            restored.stop_node(name)
        nonempty_before = inventory(restored)
        nonempty = cli(restored, "project-restore", "--from", root / "backup", "--backup-key-env", KEY_ENV, success=False)
        receipt.record("restore-refuses-nonempty-destination", nonempty.returncode != 0 and inventory(restored) == nonempty_before,
            {"exit": nonempty.returncode, "stderr": nonempty.stderr.strip()},
            "restoring over existing application data is rejected without altering durable inventory")
        resumed = cli(restored, "project-recover", "--policy", "resume")
        restored.start_worker("resumed-one")
        restored.start_worker("resumed-two")
        wait_for(lambda: restored.sql("SELECT state FROM diamond_w7.jobs WHERE id=" + quote(pending)) == "done",
                 "explicit recovery resumes pending work", timeout=15)
        receipt.record("explicit-resume-releases-restored-durable-work", pending in receiver.snapshot()["effect_keys"],
            {"exit": resumed.returncode, "receiver": receiver.snapshot()},
            "operator-selected at-least-once resume runs restored pending job through actual external receiver")
    except Exception as error:
        fatal = repr(error)
    finally:
        if ws:
            ws.close()
        if receiver:
            receiver.release.set()
        for service in services:
            service.close()
        if receiver:
            receiver.close()
    source.shutdowns.extend(restored.shutdowns)
    source.cleanup_errors.extend(restored.cleanup_errors)
    report = receipt.report(source, sha256(binary), error=fatal, elapsed_seconds=time.monotonic() - start,
        temporary_root=str(root), source_sha256=source_hashes,
        coverage_gaps=["Local Linux/Unix-socket PostgreSQL and shared filesystem only; no provisioned TLS edge or host deployment.",
            "No old/new schema coexistence; only same current-artifact process replacement.",
            "Reference tracker/catalog browser journeys and clean installation are verified separately."])
    options.report.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"status": report["status"], "observations": len(receipt.results), "error": fatal,
                      "root": str(root), "elapsed_seconds": report["elapsed_seconds"]}), flush=True)
    if report["status"] == "PASS":
        shutil.rmtree(root)
    return report["status"] != "PASS"


if __name__ == "__main__":
    raise SystemExit(main())
