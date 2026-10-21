#!/usr/bin/env python3
"""Cancel our actual CLI/HTTP child at a controlled SQLite commit boundary.

The Linux C shim pauses only one exact disposable journal unlink, before or
after the real syscall. The parent SIGKILLs its own paused child or disconnects
its own client and releases it. Fresh Diamond reads recover first; an independent
SQLite connection then checks integrity and exact values. No power-loss claim.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import random
import re
import resource
import select
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time

from transaction_faults import PREFIX

ROOT = Path(__file__).resolve().parents[1]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def limits() -> None:
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024,) * 2)
    resource.setrlimit(resource.RLIMIT_CPU, (10, 10))


def environment(directory: Path, shim: Path, phase: str) -> dict[str, str]:
    values = {key: value for key, value in os.environ.items()
              if key != "LD_PRELOAD" and not key.startswith("DIAMOND_PAUSE_")}
    return values | {"LD_PRELOAD": str(shim), "DIAMOND_PAUSE_JOURNAL": str(directory / "app.sqlite-journal"),
                     "DIAMOND_PAUSE_ARM": str(directory / "armed"), "DIAMOND_PAUSE_PHASE": phase,
                     "DIAMOND_PAUSE_MARKER": str(directory / "paused"),
                     "DIAMOND_PAUSE_RELEASE": str(directory / "released")}


def source(protocol: str, scope: str, values: list[str]) -> str:
    writes = [f'let saved{index} = notes.create(NewNote(title: {json.dumps(value, ensure_ascii=False)}))?'
              for index, value in enumerate(values)]
    indent = "    " if scope == "explicit" else "  "
    body = "  transaction db:\n" if scope == "explicit" else ""
    body += "".join(indent + write + "\n" for write in writes)
    body += indent + (f"Ok({len(values)})\n" if protocol == "CLI" else '"committed"\n')
    if protocol == "CLI":
        return PREFIX + "fn main() -> Result<Int, DataError>:\n" + body
    return PREFIX + 'public route POST "/write":\n' + body + '\npublic route GET "/ready":\n  "ready"\n'


def invoke(binary: Path, command: str, program: Path, database: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run([str(binary), command, str(program), "--database", str(database)],
                          cwd=ROOT, capture_output=True, text=True, timeout=10, preexec_fn=limits)


def wait_marker(directory: Path, process: subprocess.Popen) -> None:
    deadline = time.monotonic() + 5
    while not (directory / "paused").exists():
        assert process.poll() is None, ("child exited before controlled pause", process.communicate())
        assert time.monotonic() < deadline, "controlled pause was not reached"
        time.sleep(0.005)


def healthy(port: int) -> None:
    with socket.create_connection(("127.0.0.1", port), timeout=5) as connection:
        connection.sendall(f"GET /ready HTTP/1.1\r\nHost: localhost:{port}\r\n\r\n".encode())
        response = b""
        while part := connection.recv(4096):
            response += part
    assert response.startswith(b"HTTP/1.1 200") and response.endswith(b"ready"), response


def case(binary: Path, shim: Path, directory: Path, protocol: str, scope: str,
         phase: str, action: str, values: list[str]) -> dict:
    database, program = directory / "app.sqlite", directory / "program.dia"
    program.write_text(source(protocol, scope, values))
    initialized = invoke(binary, "db-init", program, database)
    assert initialized.returncode == 0, initialized.stderr
    command = [str(binary), "run", str(program), "--database", str(database)]
    if protocol == "HTTP":
        command += ["--listen", "127.0.0.1:0"]
    # CLI opening checks the already initialized schema without deleting a journal.
    if protocol == "CLI":
        (directory / "armed").touch()
    process = subprocess.Popen(command, cwd=ROOT, env=environment(directory, shim, phase),
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                               preexec_fn=limits)
    connection = None
    try:
        port = 0
        if protocol == "HTTP":
            assert process.stderr is not None
            assert select.select([process.stderr], [], [], 5)[0], "server startup timeout"
            line = process.stderr.readline()
            match = re.search(r"http://localhost:(\d+)", line)
            assert match, line
            port = int(match[1])
            healthy(port)
            (directory / "armed").touch()
            connection = socket.create_connection(("127.0.0.1", port), timeout=5)
            connection.sendall(f"POST /write HTTP/1.1\r\nHost: localhost:{port}\r\nOrigin: http://localhost:{port}\r\nContent-Length: 0\r\n\r\n".encode())
        wait_marker(directory, process)
        assert (directory / "paused").read_text() == phase
        journal = Path(str(database) + "-journal")
        assert journal.exists() == (phase == "before"), (phase, "journal state")
        if phase == "before":
            assert journal.stat().st_size > 512 and journal.read_bytes()[:8] != bytes(8), "journal not hot-ready"
        if action == "kill":
            process.kill()
            stdout, stderr = process.communicate(timeout=5)
            assert process.returncode == -9 and not stdout and "panicked at" not in stderr
        else:
            assert connection is not None
            connection.shutdown(socket.SHUT_RDWR)
            connection.close()
            connection = None
            (directory / "released").touch()
            # This queues behind the released handler on the same real server.
            healthy(port)
            process.terminate()
            stdout, stderr = process.communicate(timeout=5)
            assert not stdout and "panicked at" not in stderr
        if connection:
            connection.close()
            connection = None
        expected = [] if action == "kill" and phase == "before" else values
        readback = directory / "readback.dia"
        readback.write_text(PREFIX + 'fn main() -> Result<Int, DataError>:\n  let found = notes.select(limit: 100)?\n  Ok(len(found))\n')
        # Actual Diamond performs first post-crash open/recovery, before Python.
        recovered = invoke(binary, "run", readback, database)
        assert recovered.returncode == 0 and recovered.stdout == f"{len(expected)}\n", recovered.stderr
        with sqlite3.connect(database) as independent:
            assert independent.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
            observed = independent.execute("SELECT title,visible FROM Notes ORDER BY title COLLATE BINARY").fetchall()
        assert observed == sorted((value, 1) for value in expected), (observed, expected)
        assert not journal.exists(), "recovery left a journal"
        return {"protocol": protocol, "scope": scope, "phase": phase, "action": action,
                "source_sha256": digest(program), "expected_rows": len(expected),
                "observed_rows": observed, "fresh_Diamond_readback": recovered.stdout.strip(),
                "integrity": "ok", "result": "PASS"}
    finally:
        if connection:
            connection.close()
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, default=ROOT / "target/debug/diamond")
    parser.add_argument("--seed", type=lambda value: int(value, 0), default=0xD1A05)
    parser.add_argument("--report", type=Path)
    options = parser.parse_args()
    rng = random.Random(options.seed)
    binary = options.binary.resolve()
    original = digest(binary)
    report = {"binary_sha256": original, "seed": options.seed, "cases": []}
    directory = Path(tempfile.mkdtemp(prefix="diamond-cp5-cancellation-"))
    try:
        frozen = directory / "diamond"
        shutil.copy2(binary, frozen)
        assert digest(frozen) == original
        shim = directory / "pause.so"
        subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-fPIC", "-shared",
                        str(ROOT / "verification/process_pause.c"), "-o", str(shim)],
                       check=True, capture_output=True, timeout=15)
        for round_number in range(2):
            payload = ["SQL '); -- <b>雪</b>", "é" * 110][round_number]
            suffix = str(rng.randrange(10_000))
            for protocol in ["CLI", "HTTP"]:
                for scope in ["implicit", "explicit"]:
                    values = [payload + " A" + suffix]
                    if scope == "explicit": values.append(payload + " B" + suffix)
                    for phase in ["before", "after"]:
                        case_dir = directory / str(len(report["cases"]))
                        case_dir.mkdir()
                        report["cases"].append(case(frozen, shim, case_dir, protocol, scope, phase, "kill", values))
            # A completed submission with a lost connection has no automatic retry.
            for phase in ["before", "after"]:
                case_dir = directory / str(len(report["cases"]))
                case_dir.mkdir()
                report["cases"].append(case(frozen, shim, case_dir, "HTTP", "explicit", phase,
                                            "disconnect", [payload + " A" + suffix, payload + " B" + suffix]))
    except BaseException:
        print(f"Failure source and isolated state retained at {directory}", file=sys.stderr)
        raise
    else:
        shutil.rmtree(directory)
    report.update(result="PASS", case_count=len(report["cases"]), temporary_resources_removed=True)
    if options.report:
        options.report.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({key: value for key, value in report.items() if key != "cases"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
