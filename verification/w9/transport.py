#!/usr/bin/env python3
"""Hostile real-socket W9 transport campaign against a connected Diamond app."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import socket
import sys
import tempfile
import time

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[1]
W78 = BASE.parent / "w7_w8_independent"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fixture = load("_w9_fixture", W78 / "fixture.py")
sys.modules["fixture"] = fixture
service_module = load("_w9_service", W78 / "service.py")
sys.modules["service"] = service_module
w5_common = load("_w9_common", BASE.parent / "w5_independent" / "common.py")
receiver_module = load("_w9_receiver", W78 / "receiver.py")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def raw(port: int, request: bytes, *, timeout: float = 4.0) -> dict:
    started = time.monotonic()
    data = bytearray()
    error = None
    with socket.create_connection(("127.0.0.1", port), timeout=timeout) as stream:
        stream.settimeout(timeout)
        stream.sendall(request)
        while len(data) < 131_072:
            try:
                chunk = stream.recv(8192)
            except (ConnectionResetError, socket.timeout) as exc:
                error = type(exc).__name__
                break
            if not chunk:
                break
            data.extend(chunk)
    statuses = []
    for line in bytes(data).split(b"\r\n"):
        if line.startswith(b"HTTP/1.1 "):
            try:
                statuses.append(int(line.split()[1]))
            except (IndexError, ValueError):
                pass
    return {
        "statuses": statuses,
        "bytes": len(data),
        "error": error,
        "elapsed_ms": round((time.monotonic() - started) * 1000, 3),
        "head_sha256": hashlib.sha256(bytes(data[:4096])).hexdigest(),
    }


def request_bytes(method: str, path: str, headers: list[tuple[str, str]], body: bytes = b"") -> bytes:
    return (
        f"{method} {path} HTTP/1.1\r\n".encode()
        + b"".join(f"{name}: {value}\r\n".encode() for name, value in headers)
        + b"\r\n"
        + body
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", required=True, type=Path)
    parser.add_argument("--report", type=Path, default=BASE / "transport_results.json")
    options = parser.parse_args()
    root = Path(tempfile.mkdtemp(prefix="diamond-w9-transport-"))
    binary = root / "diamond"
    shutil.copy2(options.binary, binary)
    project = fixture.write(root / "project")
    service = service_module.Service(root, binary, project)
    receiver = receiver_module.Receiver()
    service.runtime["http"] = {
        "inventory": {
            "origin": receiver.origin,
            "network": "loopback",
            "paths": ["/v1/"],
            "methods": ["POST"],
            "timeout_ms": 5000,
        }
    }
    observations = []
    failures = []

    def record(name: str, okay: bool, observed, expected: str) -> None:
        row = {"name": name, "status": "PASS" if okay else "FAIL", "observed": observed, "expected": expected}
        observations.append(row)
        if not okay:
            failures.append(row)

    try:
        service.postgres()
        contract, _build = service.build()
        service.cli("project-init", project, "--postgres-env", service_module.ENV_NAME)
        service.files_cli(initialize=True)
        for username in ["alice", "bob", "carol", "outsider"]:
            service.cli(
                "project-admin-add",
                project,
                "--postgres-env",
                service_module.ENV_NAME,
                "--user",
                username,
                "--email",
                username + "@qualification.invalid",
                "--password-stdin",
                input=fixture.PASSWORD + "\n",
            )
        w5_common.seed(service)
        service.start_node("A")
        client = service_module.Client(service, contract["rpc_id"])
        client.login("alice", fixture.PASSWORD)
        port = service.ports["A"]
        authority = service.authority

        baseline = service.request("A", "/_diamond/health/live")
        record("baseline-live", baseline[0] == 200, baseline[0], "real server liveness returns 200 before hostile traffic")

        cases: list[tuple[str, bytes, set[int]]] = []
        ordinary = [("Host", authority), ("Connection", "close")]
        cases.extend(
            [
                ("absolute-form", request_bytes("GET", f"http://{authority}/", ordinary), {400}),
                ("double-slash", request_bytes("GET", "//hostile.invalid/", ordinary), {400}),
                ("percent-path", request_bytes("GET", "/%2e%2e/server.json", ordinary), {400}),
                ("backslash-path", request_bytes("GET", "/assets\\server.json", ordinary), {400}),
                ("query-string", request_bytes("GET", "/?x=1", ordinary), {400}),
                ("duplicate-host", request_bytes("GET", "/", [("Host", authority), ("Host", "hostile.invalid")]), {400}),
                ("host-comma-list", request_bytes("GET", "/", [("Host", authority + ",hostile.invalid")]), {400}),
                ("forwarded", request_bytes("GET", "/", ordinary + [("Forwarded", "host=hostile.invalid")]), {400}),
                ("x-forwarded-host", request_bytes("GET", "/", ordinary + [("X-Forwarded-Host", authority)]), {400}),
                ("transfer-encoding", request_bytes("POST", "/_diamond/login", ordinary + [("Transfer-Encoding", "chunked")], b"0\r\n\r\n"), {400}),
                ("te-and-cl", request_bytes("POST", "/_diamond/login", ordinary + [("Transfer-Encoding", "chunked"), ("Content-Length", "0")], b"0\r\n\r\n"), {400}),
                ("conflicting-content-length", request_bytes("POST", "/_diamond/login", ordinary + [("Content-Length", "0"), ("Content-Length", "2")], b"{}"), {400}),
                ("oversize-content-length", request_bytes("POST", "/_diamond/login", ordinary + [("Content-Length", "999999999")]), {400, 413}),
                ("unsupported-connect", request_bytes("CONNECT", authority, ordinary), {400, 405}),
                ("asterisk-form", request_bytes("OPTIONS", "*", ordinary), {400, 405}),
                ("cross-site-fetch", request_bytes("GET", "/", ordinary + [("Sec-Fetch-Site", "cross-site")]), {400}),
                ("hostile-origin-asset", request_bytes("GET", "/", ordinary + [("Origin", "https://hostile.invalid")]), {400}),
                ("authorization-on-cookie-surface", request_bytes("GET", "/", ordinary + [("Authorization", "Bearer dia_" + "0" * 64)]), {403}),
            ]
        )
        for name, message, allowed in cases:
            observed = raw(port, message)
            okay = bool(observed["statuses"]) and observed["statuses"][0] in allowed and observed["elapsed_ms"] < 4500
            record(name, okay, observed, "ambiguous/hostile request is bounded and rejected before protected dispatch")
            live = service.request("A", "/_diamond/health/live")
            record(name + "-survives", live[0] == 200, live[0], "server remains live after hostile request")

        body = json.dumps(
            {"contract": contract["rpc_id"], "arguments": [fixture.identifier("issue-red-one"), "W9 hostile header event"]},
            separators=(",", ":"),
        ).encode()
        cookie = client.cookie or ""
        csrf = client.csrf
        post = [
            ("Host", authority),
            ("Origin", service.origin),
            ("Content-Type", "application/json"),
            ("Cookie", cookie),
            ("X-Diamond-CSRF", csrf),
            ("Content-Length", str(len(body))),
            ("Connection", "close"),
        ]
        event_count = lambda: int(service.sql("SELECT count(*) FROM diamond_w7.events WHERE app=" + w5_common.quote(fixture.app_id())))
        before = event_count()
        accepted = raw(port, request_bytes("POST", "/_diamond/rpc/emit", post, body))
        record("raw-valid-control", accepted["statuses"] == [200] and event_count() == before + 1,
               {"response": accepted, "events_before": before, "events_after": event_count()},
               "independent raw request reaches exactly one declared protected operation")

        mutations = [
            ("duplicate-origin", post + [("Origin", service.origin)]),
            ("duplicate-cookie-header", post + [("Cookie", cookie)]),
            ("duplicate-csrf", post + [("X-Diamond-CSRF", csrf)]),
            ("hostile-origin", [(n, "https://hostile.invalid" if n == "Origin" else v) for n, v in post]),
            ("forged-cookie", [(n, "__Host-diamond_forged=" + "0" * 64 if n == "Cookie" else v) for n, v in post]),
            ("missing-origin", [(n, v) for n, v in post if n != "Origin"]),
            ("cookie-plus-authorization", post + [("Authorization", "Bearer dia_" + "0" * 64)]),
        ]
        for name, headers in mutations:
            before = event_count()
            observed = raw(port, request_bytes("POST", "/_diamond/rpc/emit", headers, body))
            after = event_count()
            record(name, observed["statuses"] and observed["statuses"][0] >= 400 and before == after,
                   {"response": observed, "events_before": before, "events_after": after},
                   "duplicate/forged authority input is denied with no event side effect")

        json_cases = [
            ("duplicate-envelope-key", b'{"contract":"' + contract["rpc_id"].encode() + b'","contract":"' + contract["rpc_id"].encode() + b'","arguments":[]}'),
            ("extra-envelope-key", json.dumps({"contract": contract["rpc_id"], "arguments": [], "principal": "alice"}).encode()),
            ("trailing-json", b'{"contract":"' + contract["rpc_id"].encode() + b'","arguments":[]}{}'),
            ("deep-json", (b'{"contract":"' + contract["rpc_id"].encode() + b'","arguments":[' + b'[' * 80 + b'0' + b']' * 80 + b']}')),
            ("invalid-utf8-json", b'{"contract":"' + contract["rpc_id"].encode() + b'","arguments":["\xff"]}'),
        ]
        for name, hostile_body in json_cases:
            headers = [(n, str(len(hostile_body)) if n == "Content-Length" else v) for n, v in post]
            before = event_count()
            observed = raw(port, request_bytes("POST", "/_diamond/rpc/emit", headers, hostile_body))
            record(name, observed["statuses"] and observed["statuses"][0] >= 400 and before == event_count(),
                   {"response": observed, "events_before": before, "events_after": event_count()},
                   "strict bounded JSON rejects malformed shape without protected side effect")

        pipelined = request_bytes("GET", "/", ordinary) + request_bytes("GET", "/assets/absent", ordinary)
        observed = raw(port, pipelined)
        record("pipelined-independent-framing", observed["statuses"] in ([200, 404], [200]), observed,
               "HTTP stack frames at most the two explicit requests without an injected protected dispatch")

        logs = "\n".join(path.read_text(errors="replace") for path in service.node_logs.values())
        canaries = [fixture.PASSWORD, client.csrf, (client.cookie or "").partition("=")[2]]
        record("logs-redact-credentials", not any(value and value in logs for value in canaries),
               {"log_bytes": len(logs), "credential_canaries_found": [index for index, value in enumerate(canaries) if value and value in logs]},
               "server logs contain only request classes/statuses and no password, CSRF, or session token")
    except Exception as exc:  # retained receipt contains the exact unexpected failure
        failures.append({"name": "campaign-exception", "status": "FAIL", "observed": repr(exc), "expected": "campaign completes"})
    finally:
        service.close()
        receiver.close()
        cleanup = {"root": str(root), "exists_before_removal": root.exists(), "service_errors": service.cleanup_errors}
        shutil.rmtree(root)
        cleanup["exists_after_removal"] = root.exists()

    report = {
        "status": "PASS" if not failures and not service.cleanup_errors else "FAIL",
        "binary_sha256": sha256(options.binary),
        "source_sha256": sha256(Path(__file__)),
        "observations": len(observations),
        "results": observations,
        "failures": failures,
        "shutdowns": service.shutdowns,
        "cleanup": cleanup,
        "scope": "actual connected Diamond process, PostgreSQL, raw HTTP sockets, authenticated RPC and independent database side-effect readback",
    }
    options.report.parent.mkdir(parents=True, exist_ok=True)
    options.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"status": report["status"], "observations": len(observations), "failures": len(failures)}))
    raise SystemExit(0 if report["status"] == "PASS" else 1)


if __name__ == "__main__":
    main()
