#!/usr/bin/env python3
"""Real sockets: admission, expiration, retained-window resync and slow clients."""
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

from fixture import PASSWORD, app_id, identifier, write
from receiver import Receiver
from service import Client, ENV_NAME, Service, sha256
from wire import WebSocket

BASE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("_w7_limits_common", BASE.parent / "w5_independent/common.py")
previous = importlib.util.module_from_spec(spec)
spec.loader.exec_module(previous)
Receipt, quote, seed, wait_for = previous.Receipt, previous.quote, previous.seed, previous.wait_for


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--report", type=Path, default=BASE / "events_limits_results.json")
    options = parser.parse_args()
    root = Path(tempfile.mkdtemp(prefix="diamond-w7-event-limits-"))
    binary = root / "diamond"
    shutil.copy2(options.binary, binary)
    project = write(root / "project")
    service = Service(root, binary, project)
    receipt, receiver, connections, fatal = Receipt(), None, [], None
    receipt.secret(PASSWORD)
    started = time.monotonic()

    def metrics():
        status, _, body = service.request("A", "/_diamond/health/metrics")
        if status != 200:
            raise AssertionError(("metrics unavailable", status))
        return json.loads(body)

    def connect(cursor="0", cookie=None, receive_buffer=None):
        connection = WebSocket("127.0.0.1", service.ports["A"], "/_diamond/events/issue_changes/" +
            identifier("issue-red-one") + "/" + str(cursor), authority=service.authority,
            origin=service.origin, cookie=cookie or alice.cookie, receive_buffer=receive_buffer)
        connections.append(connection)
        return connection

    def emit(payload):
        result = alice.rpc("emit", identifier("issue-red-one"), payload, node="B")
        if result[0] != 200:
            raise AssertionError(("real event publication failed", result))
        return result[1]["value"]

    try:
        receiver = Receiver()
        service.runtime["http"] = {"inventory": {"origin": receiver.origin, "network": "loopback", "paths": ["/v1/"], "methods": ["POST"]}}
        service.postgres()
        contract, _ = service.build()
        service.cli("project-init", project, "--postgres-env", ENV_NAME)
        service.files_cli(initialize=True)
        for username in ["alice", "bob"]:
            service.cli("project-admin-add", project, "--postgres-env", ENV_NAME, "--user", username, "--email", username + "@qualification.invalid",
                        "--password-stdin", input=PASSWORD + "\n")
        seed(service)
        service.start_node("A")
        service.start_node("B")
        alice = Client(service, contract["rpc_id"])
        alice.login("alice", PASSWORD)
        bob = Client(service, contract["rpc_id"])
        bob.login("bob", PASSWORD)
        for cursor in ["-1", "01", "9223372036854775808", "not-a-cursor"]:
            connection = connect(cursor)
            receipt.record("invalid-cursor-" + cursor, connection.status >= 400,
                           {"status": connection.status}, "invalid/noncanonical cursor cannot select event history")
            connection.close()
        admitted = [connect() for _ in range(32)]
        excess = connect()
        during = metrics()
        ordinary = service.request("A", "/")[0]
        receipt.record("bounded-socket-admission-preserves-http", all(c.status == 101 for c in admitted) and
            excess.status == 503 and during["active_sockets"] == 32 and ordinary == 200,
            {"accepted": sum(c.status == 101 for c in admitted), "excess": excess.status, "metrics": during, "ordinary_status": ordinary},
            "32 sockets use separate bounded quota; excess rejected while ordinary serving remains usable")
        for connection in admitted + [excess]:
            connection.close()
        wait_for(lambda: metrics()["active_sockets"] == 0, "all closed socket permits return", timeout=6)

        expiring = Client(service, contract["rpc_id"])
        expiring.login("alice", PASSWORD)
        live = connect(cookie=expiring.cookie)
        changed = service.sql("UPDATE diamond_identity.sessions SET expires=clock_timestamp()-INTERVAL '1 second' WHERE app=" +
                    quote(app_id()) + " AND username='alice' RETURNING username").splitlines()
        if not changed or any(item != "alice" for item in changed):
            raise AssertionError("owned session expiration fixture changed no actual Alice sessions")
        expire_start = time.monotonic()
        published = bob.rpc("emit", identifier("issue-red-one"), "never disclose after this independently expired session", node="B")
        if published[0] != 200:
            raise AssertionError(("independent nonexpired emitter denied", published))
        try:
            observed = live.receive()
            denied = observed.get("kind") == "close"
        except EOFError:
            observed, denied = {"closed": True}, True
        reopened = connect(cookie=expiring.cookie)
        receipt.record("database-clock-session-expiration", denied and reopened.status >= 400 and time.monotonic() - expire_start < 5,
            {"message": observed, "new_handshake": reopened.status, "elapsed_seconds": time.monotonic() - expire_start},
            "authoritative expired session closes existing connection within five seconds and cannot reconnect")
        live.close()
        reopened.close()
        alice.login("alice", PASSWORD)

        first = emit("cursor that will become unavailable")
        maximum = emit("retained latest value")
        service.sql("DELETE FROM diamond_w7.events WHERE app=" + quote(app_id()) + " AND cursor<" + maximum)
        stale = connect(first)
        resync = stale.receive()
        receipt.record("retained-window-floor-resynchronization", resync == {"resync": True, "cursor": maximum},
            resync, "a formerly valid cursor below independently advanced retention floor requires explicit resync")
        stale.close()
        unauthorized_inbound = connect(maximum)
        before_count = service.sql("SELECT count(*) FROM diamond_w7.events WHERE app=" + quote(app_id()))
        unauthorized_inbound.send_json({"operation": "emit", "principal": "admin", "value": "forged inbound event"})
        try:
            inbound_message = unauthorized_inbound.receive()
            inbound_denied = inbound_message.get("kind") == "close"
        except EOFError:
            inbound_message, inbound_denied = {"closed": True}, True
        receipt.record("unsolicited-inbound-cannot-publish", inbound_denied and
            service.sql("SELECT count(*) FROM diamond_w7.events WHERE app=" + quote(app_id())) == before_count,
            inbound_message, "outbound-only event endpoint rejects client actions without creating application/event state")
        unauthorized_inbound.close()
        wait_for(lambda: metrics()["active_sockets"] == 0, "earlier sockets drain", timeout=6)

        slow = connect(maximum, receive_buffer=1024)
        # Never read this socket. All emitted messages go through real Diamond
        # RPC and its durable event transaction, not fabricated server frames.
        burst_start = time.monotonic()
        last = None
        for index in range(320):
            last = emit(f"slow-client-{index:04d}:" + "x" * 15800)
        wait_for(lambda: metrics()["active_sockets"] == 0, "bounded-send timeout disconnects unread socket", timeout=12)
        elapsed = time.monotonic() - burst_start
        fresh = alice.rpc("read", {"tag": "issue", "value": identifier("issue-red-one")}, node="A")
        receipt.record("real-slow-consumer-disconnect-and-service-recovery", metrics()["active_sockets"] == 0 and fresh[0] == 200,
            {"published_messages": 320, "payload_bytes_each_at_least": 15800, "last_cursor": last,
             "elapsed_seconds": elapsed, "metrics": metrics(), "fresh_rpc_status": fresh[0]},
            "unread client exhausts bounded transport send time and releases permit; ordinary authenticated work continues")
        slow.close()
    except Exception as error:
        fatal = repr(error)
    finally:
        for connection in connections:
            connection.close()
        service.close()
        if receiver:
            receiver.close()
    report = receipt.report(service, sha256(binary), error=fatal, elapsed_seconds=time.monotonic() - started,
        temporary_root=str(root), generated_budget={"seed": 20261022, "slow_consumer_publications": 320, "accepted_socket_bound": 32},
        coverage_gaps=["Retention floor is advanced as owned database fixture state; the automatic 10000-row trim threshold is not exercised.",
                       "No Internet proxy/TLS edge, thirty-minute wall-clock connection expiry, or arbitrary network failure scheduling."])
    options.report.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"status": report["status"], "observations": len(receipt.results), "error": fatal,
                      "root": str(root), "elapsed_seconds": report["elapsed_seconds"]}), flush=True)
    if report["status"] == "PASS":
        shutil.rmtree(root)
    return report["status"] != "PASS"


if __name__ == "__main__":
    raise SystemExit(main())
