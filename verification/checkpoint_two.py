#!/usr/bin/env python3
"""Exercise the actual authenticated application, CLI, SQLite, and restart path.

The HTTP driver transports local Secure cookies explicitly; a separate actual
browser journey verifies browser cookie acceptance and real form submission.
Expectations come from checkpoint_two.md, not from production implementations.
"""

from __future__ import annotations

from html.parser import HTMLParser
from http.cookies import SimpleCookie
import http.client
import json
from pathlib import Path
import random
import re
import secrets
import select
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import urllib.parse

ROOT = Path(__file__).resolve().parents[1]
BINARY = ROOT / "target/debug/diamond"
SEED = 0xD1A02


class Page(HTMLParser):
    def __init__(self, body: bytes) -> None:
        super().__init__(convert_charrefs=True)
        self.csrf: list[str] = []
        self.tags: list[str] = []
        self.text: list[str] = []
        self.feed(body.decode())

    def handle_starttag(self, tag: str, attributes: list[tuple[str, str | None]]) -> None:
        self.tags.append(tag)
        values = dict(attributes)
        if tag == "input" and values.get("name") == "_diamond_csrf":
            self.csrf.append(values.get("value") or "")

    def handle_data(self, value: str) -> None:
        self.text.append(value)


class Server:
    def __init__(self, directory: Path, source: str = "examples/message_board.dia") -> None:
        self.directory = directory
        self.source = source
        self.process: subprocess.Popen[str] | None = None
        self.port = 0
        self.requests = 0

    def start(self) -> None:
        self.process = subprocess.Popen(
            [str(BINARY), "run", self.source, "--listen", "127.0.0.1:0", "--database", str(self.directory / "board.sqlite"), "--auth", str(self.directory / "accounts.sqlite")],
            cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        assert self.process.stderr is not None
        ready, _, _ = select.select([self.process.stderr], [], [], 8)
        assert ready, "server startup timed out"
        line = self.process.stderr.readline()
        match = re.search(r"http://localhost:(\d+)", line)
        assert match, f"server did not become ready: {line}"
        self.port = int(match[1])

    def stop(self) -> None:
        if self.process is None:
            return
        self.process.terminate()
        try:
            stdout, stderr = self.process.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            self.process.kill()
            stdout, stderr = self.process.communicate(timeout=3)
        assert "panicked at" not in stderr
        assert not stdout, "application unexpectedly wrote HTTP data or credentials to stdout"
        self.process = None

    def raw(self, method: str, path: str, fields: dict[str, str] | None = None, headers: dict[str, str] | None = None, body: bytes | None = None) -> tuple[int, dict[str, str], bytes]:
        outgoing = {"Host": f"localhost:{self.port}", **(headers or {})}
        if method not in {"GET", "HEAD"}:
            outgoing.setdefault("Origin", f"http://localhost:{self.port}")
        if fields is not None:
            body = urllib.parse.urlencode(fields).encode()
            outgoing.setdefault("Content-Type", "application/x-www-form-urlencoded")
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=8)
        try:
            connection.request(method, path, body=body, headers=outgoing)
            response = connection.getresponse()
            result = response.status, dict(response.getheaders()), response.read()
            self.requests += 1
            return result
        finally:
            connection.close()


class Client:
    def __init__(self, server: Server) -> None:
        self.server = server
        self.cookie: str | None = None
        self.csrf = ""

    def request(self, method: str, path: str, fields: dict[str, str] | None = None, headers: dict[str, str] | None = None) -> tuple[int, dict[str, str], bytes]:
        outgoing = dict(headers or {})
        if self.cookie is not None:
            outgoing.setdefault("Cookie", self.cookie)
        result = self.server.raw(method, path, fields, outgoing)
        encoded = result[1].get("Set-Cookie")
        if encoded:
            cookies = SimpleCookie(encoded)
            session = cookies["diamond_session"]
            assert session["secure"] and session["httponly"] and session["samesite"] == "Strict"
            assert session["path"] == "/" and not session["domain"]
            self.cookie = "diamond_session=" + session.value if session.value else None
        return result

    def login(self, user: str, password: str) -> None:
        status, _, body = self.request("GET", "/_diamond/login")
        assert status == 200
        csrf = Page(body).csrf
        assert len(csrf) == 1 and re.fullmatch("[0-9a-f]{64}", csrf[0])
        before = self.cookie
        status, headers, _ = self.request("POST", "/_diamond/login", {"username": user, "password": password, "_diamond_csrf": csrf[0]})
        assert status == 303 and headers["Location"] == "/" and self.cookie != before
        self.home()

    def home(self) -> None:
        status, _, body = self.request("GET", "/")
        page = Page(body)
        assert status == 200 and "Message board" in "".join(page.text)
        assert len(page.csrf) == 2 and len(set(page.csrf)) == 1
        self.csrf = page.csrf[0]

    def post(self, title: str, body: str) -> str:
        status, headers, _ = self.request("POST", "/posts", {"title": title, "body": body, "_diamond_csrf": self.csrf})
        assert status == 303
        path = headers["Location"]
        assert re.fullmatch("/posts/[0-9a-f]{64}", path)
        status, _, rendered = self.request("GET", path)
        page = Page(rendered)
        assert status == 200 and title in "".join(page.text) and body in "".join(page.text)
        assert "script" not in page.tags
        return path


def provision(directory: Path, user: str, password: str, success: bool = True) -> None:
    result = subprocess.run([str(BINARY), "user-add", "--auth", str(directory / "accounts.sqlite"), "--user", user, "--password-stdin"], cwd=ROOT, input=password + "\n", text=True, capture_output=True, timeout=10)
    assert (result.returncode == 0) == success, result.stderr
    assert password not in result.stdout + result.stderr


def rows(directory: Path) -> list[tuple[str, str, str, str, int]]:
    with sqlite3.connect(directory / "board.sqlite") as connection:
        return connection.execute('SELECT id, author, title, body, published FROM "Posts" ORDER BY id').fetchall()


def run(directory: Path) -> dict[str, int]:
    alice_password, bob_password = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    provision(directory, "alice", alice_password)
    provision(directory, "bob", bob_password)
    with sqlite3.connect(directory / "accounts.sqlite") as connection:
        before = connection.execute("SELECT username, password_hash FROM diamond_auth_users ORDER BY username").fetchall()
    assert len(before) == 2 and all(value.startswith("$argon2id$v=19$m=19456,t=2,p=1$") for _, value in before)
    assert len({value for _, value in before}) == 2
    assert (directory / "accounts.sqlite").stat().st_mode & 0o777 == 0o600
    provision(directory, "alice", "Replacement must not be accepted", success=False)
    with sqlite3.connect(directory / "accounts.sqlite") as connection:
        assert connection.execute("SELECT username, password_hash FROM diamond_auth_users ORDER BY username").fetchall() == before

    server = Server(directory)
    observations = {"provisioning state checks": 3, "generated post journeys": 0, "rejected mutations": 0, "restart journeys": 0}
    try:
        server.start()
        alice, bob = Client(server), Client(server)
        assert alice.request("GET", "/")[0] == 401
        assert alice.request("POST", "/posts", {"title": "unauthorized", "body": "must not be saved"})[0] == 401
        assert rows(directory) == []
        status, _, login_body = alice.request("GET", "/_diamond/login")
        assert status == 200
        login_csrf = Page(login_body).csrf[0]
        prelogin_cookie = alice.cookie
        assert alice.request("POST", "/_diamond/login", {"username": "alice", "password": alice_password})[0] == 404
        a = alice.request("POST", "/_diamond/login", {"username": "alice", "password": "Wrong password for this fixture", "_diamond_csrf": login_csrf})
        b = alice.request("POST", "/_diamond/login", {"username": "nonexistent", "password": "Wrong password for this fixture", "_diamond_csrf": login_csrf})
        assert a[0] == b[0] == 401 and a[2] == b[2]
        assert alice.cookie == prelogin_cookie
        assert alice.request("POST", "/_diamond/login", {"username": "alice", "password": alice_password, "_diamond_csrf": login_csrf}, {"Origin": "https://attacker.example"})[0] == 404
        alice.login("alice", alice_password)
        assert server.raw("GET", "/", headers={"Cookie": prelogin_cookie or ""})[0] == 401
        bob.login("bob", bob_password)

        title = "Alice's <post> & café"
        body = 'First line\n<script>alert("this is text")</script> 雪'
        path = alice.post(title, body)
        assert rows(directory) == [(path.rsplit("/", 1)[1], "alice", title, body, 0)]
        assert bob.request("GET", path)[0] == 404
        assert server.raw("GET", path)[0] == 404
        assert bob.request("GET", path)[2] == bob.request("GET", "/posts/missing")[2]
        before_rows = rows(directory)
        invalid_fields = [
            {"title": "missing csrf", "body": "invalid"},
            {"title": "wrong csrf", "body": "invalid", "_diamond_csrf": "0" * 64},
            {"title": "cross-session csrf", "body": "invalid", "_diamond_csrf": bob.csrf},
            {"title": "prelogin csrf", "body": "invalid", "_diamond_csrf": login_csrf},
            {"title": "forged author", "body": "invalid", "author": "bob", "_diamond_csrf": alice.csrf},
            {"title": "", "body": "invalid", "_diamond_csrf": alice.csrf},
            {"title": "x" * 121, "body": "invalid", "_diamond_csrf": alice.csrf},
            {"title": "overlong body", "body": "x" * 10001, "_diamond_csrf": alice.csrf},
        ]
        for fields in invalid_fields:
            assert alice.request("POST", "/posts", fields)[0] in {400, 404}
            assert rows(directory) == before_rows
            observations["rejected mutations"] += 1
        assert alice.request("POST", "/posts", {"title": "hostile origin", "body": "invalid", "_diamond_csrf": alice.csrf}, {"Origin": "https://attacker.example"})[0] == 404
        assert alice.request("POST", "/posts", {"title": "conflicting tokens", "body": "invalid", "_diamond_csrf": alice.csrf}, {"X-Diamond-CSRF": bob.csrf})[0] == 404
        assert alice.request("POST", "/posts", {"title": "duplicate cookie", "body": "invalid", "_diamond_csrf": alice.csrf}, {"Cookie": (alice.cookie or "") + "; " + (bob.cookie or "")})[0] == 400
        assert bob.request("DELETE", path, headers={"X-Diamond-CSRF": bob.csrf})[0] == 404
        assert rows(directory) == before_rows
        observations["rejected mutations"] += 4

        generator = random.Random(SEED)
        generated_paths = []
        for index, text in enumerate(["Robert'); DROP TABLE Posts; --", "雪 café & <b>text</b>", "x" * 120]):
            post_body = f"Generated case {index}: {generator.randrange(1_000_000)}\n{text}"
            generated = alice.post(text, post_body)
            generated_paths.append(generated)
            assert any(row[0] == generated.rsplit("/", 1)[1] and row[1:] == ("alice", text, post_body, 0) for row in rows(directory))
            observations["generated post journeys"] += 1
        count = len(rows(directory))
        assert alice.request("DELETE", generated_paths[-1], headers={"X-Diamond-CSRF": alice.csrf})[0] == 303
        assert len(rows(directory)) == count - 1
        assert alice.request("GET", "/_diamond/logout")[0] == 405
        alice.home()
        assert alice.request("POST", "/_diamond/logout", {"_diamond_csrf": bob.csrf})[0] == 404
        alice.home()
        retired_cookie, retired_csrf = alice.cookie, alice.csrf
        assert alice.request("POST", "/_diamond/logout", {"_diamond_csrf": alice.csrf})[0] == 303
        assert alice.cookie is None
        assert server.raw("GET", "/", headers={"Cookie": retired_cookie or ""})[0] == 401
        assert server.raw("POST", "/posts", {"title": "revoked", "body": "invalid", "_diamond_csrf": retired_csrf}, {"Cookie": retired_cookie or ""})[0] == 401
        assert len(rows(directory)) == count - 1

        stale_bob = bob.cookie
        persisted = rows(directory)
        server.stop()
        server.start()
        assert server.raw("GET", "/", headers={"Cookie": stale_bob or ""})[0] == 401
        assert server.raw("GET", path, headers={"Cookie": stale_bob or ""})[0] == 404
        alice.login("alice", alice_password)
        status, _, rendered = alice.request("GET", path)
        assert status == 200 and title in "".join(Page(rendered).text)
        assert rows(directory) == persisted
        bob.login("bob", bob_password)
        assert bob.request("GET", path)[0] == 404
        observations["restart journeys"] += 1
        observations["HTTP requests"] = server.requests
        return observations
    finally:
        server.stop()


def main() -> None:
    directory = Path(tempfile.mkdtemp(prefix="diamond-checkpoint-two-"))
    try:
        observations = run(directory)
    except Exception:
        print(f"Isolated failure state retained at {directory}", file=sys.stderr)
        raise
    else:
        shutil.rmtree(directory)
        print(json.dumps({"status": "PASS", "seed": SEED, "observations": observations}, indent=2))


if __name__ == "__main__":
    main()
