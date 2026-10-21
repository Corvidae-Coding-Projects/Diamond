#!/usr/bin/env python3
"""Authorized bounded offensive campaign against Diamond's real local interfaces.

The target is a frozen Diamond executable, disposable files, SQLite databases,
and loopback listeners only. Existing language, stateful-policy, and transport
campaigns are rerun with the supplied fresh seed; this driver adds exploit
replays discovered during the red-team pass and independent injection oracles.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import http.client
import json
import os
from pathlib import Path
import random
import re
import resource
import secrets
import select
import shutil
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.parse

import transport_exploration as transport


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SEED = 0xD1A0_6001


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def child_limits() -> None:
    resource.setrlimit(resource.RLIMIT_AS, (512 * 1024**2, 512 * 1024**2))
    resource.setrlimit(resource.RLIMIT_CPU, (10, 10))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def invoke(binary: Path, arguments: list[str], *, timeout: float = 12,
           input_text: str | None = None, environment: dict[str, str] | None = None,
           limited: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(binary), *arguments], cwd=ROOT, input=input_text, text=True,
        capture_output=True, timeout=timeout, env=environment,
        preexec_fn=child_limits if limited else None,
    )


def parsed_command(arguments: list[str], timeout: int = 600,
                   environment: dict[str, str] | None = None) -> dict[str, object]:
    started = time.monotonic()
    result = subprocess.run(
        arguments, cwd=ROOT, text=True, capture_output=True, timeout=timeout,
        env=os.environ | (environment or {}),
    )
    assert result.returncode == 0, (arguments, result.returncode, result.stdout, result.stderr)
    assert "panicked at" not in result.stderr
    return {
        "command": arguments,
        "seconds": round(time.monotonic() - started, 3),
        "receipt": json.loads(result.stdout),
    }


def safe_stderr(value: bytes) -> None:
    assert b"\x1b" not in value
    assert b"\n" not in value.rstrip(b"\n")
    assert b"panicked" not in value


def cli_and_compiler(binary: Path, directory: Path, generator: random.Random) -> dict[str, object]:
    terminal_cases: list[dict[str, object]] = []
    arguments = [
        ["bad\x1b[2Jcommand"],
        ["check", "name\x1b[31m\nforged: ok.dia"],
        ["db-init", "examples/storage.dia", "--database",
         "missing\x1b[31m\nforged-dir/database.sqlite"],
    ]
    for case in arguments:
        result = subprocess.run([os.fsencode(binary), *map(os.fsencode, case)],
                                cwd=ROOT, capture_output=True, timeout=4,
                                preexec_fn=child_limits)
        assert result.returncode == 1
        safe_stderr(result.stderr)
        terminal_cases.append({"arguments_hex": [os.fsencode(item).hex() for item in case],
                               "stderr_hex": result.stderr.hex()})
    invalid = subprocess.run([os.fsencode(binary), b"bad\xff"], cwd=ROOT,
                             capture_output=True, timeout=4, preexec_fn=child_limits)
    assert invalid.returncode == 1
    safe_stderr(invalid.stderr)

    fifo = directory / "source.fifo"
    os.mkfifo(fifo, 0o600)
    started = time.monotonic()
    rejected_fifo = invoke(binary, ["check", str(fifo)], timeout=3)
    fifo_seconds = time.monotonic() - started
    assert rejected_fifo.returncode == 1 and fifo_seconds < 1
    assert "regular file" in rejected_fifo.stderr

    oversized = directory / "oversized.dia"
    with oversized.open("wb") as output:
        output.truncate(64 * 1024**2)
    started = time.monotonic()
    rejected_size = invoke(binary, ["check", str(oversized)], timeout=3)
    size_seconds = time.monotonic() - started
    assert rejected_size.returncode == 1 and size_seconds < 1
    assert "source size" in rejected_size.stderr and "1048576-byte limit" in rejected_size.stderr

    fuzz_cases = 48
    alphabet = "[]():,.<>+-*/?!@|_ abcdef0123456789雪\u202e"
    for index in range(fuzz_cases):
        source = directory / f"malformed-{index}.dia"
        hostile = "unsupported_" + "".join(generator.choice(alphabet) for _ in range(generator.randint(1, 5000)))
        source.write_text(hostile, encoding="utf-8")
        result = invoke(binary, ["check", str(source)], timeout=4)
        assert result.returncode != 0
        assert "panicked at" not in result.stderr

    function_count = 4094
    pieces = ["app Stress:\n  database SQLite\n"]
    for index in range(function_count - 2):
        pieces.append(f"fn f{index}() -> Int:\n  f{index + 1}()\n")
    pieces.append(
        f"fn f{function_count - 2}() -> Int:\n  transaction db:\n    1\n"
    )
    pieces.append("fn main() -> Int:\n  f0()\n")
    graph = directory / "maximum-call-graph.dia"
    graph.write_text("".join(pieces))
    started = time.monotonic()
    checked = invoke(binary, ["check", str(graph)], timeout=8)
    graph_seconds = time.monotonic() - started
    assert checked.returncode == 0 and graph_seconds < 5, (graph_seconds, checked.stderr)
    assert "4094 functions" in checked.stdout
    return {
        "terminal_cases": len(terminal_cases) + 1,
        "malformed_source_cases": fuzz_cases,
        "fifo_rejection_seconds": round(fifo_seconds, 3),
        "oversize_rejection_seconds": round(size_seconds, 3),
        "maximum_call_graph_functions": function_count,
        "maximum_call_graph_seconds": round(graph_seconds, 3),
    }


class EchoServer:
    def __init__(self, binary: Path, source: Path):
        self.process = subprocess.Popen(
            [str(binary), "run", str(source), "--listen", "127.0.0.1:0"],
            cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            preexec_fn=child_limits,
        )
        assert self.process.stderr is not None
        assert select.select([self.process.stderr], [], [], 8)[0]
        line = self.process.stderr.readline()
        match = re.search(r"http://localhost:(\d+)", line)
        assert match, line
        self.port = int(match[1])

    def request(self, payload: str) -> tuple[int, dict[str, str], str]:
        body = urllib.parse.urlencode({"value": payload}).encode()
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=8)
        try:
            connection.request(
                "POST", "/echo", body,
                {"Host": f"localhost:{self.port}",
                 "Origin": f"http://localhost:{self.port}",
                 "Content-Type": "application/x-www-form-urlencoded"},
            )
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read().decode()
        finally:
            connection.close()

    def stop(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
        stdout, stderr = self.process.communicate(timeout=4)
        assert not stdout and "panicked at" not in stderr


def html_escape(value: str) -> str:
    return (value.replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;").replace("'", "&#39;"))


def html_and_redirects(binary: Path, directory: Path,
                       generator: random.Random) -> dict[str, object]:
    source = directory / "echo.dia"
    source.write_text(
        "type Echo:\n  value: Text\n\n"
        "public route POST \"/echo\":\n"
        "  let form = request.form<Echo>()?\n"
        "  html:\n    <p title={form.value}>{form.value}</p>\n"
    )
    payloads = [
        '<script>alert("x")</script>',
        '\"><img src=x onerror=alert(1)>',
        "' autofocus onfocus='alert(1)",
        "javascript:alert(1)&<>'\"",
        "</p><svg/onload=alert(1)>",
    ]
    alphabet = "<>\"'&/=` abcXYZ012雪💎"
    payloads += ["".join(generator.choice(alphabet) for _ in range(generator.randint(1, 96)))
                 for _ in range(27)]
    server = EchoServer(binary, source)
    try:
        for payload in payloads:
            status, headers, body = server.request(payload)
            escaped = html_escape(payload)
            assert status == 200
            assert body.strip() == f'<p title="{escaped}">{escaped}</p>'
            assert headers["Content-Security-Policy"] == (
                "default-src 'none'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
            )
            assert headers["X-Content-Type-Options"] == "nosniff"
    finally:
        server.stop()

    redirects = ["/safe\\r\\nInjected: yes", "//outside/path", "/encoded%0d%0aHeader:x", "/back\\slash"]
    for index, value in enumerate(redirects):
        candidate = directory / f"redirect-{index}.dia"
        candidate.write_text(f'public route GET "/":\n  redirect "{value}"\n')
        result = invoke(binary, ["check", str(candidate)])
        assert result.returncode != 0
    return {"escaped_html_payloads": len(payloads), "rejected_redirects": len(redirects)}


def account(binary: Path, directory: Path, username: str) -> tuple[Path, str]:
    path = directory / f"{username}.sqlite"
    password = secrets.token_urlsafe(24)
    result = transport.user_add(binary, path, username, password)
    assert result.returncode == 0, (result.stdout, result.stderr)
    return path, password


def login_shape(binary: Path, directory: Path) -> dict[str, object]:
    accounts, password = account(binary, directory, "alice")
    server = transport.Server(binary, directory, accounts)
    durations: dict[str, list[float]] = defaultdict(list)
    try:
        _, headers, page = server.request("GET", "/_diamond/login")
        request_headers = {"Cookie": transport.cookie(headers),
                           "Content-Type": "application/x-www-form-urlencoded"}
        csrf = transport.csrf(page)
        body_hashes = set()
        response_headers = set()
        for index in range(16):
            username = "alice" if index % 2 == 0 else "nonexistent"
            body = transport.form({"username": username, "password": password + "-wrong",
                                   "_diamond_csrf": csrf})
            started = time.monotonic()
            status, observed_headers, response = server.request(
                "POST", "/_diamond/login", body, request_headers
            )
            durations[username].append(time.monotonic() - started)
            assert status == 401
            body_hashes.add(hashlib.sha256(response).hexdigest())
            response_headers.add(tuple(sorted(observed_headers.items())))
        assert len(body_hashes) == 1 and len(response_headers) == 1
    finally:
        server.stop()
    return {
        "comparisons": 16,
        "response_bodies": len(body_hashes),
        "response_header_shapes": len(response_headers),
        "known_wrong_median_ms": round(statistics.median(durations["alice"]) * 1000, 3),
        "unknown_median_ms": round(statistics.median(durations["nonexistent"]) * 1000, 3),
    }


def session_exhaustion(binary: Path, directory: Path) -> dict[str, object]:
    accounts, password = account(binary, directory, "capacity")
    server = transport.Server(binary, directory, accounts)
    try:
        last = None
        for _ in range(4097):
            last = server.request("GET", "/_diamond/login")
            assert last[0] == 200
        assert last is not None
        _, headers, page = last
        anonymous = transport.cookie(headers)
        login = transport.form({"username": "capacity", "password": password,
                                "_diamond_csrf": transport.csrf(page)})
        status, headers, _ = server.request(
            "POST", "/_diamond/login", login,
            {"Cookie": anonymous, "Content-Type": "application/x-www-form-urlencoded"},
        )
        assert status == 303
        user = transport.cookie(headers)
        for _ in range(4097):
            assert server.request("GET", "/_diamond/login")[0] == 200
        assert server.request("GET", "/health")[0] == 200
        assert server.request("GET", "/", headers={"Cookie": user})[0] == 200
        assert server.process.poll() is None
        return {"anonymous_requests": 8194, "login_after_first_flood": status,
                "authenticated_session_after_second_flood": 200,
                "total_http_requests": server.requests}
    finally:
        server.stop()


def users(path: Path) -> list[str]:
    with sqlite3.connect(path) as connection:
        return [row[0] for row in connection.execute(
            "SELECT username FROM diamond_auth_users ORDER BY username"
        )]


def account_replacement(binary: Path, directory: Path) -> dict[str, object]:
    shared = directory / "red-team-lstat-pause.so"
    compiler = subprocess.run(
        ["cc", "-shared", "-fPIC", "-Wall", "-Wextra", "-Werror", "-o", str(shared),
         str(ROOT / "verification/red_team_lstat_pause.c"), "-ldl"],
        capture_output=True, text=True, timeout=20,
    )
    assert compiler.returncode == 0, compiler.stderr
    race = directory / "race"
    race.mkdir()
    candidate, _ = account(binary, race, "candidate-owner")
    candidate.rename(race / "candidate.sqlite")
    candidate = race / "candidate.sqlite"
    victim, _ = account(binary, race, "victim-owner")
    victim.rename(race / "victim.sqlite")
    victim = race / "victim.sqlite"
    backup, ready, release = race / "candidate.original", race / "ready", race / "release"
    environment = os.environ | {
        "LD_PRELOAD": str(shared),
        "DIAMOND_RED_TEAM_RACE_PATH": str(candidate),
        "DIAMOND_RED_TEAM_RACE_READY": str(ready),
        "DIAMOND_RED_TEAM_RACE_RELEASE": str(release),
    }
    process = subprocess.Popen(
        [str(binary), "user-add", "--auth", str(candidate), "--user", "injected-user",
         "--password-stdin"], cwd=ROOT, text=True, stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment,
    )
    assert process.stdin is not None
    process.stdin.write("red-team-password-12345\n")
    process.stdin.close()
    deadline = time.monotonic() + 12
    while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
        time.sleep(0.002)
    assert ready.exists(), process.poll()
    candidate.rename(backup)
    victim.rename(candidate)
    release.touch()
    process.wait(timeout=12)
    stdout = process.stdout.read() if process.stdout else ""
    stderr = process.stderr.read() if process.stderr else ""
    assert process.returncode != 0 and not stdout
    assert "changed while it was being opened" in stderr
    assert users(candidate) == ["victim-owner"]
    assert users(backup) == ["candidate-owner"]
    return {"exit": process.returncode, "injected_account_present": False,
            "replacement_users": users(candidate), "original_users": users(backup)}


def corrupted_hash_startup(binary: Path, directory: Path) -> dict[str, object]:
    case = directory / "corrupt-provider"
    case.mkdir()
    accounts, _ = account(binary, case, "corrupt-user")
    with sqlite3.connect(accounts) as connection:
        encoded = connection.execute(
            "SELECT password_hash FROM diamond_auth_users WHERE username='corrupt-user'"
        ).fetchone()[0]
        connection.execute(
            "UPDATE diamond_auth_users SET password_hash=? WHERE username='corrupt-user'",
            (encoded.replace("$argon2id$", "$argon2i$"),),
        )
    database = case / "must-not-create.sqlite"
    result = invoke(
        binary,
        ["run", str(transport.FIXTURE), "--listen", "127.0.0.1:0",
         "--database", str(database), "--auth", str(accounts)],
    )
    assert result.returncode != 0
    assert "account hash configuration" in result.stderr
    assert not database.exists()
    return {"exit": result.returncode, "application_database_created": database.exists()}


def audit_dependencies() -> dict[str, object]:
    result = subprocess.run(
        ["cargo", "audit", "--no-fetch", "--json"], cwd=ROOT,
        text=True, capture_output=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    assert observed["vulnerabilities"]["count"] == 0
    assert not observed["warnings"]
    return {"database": observed["database"], "dependency_count": observed["lockfile"]["dependency-count"],
            "vulnerabilities": observed["vulnerabilities"]["count"], "warnings": observed["warnings"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, default=ROOT / "target/debug/diamond")
    parser.add_argument("--seed", type=lambda value: int(value, 0), default=DEFAULT_SEED)
    parser.add_argument("--report", type=Path)
    options = parser.parse_args()
    directory = Path(tempfile.mkdtemp(prefix=f"diamond-red-team-{options.seed:x}-"))
    frozen = directory / "diamond"
    shutil.copyfile(options.binary.resolve(), frozen)
    frozen.chmod(0o700)
    fingerprint = digest(frozen)
    generator = random.Random(options.seed)
    report: dict[str, object] = {
        "status": "RUNNING", "seed": options.seed, "binary_sha256": fingerprint,
        "scope": "frozen local executable, disposable files/SQLite, and loopback only",
        "objectives": {}, "subcampaigns": [],
    }
    try:
        report["objectives"] = {
            "source_and_compiler": cli_and_compiler(frozen, directory, generator),
            "html_redirect_and_headers": html_and_redirects(frozen, directory, generator),
            "login_response_uniformity": login_shape(frozen, directory),
            "session_capacity_resilience": session_exhaustion(frozen, directory),
            "account_path_replacement": account_replacement(frozen, directory),
            "corrupted_hash_startup": corrupted_hash_startup(frozen, directory),
            "native_and_dependency_security": audit_dependencies(),
        }
        campaigns = [
            [sys.executable, "verification/language_exploration.py", "--binary", str(frozen),
             "--library", str(ROOT / "target/debug/libdiamond.rlib"), "--seed", hex(options.seed)],
            [sys.executable, "verification/stateful_exploration.py", "--binary", str(frozen),
             "--seed", hex(options.seed ^ 0x51A7_EF)],
            [sys.executable, "verification/transport_exploration.py", "--binary", str(frozen),
             "--seed", hex(options.seed ^ 0x7A45_90A7)],
            [sys.executable, "verification/compiler_enforcement.py"],
        ]
        report["subcampaigns"] = [
            parsed_command(command, environment={"DIAMOND_BINARY": str(frozen)})
            for command in campaigns
        ]
        assert digest(frozen) == fingerprint
        report["status"] = "PASS"
    except BaseException as error:
        report["status"] = "FAIL"
        report["error"] = repr(error)
        retained = directory
        print(f"Failure inputs and isolated state retained at {retained}", file=sys.stderr)
        if options.report:
            options.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        raise
    else:
        if options.report:
            options.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        shutil.rmtree(directory)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
