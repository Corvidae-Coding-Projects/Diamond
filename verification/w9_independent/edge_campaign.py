#!/usr/bin/env python3
"""Bounded hostile HTTP/WebSocket/event-retention/database-loss campaign."""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import re
import shutil
import socket
import sys
import tempfile
import time

BASE = Path(__file__).resolve().parent
PREVIOUS = BASE.parent / "w7_w8_independent"
sys.path.insert(0, str(PREVIOUS))
from fixture import PASSWORD, app_id, identifier, table, write  # noqa: E402
from receiver import Receiver  # noqa: E402
from service import Client, ENV_NAME, Service, sha256  # noqa: E402
from wire import WebSocket  # noqa: E402

spec = importlib.util.spec_from_file_location("_w9_edge_common", BASE.parent / "w5_independent/common.py")
previous = importlib.util.module_from_spec(spec)
spec.loader.exec_module(previous)
Receipt, nominal, quote, seed, wait_for = (
    previous.Receipt,
    previous.nominal,
    previous.quote,
    previous.seed,
    previous.wait_for,
)


def raw_request(port: int, request: bytes, *, delay_after_send: float = 0.0) -> dict:
    started = time.monotonic()
    data = bytearray()
    error = None
    with socket.create_connection(("127.0.0.1", port), timeout=5) as connection:
        connection.settimeout(6)
        try:
            connection.sendall(request)
            if delay_after_send:
                time.sleep(delay_after_send)
            while len(data) < 131072:
                chunk = connection.recv(65536)
                if not chunk:
                    break
                data.extend(chunk)
        except (ConnectionError, OSError, socket.timeout) as exc:
            error = type(exc).__name__
    match = re.match(br"HTTP/1\.[01] ([0-9]{3})", data)
    return {
        "status": int(match.group(1)) if match else None,
        "bytes": len(data),
        "error": error,
        "elapsed_seconds": time.monotonic() - started,
        "body_tail": bytes(data[-160:]).decode("utf-8", "replace"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--report", type=Path, default=BASE / "edge_campaign_results.json")
    options = parser.parse_args()
    root = Path(tempfile.mkdtemp(prefix="diamond-w9-edge-"))
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
[http_api.operations.edit]
function = "server.edit"
auth = "token"
[http_api.operations.public_value]
function = "server.public_value"
auth = "public"
"""
        )
    service = Service(root, binary, project)
    receiver = None
    receipt = Receipt()
    receipt.secret(PASSWORD)
    sockets: list[WebSocket] = []
    fatal = None
    started = time.monotonic()
    raw_count = 0

    def value(result):
        if result[0] != 200 or not isinstance(result[1], dict) or "value" not in result[1]:
            raise AssertionError(("expected successful real operation", result))
        return result[1]["value"]

    def rank() -> int:
        return int(
            service.sql(
                "SELECT rank FROM " + table("Issues") + " WHERE id=" + quote(identifier("issue-red-one"))
            )
        )

    def request_bytes(method: str, target: str, headers: list[tuple[str, str]], body: bytes = b"") -> bytes:
        return (
            f"{method} {target} HTTP/1.1\r\n".encode()
            + b"".join(f"{name}: {value_}\r\n".encode() for name, value_ in headers)
            + b"\r\n"
            + body
        )

    def hostile(name: str, data: bytes, *, delay: float = 0.0):
        nonlocal raw_count
        raw_count += 1
        observed = raw_request(service.ports["A"], data, delay_after_send=delay)
        receipt.record(
            "raw-http-reject-" + name,
            observed["status"] is None or observed["status"] >= 400,
            observed,
            "noncanonical framing or authority is rejected before protected dispatch",
        )
        return observed

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
        for username in ["alice", "bob", "carol", "outsider"]:
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
        alice = Client(service, contract["rpc_id"])
        alice.login("alice", PASSWORD)
        parent = identifier("issue-red-one")
        authority = service.authority
        origin = service.origin

        canonical = raw_request(
            service.ports["A"],
            request_bytes("GET", "/", [("Host", authority), ("Connection", "close")]),
        )
        receipt.record(
            "raw-http-canonical-control",
            canonical["status"] == 200,
            canonical,
            "a canonical raw HTTP request reaches the actual server before hostile variants run",
        )

        hostile("duplicate-host", request_bytes("GET", "/", [("Host", authority), ("Host", "hostile.invalid"), ("Connection", "close")]))
        hostile("forwarded", request_bytes("GET", "/", [("Host", authority), ("Forwarded", "host=hostile.invalid"), ("Connection", "close")]))
        hostile("x-forwarded", request_bytes("GET", "/", [("Host", authority), ("X-Forwarded-Host", "hostile.invalid"), ("Connection", "close")]))
        hostile("absolute-form", request_bytes("GET", f"http://{authority}/", [("Host", authority), ("Connection", "close")]))
        hostile("encoded-path", request_bytes("GET", "/%2e%2e/", [("Host", authority), ("Connection", "close")]))
        hostile("double-slash", request_bytes("GET", "//hostile.invalid/", [("Host", authority), ("Connection", "close")]))
        hostile("transfer-encoding", request_bytes("POST", "/_diamond/rpc/edit", [("Host", authority), ("Origin", origin), ("Transfer-Encoding", "chunked"), ("Content-Type", "application/json"), ("Connection", "close")], b"0\r\n\r\n"))
        hostile("conflicting-content-length", request_bytes("POST", "/_diamond/rpc/edit", [("Host", authority), ("Origin", origin), ("Content-Type", "application/json"), ("Content-Length", "0"), ("Content-Length", "5"), ("Connection", "close")], b"abcde"))
        hostile("duplicate-origin", request_bytes("POST", "/_diamond/rpc/edit", [("Host", authority), ("Origin", origin), ("Origin", origin), ("Content-Type", "application/json"), ("Content-Length", "2"), ("Connection", "close")], b"{}"))
        hostile("cross-site-fetch", request_bytes("GET", "/", [("Host", authority), ("Sec-Fetch-Site", "cross-site"), ("Connection", "close")]))
        incomplete = request_bytes("POST", "/_diamond/rpc/edit", [("Host", authority), ("Origin", origin), ("Content-Type", "application/json"), ("Content-Length", "100"), ("Connection", "close")], b"{")
        slow = hostile("incomplete-body-deadline", incomplete, delay=3.2)
        receipt.record(
            "incomplete-body-bounded",
            slow["elapsed_seconds"] < 5 and slow["status"] is not None and slow["status"] >= 400,
            slow,
            "a truncated declared body is rejected near the three-second body deadline without holding capacity indefinitely",
        )

        before_rank = rank()
        cookie = alice.cookie or ""
        csrf = alice.csrf
        edit_arguments = [nominal("issue", parent), {"rank": "44"}]
        good_envelope = json.dumps({"contract": contract["rpc_id"], "arguments": edit_arguments}, separators=(",", ":"))
        profile_cases = []
        profile_cases.append(("cookie-rpc-with-bearer", service.request("A", "/_diamond/rpc/edit", good_envelope, {"Cookie": cookie, "X-Diamond-CSRF": csrf, "Authorization": "Bearer dia_" + "a" * 64, "Content-Type": "application/json", "Origin": origin}, method="POST")))
        profile_cases.append(("rpc-wrong-csrf", service.request("A", "/_diamond/rpc/edit", good_envelope, {"Cookie": cookie, "X-Diamond-CSRF": "0" * 64, "Content-Type": "application/json", "Origin": origin}, method="POST")))
        duplicate_json = ("{\"contract\":" + json.dumps(contract["rpc_id"]) + ",\"contract\":" + json.dumps(contract["rpc_id"]) + ",\"arguments\":" + json.dumps(edit_arguments) + "}")
        profile_cases.append(("rpc-duplicate-json-key", service.request("A", "/_diamond/rpc/edit", duplicate_json, {"Cookie": cookie, "X-Diamond-CSRF": csrf, "Content-Type": "application/json", "Origin": origin}, method="POST")))
        extra_json = json.dumps({"contract": contract["rpc_id"], "arguments": edit_arguments, "principal": "alice"})
        profile_cases.append(("rpc-extra-principal", service.request("A", "/_diamond/rpc/edit", extra_json, {"Cookie": cookie, "X-Diamond-CSRF": csrf, "Content-Type": "application/json", "Origin": origin}, method="POST")))
        duplicate_cookie = cookie + "; " + cookie
        profile_cases.append(("rpc-duplicate-session-cookie", service.request("A", "/_diamond/rpc/edit", good_envelope, {"Cookie": duplicate_cookie, "X-Diamond-CSRF": csrf, "Content-Type": "application/json", "Origin": origin}, method="POST")))
        token_result = alice.account("create_token", {"label": "W9 edge credential", "operations": ["read", "edit"], "scopes": {"workspace": [identifier("red")]}, "lifetime_seconds": "3600"})
        bearer = value(token_result)
        receipt.secret(bearer)
        profile_cases.append(("api-cookie-bearer-confusion", service.request("B", "/api/v1/read", json.dumps({"arguments": [nominal("issue", parent)]}), {"Cookie": cookie, "Authorization": "Bearer " + bearer, "X-Diamond-API-Version": "1", "Content-Type": "application/json", "Origin": origin}, method="POST")))
        profile_cases.append(("public-api-with-bearer", service.request("B", "/api/v1/public_value", b'{"arguments":[]}', {"Authorization": "Bearer " + bearer, "X-Diamond-API-Version": "1", "Content-Type": "application/json", "Origin": origin}, method="POST")))
        decoded_profiles = [{"name": name, "status": result[0], "body": result[2].decode("utf-8", "replace")} for name, result in profile_cases]
        receipt.record(
            "profile-authority-and-strict-envelope-confusion",
            all(row["status"] >= 400 for row in decoded_profiles) and rank() == before_rank,
            {"cases": decoded_profiles, "rank_before": before_rank, "rank_after": rank()},
            "cookie, bearer, CSRF and strict JSON profiles do not fall back or accept client principal claims; protected state is unchanged",
        )
        healthy = alice.rpc("read", nominal("issue", parent), node="B")
        receipt.record(
            "fresh-request-after-hostile-http",
            healthy[0] == 200 and healthy[1]["value"]["rank"] == str(before_rank),
            healthy,
            "a fresh canonical authenticated request succeeds after malformed traffic with the unchanged row",
        )

        oversized = WebSocket("127.0.0.1", service.ports["A"], f"/_diamond/events/issue_changes/{parent}/0", authority=authority, origin=origin, cookie=cookie)
        sockets.append(oversized)
        oversized.send_frame(b"x" * 1025)
        try:
            close_frame = oversized.receive()
            oversized_closed = close_frame.get("kind") == "close"
        except (EOFError, ConnectionError, OSError, ValueError) as exc:
            close_frame, oversized_closed = {"closed": type(exc).__name__}, True
        receipt.record(
            "websocket-oversized-inbound-closes-without-starvation",
            oversized.status == 101 and oversized_closed and alice.rpc("read", nominal("issue", parent), node="B")[0] == 200,
            {"upgrade": oversized.status, "close": close_frame},
            "an oversized unsolicited client message closes the bounded socket and ordinary HTTP remains usable",
        )
        oversized.close()

        app = app_id()
        service.sql(
            "INSERT INTO diamond_w7.events(app,binding,parent,payload) "
            "SELECT " + quote(app) + ", 'issue_changes', " + quote(parent) + ", "
            "pg_catalog.to_json(('retained-' || g)::text)::text FROM pg_catalog.generate_series(1,9999) AS g"
        )
        seeded = int(service.sql("SELECT count(*) FROM diamond_w7.events WHERE app=" + quote(app)))
        first_cursor = int(value(alice.rpc("emit", parent, "retention-edge-a", node="A")))
        exact = int(service.sql("SELECT count(*) FROM diamond_w7.events WHERE app=" + quote(app)))
        second_cursor = int(value(alice.rpc("emit", parent, "retention-edge-b", node="B")))
        bounds = service.sql_json("SELECT count(*)::bigint AS count,min(cursor) AS minimum,max(cursor) AS maximum FROM diamond_w7.events WHERE app=" + quote(app))[0]
        old = WebSocket("127.0.0.1", service.ports["A"], f"/_diamond/events/issue_changes/{parent}/1", authority=authority, origin=origin, cookie=cookie)
        current = WebSocket("127.0.0.1", service.ports["B"], f"/_diamond/events/issue_changes/{parent}/{second_cursor - 1}", authority=authority, origin=origin, cookie=cookie)
        zero = WebSocket("127.0.0.1", service.ports["A"], f"/_diamond/events/issue_changes/{parent}/0", authority=authority, origin=origin, cookie=cookie)
        ahead = WebSocket("127.0.0.1", service.ports["B"], f"/_diamond/events/issue_changes/{parent}/{second_cursor + 1}", authority=authority, origin=origin, cookie=cookie)
        sockets.extend([old, current, zero, ahead])
        old_message, current_message, zero_message, ahead_message = old.receive(), current.receive(), zero.receive(), ahead.receive()
        receipt.record(
            "automatic-ten-thousand-event-retention-and-resync",
            seeded == 9999
            and exact == 10000
            and bounds["count"] == 10000
            and bounds["minimum"] == 2
            and bounds["maximum"] == second_cursor
            and old_message == {"resync": True, "cursor": str(second_cursor)}
            and ahead_message == {"resync": True, "cursor": str(second_cursor)}
            and current_message == {"cursor": str(second_cursor), "value": "retention-edge-b"}
            and zero_message.get("cursor") == str(bounds["minimum"]),
            {
                "seeded": seeded,
                "at_exact_limit": exact,
                "first_cursor": first_cursor,
                "second_cursor": second_cursor,
                "bounds": bounds,
                "old": old_message,
                "ahead": ahead_message,
                "current": current_message,
                "zero_first": zero_message,
            },
            "real publication trims the oldest row at the edge; unavailable/ahead cursors resync and cursor zero starts retained history",
        )
        for ws in [old, current, zero, ahead]:
            ws.close()

        outage_socket = WebSocket("127.0.0.1", service.ports["A"], f"/_diamond/events/issue_changes/{parent}/{second_cursor}", authority=authority, origin=origin, cookie=cookie)
        sockets.append(outage_socket)
        service.stop_postgres()
        live_during = service.request("A", "/_diamond/health/live")[0]
        ready_during = service.request("B", "/_diamond/health/ready")[0]
        denied_during = alice.rpc("read", nominal("issue", parent), node="A")
        try:
            outage_close = outage_socket.receive()
            outage_closed = outage_close.get("kind") == "close"
        except (EOFError, ConnectionError, OSError, socket.timeout) as exc:
            outage_close, outage_closed = {"closed": type(exc).__name__}, True
        receipt.record(
            "postgres-outage-fails-closed",
            live_during == 200 and ready_during != 200 and denied_during[0] >= 500 and outage_closed,
            {"live": live_during, "ready": ready_during, "rpc": denied_during, "socket": outage_close},
            "dependency loss preserves liveness but denies readiness/protected access and closes a socket that cannot reauthorize",
        )
        service.start_postgres()
        recovery = []
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            for node in ["A", "B"]:
                outcome = alice.rpc("read", nominal("issue", parent), node=node)
                recovery.append({"node": node, "status": outcome[0]})
            if recovery[-1]["status"] == 200 and recovery[-2]["status"] == 200:
                break
            time.sleep(0.1)
        receipt.record(
            "live-nodes-recover-after-postgres-restart",
            len(recovery) >= 2 and recovery[-1]["status"] == 200 and recovery[-2]["status"] == 200 and all(process.poll() is None for process in service.nodes.values()),
            {"attempts": recovery, "nodes_alive": {name: process.poll() for name, process in service.nodes.items()}},
            "both unchanged server processes discard broken pooled connections and serve fresh authenticated reads after the owned database returns",
        )
    except Exception as error:
        fatal = repr(error)
    finally:
        for ws in sockets:
            try:
                ws.close()
            except Exception:
                pass
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
        raw_socket_requests=raw_count + 1,
        command=f"python3 verification/w9_independent/edge_campaign.py --binary {options.binary}",
        oracle="ORACLES.md H1 H2 E1 E2 D1",
        temporary_root_removed=True,
    )
    options.report.parent.mkdir(parents=True, exist_ok=True)
    options.report.write_text(json.dumps(report, indent=2) + "\n")
    shutil.rmtree(root)
    print(json.dumps({"status": report["status"], "observations": len(receipt.results), "error": fatal, "raw": raw_count + 1}))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
