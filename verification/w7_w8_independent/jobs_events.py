#!/usr/bin/env python3
"""Independent bounded W7 campaign through actual CLI, PG, HTTP and WS."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import socket
import tempfile
import time

from fixture import APP, PASSWORD, app_id, identifier as nominal_id, short_ids, table, write
from receiver import Receiver
from service import Client, ENV_NAME, Service, sha256
from wire import WebSocket

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[1]
spec = importlib.util.spec_from_file_location("_w7_w8_previous_common", BASE.parent / "w5_independent/common.py")
previous = importlib.util.module_from_spec(spec)
spec.loader.exec_module(previous)
Receipt, quote, seed, wait_for = previous.Receipt, previous.quote, previous.seed, previous.wait_for


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--report", type=Path, default=BASE / "jobs_events_results.json")
    parser.add_argument("--short-ids", action="store_true", help="replay original legal short-ID WebSocket failure")
    options = parser.parse_args()
    if options.short_ids:
        short_ids()
    source_hashes = {path.name: sha256(path) for path in sorted(BASE.glob("*.py"))}
    start = time.monotonic()
    root = Path(tempfile.mkdtemp(prefix="diamond-w7-independent-"))
    binary = root / "diamond"
    shutil.copy2(options.binary, binary)
    project = write(root / "project")
    with project.open("a") as manifest:
        manifest.write('''
[http_api]
version = 1
[http_api.operations.submit]
function = "server.submit"
auth = "token"
''')
    service = Service(root, binary, project)
    receipt = Receipt()
    receipt.secret(PASSWORD)
    receiver = None
    sockets = []
    fatal = None
    clients = {}

    def jobs():
        return service.sql_json("SELECT * FROM diamond_w7.jobs WHERE app=" + quote(app_id()) + " ORDER BY id")

    def job(identifier):
        return next((row for row in jobs() if row["id"] == identifier), None)

    def rank(parent="issue-red-one"):
        return int(service.sql("SELECT rank FROM " + table("Issues") + " WHERE id=" + quote(nominal_id(parent))))

    def rpc(operation, *arguments, user="alice", node="A"):
        if operation not in {"status", "cancel"}:
            arguments = (nominal_id(arguments[0]), *arguments[1:])
        return clients[user].rpc(operation, *arguments, node=node)

    def value(outcome):
        if outcome[0] != 200 or not isinstance(outcome[1], dict) or "value" not in outcome[1]:
            raise AssertionError(("expected successful real RPC", outcome))
        return outcome[1]["value"]

    def connect(node, parent="issue-red-one", cursor="0", user="alice", origin=True):
        connection = WebSocket("127.0.0.1", service.ports[node],
            "/_diamond/events/issue_changes/" + nominal_id(parent) + "/" + cursor,
            authority=service.authority,
            origin=service.origin if origin is True else origin,
            cookie=clients[user].cookie if user else None)
        sockets.append(connection)
        return connection

    try:
        receiver = Receiver()
        service.runtime["http"] = {"inventory": {"origin": receiver.origin, "network": "loopback",
            "paths": ["/v1/"], "methods": ["POST"], "timeout_ms": 5000}}
        service.postgres()
        contract, build = service.build()
        service.cli("project-init", project, "--postgres-env", ENV_NAME)
        service.files_cli(initialize=True)
        for username in ["alice", "bob", "carol", "outsider"]:
            service.cli("project-admin-add", project, "--postgres-env", ENV_NAME, "--user", username,
                "--email", username + "@qualification.invalid", "--password-stdin", input=PASSWORD + "\n")
        seed(service)
        service.start_node("A")
        service.start_node("B")
        for username in ["alice", "bob", "carol", "outsider"]:
            client = Client(service, contract["rpc_id"])
            client.login(username, PASSWORD)
            clients[username] = client

        initial_rank, initial_jobs = rank(), len(jobs())
        aborted = rpc("submit_and_abort", "issue-red-one", "rolled back exact", "rollback-key")
        receipt.record("transaction-abort-has-no-record-or-outbox", aborted[0] >= 400 and
            rank() == initial_rank and len(jobs()) == initial_jobs,
            {"rpc": aborted, "rank": rank(), "job_count": len(jobs())},
            "propagated failing protected get rolls back both earlier rank edit and outbox entry")

        denied = rpc("submit", "issue-blue-one", "forbidden payload", "outsider-key", user="bob")
        reader = rpc("submit", "issue-red-one", "reader payload", "reader-key", user="carol")
        malformed = rpc("submit", "issue-red-one", {"forged": "authority"}, "bad-shape")
        receipt.record("submit-fresh-scope-and-wire-shape", denied[0] >= 400 and reader[0] >= 400 and
            malformed[0] >= 400 and len(jobs()) == initial_jobs,
            {"cross_workspace": denied, "read_only": reader, "malformed": malformed, "jobs": len(jobs())},
            "cross-workspace, reader-only and malformed payload calls enqueue no work")

        identifier = value(rpc("edit_and_submit", "issue-red-one", "committed exact 雪", "commit-key", node="B"))
        same = value(rpc("submit", "issue-red-one", "committed exact 雪", "commit-key"))
        receipt.record("committed-outbox-and-deduplicated-submission", rank() == initial_rank + 1 and
            len(jobs()) == initial_jobs + 1 and identifier == same and job(identifier)["state"] == "pending",
            {"rank": rank(), "id": identifier, "same_id": same, "row": job(identifier)},
            "record edit and durable pending outbox commit together; same owner binding/key identifies one job")
        cancelled = rpc("cancel", identifier, node="B")
        receipt.record("cancel-queued-across-node", cancelled[0] == 200 and job(identifier)["state"] == "cancelled",
            {"rpc": cancelled, "row": job(identifier)}, "current editor cancels pending work through other node")

        a, b = connect("A"), connect("B", user="bob")
        receipt.record("two-real-websocket-upgrades", a.status == 101 and b.status == 101,
            {"A": a.status, "B": b.status}, "authenticated exact-origin sockets upgrade on both nodes")
        if a.status != 101 or b.status != 101:
            raise AssertionError("valid WebSocket handshake rejected")
        payload = "typed cross-node event 雪 <script> remains text"
        emitted = value(rpc("emit", "issue-red-one", payload, node="B"))
        first, second = a.receive(), b.receive()
        cursor = first.get("cursor")
        receipt.record("typed-durable-cross-node-event", first == second and first.get("value") == payload and
            isinstance(cursor, str) and int(cursor) > 0,
            {"emit": emitted, "A": first, "B": second},
            "each authorized client receives identical typed data and authoritative decimal-string durable cursor")
        for label, kwargs in [("missing-origin", {"origin": None}),
            ("hostile-origin", {"origin": "https://hostile.invalid"}),
            ("anonymous", {"user": None}), ("cross-workspace", {"user": "outsider"})]:
            connection = connect("A", **kwargs)
            receipt.record("websocket-denial-" + label, connection.status >= 400,
                {"status": connection.status}, "handshake discloses no protected event")
            connection.close()
        denied = rpc("emit", "issue-red-one", "reader forged outbound", user="carol")
        receipt.record("read-only-cannot-publish-protected-event", denied[0] >= 400,
            denied, "parent read permission alone cannot publish mutation/event")
        a.close()
        b.close()
        later_payload = "missed durable event, reconnect"
        later_cursor = value(rpc("emit", "issue-red-one", later_payload))
        replay = connect("B", cursor=cursor)
        replayed = replay.receive()
        receipt.record("reconnect-replays-missed-durable-event", replayed.get("value") == later_payload and
            int(replayed["cursor"]) > int(cursor), replayed, "durable cursor replay bridges disconnected delivery")
        replay.close()
        ahead = connect("A", cursor="999999999")
        ahead_message = ahead.receive() if ahead.status == 101 else {"status": ahead.status}
        receipt.record("ahead-cursor-resynchronizes", ahead_message.get("resync") is True,
            ahead_message, "cursor beyond authoritative store requests explicit resynchronization")
        ahead.close()

        revoked = connect("B", user="bob", cursor=str(later_cursor))
        before_revoke = time.monotonic()
        service.sql("DELETE FROM " + table("Memberships") + " WHERE \"user\"='bob' AND workspace=" + quote(nominal_id("red")))
        time.sleep(5.1)
        value(rpc("emit", "issue-red-one", "post-revocation forbidden delivery"))
        try:
            observed = revoked.receive()
            safe = observed.get("kind") == "close"
        except (EOFError, socket.timeout):
            observed, safe = {"closed_or_no_delivery": True}, True
        receipt.record("live-membership-revocation-bound", safe,
            {"elapsed_seconds": time.monotonic() - before_revoke, "message": observed},
            "after five-second bound revoked member receives no subsequent protected event")
        revoked.close()

        credential = clients["alice"].account("create_token", {"label": "W7 restricted durable job fixture",
            "operations": ["submit"], "scopes": {"workspace": [nominal_id("red")]}, "lifetime_seconds": "3600"})
        bearer = value(credential)
        receipt.secret(bearer)
        count_before_scoped = len(jobs())
        forbidden_scope = clients["alice"].api("submit", bearer,
            [nominal_id("issue-blue-one"), "forbidden blue scope", "outside-scope-key"])
        restricted_job = value(clients["alice"].api("submit", bearer,
            [nominal_id("issue-red-two"), "restricted scope durable payload", "restricted-scope-key"]))
        moved = clients["alice"].rpc("move", {"tag": "issue", "value": nominal_id("issue-red-two")},
            {"workspace": {"tag": "workspace", "value": nominal_id("blue")},
             "project": {"tag": "project", "value": nominal_id("project-blue-visible")}})
        receipt.record("scoped-api-job-admission-and-parent-move", forbidden_scope[0] >= 400 and
            len(jobs()) == count_before_scoped + 1 and moved[0] == 200,
            {"forbidden_scope": forbidden_scope, "restricted_row": job(restricted_job), "moved": moved},
            "token restricted to red cannot submit blue; authorized owner may separately move admitted parent to blue before execution")
        service.start_worker("one")
        service.start_worker("two")
        wait_for(lambda: job(restricted_job)["state"] == "dead", "worker preserves real credential attenuation", timeout=10)
        receipt.record("worker-preserves-captured-api-restrictions", restricted_job not in receiver.snapshot()["effect_keys"] and
            not any(item["key"] == restricted_job for item in receiver.snapshot()["attempts"]),
            {"row": job(restricted_job), "receiver": receiver.snapshot()},
            "fresh principal still has blue membership but captured red-only scope prevents disclosure/effect after parent move")
        before_changes = rank()
        change_socket = connect("A", cursor=value(rpc("emit", "issue-red-one", "before checked worker changes")))
        changes = [value(rpc("submit_change", "issue-red-one", "transactional-change-" + str(index),
            "change-" + str(index))) for index in range(3)]
        wait_for(lambda: all(job(key)["state"] == "done" for key in changes), "transactional handlers complete", timeout=15)
        changed_messages = [change_socket.receive() for _ in changes]
        receipt.record("transactional-worker-record-and-event-completion", rank() == before_changes + len(changes) and
            {item.get("value") for item in changed_messages} == {"transactional-change-" + str(index) for index in range(3)},
            {"rank_before": before_changes, "rank_after": rank(), "rows": [job(key) for key in changes], "messages": changed_messages},
            "database-only checked handlers commit each rank edit/event with durable done outcome")
        before_aborted_change = rank()
        aborted_change = value(rpc("submit_abort_change", "issue-red-one", "aborted change must never emit", "aborted-change"))
        wait_for(lambda: job(aborted_change)["state"] == "dead", "transactional failure exhausts bounded attempts", timeout=15)
        change_socket.socket.settimeout(1)
        try:
            aborted_message = change_socket.receive()
        except socket.timeout:
            aborted_message = None
        receipt.record("failed-worker-handler-rolls-back-record-and-event", rank() == before_aborted_change and
            aborted_message is None and job(aborted_change)["attempts"] == 3,
            {"rank_before": before_aborted_change, "rank_after": rank(), "row": job(aborted_change), "message": aborted_message},
            "each propagated failure rolls back protected edit/event; bounded job outcome remains visible")
        change_socket.close()
        receiver.hold = True
        receiver.release.clear()
        parallel = [value(rpc("submit", parent, "parallel independent " + parent, "parallel-" + parent))
                    for parent in ["issue-red-one", "issue-blue-one"]]
        wait_for(lambda: all(key in receiver.snapshot()["effect_keys"] for key in parallel),
                 "both one-job workers reach independently held receiver", timeout=10)
        receipt.record("two-workers-execute-concurrently", all(job(key)["state"] == "running" for key in parallel),
            {"rows": [job(key) for key in parallel], "receiver": receiver.snapshot()},
            "two processes each execute one job while both actual external acknowledgments are withheld")
        receiver.hold = False
        receiver.release.set()
        wait_for(lambda: all(job(key)["state"] == "done" for key in parallel), "parallel jobs complete", timeout=10)
        normal = [value(rpc("submit", "issue-red-one", f"normal-{index}-雪", f"normal-{index}",
            node="A" if index % 2 else "B")) for index in range(5)]
        wait_for(lambda: all(job(key)["state"] == "done" for key in normal), "two workers complete real jobs", timeout=30)
        normal_observation = receiver.snapshot()
        receipt.record("two-workers-complete-and-cancel-remains-inert", all(1 <= job(key)["attempts"] <= 3 for key in normal) and
            all(len({item["body_sha256"] for item in normal_observation["attempts"] if item["key"] == key}) == 1 for key in normal) and
            set(normal).issubset(normal_observation["effect_keys"]) and identifier not in normal_observation["effect_keys"] and
            job(identifier)["state"] == "cancelled",
            {"rows": [job(key) for key in normal], "receiver": normal_observation},
            "two actual worker processes complete bounded at-least-once delivery with stable payload/key and one receiver business effect; cancelled job never dispatches")

        receiver.recorded.clear()
        receiver.release.clear()
        receiver.hold = True
        before_crash_rank = rank()
        recovering = value(rpc("submit", "issue-red-one", "effect received before worker death", "after-effect-crash"))
        if not receiver.recorded.wait(10):
            raise AssertionError("receiver did not observe real side effect")
        before = receiver.snapshot()
        receipt.record("real-effect-precedes-unacknowledged-crash", recovering in before["effect_keys"] and
            job(recovering)["state"] == "running", {"row": job(recovering), "receiver": before},
            "receiver independently commits destination effect while worker is blocked before HTTP acknowledgment")
        # Neither process identity nor scheduler ordering supplies the oracle:
        # terminate both owned workers to ensure the in-flight holder is lost.
        service.stop_worker("one", kill=True)
        service.stop_worker("two", kill=True)
        receiver.hold = False
        receiver.release.set()
        service.start_worker("survivor")
        recovery_start = time.monotonic()
        wait_for(lambda: job(recovering)["state"] == "done", "actual lease expiry recovery", timeout=45)
        after = receiver.snapshot()
        attempts = [item for item in after["attempts"] if item["key"] == recovering]
        receipt.record("lease-recovery-destination-dedup-and-atomic-completion", len(attempts) >= 2 and
            len({item["body_sha256"] for item in attempts}) == 1 and
            after["effect_keys"].count(recovering) == 1 and rank() == before_crash_rank and
            job(recovering)["attempts"] >= 2,
            {"row": job(recovering), "receiver_attempts": attempts,
             "business_effects_for_key": after["effect_keys"].count(recovering),
             "rank": rank(), "before_rank": before_crash_rank,
             "recovery_seconds": time.monotonic() - recovery_start},
            "lost acknowledgment yields repeated actual delivery with stable key and one receiver effect; external-only handler makes no application write")
        receiver.fail = True
        failed = value(rpc("submit", "issue-red-one", "destination failure payload", "bounded-failure"))
        wait_for(lambda: job(failed)["state"] == "dead", "bounded retries reach dead letter", timeout=20)
        failing = [item for item in receiver.snapshot()["attempts"] if item["key"] == failed]
        receipt.record("bounded-retry-visible-dead-letter", job(failed)["attempts"] == 3 and len(failing) == 3 and
            failed not in receiver.snapshot()["effect_keys"],
            {"row": job(failed), "actual_receiver_attempts": len(failing)},
            "declared three-attempt bound becomes operator-visible dead state; failing destination records no business effect")
        receiver.fail = False
        service.stop_worker("survivor")
        revoked_job = value(rpc("submit", "issue-blue-one", "revoked owner must not dispatch", "revoke-before-work"))
        service.sql("DELETE FROM " + table("Memberships") + " WHERE \"user\"='alice' AND workspace=" + quote(nominal_id("blue")))
        service.start_worker("restricted")
        wait_for(lambda: job(revoked_job)["state"] == "dead", "fresh worker permission recheck", timeout=20)
        receipt.record("worker-uses-fresh-restricted-authority", revoked_job not in receiver.snapshot()["effect_keys"] and
            not any(item["key"] == revoked_job for item in receiver.snapshot()["attempts"]),
            {"row": job(revoked_job), "receiver_has_effect": revoked_job in receiver.snapshot()["effect_keys"]},
            "revoking owner membership after submission prevents later protected job execution and outbound effect")
        status = rpc("status", failed, node="B")
        receipt.record("cross-node-operator-status", status[0] == 200 and '"dead"' in str(status[1]),
            status, "declared status operation exposes bounded failed outcome across application nodes")
    except Exception as error:
        fatal = repr(error)
    finally:
        for connection in sockets:
            connection.close()
        if receiver:
            receiver.release.set()
        service.close()
        if receiver:
            receiver.close()
    report = receipt.report(service, sha256(binary), error=fatal,
        elapsed_seconds=time.monotonic() - start, temporary_root=str(root),
        receiver=receiver.snapshot() if receiver else None,
        source_sha256=source_hashes,
        coverage_gaps=["No browser component subscription API exercised; raw WebSocket endpoint only.",
            "No slow-consumer saturation, retention expiration, job payload version mismatch, or live stale-holder acknowledgment schedule yet."])
    options.report.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"status": report["status"], "observations": len(receipt.results),
                      "error": fatal, "root": str(root), "elapsed_seconds": report["elapsed_seconds"]}), flush=True)
    if report["status"] == "PASS":
        shutil.rmtree(root)
    return report["status"] != "PASS"


if __name__ == "__main__":
    raise SystemExit(main())
