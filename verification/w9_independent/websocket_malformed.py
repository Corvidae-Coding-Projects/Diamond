#!/usr/bin/env python3
"""Hostile WebSocket handshake and frame campaign against the real event endpoint."""
from __future__ import annotations

import argparse
import base64
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import struct
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

spec = importlib.util.spec_from_file_location("_w9_ws_common", BASE.parent / "w5_independent/common.py")
previous = importlib.util.module_from_spec(spec)
spec.loader.exec_module(previous)
Receipt, quote, seed = previous.Receipt, previous.quote, previous.seed


def handshake(port: int, path: str, headers: list[tuple[str, str]]) -> dict:
    connection = socket.create_connection(("127.0.0.1", port), timeout=5)
    connection.settimeout(5)
    key = base64.b64encode(os.urandom(16)).decode()
    base = [
        ("Connection", "Upgrade"),
        ("Upgrade", "websocket"),
        ("Sec-WebSocket-Version", "13"),
        ("Sec-WebSocket-Key", key),
    ]
    connection.sendall(
        (f"GET {path} HTTP/1.1\r\n" + "".join(f"{name}: {value}\r\n" for name, value in [*headers, *base]) + "\r\n").encode()
    )
    raw = bytearray()
    try:
        while b"\r\n\r\n" not in raw and len(raw) < 65536:
            chunk = connection.recv(4096)
            if not chunk:
                break
            raw.extend(chunk)
    except (ConnectionError, OSError, socket.timeout):
        pass
    connection.close()
    line = bytes(raw).split(b"\r\n", 1)[0].decode("ascii", "replace")
    try:
        status = int(line.split()[1])
    except (IndexError, ValueError):
        status = None
    return {"status": status, "status_line": line, "bytes": len(raw)}


def masked_frame(payload: bytes, *, opcode: int, fin: bool = True) -> bytes:
    if len(payload) >= 126:
        raise ValueError("small hostile frame helper")
    mask = os.urandom(4)
    return bytes([(0x80 if fin else 0) | opcode, 0x80 | len(payload)]) + mask + bytes(
        value ^ mask[index % 4] for index, value in enumerate(payload)
    )


def closes(connection: WebSocket) -> tuple[bool, object]:
    try:
        value = connection.receive()
        return isinstance(value, dict) and value.get("kind") == "close", value
    except (EOFError, ConnectionError, OSError, socket.timeout, ValueError) as exc:
        return True, {"closed": type(exc).__name__}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--report", type=Path, default=BASE / "websocket_malformed_results.json")
    options = parser.parse_args()
    root = Path(tempfile.mkdtemp(prefix="diamond-w9-ws-malformed-"))
    binary = root / "diamond"
    shutil.copy2(options.binary, binary)
    project = write(root / "project")
    service = Service(root, binary, project)
    receiver = None
    receipt = Receipt()
    receipt.secret(PASSWORD)
    opened: list[WebSocket] = []
    fatal = None
    started = time.monotonic()

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
        service.cli(
            "project-admin-add",
            project,
            "--postgres-env",
            ENV_NAME,
            "--user",
            "alice",
            "--email",
            "alice@qualification.invalid",
            "--password-stdin",
            input=PASSWORD + "\n",
        )
        seed(service)
        service.start_node("A")
        alice = Client(service, contract["rpc_id"])
        alice.login("alice", PASSWORD)
        parent = identifier("issue-red-one")
        path = f"/_diamond/events/issue_changes/{parent}/0"
        standard = [("Host", service.authority), ("Origin", service.origin), ("Cookie", alice.cookie or "")]
        variants = {
            "missing-origin": handshake(service.ports["A"], path, [("Host", service.authority), ("Cookie", alice.cookie or "")]),
            "hostile-origin": handshake(service.ports["A"], path, [("Host", service.authority), ("Origin", "https://hostile.invalid"), ("Cookie", alice.cookie or "")]),
            "duplicate-origin": handshake(service.ports["A"], path, [*standard, ("Origin", service.origin)]),
            "duplicate-cookie": handshake(service.ports["A"], path, [*standard, ("Cookie", alice.cookie or "")]),
            "authorization-confusion": handshake(service.ports["A"], path, [*standard, ("Authorization", "Bearer dia_" + "a" * 64)]),
            "query": handshake(service.ports["A"], path + "?cursor=0", standard),
            "noncanonical-zero": handshake(service.ports["A"], path[:-1] + "00", standard),
            "signed-zero": handshake(service.ports["A"], path[:-1] + "+0", standard),
            "negative-zero": handshake(service.ports["A"], path[:-1] + "-0", standard),
            "cursor-overflow": handshake(service.ports["A"], path[:-1] + "9223372036854775808", standard),
            "encoded-parent": handshake(service.ports["A"], f"/_diamond/events/issue_changes/{parent}%2fother/0", standard),
            "unknown-channel": handshake(service.ports["A"], f"/_diamond/events/unknown/{parent}/0", standard),
        }
        receipt.record(
            "hostile-websocket-handshakes-reject",
            all(item["status"] is None or item["status"] >= 400 for item in variants.values()),
            variants,
            "missing/duplicate/hostile authority, query/encoding, unknown binding and noncanonical cursor handshakes do not upgrade",
        )

        initial_events = int(service.sql("SELECT count(*) FROM diamond_w7.events WHERE app=" + quote(app_id())))
        text_socket = WebSocket("127.0.0.1", service.ports["A"], path, authority=service.authority, origin=service.origin, cookie=alice.cookie)
        opened.append(text_socket)
        text_socket.send_frame("forged-client-publication")
        text_closed, text_observed = closes(text_socket)

        unmasked = WebSocket("127.0.0.1", service.ports["A"], path, authority=service.authority, origin=service.origin, cookie=alice.cookie)
        opened.append(unmasked)
        unmasked.socket.sendall(b"\x81\x01x")
        unmasked_closed, unmasked_observed = closes(unmasked)

        fragmented = WebSocket("127.0.0.1", service.ports["A"], path, authority=service.authority, origin=service.origin, cookie=alice.cookie)
        opened.append(fragmented)
        fragmented.socket.sendall(masked_frame(b"forged-", opcode=1, fin=False) + masked_frame(b"fragment", opcode=0, fin=True))
        fragmented_closed, fragmented_observed = closes(fragmented)

        invalid_control = WebSocket("127.0.0.1", service.ports["A"], path, authority=service.authority, origin=service.origin, cookie=alice.cookie)
        opened.append(invalid_control)
        invalid_control.socket.sendall(masked_frame(b"x", opcode=9, fin=False))
        control_closed, control_observed = closes(invalid_control)
        after_hostile = int(service.sql("SELECT count(*) FROM diamond_w7.events WHERE app=" + quote(app_id())))
        receipt.record(
            "hostile-websocket-frames-close-without-publication",
            all([text_socket.status == 101, unmasked.status == 101, fragmented.status == 101, invalid_control.status == 101])
            and text_closed
            and unmasked_closed
            and fragmented_closed
            and control_closed
            and after_hostile == initial_events,
            {
                "text": text_observed,
                "unmasked": unmasked_observed,
                "fragmented": fragmented_observed,
                "fragmented_control": control_observed,
                "events_before": initial_events,
                "events_after": after_hostile,
            },
            "unsolicited text/fragmented messages and malformed mask/control framing terminate their connection and never create durable events",
        )

        ping = WebSocket("127.0.0.1", service.ports["A"], path, authority=service.authority, origin=service.origin, cookie=alice.cookie)
        opened.append(ping)
        ping.send_frame(b"w9-ping", opcode=9)
        pong = ping.receive()
        emitted = alice.rpc("emit", parent, "post-hostile typed event")
        delivered = ping.receive()
        healthy = alice.rpc("read", {"tag": "issue", "value": parent})
        receipt.record(
            "valid-ping-and-event-recover-after-hostile-frames",
            ping.status == 101
            and pong == {"kind": "frame", "opcode": 10, "bytes": 7}
            and emitted[0] == 200
            and delivered.get("value") == "post-hostile typed event"
            and healthy[0] == 200,
            {"upgrade": ping.status, "pong": pong, "emit": emitted, "delivery": delivered, "read_status": healthy[0]},
            "a canonical socket permits bounded ping/pong and receives a real typed publication after hostile peers are dropped; ordinary HTTP remains usable",
        )
    except Exception as error:
        fatal = repr(error)
    finally:
        for connection in opened:
            try:
                connection.close()
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
        raw_handshakes=12,
        upgraded_frame_scenarios=5,
        command=f"python3 verification/w9_independent/websocket_malformed.py --binary {options.binary}",
        oracle="ORACLES.md E2",
        temporary_root_removed=True,
    )
    options.report.parent.mkdir(parents=True, exist_ok=True)
    options.report.write_text(json.dumps(report, indent=2) + "\n")
    shutil.rmtree(root)
    print(json.dumps({"status": report["status"], "observations": len(receipt.results), "error": fatal, "elapsed_seconds": report["elapsed_seconds"]}))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
