#!/usr/bin/env python3
"""Cross-node session, token, account, socket, and queued-job revocation campaign."""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import shutil
import socket
import sys
import tempfile
import time

BASE = Path(__file__).resolve().parent
PREVIOUS = BASE.parent / "w7_w8_independent"
sys.path.insert(0, str(PREVIOUS))
from fixture import PASSWORD, app_id, identifier, write  # noqa: E402
from receiver import Receiver  # noqa: E402
from service import Client, ENV_NAME, Service, sha256  # noqa: E402
from wire import WebSocket  # noqa: E402

spec = importlib.util.spec_from_file_location("_w9_auth_common", BASE.parent / "w5_independent/common.py")
previous = importlib.util.module_from_spec(spec)
spec.loader.exec_module(previous)
Receipt, nominal, quote, seed, wait_for = (
    previous.Receipt,
    previous.nominal,
    previous.quote,
    previous.seed,
    previous.wait_for,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--report", type=Path, default=BASE / "auth_policy_races_results.json")
    options = parser.parse_args()
    root = Path(tempfile.mkdtemp(prefix="diamond-w9-auth-races-"))
    binary = root / "diamond"
    shutil.copy2(options.binary, binary)
    project = write(root / "project")
    with project.open("a") as manifest:
        manifest.write(
            """
[http_api]
version = 1
[http_api.operations.read]
function = "server.read"
auth = "token"
[http_api.operations.submit]
function = "server.submit"
auth = "token"
"""
        )
    service = Service(root, binary, project)
    receiver = None
    receipt = Receipt()
    receipt.secret(PASSWORD)
    sockets: list[WebSocket] = []
    fatal = None
    started = time.monotonic()

    def value(result):
        if result[0] != 200 or "value" not in result[1]:
            raise AssertionError(("expected success", result))
        return result[1]["value"]

    def job(job_id: str):
        rows = service.sql_json(
            "SELECT id,state,attempts,fence,outcome FROM diamond_w7.jobs WHERE app="
            + quote(app_id())
            + " AND id="
            + quote(job_id)
        )
        return rows[0] if len(rows) == 1 else None

    try:
        receiver = Receiver()
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
        for username in ["alice", "bob"]:
            service.cli(
                "project-admin-add",
                project,
                "--postgres-env",
                ENV_NAME,
                "--user",
                username,
                "--email",
                username + "@qualification.invalid",
                "--password-stdin",
                input=PASSWORD + "\n",
            )
        seed(service)
        service.start_node("A")
        service.start_node("B")
        alice_a = Client(service, contract["rpc_id"])
        alice_b = Client(service, contract["rpc_id"])
        alice_a.login("alice", PASSWORD, node="A")
        alice_b.login("alice", PASSWORD, node="B")
        red = identifier("red")
        parent = identifier("issue-red-one")
        alice_credential = alice_a.account(
            "create_token",
            {"label": "logout-all survivor", "operations": ["read"], "scopes": {"workspace": [red]}, "lifetime_seconds": "3600"},
            node="A",
        )
        alice_token = value(alice_credential)
        receipt.secret(alice_token)
        before_logout_api = alice_b.api("read", alice_token, [nominal("issue", parent)], node="B")
        old_b_cookie = alice_b.cookie
        logout = alice_a.account("logout_all", {}, node="A")
        alice_b.cookie = old_b_cookie
        stale_b_a = alice_b.rpc("read", nominal("issue", parent), node="A")
        stale_b_b = alice_b.rpc("read", nominal("issue", parent), node="B")
        token_after_logout = alice_b.api("read", alice_token, [nominal("issue", parent)], node="B")
        receipt.record(
            "logout-all-cross-node-revokes-sessions-but-retains-api-credential",
            before_logout_api[0] == 200
            and logout[0] == 200
            and stale_b_a[0] == 401
            and stale_b_b[0] == 401
            and token_after_logout[0] == 200,
            {
                "api_before": before_logout_api[0],
                "logout": logout,
                "stale_session_A": stale_b_a,
                "stale_session_B": stale_b_b,
                "api_after": token_after_logout[0],
            },
            "a committed logout-all is immediately shared by both nodes, while separately managed API credentials intentionally survive",
        )
        alice_a.login("alice", PASSWORD, node="B")

        bob = Client(service, contract["rpc_id"])
        bob.login("bob", PASSWORD, node="A")
        bob_credential = bob.account(
            "create_token",
            {"label": "queued-before-disable", "operations": ["read", "submit"], "scopes": {"workspace": [red]}, "lifetime_seconds": "3600"},
            node="B",
        )
        bob_token = value(bob_credential)
        receipt.secret(bob_token)
        queued_result = bob.api("submit", bob_token, [parent, "must-not-dispatch-after-disable", "disable-race-key"], node="B")
        queued = value(queued_result)
        bob_socket = WebSocket(
            "127.0.0.1",
            service.ports["A"],
            f"/_diamond/events/issue_changes/{parent}/0",
            authority=service.authority,
            origin=service.origin,
            cookie=bob.cookie,
        )
        sockets.append(bob_socket)
        old_bob_cookie = bob.cookie
        disable = alice_a.account("disable", {"username": "bob", "disabled": True}, node="B")
        bob.cookie = old_bob_cookie
        stale_cookie = [bob.rpc("read", nominal("issue", parent), node=node) for node in ["A", "B"]]
        stale_api = [bob.api("read", bob_token, [nominal("issue", parent)], node=node) for node in ["A", "B"]]
        reconnect = WebSocket(
            "127.0.0.1",
            service.ports["B"],
            f"/_diamond/events/issue_changes/{parent}/0",
            authority=service.authority,
            origin=service.origin,
            cookie=old_bob_cookie,
        )
        sockets.append(reconnect)
        try:
            close = bob_socket.receive()
            closed = close.get("kind") == "close"
        except (EOFError, ConnectionError, OSError, socket.timeout) as exc:
            close, closed = {"closed": type(exc).__name__}, True
        receipt.record(
            "account-disable-closes-live-socket-and-revokes-cookie-token-on-both-nodes",
            disable[0] == 200
            and bob_socket.status == 101
            and closed
            and reconnect.status >= 400
            and all(result[0] == 401 for result in stale_cookie)
            and all(result[0] == 401 for result in stale_api),
            {
                "disable": disable,
                "old_socket": close,
                "reconnect": reconnect.status,
                "cookie": [result[0] for result in stale_cookie],
                "api": [result[0] for result in stale_api],
            },
            "after disable commits, both servers deny old cookie/API authority and the already-open socket closes on fresh reauthorization",
        )
        attempts_before = len(receiver.snapshot()["attempts"])
        service.start_worker("one")
        service.start_worker("two")
        wait_for(lambda: job(queued)["state"] == "dead", "disabled owner job becomes dead letter", timeout=12)
        attempts_after = len(receiver.snapshot()["attempts"])
        dead = job(queued)
        receipt.record(
            "queued-job-rechecks-disabled-owner-before-external-effect",
            dead["state"] == "dead"
            and dead["attempts"] == 1
            and attempts_after == attempts_before,
            {"job": dead, "receiver_attempts_before": attempts_before, "receiver_attempts_after": attempts_after},
            "workers reconstruct current account authority and dead-letter disabled-owner work before the controlled external destination sees bytes",
        )

        enable = alice_a.account("disable", {"username": "bob", "disabled": False}, node="A")
        old_cookie_after_enable = bob.rpc("read", nominal("issue", parent), node="B")
        old_token_after_enable = bob.api("read", bob_token, [nominal("issue", parent)], node="A")
        fresh_bob = Client(service, contract["rpc_id"])
        fresh_login = fresh_bob.login("bob", PASSWORD, node="B")
        fresh_read = fresh_bob.rpc("read", nominal("issue", parent), node="A")
        receipt.record(
            "reenable-does-not-resurrect-old-authority-or-dead-job",
            enable[0] == 200
            and old_cookie_after_enable[0] == 401
            and old_token_after_enable[0] == 401
            and fresh_login[0] == 200
            and fresh_read[0] == 200
            and job(queued)["state"] == "dead"
            and len(receiver.snapshot()["attempts"]) == attempts_before,
            {
                "enable": enable,
                "old_cookie": old_cookie_after_enable[0],
                "old_token": old_token_after_enable[0],
                "fresh_login": fresh_login[0],
                "fresh_read": fresh_read[0],
                "job": job(queued),
            },
            "account epoch advancement prevents old sessions/tokens from resurrecting after re-enable; a fresh password login works and dead work remains explicit",
        )
    except Exception as error:
        fatal = repr(error)
    finally:
        for ws in sockets:
            try:
                ws.close()
            except Exception:
                pass
        service.close()
        if receiver is not None:
            receiver.close()
    report = receipt.report(
        service,
        sha256(binary),
        error=fatal,
        elapsed_seconds=time.monotonic() - started,
        command=f"python3 verification/w9_independent/auth_policy_races.py --binary {options.binary}",
        oracle="ORACLES.md P1 P2",
        temporary_root_removed=True,
    )
    options.report.parent.mkdir(parents=True, exist_ok=True)
    options.report.write_text(json.dumps(report, indent=2) + "\n")
    shutil.rmtree(root)
    print(json.dumps({"status": report["status"], "observations": len(receipt.results), "error": fatal, "elapsed_seconds": report["elapsed_seconds"]}))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
