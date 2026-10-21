#!/usr/bin/env python3
"""Replay checkpoint-one behavior through the built CLI, HTTP, and real SQLite.

Expectations come from the documented initial subset and explicit state changes.
No production parser, evaluator, policy function, or SQL builder is imported.
The standard-library generator uses seed 0xD1A0D and bounded cases.
"""

from __future__ import annotations

from html.parser import HTMLParser
import http.client
import json
from pathlib import Path
import random
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import urllib.parse

ROOT = Path(__file__).resolve().parents[1]
BINARY = ROOT / "target/debug/diamond"
SEED = 0xD1A0D


class Paragraph(HTMLParser):
    """Observe DOM text and element structure, allowing equivalent entity spellings."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: list[tuple[str, list[tuple[str, str | None]]]] = []
        self.text: list[str] = []
        self.inside = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append((tag, attrs))
        self.inside = tag == "p"

    def handle_endtag(self, tag: str) -> None:
        if tag == "p":
            self.inside = False

    def handle_data(self, data: str) -> None:
        if self.inside:
            self.text.append(data)


class Campaign:
    def __init__(self) -> None:
        self.directory = Path(tempfile.mkdtemp(prefix="diamond-checkpoint-one-"))
        self.counts: dict[str, int] = {}

    def record(self, group: str) -> None:
        self.counts[group] = self.counts.get(group, 0) + 1

    def cli(self, arguments: list[str], success: bool = True) -> subprocess.CompletedProcess[str]:
        result = subprocess.run([str(BINARY), *arguments], cwd=ROOT, text=True, capture_output=True, timeout=8)
        if (result.returncode == 0) != success:
            raise AssertionError(f"{arguments}: exit {result.returncode}\n{result.stdout}\n{result.stderr}")
        if result.returncode < 0 or "panicked at" in result.stderr:
            raise AssertionError(f"program crashed: {arguments}\n{result.stderr}")
        return result

    def source(self, name: str, source: str) -> str:
        path = self.directory / f"{name}.dia"
        path.write_text(source)
        return str(path)

    def evaluate(self, name: str, source: str, expected: str) -> None:
        result = self.cli(["run", self.source(name, source)])
        assert result.stdout == expected + "\n", (name, result.stdout, expected)
        self.record("CLI evaluations")

    def reject(self, name: str, source: str, diagnostic: str | None = None) -> None:
        result = self.cli(["check", self.source(name, source)], success=False)
        if diagnostic:
            assert diagnostic in result.stderr, (name, result.stderr, diagnostic)
        self.record("compile rejections")

    def language(self) -> None:
        for path in sorted((ROOT / "examples").glob("*.dia")):
            self.cli(["check", str(path)])
            self.record("example checks")
        self.evaluate("greeting", (ROOT / "examples/hello.dia").read_text(), "Hello, Diamond!")
        self.evaluate("short-circuit", "fn main() -> Bool:\n  false and (7 / 0 == 1)\n", "false")
        self.evaluate("result", "fn answer() -> Result<Int, DataError>:\n  Ok(37)\n\nfn main() -> Result<Int, DataError>:\n  let value = answer()?\n  Ok(value + 5)\n", "42")
        self.evaluate("record-default", 'type Label:\n  text: Text\n  visible: Bool = true\n\nfn main() -> Bool:\n  let label = Label(text: "hello")\n  label.visible\n', "true")
        for index in range(32):
            generator = random.Random(SEED + index)
            a, b, c = [generator.randint(-1000, 1000) for _ in range(3)]
            source = f"fn combine(a: Int, b: Int) -> Int:\n  a * 3 - b\n\nfn main() -> Int:\n  combine({a}, {b}) + {c}\n"
            self.evaluate(f"arithmetic-{index}", source, str(a * 3 - b + c))
        for label, source, expected in [
            ("wrong-return", 'fn main() -> Int:\n  "text"\n', "D110"),
            ("no-coercion", 'fn main() -> Text:\n  "value=" + 7\n', "D110"),
            ("missing-value", "fn main() -> Int:\n  unknown\n", "D112"),
            ("tabs", "fn main() -> Int:\n\t7\n", "D001"),
            ("bad-indent", "fn main() -> Int:\n    let a = 3\n  a\n", "D002"),
            ("string", 'fn main() -> Text:\n  "unfinished\n', "D005"),
            ("integer-overflow-literal", "fn main() -> Int:\n  9223372036854775808\n", "D004"),
            ("generic-enum", "enum State<T>:\n  Active\n", "D010"),
            ("mutable", "fn main() -> Int:\n  var a = 1\n  a\n", "D900"),
            ("sql-string", 'fn main() -> Text:\n  posts.execute("DROP TABLE Posts")\n', "D900"),
            ("script-tag", 'public route GET "/":\n  html:\n    <script>alert(1)</script>\n', "D900"),
            ("unsafe-url", 'public route GET "/":\n  html:\n    <a href="javascript:alert(1)">bad</a>\n', None),
            ("dynamic-url", 'public route GET "/":\n  let url = "javascript:alert(1)"\n  html:\n    <a href={url}>bad</a>\n', "D121"),
            ("private-without-provider", 'route GET "/private":\n  "private"\n', "D401"),
            ("route-overlap", 'type Id = Id<"item">\npublic route GET "/{id:Id}":\n  "one"\npublic route GET "/fixed":\n  "two"\n', "D402"),
            ("alias-cycle", "type First = Second\ntype Second = First\n", "cyclic"),
            ("deep-expression", "fn main() -> Int:\n  " + "(" * 1000 + "1" + ")" * 1000 + "\n", "limit"),
            ("large-expression", "fn main() -> Int:\n  " + " + ".join(["1"] * 2000) + "\n", "limit"),
        ]:
            self.reject(label, source, expected)
        for label, expression in [("overflow", "9223372036854775807 + 1"), ("divide-zero", "7 / 0")]:
            result = self.cli(["run", self.source(label, f"fn main() -> Int:\n  {expression}\n")], success=False)
            assert "integer overflow or division by zero" in result.stderr
            self.record("runtime rejections")

    def database(self) -> None:
        source = (ROOT / "examples/storage.dia").read_text()
        prefix = source.split("fn main()", 1)[0]
        path = self.directory / "notes.sqlite"
        board = self.directory / "board.sqlite"
        self.cli(["db-init", "examples/message_board.dia", "--database", str(board)])
        with sqlite3.connect(board) as connection:
            assert {row[1] for row in connection.execute('PRAGMA table_info("Posts")')} == {"id", "author", "title", "body", "published"}
        self.record("database state checks")
        self.cli(["run", "examples/storage.dia"], success=False)
        identifiers = []
        for _ in range(2):
            identifier = self.cli(["run", "examples/storage.dia", "--database", str(path)]).stdout.strip()
            assert re.fullmatch(r"[0-9a-f]{64}", identifier), identifier
            identifiers.append(identifier)
        assert identifiers[0] != identifiers[1]
        with sqlite3.connect(path) as connection:
            rows = connection.execute('SELECT id, title, visible FROM "Notes" ORDER BY id').fetchall()
            assert len(rows) == 2 and all(row[1:] == ("A structurally bound value", 1) for row in rows), rows
        self.record("database state checks")

        payload = "Robert'); DROP TABLE Notes; -- <script>alert(\"x\")</script> & café 雪"
        source = prefix + 'fn main() -> Result<NoteId, DataError>:\n  let saved = notes.create(NewNote(title: ' + json.dumps(payload, ensure_ascii=False) + '))?\n  Ok(saved.id)\n'
        identifier = self.cli(["run", self.source("bound-value", source), "--database", str(path)]).stdout.strip()
        with sqlite3.connect(path) as connection:
            assert connection.execute('SELECT title FROM "Notes" WHERE id=?', [identifier]).fetchone() == (payload,)
            assert connection.execute('SELECT count(*) FROM "Notes"').fetchone() == (3,)
        self.record("database state checks")

        read = prefix + f'fn main() -> Result<Text, DataError>:\n  let note = notes.get(NoteId("{identifier}"))?\n  Ok(note.title)\n'
        assert self.cli(["run", self.source("fresh-read", read), "--database", str(path)]).stdout == payload + "\n"
        self.record("database state checks")
        update = prefix + f'fn main() -> Result<Text, DataError>:\n  let note = notes.update(NoteId("{identifier}"), NewNote(title: "Updated title"))?\n  Ok(note.title)\n'
        assert self.cli(["run", self.source("update", update), "--database", str(path)]).stdout == "Updated title\n"
        with sqlite3.connect(path) as connection:
            assert connection.execute('SELECT title FROM "Notes" WHERE id=?', [identifier]).fetchone() == ("Updated title",)
        self.record("database state checks")

        rollback = prefix + 'fn main() -> Result<NoteId, DataError>:\n  transaction db:\n    let saved = notes.create(NewNote(title: "Must roll back"))?\n    let stop = Err("abort this transaction")?\n    Ok(saved.id)\n'
        self.cli(["run", self.source("rollback", rollback), "--database", str(path)], success=False)
        with sqlite3.connect(path) as connection:
            assert connection.execute('SELECT count(*) FROM "Notes"').fetchone() == (3,)
            assert connection.execute('SELECT count(*) FROM "Notes" WHERE title=?', ["Must roll back"]).fetchone() == (0,)
        self.record("database state checks")

        hidden_id = identifiers[0]
        with sqlite3.connect(path) as connection:
            connection.execute('UPDATE "Notes" SET visible=0 WHERE id=?', [hidden_id])
        missing = prefix + 'fn main() -> Result<Text, DataError>:\n  let note = notes.get(NoteId("missing"))?\n  Ok(note.title)\n'
        hidden = missing.replace('NoteId("missing")', f'NoteId("{hidden_id}")')
        a = self.cli(["run", self.source("opaque-read", missing), "--database", str(path)], success=False)
        b = self.cli(["run", self.source("opaque-read", hidden), "--database", str(path)], success=False)
        assert a.stderr == b.stderr and "access denied or resource not found" in a.stderr
        self.record("database state checks")

        # The read policy must also authorize a returned mutation result: a denied
        # return rolls back the update rather than committing and leaking a record.
        hidden_update = update.replace(identifier, hidden_id)
        self.cli(["run", self.source("denied-update", hidden_update), "--database", str(path)], success=False)
        with sqlite3.connect(path) as connection:
            assert connection.execute('SELECT title, visible FROM "Notes" WHERE id=?', [hidden_id]).fetchone() == ("A structurally bound value", 0)
        self.record("database state checks")

        delete = prefix + f'fn main() -> Result<Unit, DataError>:\n  notes.delete(NoteId("{identifier}"))\n'
        self.cli(["run", self.source("delete", delete), "--database", str(path)])
        with sqlite3.connect(path) as connection:
            assert connection.execute('SELECT count(*) FROM "Notes" WHERE id=?', [identifier]).fetchone() == (0,)
            assert connection.execute('SELECT count(*) FROM "Notes"').fetchone() == (2,)
        self.record("database state checks")

        self.reject("missing-policy", prefix.replace('  read(note, by principal):\n    note.visible\n', '') + "fn main()" + missing.split('fn main()', 1)[1], "D204")
        self.reject("authority-budget", prefix.replace('  provides DbRead<Notes>, DbWrite<Notes>\n', '') + 'fn main() -> Result<Note, DataError>:\n  notes.get(NoteId("demo"))\n', "D301")
        indirect = prefix + 'fn write_note() -> Result<Note, DataError>:\n  notes.create(NewNote(title: "write"))\n\npublic route GET "/":\n  let note = write_note()?\n  note.title\n'
        self.reject("indirect-get-write", indirect, "D302")
        helper = prefix.replace('    note.visible\n', '    helper(note)\n', 1) + 'fn helper(note: Note) -> Bool:\n  true\n'
        self.reject("policy-application-call", helper, "D900")
        with sqlite3.connect(path) as connection:
            connection.execute('ALTER TABLE "Notes" ADD COLUMN unexpected TEXT')
        self.cli(["db-init", "examples/storage.dia", "--database", str(path)], success=False)
        self.record("database state checks")

    def http(self) -> None:
        # Ephemeral port selection plus bounded startup retries avoids readiness
        # requests that could themselves consume an application interaction.
        with socket.socket() as selection:
            selection.bind(("127.0.0.1", 0))
            port = selection.getsockname()[1]
        process = subprocess.Popen([str(BINARY), "run", "examples/web.dia", "--listen", f"127.0.0.1:{port}"], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            assert process.stderr is not None
            startup = process.stderr.readline()
            assert f"http://127.0.0.1:{port}" in startup, startup

            def request(method: str, path: str, body: bytes | None = None, headers: dict[str, str] | None = None) -> tuple[int, dict[str, str], bytes]:
                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
                try:
                    connection.request(method, path, body=body, headers=headers or {})
                    response = connection.getresponse()
                    result = response.status, dict(response.getheaders()), response.read()
                    self.record("HTTP requests")
                    return result
                finally:
                    connection.close()

            status, headers, body = request("GET", "/")
            assert status == 200 and b"Diamond &lt;checkpoint one&gt;" in body and b'href="/notes/demo"' in body
            assert headers["Content-Security-Policy"].startswith("default-src 'none'") and headers["X-Content-Type-Options"] == "nosniff"
            assert request("HEAD", "/")[2] == b""
            assert request("GET", "/notes/alice-7")[0:1] == (200,)
            assert request("GET", "/numbers/-7")[2] == b"-21"
            assert request("GET", "/numbers/wrong")[0] == 400
            assert request("GET", "/account")[0] == 401
            assert request("GET", "/account", headers={"Cookie": "diamond_session=" + "a" * 64})[0] == 401
            assert request("GET", "/absent")[0] == 404
            assert request("GET", "/", headers={"Host": "attacker.example"})[0] == 400
            assert request("GET", "/", headers={"X-Forwarded-Host": "attacker.example"})[0] == 400
            assert request("GET", "/", headers={"Upgrade": "websocket"})[0] == 501
            assert request("GET", "/?unknown=1")[0] == 501

            values = ['<script>alert("x")</script>', '" onmouseover="alert(1)', "café 雪", "&<>\"'", "x" * 120, "a\nb", "\u2028\u2029", "plain text"]
            for value in values:
                body = urllib.parse.urlencode({"message": value}).encode()
                status, _, rendered = request("POST", "/echo", body, {"Content-Type": "application/x-www-form-urlencoded"})
                paragraph = Paragraph()
                paragraph.feed(rendered.decode())
                assert status == 200 and paragraph.tags == [("p", [])] and "".join(paragraph.text) == value, (value, status, rendered)
            for body in [b"message=", b"message=" + b"x" * 121, b"message=a&message=b", b"message=x&author=admin", b"message=%FF", b"message=%QQ"]:
                assert request("POST", "/echo", body, {"Content-Type": "application/x-www-form-urlencoded"})[0] == 400
            assert request("POST", "/echo", b"message=x", {"Content-Type": "text/plain"})[0] == 400
            assert request("POST", "/echo", b"message=x", {"Content-Type": "application/x-www-form-urlencoded", "Origin": "https://attacker.example"})[0] == 404

            with socket.create_connection(("127.0.0.1", port), timeout=3) as raw:
                raw.sendall(f"GET / HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nHost: attacker.example\r\n\r\n".encode())
                status_line = b""
                while b"\r\n" not in status_line:
                    part = raw.recv(4096)
                    assert part, "connection closed before the HTTP status line"
                    status_line += part
                    assert len(status_line) <= 16384
                assert status_line.startswith(b"HTTP/1.1 400"), status_line
                self.record("HTTP requests")
            assert process.poll() is None, "server unexpectedly exited"
        finally:
            process.terminate()
            try:
                process.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate(timeout=3)

    def run(self) -> None:
        self.language()
        self.database()
        self.http()


def main() -> int:
    campaign = Campaign()
    try:
        campaign.run()
    except Exception:
        print(f"Failure inputs and isolated data retained at {campaign.directory}", file=sys.stderr)
        raise
    else:
        shutil.rmtree(campaign.directory)
        print(json.dumps({"status": "PASS", "seed": SEED, "observations": campaign.counts}, indent=2))
        return 0


if __name__ == "__main__":
    sys.exit(main())
