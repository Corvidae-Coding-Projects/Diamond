#!/usr/bin/env python3
"""Replay real SQLite commit rejection and acknowledgment loss through Diamond.

Build first: cargo build --locked
Replay: python3 verification/transaction_faults.py

Linux and a C compiler are required. The small LD_PRELOAD shim is compiled into
an automatically removed temporary directory and passed only to child Diamond
processes. It matches one exact disposable journal path and fires once after an
arm file is created. No production switch, substituted database, or evaluator is
used. Python's separate SQLite connection supplies the persistence oracle.

Predefined outcomes:
* A normal write commits exactly once and remains readable after restart.
* A SHARED SQLite read lock rejects COMMIT; a successful ROLLBACK permits a
  definite abort (HTTP 409), leaves no row/journal, and preserves connection use.
* Real journal deletion followed by a synthetic EIO loses acknowledgment at
  SQLite's DELETE-journal commit point. The row exists, but Diamond must report
  unknown (HTTP 503 with opaque operation ID and no automatic retry), then deny
  further database operations on that connection. A restart can read the row.

The unlink fault does not emulate power loss or verify physical disk durability.
Atomic-commit reference: https://sqlite.org/atomiccommit.html#_deleting_the_rollback_journal
"""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
from pathlib import Path
import random
import re
import select
import shutil
import sqlite3
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SEED = 0xD1A04
PREFIX = '''app FaultReplay:
  database SQLite
  provides DbRead<Notes>, DbWrite<Notes>

type NoteId = Id<"note">

type Note:
  id: NoteId
  title: Text
  visible: Bool = true

type NewNote:
  title: Text<length: 1..120>

resource Notes: Note

policy Notes:
  read(note, by principal):
    note.visible
  create(proposed, by principal):
    true
  update(before, after, by principal):
    before.visible and after.visible
  delete(note, by principal):
    note.visible

'''
EVENT = "unlink committed journal; returned EIO\n"
OPERATION = re.compile(r"operation[ :]+([0-9a-f]{64})(?:[; .\n]|$)", re.IGNORECASE)


def fingerprint(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def environment(database: Path, directory: Path, shim: Path | None) -> dict[str, str]:
    result = {key: value for key, value in os.environ.items()
              if key != "LD_PRELOAD" and not key.startswith("DIAMOND_FAULT_")}
    if shim is not None:
        result.update(LD_PRELOAD=str(shim),
                      DIAMOND_FAULT_JOURNAL=str(database) + "-journal",
                      DIAMOND_FAULT_ARM=str(directory / "armed"),
                      DIAMOND_FAULT_LOG=str(directory / "fault.log"))
    return result


def source_text(protocol: str, scope: str, title: str) -> str:
    write = f"let saved = notes.create(NewNote(title: {json.dumps(title, ensure_ascii=False)}))?"
    if protocol == "CLI":
        body = (f"  transaction db:\n    {write}\n    Ok(saved.id)\n" if scope == "explicit"
                else f"  {write}\n  Ok(saved.id)\n")
        return PREFIX + "fn main() -> Result<NoteId, DataError>:\n" + body
    body = (f'  transaction db:\n    {write}\n    "committed"\n' if scope == "explicit"
            else f'  {write}\n  "committed"\n')
    return (PREFIX + 'public route POST "/write":\n' + body +
            '\npublic route GET "/ready":\n  "ready"\n' +
            '\npublic route GET "/read":\n' +
            '  let entries = for note in notes.select(limit: 100)? :\n' +
            '    html:\n      <p>{note.title}</p>\n' +
            '  html:\n    <main>{entries}</main>\n')


def rows(database: Path) -> list[tuple[str, str, int]]:
    with sqlite3.connect(database, timeout=3) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        return connection.execute("SELECT id,title,visible FROM Notes ORDER BY id").fetchall()


def check_rows(database: Path, title: str, count: int) -> list[tuple[str, str, int]]:
    actual = rows(database)
    assert len(actual) == count, ("row count", actual, count)
    for identifier, saved_title, visible in actual:
        assert re.fullmatch("[0-9a-f]{64}", identifier), actual
        assert (saved_title, visible) == (title, 1), actual
    return actual


class Server:
    def __init__(self, binary: Path, source: Path, database: Path,
                 directory: Path, shim: Path | None) -> None:
        self.process = subprocess.Popen(
            [str(binary), "run", str(source), "--database", str(database),
             "--listen", "127.0.0.1:0"], cwd=ROOT,
            env=environment(database, directory, shim), stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True)
        self.port = 0
        self.requests = 0
        try:
            assert self.process.stderr is not None
            assert select.select([self.process.stderr], [], [], 8)[0], "startup timeout"
            line = self.process.stderr.readline()
            match = re.search(r"http://localhost:(\d+)", line)
            assert match, ("startup failed", line)
            self.port = int(match[1])
            assert self.request("GET", "/ready") == (200, "ready")
        except BaseException:
            self.stop()
            raise

    def request(self, method: str, path: str) -> tuple[int, str]:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=8)
        headers = {"Host": f"localhost:{self.port}"}
        if method == "POST":
            headers["Origin"] = f"http://localhost:{self.port}"
        try:
            connection.request(method, path, headers=headers)
            response = connection.getresponse()
            result = response.status, response.read().decode()
            self.requests += 1
            return result
        finally:
            connection.close()

    def stop(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
        try:
            stdout, stderr = self.process.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            self.process.kill()
            stdout, stderr = self.process.communicate(timeout=3)
        assert not stdout, ("unexpected server stdout", stdout)
        assert "panicked at" not in stderr, stderr


def observe_unknown(text: str) -> str:
    assert "outcome unknown" in text.lower(), text
    assert "do not automatically retry" in text.lower(), text
    operation = OPERATION.search(text)
    assert operation, ("missing opaque operation ID", text)
    return operation[1]


def run_case(binary: Path, shim: Path, directory: Path, protocol: str,
             scope: str, outcome: str, title: str, receipt: dict) -> None:
    database, source = directory / "app.sqlite", directory / "replay.dia"
    source.write_text(source_text(protocol, scope, title))
    receipt["source_sha256"] = fingerprint(source)
    init = subprocess.run([str(binary), "db-init", str(source), "--database", str(database)],
                          cwd=ROOT, env=environment(database, directory, None),
                          capture_output=True, text=True, timeout=8)
    assert init.returncode == 0, init.stderr
    assert rows(database) == []
    reader = None
    server = None
    try:
        if protocol == "HTTP":
            server = Server(binary, source, database, directory,
                            shim if outcome == "ack-loss" else None)
        if outcome == "read-lock":
            reader = sqlite3.connect(database)
            assert reader.execute("PRAGMA journal_mode").fetchone() == ("delete",)
            reader.execute("BEGIN")
            assert reader.execute("SELECT * FROM Notes").fetchall() == []
        elif outcome == "ack-loss":
            (directory / "armed").touch()
        if protocol == "HTTP":
            assert server is not None
            status, body = server.request("POST", "/write")
            receipt.update(status=status, body=body)
        else:
            result = subprocess.run([str(binary), "run", str(source), "--database", str(database)],
                                    cwd=ROOT, env=environment(database, directory,
                                    shim if outcome == "ack-loss" else None),
                                    capture_output=True, text=True, timeout=8)
            receipt.update(exit=result.returncode, stdout=result.stdout, stderr=result.stderr)
            body = result.stderr
            status = result.returncode
        if outcome == "read-lock":
            assert reader is not None
            reader.rollback()
            reader.close()
            reader = None
            receipt["rows_after_failure"] = check_rows(database, title, 0)
            assert not Path(str(database) + "-journal").exists(), "rollback journal survived"
            if protocol == "HTTP":
                assert (status, body) == (409, "Transaction aborted"), receipt
                assert server is not None
                receipt["after_unlock"] = server.request("POST", "/write")
                assert receipt["after_unlock"] == (200, "committed"), receipt
            else:
                assert status != 0 and "transaction aborted" in body, receipt
                recovered = subprocess.run(
                    [str(binary), "run", str(source), "--database", str(database)],
                    cwd=ROOT, env=environment(database, directory, None),
                    capture_output=True, text=True, timeout=8)
                assert recovered.returncode == 0, recovered.stderr
        elif outcome == "ack-loss":
            receipt["fault_log"] = (directory / "fault.log").read_text()
            assert receipt["fault_log"] == EVENT, receipt
            receipt["rows_after_failure"] = check_rows(database, title, 1)
            assert not Path(str(database) + "-journal").exists(), "commit point not reached"
            if protocol == "HTTP":
                assert status == 503, receipt
                assert server is not None
                receipt["poisoned_write"] = server.request("POST", "/write")
                receipt["poisoned_read"] = server.request("GET", "/read")
                assert receipt["poisoned_write"][0] == 404, receipt
                assert receipt["poisoned_read"][0] == 404, receipt
                assert server.request("GET", "/ready") == (200, "ready")
            else:
                assert status != 0 and not receipt["stdout"], receipt
            # Keep this assertion strict: an absent ID or false abort is a defect.
            receipt["operation_id"] = observe_unknown(body)
        elif protocol == "HTTP":
            assert (status, body) == (200, "committed"), receipt
        else:
            assert status == 0 and re.fullmatch("[0-9a-f]{64}\n", receipt["stdout"]), receipt
        receipt["final_rows"] = check_rows(database, title, 1)
        if server is not None:
            receipt["requests"] = server.requests
            server.stop()
            server = None
        # New process opens the actual committed SQLite file and renders its row.
        read_source = directory / "readback.dia"
        read_source.write_text(PREFIX + 'fn main() -> Result<Text, DataError>:\n' +
                               f'  let note = notes.get(NoteId("{receipt["final_rows"][0][0]}"))?\n' +
                               '  Ok(note.title)\n')
        reopened = subprocess.run([str(binary), "run", str(read_source), "--database", str(database)],
                                  cwd=ROOT, env=environment(database, directory, None),
                                  capture_output=True, text=True, timeout=8)
        assert reopened.returncode == 0 and reopened.stdout == title + "\n", reopened.stderr
        receipt["fresh_process_readback"] = reopened.stdout.strip()
    finally:
        if reader is not None:
            reader.rollback()
            reader.close()
        if server is not None:
            receipt["requests"] = server.requests
            server.stop()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, default=ROOT / "target/debug/diamond")
    parser.add_argument("--seed", type=lambda text: int(text, 0), default=DEFAULT_SEED)
    parser.add_argument("--rounds", type=int, default=2, choices=range(1, 9))
    parser.add_argument("--report", type=Path, help="optional full JSON observation receipt")
    options = parser.parse_args()
    binary = options.binary.resolve(strict=True)
    rng = random.Random(options.seed)
    report = {"binary": str(binary), "binary_sha256": fingerprint(binary),
              "driver_sha256": fingerprint(Path(__file__)),
              "shim_sha256": fingerprint(ROOT / "verification/transaction_fault.c"),
              "seed": options.seed, "rounds": options.rounds, "cases": []}
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="diamond-transaction-faults-") as temporary:
        directory = Path(temporary)
        # Freeze the selected executable so a concurrent rebuild cannot mix states.
        frozen_binary = directory / "diamond"
        shutil.copy2(binary, frozen_binary)
        assert fingerprint(frozen_binary) == report["binary_sha256"]
        shim = directory / "transaction_fault.so"
        subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-fPIC", "-shared",
                        str(ROOT / "verification/transaction_fault.c"), "-o", str(shim)],
                       check=True, capture_output=True, text=True, timeout=15)
        for round_number in range(options.rounds):
            # Distinct actual values include binding metacharacters, Unicode,
            # and length boundaries; scope/protocol/fault vary independently.
            variants = ["Robert'); DROP TABLE Notes; -- <b>雪</b>", "é" * 120, "x"]
            title = variants[round_number % len(variants)] + "".join(
                rng.choice("abcXYZ019") for _ in range(0 if round_number % 3 == 1 else 5))
            for protocol in ("CLI", "HTTP"):
                for scope in ("implicit", "explicit"):
                    for outcome in ("success", "read-lock", "ack-loss"):
                        receipt = {"round": round_number, "protocol": protocol,
                                   "scope": scope, "outcome": outcome, "title": title}
                        report["cases"].append(receipt)
                        case_directory = directory / str(len(report["cases"]))
                        case_directory.mkdir()
                        try:
                            run_case(frozen_binary, shim, case_directory, protocol, scope,
                                     outcome, title, receipt)
                            receipt["result"] = "PASS"
                        except (AssertionError, OSError, subprocess.SubprocessError) as error:
                            receipt.update(result="FAIL", error=str(error))
    failures = [case for case in report["cases"] if case["result"] == "FAIL"]
    report.update(elapsed_seconds=round(time.monotonic() - started, 3),
                  passed=len(report["cases"]) - len(failures), failed=len(failures),
                  temporary_resources_removed=True,
                  requests=sum(case.get("requests", 0) for case in report["cases"]))
    if options.report:
        options.report.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({key: value for key, value in report.items() if key != "cases"} |
                     {"failures": failures}, indent=2, ensure_ascii=False))
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
