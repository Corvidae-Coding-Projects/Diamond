#!/usr/bin/env python3
"""Bounded loopback integration campaign, using the real CLI/TCP/SQLite paths.

The oracle is the checkpoint-five transport/form/session contract: rejected or
incomplete forms do not write, typed fields preserve their values, a connection
contains at most one request, and a healthy request recovers after each failure.
No production parser, form decoder, authentication function, or mock is imported.
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
import resource
import secrets
import select
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.parse

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "verification/regressions/cp5_transport/forms.dia"
SEED = 0xD1A05


def child_limits() -> None:
    resource.setrlimit(resource.RLIMIT_AS, (512 * 1024**2, 512 * 1024**2))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


class Server:
    def __init__(self, binary: Path, directory: Path, accounts: Path):
        self.database = directory / "entries.sqlite"
        self.process = subprocess.Popen(
            [str(binary), "run", str(FIXTURE), "--listen", "127.0.0.1:0",
             "--database", str(self.database), "--auth", str(accounts)],
            cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            preexec_fn=child_limits,
        )
        assert self.process.stderr is not None
        assert select.select([self.process.stderr], [], [], 8)[0], "startup deadline"
        line = self.process.stderr.readline()
        match = re.search(r"http://localhost:(\d+)", line)
        assert match, f"server did not become ready: {line}"
        self.port = int(match[1])
        self.requests = 0

    def stop(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
        try:
            stdout, stderr = self.process.communicate(timeout=4)
        except subprocess.TimeoutExpired:
            self.process.kill()
            stdout, stderr = self.process.communicate(timeout=4)
        assert not stdout and "panicked at" not in stderr

    def request(self, method: str, path: str, body: bytes | None = None,
                headers: dict[str, str] | None = None) -> tuple[int, dict[str, str], bytes]:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=8)
        outgoing = {"Host": f"localhost:{self.port}", **(headers or {})}
        try:
            connection.request(method, path, body=body, headers=outgoing)
            response = connection.getresponse()
            self.requests += 1
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def raw(self, payload: bytes, fragments: list[int] | None = None,
            half_close: bool = True, hold: bool = False) -> tuple[int, bytes, float]:
        started = time.monotonic()
        connection = socket.create_connection(("127.0.0.1", self.port), timeout=8)
        connection.settimeout(8)
        connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        response = bytearray()
        try:
            if fragments:
                cursor = 0
                for length in fragments:
                    if cursor >= len(payload):
                        break
                    connection.sendall(payload[cursor:cursor + length])
                    cursor += length
                    time.sleep(0.001)
                if cursor < len(payload):
                    connection.sendall(payload[cursor:])
            else:
                connection.sendall(payload)
            if half_close and not hold:
                connection.shutdown(socket.SHUT_WR)
            while True:
                try:
                    chunk = connection.recv(8192)
                except ConnectionResetError:
                    break
                if not chunk:
                    break
                response.extend(chunk)
        finally:
            connection.close()
        self.requests += 1
        match = re.match(rb"HTTP/1\.1 (\d{3}) ", response)
        assert match, f"no HTTP response: {bytes(response[:120])!r}"
        return int(match[1]), bytes(response), time.monotonic() - started

    def health(self) -> None:
        status, _, body = self.request("GET", "/health")
        assert status == 200 and body == b"healthy" and self.process.poll() is None

    def rows(self) -> list[tuple[object, ...]]:
        with sqlite3.connect(self.database) as connection:
            return connection.execute(
                'SELECT owner,text,number,enabled,reference,optional FROM Entries ORDER BY id'
            ).fetchall()


def form(values: dict[str, str]) -> bytes:
    return urllib.parse.urlencode(values).encode()


def csrf(body: bytes) -> str:
    values = re.findall(rb'name="_diamond_csrf" value="([0-9a-f]{64})"', body)
    assert values and len(set(values)) == 1
    return values[0].decode()


def cookie(headers: dict[str, str]) -> str:
    value = headers["Set-Cookie"]
    assert all(flag in value for flag in ["HttpOnly", "Secure", "SameSite=Strict", "Path=/"])
    return value.split(";", 1)[0]


def user_add(binary: Path, path: Path, username: str, password: str) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [str(binary), "user-add", "--auth", str(path), "--user", username, "--password-stdin"],
        input=password + "\n", capture_output=True, text=True, timeout=12,
        cwd=ROOT, preexec_fn=child_limits,
    )
    assert password not in result.stdout + result.stderr
    return result


def accounts(binary: Path, directory: Path) -> tuple[Path, str, dict[str, int]]:
    path = directory / "accounts.sqlite"
    password = secrets.token_urlsafe(24)
    assert user_add(binary, path, "alice", password).returncode == 0
    assert path.stat().st_mode & 0o777 == 0o600
    with sqlite3.connect(path) as connection:
        before = connection.execute("SELECT username,password_hash FROM diamond_auth_users").fetchall()
    assert len(before) == 1 and before[0][0] == "alice"
    assert before[0][1].startswith("$argon2id$v=19$m=19456,t=2,p=1$")
    assert user_add(binary, path, "alice", secrets.token_urlsafe(24)).returncode != 0
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT username,password_hash FROM diamond_auth_users").fetchall() == before
    rejected = 1
    for username, value in [("invalid/name", password), ("x" * 129, password),
                            ("short", "x" * 11), ("long", "x" * 257)]:
        assert user_add(binary, path, username, value).returncode != 0
        rejected += 1
    for kind in ["mode", "symlink", "hardlink", "schema", "trigger"]:
        candidate = directory / f"bad-{kind}.sqlite"
        shutil.copyfile(path, candidate)
        candidate.chmod(0o600)
        if kind == "mode":
            candidate.chmod(0o644)
        elif kind == "symlink":
            candidate.unlink()
            candidate.symlink_to(path)
        elif kind == "hardlink":
            candidate.unlink()
            os.link(path, candidate)
        else:
            with sqlite3.connect(candidate) as connection:
                if kind == "schema":
                    connection.execute("ALTER TABLE diamond_auth_users ADD COLUMN extra TEXT")
                else:
                    connection.execute("CREATE TRIGGER forbidden AFTER INSERT ON diamond_auth_users BEGIN SELECT 1; END")
        assert user_add(binary, candidate, "mallory", password).returncode != 0
        application = directory / f"must-not-create-{kind}.sqlite"
        startup = subprocess.run(
            [str(binary), "run", str(FIXTURE), "--listen", "127.0.0.1:0",
             "--database", str(application), "--auth", str(candidate)],
            cwd=ROOT, capture_output=True, text=True, timeout=8,
            preexec_fn=child_limits,
        )
        assert startup.returncode != 0 and "development server" not in startup.stderr
        assert not application.exists() and not startup.stdout
        rejected += 1
        candidate.unlink()
    # Inclusive byte limits and identifier length, each using independent accounts.
    for index, value in enumerate(["x" * 12, "é" * 128]):
        assert user_add(binary, path, "b" * 128 if index else "minimum", value).returncode == 0
    return path, password, {"account rejection cases": rejected, "account positive boundaries": 3,
                            "browser account startup rejections": 5}


def corrupt_hashes(binary: Path, directory: Path, path: Path, password: str) -> tuple[int, int]:
    # The schema remains valid; only hash configuration is corrupted. A local
    # account file cannot make verification honor arbitrary memory/time costs.
    replacements = [("$argon2id$", "$argon2i$"), ("v=19$", "v=16$"),
                    ("m=19456,", "m=999999999,"), ("t=2,", "t=999999999,"),
                    ("p=1$", "p=999999999$")]
    requests = 0
    for index, (old, new) in enumerate(replacements):
        case = directory / f"corrupt-hash-{index}"
        case.mkdir()
        candidate = case / "accounts.sqlite"
        shutil.copyfile(path, candidate)
        candidate.chmod(0o600)
        with sqlite3.connect(candidate) as connection:
            encoded = connection.execute("SELECT password_hash FROM diamond_auth_users WHERE username='alice'").fetchone()[0]
            assert old in encoded
            connection.execute("UPDATE diamond_auth_users SET password_hash=? WHERE username='alice'", (encoded.replace(old, new),))
        database = case / "entries.sqlite"
        result = subprocess.run(
            [str(binary), "run", str(FIXTURE), "--listen", "127.0.0.1:0",
             "--database", str(database), "--auth", str(candidate)],
            capture_output=True, text=True, timeout=8, cwd=ROOT,
            preexec_fn=child_limits,
        )
        assert result.returncode != 0
        assert "account hash configuration" in result.stderr
        assert not database.exists()
    return len(replacements), requests


def id_regression(binary: Path, directory: Path, observe_known: bool) -> dict[str, object]:
    """Minimal replay of the discovered supported-Id validation bypass."""
    path, password = directory / "accounts.sqlite", secrets.token_urlsafe(24)
    assert user_add(binary, path, "alice", password).returncode == 0
    server = Server(binary, directory, path)
    results: dict[str, int] = {}
    matched: list[dict[str, object]] = []
    try:
        _, headers, page = server.request("GET", "/_diamond/login")
        login_headers = {"Cookie": cookie(headers), "Content-Type": "application/x-www-form-urlencoded"}
        status, headers, _ = server.request("POST", "/_diamond/login", form({"username": "alice", "password": password, "_diamond_csrf": csrf(page)}), login_headers)
        assert status == 303
        current = cookie(headers)
        _, _, page = server.request("GET", "/", headers={"Cookie": current})
        outgoing = {"Cookie": current, "Content-Type": "application/x-www-form-urlencoded", "X-Diamond-CSRF": csrf(page)}
        for reference in ["invalid/name", "雪", "x" * 129]:
            payload = form({"text": "ordinary", "number": "0", "enabled": "false", "reference": reference, "optional": ""})
            status = server.request("POST", "/entries", payload, outgoing)[0]
            assert status == 400 or (observe_known and status == 501), status
            results[reference if len(reference) < 20 else "overlong Id"] = status
            assert server.rows() == []
            server.health()
        for field in ["reference", "optional"]:
            values = {"reference": "alice", "optional": ""}
            values[field] = "invalid/name"
            status, _, body = server.request("POST", "/id-validation", form(values), outgoing)
            has_validation = b"Typed validation failed" in body
            assert (status == 400 and has_validation) or (observe_known and status == 501)
            matched.append({"field": field, "status": status, "application validation page": has_validation})
            assert server.rows() == []
            server.health()
        return {"invalid Id form statuses": results, "matched Id validation results": matched,
                "cases": {"Id value rejection cases": 3, "matched Id validation cases": 2, "HTTP requests": server.requests}}
    finally:
        server.stop()


def authority_rejections(server: Server) -> list[dict[str, object]]:
    """A fully framed authority rejection must have a complete HTTP response.

    The 16 KiB header boundary leaves every body byte outside the transport's
    header read. It reproduces the early-Origin/Host rejection's unread-data
    reset without depending on ordinary packet coalescing.
    """
    results: list[dict[str, object]] = []
    before = server.rows()
    for authority, expected in [("Origin", 404), ("Host", 400)]:
        host = f"localhost:{server.port}" if authority == "Origin" else "other.invalid"
        prefix = f"POST /probe HTTP/1.1\r\nHost: {host}\r\nContent-Length: 8192\r\n".encode()
        if authority == "Origin":
            prefix += b"Origin: https://other.invalid\r\n"
        prefix += b"Padding: "
        header = prefix + b"p" * (16_384 - len(prefix) - 4) + b"\r\n\r\n"
        assert len(header) == 16_384
        request = header + b"x" * 8192
        for fragmented in [False, True]:
            chunks = [1024] * 16 + [128] * 64 if fragmented else None
            status, response, _ = server.raw(request, chunks)
            assert status == expected, (authority, fragmented, status)
            headers, separator, body = response.partition(b"\r\n\r\n")
            length = re.search(rb"\r\nContent-Length: (\d+)\r\n", headers + b"\r\n")
            assert separator and length and len(body) == int(length[1]), (authority, fragmented, "incomplete HTTP response", response)
            assert body == (b"Not found" if authority == "Origin" else b"Invalid input")
            assert server.rows() == before
            server.health()
            results.append({"authority": authority, "fragmented": fragmented, "status": status,
                            "complete response": True})
    return results


def authority_regression(binary: Path, directory: Path) -> dict[str, object]:
    path = directory / "accounts.sqlite"
    assert user_add(binary, path, "alice", secrets.token_urlsafe(24)).returncode == 0
    server = Server(binary, directory, path)
    try:
        results = authority_rejections(server)
        return {"authority rejection responses": results, "cases": {"complete rejected authority responses": len(results), "HTTP requests": server.requests}}
    finally:
        server.stop()


def oversized_bodies(server: Server) -> dict[str, object]:
    """Near-boundary complete submissions retain the strict 413 oracle.

    A separate incomplete, much larger declaration only promises bounded
    release. It cannot force the runtime to buffer an unbounded rejected body.
    """
    before = server.rows()
    ordinary: list[int] = []
    for _ in range(10):
        status, _, body = server.request("POST", "/probe", b"x" * 65_537)
        assert status == 413 and body == b"Resource limit exceeded"
        ordinary.append(status)
        assert server.rows() == before
        server.health()
    results: list[dict[str, object]] = []
    for length in [65_537, 66_560]:
        header = f"POST /probe HTTP/1.1\r\nHost: localhost:{server.port}\r\nContent-Length: {length}\r\n\r\n".encode()
        for fragmented in [False, True]:
            chunks = [len(header)] + [1024] * 65 if fragmented else None
            status, response, elapsed = server.raw(header + b"x" * length, chunks)
            headers, separator, body = response.partition(b"\r\n\r\n")
            declared = re.search(rb"\r\nContent-Length: (\d+)\r\n", headers + b"\r\n")
            assert status == 413 and separator and declared and len(body) == int(declared[1])
            assert body == b"Resource limit exceeded" and elapsed < 7.5
            assert server.rows() == before
            server.health()
            results.append({"declared bytes": length, "fragmented": fragmented,
                            "status": status, "complete response": True})

    # This sender declares substantially more than the bounded discard allowance
    # and supplies only a small prefix without EOF. Early close or a deadline
    # response are both legal. No response delivery assumption applies here.
    header = f"POST /probe HTTP/1.1\r\nHost: localhost:{server.port}\r\nContent-Length: 1000000\r\n\r\n".encode()
    connection = socket.create_connection(("127.0.0.1", server.port), timeout=8)
    connection.settimeout(8)
    started = time.monotonic()
    response = bytearray()
    outcome = "EOF"
    try:
        connection.sendall(header + b"x" * 1024)
        while True:
            part = connection.recv(1024)
            if not part:
                break
            response.extend(part)
    except (ConnectionResetError, BrokenPipeError) as error:
        outcome = type(error).__name__
    finally:
        connection.close()
    elapsed = time.monotonic() - started
    server.requests += 1
    assert elapsed < 7.5, ("incomplete oversized request retained server", elapsed)
    assert server.rows() == before
    server.health()
    match = re.match(rb"HTTP/1\.1 (\d{3}) ", response)
    assert match is None or int(match[1]) in {400, 413, 500}, ("incomplete oversized request was accepted", bytes(response))
    return {"ordinary statuses": ordinary, "near-boundary responses": results,
            "large incomplete release": {"declared bytes": 1_000_000,
                                         "supplied body bytes": 1024,
                                         "elapsed seconds": round(elapsed, 3),
                                         "connection outcome": outcome,
                                         "status if received": int(match[1]) if match else None}}


def oversize_regression(binary: Path, directory: Path) -> dict[str, object]:
    path = directory / "accounts.sqlite"
    assert user_add(binary, path, "alice", secrets.token_urlsafe(24)).returncode == 0
    server = Server(binary, directory, path)
    try:
        results = oversized_bodies(server)
        return {"oversize rejection responses": results,
                "cases": {"ordinary oversized body submissions": 10,
                          "whole and fragmented near-boundary rejections": 4,
                          "large incomplete bounded release": 1,
                          "HTTP requests": server.requests}}
    finally:
        server.stop()


def run(binary: Path, directory: Path, seed: int, wait_window: bool, observe_known: bool) -> dict[str, object]:
    account_path, password, counts = accounts(binary, directory)
    corrupt_cases, corrupt_requests = corrupt_hashes(binary, directory, account_path, password)
    counts["bounded corrupted account hash cases"] = corrupt_cases
    server = Server(binary, directory, account_path)
    generator = random.Random(seed)
    observations: dict[str, object] = {}
    try:
        server.health()
        host = f"Host: localhost:{server.port}\r\n".encode()
        ordinary = b"GET /health HTTP/1.1\r\n" + host + b"\r\n"
        # Each request is sent on a fresh TCP connection. Distinct framing and
        # authority failures must leave the database unchanged and server usable.
        bad = {
            "missing HTTP version": b"GET /health\r\n" + host + b"\r\n",
            "HTTP 1.0": ordinary.replace(b"HTTP/1.1", b"HTTP/1.0"),
            "extra request field": ordinary.replace(b"GET /health", b"GET /health extra"),
            "double request space": ordinary.replace(b"GET /health", b"GET  /health"),
            "missing Host": b"GET /health HTTP/1.1\r\n\r\n",
            "foreign Host": ordinary.replace(host, b"Host: other.invalid\r\n"),
            "duplicate Host": ordinary.replace(host, host + host.lower()),
            "header without colon": ordinary.replace(host, host + b"Broken\r\n"),
            "header name whitespace": ordinary.replace(host, host + b"Bad Key: value\r\n"),
            "header control": ordinary.replace(host, host + b"Test: a\x01b\r\n"),
            "header non ASCII": ordinary.replace(host, host + b"Test: \xc3\xa9\r\n"),
            "header invalid UTF8": ordinary.replace(host, host + b"Test: \xff\r\n"),
            "absolute path": ordinary.replace(b"/health", b"http://localhost/health"),
            "network path": ordinary.replace(b"/health", b"//health"),
            "backslash path": ordinary.replace(b"/health", b"/a\\b"),
            "fragment path": ordinary.replace(b"/health", b"/health#x"),
            "query unsupported": ordinary.replace(b"/health", b"/health?a=b"),
            "encoded path unsupported": ordinary.replace(b"/health", b"/%68ealth"),
            "safe method body": ordinary.replace(b"\r\n\r\n", b"\r\nContent-Length: 1\r\n\r\nx"),
            "negative length": ordinary.replace(host, host + b"Content-Length: -1\r\n"),
            "signed length": ordinary.replace(host, host + b"Content-Length: +0\r\n"),
            "length non decimal": ordinary.replace(host, host + b"Content-Length: 0x0\r\n"),
            "length overflow": ordinary.replace(host, host + b"Content-Length: " + b"9" * 64 + b"\r\n"),
            "duplicate length": ordinary.replace(host, host + b"Content-Length: 0\r\ncontent-length: 0\r\n"),
            "transfer encoding": ordinary.replace(host, host + b"Transfer-Encoding: chunked\r\n"),
            "upgrade unsupported": ordinary.replace(host, host + b"Upgrade: websocket\r\n"),
            "expect unsupported": ordinary.replace(host, host + b"Expect: 100-continue\r\n"),
            "forwarded untrusted": ordinary.replace(host, host + b"Forwarded: for=127.0.0.1\r\n"),
            "x forwarded untrusted": ordinary.replace(host, host + b"X-Forwarded-Proto: http\r\n"),
            "foreign Origin": ordinary.replace(host, host + b"Origin: https://other.invalid\r\n"),
            "unsupported method": ordinary.replace(b"GET", b"OPTIONS", 1),
            "trailing junk": ordinary + b"extra",
            "incomplete headers EOF": ordinary[:-4],
        }
        assert server.rows() == []
        for name, payload in bad.items():
            status, _, _ = server.raw(payload)
            assert 400 <= status < 500 or status == 501, (name, status)
            assert server.rows() == [], name
            server.health()
        counts["malformed transport cases"] = len(bad)
        for _ in range(12):
            pieces = [generator.randint(1, 7) for _ in range(len(ordinary))]
            status, response, _ = server.raw(ordinary, pieces)
            assert status == 200 and response.endswith(b"healthy")
        counts["fragmented valid requests"] = 12
        for number in [-(2**63), 0, 2**63 - 1]:
            status, _, body = server.request("GET", f"/numbers/{number}")
            assert status == 200 and body == str(number).encode()
        for number in [str(2**63), str(-(2**63) - 1), "1.5", "not-an-int"]:
            assert server.request("GET", f"/numbers/{number}")[0] == 400
            server.health()
        counts["typed path boundary cases"] = 7
        assert server.request("GET", "/health", headers={"Host": f"127.0.0.1:{server.port}"})[0] == 200
        # The documented byte caps include the complete header block and body.
        prefix = b"GET /health HTTP/1.1\r\n" + host + b"Padding: "
        exact = prefix + b"a" * (16_384 - len(prefix) - 4) + b"\r\n\r\n"
        assert len(exact) == 16_384
        assert server.raw(exact)[0] == 200
        assert server.raw(exact[:-4] + b"a\r\n\r\n")[0] == 413
        assert server.request("POST", "/probe", b"x" * 65_536)[0] == 200
        assert server.request("POST", "/probe", b"x" * 65_537)[0] == 413
        counts["exact transport limit cases"] = 4
        server.health()
        observations["oversize rejection responses"] = oversized_bodies(server)
        counts["ordinary oversized body submissions"] = 10
        counts["whole and fragmented near-boundary rejections"] = 4
        counts["large incomplete bounded release"] = 1
        authority_results = authority_rejections(server)
        observations["authority rejection responses"] = authority_results
        counts["complete rejected authority responses"] = len(authority_results)
        status, _, elapsed = server.raw(ordinary[:-4], half_close=False, hold=True)
        assert status in {400, 413, 500} and 4.5 <= elapsed <= 7.5, (status, elapsed)
        assert server.rows() == []
        server.health()
        observations["incomplete request deadline"] = {"status": status, "elapsed seconds": round(elapsed, 3)}

        status, headers, page = server.request("GET", "/_diamond/login")
        assert status == 200
        previous, old_csrf = cookie(headers), csrf(page)
        status, other_headers, other_page = server.request("GET", "/_diamond/login")
        assert status == 200
        other_cookie, other_csrf = cookie(other_headers), csrf(other_page)
        login = {"username": "alice", "password": password, "_diamond_csrf": old_csrf}
        form_headers = {"Cookie": previous, "Content-Type": "application/x-www-form-urlencoded"}
        for invalid in [dict(login, _diamond_csrf=other_csrf), dict(login, _diamond_csrf="0" * 64)]:
            assert server.request("POST", "/_diamond/login", form(invalid), form_headers)[0] == 404
        status, headers, _ = server.request("POST", "/_diamond/login", form(login), form_headers)
        assert status == 303
        current = cookie(headers)
        assert current != previous and server.request("GET", "/", headers={"Cookie": previous})[0] == 401
        status, _, page = server.request("GET", "/", headers={"Cookie": current})
        assert status == 200
        token = csrf(page)
        assert token != old_csrf and token != other_csrf
        auth_headers = {"Cookie": current, "Content-Type": "application/x-www-form-urlencoded", "X-Diamond-CSRF": token}
        base = {"text": "ordinary", "number": "0", "enabled": "false", "reference": "alice", "optional": ""}
        positive = [dict(base, text="雪é💎<>&'\"", number=str(-(2**63)), enabled="true", optional="-17"),
                    dict(base, text="é", number=str(2**63 - 1), reference="x" * 128),
                    {key: value for key, value in dict(base, text="+").items() if key != "optional"}]
        for values in positive:
            before = server.rows()
            payload = form(values)
            request = (b"POST /entries HTTP/1.1\r\n" + host +
                       f"Cookie: {current}\r\nContent-Type: application/x-www-form-urlencoded\r\nX-Diamond-CSRF: {token}\r\nContent-Length: {len(payload)}\r\n\r\n".encode() + payload)
            status, _, _ = server.raw(request, [1] * 20 + [3] * 40)
            assert status == 303
            expected = ("alice", values["text"], int(values["number"]), int(values["enabled"] == "true"), values["reference"], int(values["optional"]) if values.get("optional") else None)
            after = server.rows()
            assert len(after) == len(before) + 1 and sorted(after) == sorted(before + [expected])
            server.health()
        counts["typed fragmented form positive cases"] = len(positive)

        invalid_forms: dict[str, bytes] = {
            "missing required field": form({key: value for key, value in base.items() if key != "text"}),
            "empty bounded text": form(dict(base, text="")),
            "unicode length nine": form(dict(base, text="雪" * 9)),
            "integer overflow positive": form(dict(base, number=str(2**63))),
            "integer overflow negative": form(dict(base, number=str(-(2**63) - 1))),
            "integer fraction": form(dict(base, number="1.5")),
            "boolean numeric": form(dict(base, enabled="1")),
            "boolean mixed case": form(dict(base, enabled="True")),
            "invalid Id": form(dict(base, reference="invalid/name")),
            "unicode Id": form(dict(base, reference="雪")),
            "overlong Id": form(dict(base, reference="x" * 129)),
            "invalid optional": form(dict(base, optional="null")),
            "forged owner": form(dict(base, owner="bob")),
            "forged id": form(dict(base, id="forged")),
            "extra field": form(dict(base, unknown="extra")),
            "duplicate decoded field": form(base) + b"&%74ext=second",
            "field no equals": form(base) + b"&missing",
            "empty pair": form(base) + b"&",
            "incomplete percent": form(base) + b"&text=%",
            "invalid percent": form(base) + b"&text=%GG",
            "invalid UTF8 bytes": b"text=\xff&number=0&enabled=true&reference=alice",
            "invalid UTF8 escape": form(base).replace(b"ordinary", b"%FF"),
            "overlong UTF8 escape": form(base).replace(b"ordinary", b"%C0%AF"),
            "surrogate UTF8 escape": form(base).replace(b"ordinary", b"%ED%A0%80"),
            "duplicate CSRF": form(base) + f"&_diamond_csrf={token}&_diamond_csrf={token}".encode(),
        }
        retained = server.rows()
        invalid_id_statuses: dict[str, int] = {}
        for name, payload in invalid_forms.items():
            status = server.request("POST", "/entries", payload, auth_headers)[0]
            # Baseline-only observation can continue after the preserved known
            # failure. The ordinary/final run retains the strict typed-input
            # oracle: invalid supported values must yield a validation error.
            if name in {"invalid Id", "unicode Id", "overlong Id"}:
                assert status == 400 or (observe_known and status == 501), (name, status)
                invalid_id_statuses[name] = status
            else:
                assert status in {400, 404}, (name, status)
            assert server.rows() == retained, name
            server.health()
        observations["invalid Id form statuses"] = invalid_id_statuses
        counts["rejected typed form cases"] = len(invalid_forms)
        matched_id_results: list[dict[str, object]] = []
        for fields in [{"reference": "invalid/name", "optional": ""},
                       {"reference": "alice", "optional": "invalid/name"}]:
            status, _, body = server.request("POST", "/id-validation", form(fields), auth_headers)
            valid = status == 400 and b"Typed validation failed" in body
            assert valid or (observe_known and status == 501), (fields, status, body)
            matched_id_results.append({"field": "reference" if fields["reference"] != "alice" else "optional",
                                       "status": status, "application validation page": b"Typed validation failed" in body})
            assert server.rows() == retained
            server.health()
        assert server.request("POST", "/id-validation", form({"reference": "alice", "optional": ""}), auth_headers)[0] == 200
        counts["Id and Option Id validation feedback journeys"] = 3
        observations["matched Id validation results"] = matched_id_results
        for content_type in ["text/plain", "application/json", "multipart/form-data; boundary=x", ""]:
            assert server.request("POST", "/entries", form(base), dict(auth_headers, **{"Content-Type": content_type}))[0] == 400
            assert server.rows() == retained
            server.health()
        counts["unsupported form content types"] = 4
        assert server.request("POST", "/entries", form(base), dict(auth_headers, **{"Content-Type": "Application/X-WWW-Form-Urlencoded; charset=UTF-8"}))[0] == 303
        retained = server.rows()
        counts["typed fragmented form positive cases"] += 1
        for headers in [dict(auth_headers, **{"X-Diamond-CSRF": old_csrf}),
                        dict(auth_headers, **{"X-Diamond-CSRF": other_csrf}),
                        dict(auth_headers, Cookie=current + "; " + other_cookie),
                        dict(auth_headers, Origin="https://other.invalid")]:
            assert server.request("POST", "/entries", form(base), headers)[0] in {400, 404}
            assert server.rows() == retained
            server.health()
        assert server.request("POST", "/entries", form(dict(base, _diamond_csrf=other_csrf)), auth_headers)[0] == 404
        assert server.rows() == retained
        counts["session bound rejected mutations"] = 5

        payload = form(base)
        request_header = (b"POST /entries HTTP/1.1\r\n" + host +
                          f"Cookie: {current}\r\nContent-Type: application/x-www-form-urlencoded\r\nX-Diamond-CSRF: {token}\r\nContent-Length: {len(payload)}\r\n\r\n".encode())
        for body_prefix in [b"", payload[:1], payload[:-1]]:
            status, _, _ = server.raw(request_header + body_prefix)
            assert status == 400 and server.rows() == retained
            server.health()
        counts["canceled partial forms"] = 3
        # A byte stream may reject the first request's trailing bytes, or close
        # after accepting exactly its first fully framed request. It must never
        # execute the second mutation. Neither outcome proves an abort to client.
        aligned_padding = b"Padding: " + b"x" * (2048 - len(request_header) - len(b"Padding: \r\n")) + b"\r\n"
        pipeline_outcomes: list[dict[str, int]] = []
        for padding in [b"", aligned_padding]:
            before = server.rows()
            one = request_header.replace(host, host + padding) + payload
            status, _, _ = server.raw(one + one)
            after = server.rows()
            assert status in {303, 400} and len(after) - len(before) in {0, 1}
            expected = ("alice", "ordinary", 0, 0, "alice", None)
            assert sorted(after) == sorted(before + ([expected] if len(after) > len(before) else []))
            pipeline_outcomes.append({"status": status, "committed rows": len(after) - len(before)})
            server.health()
        counts["pipelined mutation streams"] = 2
        observations["pipelined mutation outcomes"] = pipeline_outcomes
        retained = server.rows()

        # Rotation while signed in revokes the old cookie and binds fresh CSRF.
        relogin = form({"username": "alice", "password": password, "_diamond_csrf": token})
        status, headers, _ = server.request("POST", "/_diamond/login", relogin, auth_headers)
        assert status == 303
        rotated = cookie(headers)
        assert rotated != current and server.request("GET", "/", headers={"Cookie": current})[0] == 401
        status, _, page = server.request("GET", "/", headers={"Cookie": rotated})
        assert status == 200
        rotated_csrf = csrf(page)
        assert rotated_csrf != token
        headers = {"Cookie": rotated, "X-Diamond-CSRF": rotated_csrf}
        assert server.request("POST", "/_diamond/logout", headers=dict(headers, **{"X-Diamond-CSRF": token}))[0] == 404
        status, headers, _ = server.request("POST", "/_diamond/logout", headers=headers)
        assert status == 303 and "Max-Age=0" in headers["Set-Cookie"]
        assert server.request("GET", "/", headers={"Cookie": rotated})[0] == 401
        assert server.request("POST", "/entries", form(base), dict(auth_headers, Cookie=rotated))[0] == 401
        assert server.rows() == retained
        counts["rotation revocation logout journeys"] = 1

        # A fresh server provides a clean one-minute attempt window. Individual
        # attempts use an unknown account and actual bounded dummy Argon2 path.
        server.stop()
        prior_requests = server.requests
        server = Server(binary, directory, account_path)
        _, headers, page = server.request("GET", "/_diamond/login")
        rate_cookie, rate_csrf = cookie(headers), csrf(page)
        rate_headers = {"Cookie": rate_cookie, "Content-Type": "application/x-www-form-urlencoded"}
        rate_body = form({"username": "nonexistent", "password": "Deliberately incorrect fixture password", "_diamond_csrf": rate_csrf})
        window_started = time.monotonic()
        for attempt in range(64):
            assert server.request("POST", "/_diamond/login", rate_body, rate_headers)[0] == 401, attempt
        elapsed = time.monotonic() - window_started
        assert elapsed < 59, f"machine too slow to reach one-minute rate boundary: {elapsed}"
        assert server.request("POST", "/_diamond/login", rate_body, rate_headers)[0] == 429
        server.health()
        assert server.rows() == retained
        counts["rate limit individual attempts"] = 65
        observations["rate boundary elapsed seconds"] = round(elapsed, 3)
        if wait_window:
            remaining = max(0.0, 61.0 - (time.monotonic() - window_started))
            print(f"Waiting {remaining:.1f}s for the documented login window, disposable loopback server only", file=sys.stderr, flush=True)
            # No single wait exceeds sixty seconds, and the parent can continue
            # its independent work while this child waits for a real deadline.
            while remaining > 0:
                time.sleep(min(remaining, 10))
                remaining = max(0.0, 61.0 - (time.monotonic() - window_started))
            assert server.request("POST", "/_diamond/login", rate_body, rate_headers)[0] == 401
            counts["actual rate window recovery"] = 1
        else:
            observations["actual rate window recovery"] = "not requested; replay with --wait-window"
        with sqlite3.connect(server.database) as connection:
            assert connection.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        counts["HTTP requests"] = prior_requests + server.requests + corrupt_requests
        observations["cases"] = counts
        return observations
    finally:
        server.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, default=ROOT / "target/debug/diamond")
    parser.add_argument("--seed", type=lambda value: int(value, 0), default=SEED)
    parser.add_argument("--wait-window", action="store_true")
    parser.add_argument("--id-regression-only", action="store_true")
    parser.add_argument("--authority-regression-only", action="store_true")
    parser.add_argument("--oversize-regression-only", action="store_true")
    parser.add_argument("--observe-known-id-classification", action="store_true",
                        help="baseline evidence only; report known unsupported-Id failure without stopping")
    args = parser.parse_args()
    binary = args.binary.resolve()
    directory = Path(tempfile.mkdtemp(prefix="diamond-transport-exploration-"))
    try:
        # Freeze precisely the tested artifact: another independent build may
        # replace target/debug/diamond while this bounded campaign is running.
        tested = directory / "diamond"
        shutil.copyfile(binary, tested)
        tested.chmod(0o700)
        fingerprint = hashlib.sha256(tested.read_bytes()).hexdigest()
        if args.oversize_regression_only:
            result = oversize_regression(tested, directory)
        elif args.authority_regression_only:
            result = authority_regression(tested, directory)
        elif args.id_regression_only:
            result = id_regression(tested, directory, args.observe_known_id_classification)
        else:
            result = run(tested, directory, args.seed, args.wait_window, args.observe_known_id_classification)
    except Exception:
        print(f"Isolated failure state retained at {directory}", file=sys.stderr)
        raise
    else:
        shutil.rmtree(directory)
        known = any(value == 501 for value in result.get("invalid Id form statuses", {}).values())
        print(json.dumps({"status": "KNOWN_FAILURE_OBSERVED" if known else "PASS", "seed": args.seed, "generator": 1,
                          "binary SHA256": fingerprint,
                          "observations": result}, indent=2))


if __name__ == "__main__":
    main()
