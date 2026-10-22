#!/usr/bin/env python3
"""Real held-snapshot, bounded-admission, delivery and PG commit-ack faults."""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import threading
import time

from model import corpus
from pg_proxy import PostgresProxy
from queries import seed, table
from query_fixture import write
from scalars import constrain_runtime
from service import Client, ENV_NAME, PASSWORD, Service, sha256

ROOT = Path(__file__).resolve().parents[2]


def wait_for(predicate, label, timeout=5):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        if predicate():
            return
        time.sleep(.015)
    raise AssertionError(("fixture schedule not reached", label))


def raw_mutation(service, client, contract, issue_id):
    body = json.dumps({"contract": contract, "arguments": [issue_id]}).encode()
    request = (f"POST /_diamond/rpc/increment HTTP/1.1\r\nHost: {service.authority}\r\nOrigin: {service.origin}\r\n"
               f"Cookie: {client.cookie}\r\nX-Diamond-CSRF: {client.csrf}\r\nContent-Type: application/json\r\n"
               f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n").encode() + body
    connection = socket.create_connection(("127.0.0.1", service.ports["A"]), timeout=5)
    connection.sendall(request)
    return connection


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, default=ROOT / "target/debug/diamond")
    parser.add_argument("--report", type=Path, required=True)
    options = parser.parse_args()
    root = Path(tempfile.mkdtemp(prefix="diamond-w4-independent-fault-"))
    binary = root / "diamond"
    shutil.copy2(options.binary, binary)
    project = write(root / "project")
    service = Service(root, binary, project)
    proxy, raw, results, fatal = None, None, [], None

    def record(name, okay, observed, expected):
        results.append({"name": name, "status": "PASS" if okay else "FAIL", "observed": observed, "expected": expected})

    def rank(issue):
        return int(service.sql(f"SELECT rank FROM {table('Issues')} WHERE id='{issue}'"))

    try:
        service.postgres()
        contract, _ = service.build()
        service.cli("project-init", project, "--postgres-env", ENV_NAME)
        for user in ["alice", "bob"]:
            service.cli("project-user-add", project, "--postgres-env", ENV_NAME, "--user", user, "--password-stdin", input=PASSWORD + "\n")
        seed(service, corpus(103))
        constrain_runtime(service)
        directory = root / "proxy"
        directory.mkdir(mode=0o700)
        proxy = PostgresProxy(directory / ".s.PGSQL.55449", service.sockets / ".s.PGSQL.55449")
        proxy_configuration = service.configuration.replace(str(service.sockets), str(directory))
        service.start_node("A", configuration=proxy_configuration)
        service.start_node("B")
        alice, bob = Client(service, contract), Client(service, contract)
        alice.login("alice")
        bob.login("bob")
        issue_select = lambda sql: sql.startswith("SELECT ") and table("Issues") in sql and 'AS a WHERE a."id"=' in sql

        # Real earlier serializable snapshot, paused after its authorized get.
        target = "issue-000028"
        before = rank(target)
        proxy.hold(issue_select)
        with ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(bob.rpc, "increment", target, node="A")
            wait_for(lambda: proxy.gated >= 1, "earlier snapshot read completed")
            service.sql(f"DELETE FROM {table('Memberships')} WHERE principal='bob' AND workspace='workspace-0'")
            proxy.release()
            outcome = pending.result(timeout=20)
        after = rank(target)
        record("held-snapshot-revocation-serial-order", outcome[0] in [200, 409] and after == before + (outcome[0] == 200),
               {"outcome": outcome, "before": before, "after": after}, "earlier snapshot commits once in serial order or distinctly aborts")
        for node in ["A", "B"]:
            status, value = bob.rpc("read_issue", target, node=node)
            record("new-request-after-revocation-" + node, status == 403 and value == {"error": "denied"}, {"status": status, "value": value}, "new request denies after committed revocation")

        # Client disconnect after actual authorized read, before update/commit.
        target = "issue-000001"
        before = rank(target)
        proxy.hold(issue_select)
        raw = raw_mutation(service, alice, contract, target)
        wait_for(lambda: proxy.gated >= 1, "disconnected mutation reached actual PG")
        raw.close()
        raw = None
        proxy.release()
        wait_for(lambda: rank(target) == before + 1, "owned disconnected mutation committed")
        time.sleep(.1)
        record("http-delivery-loss-no-implicit-replay", rank(target) == before + 1,
               {"before": before, "after": rank(target)}, "one committed increment, no delivered success and no automatic replay")

        # Receive PostgreSQL's real CommandComplete COMMIT, discard that ack,
        # close transport, and independently inspect committed rows.
        before = rank(target)
        proxy.arm_commit_loss()
        status, value = alice.rpc("increment", target, node="A")
        wait_for(proxy.dropped_commit.is_set, "actual COMMIT acknowledgment withheld")
        time.sleep(.1)
        record("pg-commit-ack-loss-is-unknown", status == 503 and value == {"error": "outcome_unknown"} and rank(target) == before + 1,
               {"status": status, "value": value, "before": before, "after": rank(target)}, "HTTP503 outcome_unknown while actual database has exactly one commit")
        for node in ["A", "B"]:
            status, value = alice.rpc("read_issue", target, node=node)
            record("uncertain-connection-replacement-" + node, status == 200 and value["value"]["rank"] == str(before + 1),
                   {"status": status, "value": value}, "healthy cleaned/fresh connection reads exact committed value")

        # Real deferred commit abort with a PostgreSQL serialization SQLSTATE.
        before = rank(target)
        service.sql("CREATE FUNCTION diamond_w4.independent_abort() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'synthetic confirmed serialization abort' USING ERRCODE='40001'; END $$")
        service.sql(f"CREATE CONSTRAINT TRIGGER independent_abort AFTER UPDATE ON {table('Issues')} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION diamond_w4.independent_abort()")
        status, value = alice.rpc("increment", target, node="A")
        service.sql(f"DROP TRIGGER independent_abort ON {table('Issues')}; DROP FUNCTION diamond_w4.independent_abort()")
        record("confirmed-commit-abort-distinct-from-unknown", status == 409 and value == {"error": "aborted"} and rank(target) == before,
               {"status": status, "value": value, "before": before, "after": rank(target)}, "HTTP409 confirmed abort and no committed increment")

        # Saturate actual blocking work/pooled connections while forwarding all
        # real bytes. Extra callers must remain bounded and recover afterward.
        proxy.hold(lambda sql: sql.startswith("SELECT ") and "FROM diamond_w4.apps" in sql)
        start = threading.Barrier(33)
        def count_request():
            start.wait(timeout=5)
            return alice.rpc("count", {"tag": "workspace", "value": "workspace-1"}, node="A")
        with ThreadPoolExecutor(max_workers=32) as executor:
            pending = [executor.submit(count_request) for _ in range(32)]
            start.wait(timeout=5)
            wait_for(lambda: proxy.gated >= 4, "four pooled request connections held")
            connections = int(service.sql("SELECT count(*) FROM pg_stat_activity WHERE usename='diamond_w4_runtime'"))
            time.sleep(.15)
            proxy.release()
            outcomes = [future.result(timeout=25) for future in pending]
        statuses = [status for status, value in outcomes]
        rejected = [(status, value) for status, value in outcomes if status != 200]
        record("bounded-admission-under-pressure", connections <= 8 and rejected and all(status == 503 and value == {"error": "unavailable"} for status, value in rejected),
               {"connections_two_nodes": connections, "statuses": statuses, "rejected": rejected}, "at most4 connections/node, bounded HTTP503 unavailable admission, real permitted work finishes")
        idle_transaction = service.sql("SELECT count(*) FROM pg_stat_activity WHERE usename='diamond_w4_runtime' AND state='idle in transaction'")
        status, value = alice.rpc("count", {"tag": "workspace", "value": "workspace-1"}, node="A")
        record("pool-cleanup-and-recovery", idle_transaction == "0" and status == 200 and value == {"value": "15"},
               {"idle_in_transaction": idle_transaction, "status": status, "value": value}, "no lingering transactions; fresh request returns independent count15")
        # Kill only the owned database and operate both actual nodes while its
        # Unix socket is unavailable. Explicit read probes after restart may
        # retire stale pooled connections; no mutation is retried here.
        subprocess.run(["pg_ctl", "-D", str(service.data), "-m", "fast", "-w", "stop"], check=True, capture_output=True, timeout=20)
        service.pg_started = False
        for node in ["A", "B"]:
            status, value = alice.rpc("count", {"tag": "workspace", "value": "workspace-1"}, node=node)
            record("unavailable-database-fails-safe-" + node, status == 503 and value == {"error": "unavailable"},
                   {"status": status, "value": value}, "HTTP503, no stale/fallback data or grants")
        subprocess.run(["pg_ctl", "-D", str(service.data), "-l", str(root / "postgres-restart.log"), "-o",
                        f'-k {service.sockets} -h "" -p {service.pg_port} -c max_connections=32', "-w", "start"],
                       check=True, capture_output=True, timeout=20)
        service.pg_started = True
        for node in ["A", "B"]:
            attempts = []
            for _ in range(6):
                outcome = alice.rpc("count", {"tag": "workspace", "value": "workspace-1"}, node=node)
                attempts.append(outcome)
                if outcome == (200, {"value": "15"}):
                    break
            record("database-restart-retires-stale-connections-" + node,
                   attempts[-1] == (200, {"value": "15"}) and all(code == 200 or code == 503 for code, value in attempts),
                   attempts, "bounded explicit read probes replace stale pooled connections and recover exact count15")
    except Exception as failure:
        fatal = repr(failure)
    finally:
        if raw is not None:
            raw.close()
        if proxy is not None:
            proxy.release()
        service.close()
        if proxy is not None:
            proxy.close()
        status = "FAIL" if fatal or service.cleanup_errors or proxy is not None and proxy.errors or any(result["status"] == "FAIL" for result in results) else "PASS"
        report = {"status": status, "binary_sha256": sha256(binary), "error": fatal, "results": results,
                  "shutdowns": service.shutdowns, "cleanup_errors": service.cleanup_errors,
                  "proxy_errors": [] if proxy is None else proxy.errors, "proxy_events": [] if proxy is None else proxy.events,
                  "temporary_root": str(root) if status == "FAIL" else None,
                  "scope": "real framed Unix PG transport with held/dropped actual bytes; real RPC and independent SQL; no fabricated application responses"}
        options.report.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
        if status == "PASS":
            shutil.rmtree(root)
    print(json.dumps({"status": status, "cases": len(results), "error": fatal,
                      "failures": [result["name"] for result in results if result["status"] == "FAIL"], "report": str(options.report)}))
    return status != "PASS"


if __name__ == "__main__":
    raise SystemExit(main())
