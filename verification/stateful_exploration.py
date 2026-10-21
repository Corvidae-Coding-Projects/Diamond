#!/usr/bin/env python3
"""Generated real board workflows with an independent state/authorization model.

No production code is imported. Earlier helpers only transport HTTP and launch
the real binary. Each transition is checked against exact independent SQLite
state and normal public/owner lists; fresh processes test committed persistence.
"""
from __future__ import annotations

import argparse
from collections import Counter
from html.parser import HTMLParser
import hashlib
import json
from pathlib import Path
import random
import re
import secrets
import shutil
import sqlite3
import sys
import tempfile

import checkpoint_two as transport

ROOT = Path(__file__).resolve().parents[1]
USERS = ["alice", "bob"]


class Links(HTMLParser):
    def __init__(self, body: bytes) -> None:
        super().__init__(convert_charrefs=True)
        self.paths: list[str] = []
        self.feed(body.decode())

    def handle_starttag(self, tag: str, attrs) -> None:
        values = dict(attrs)
        if tag == "a" and re.fullmatch(r"/posts/[0-9a-f]{64}", values.get("href", "")):
            self.paths.append(values["href"])
        assert tag != "script", "literal input became executable markup"


class Campaign:
    def __init__(self, directory: Path, seed: int) -> None:
        self.directory = directory
        self.rng = random.Random(seed)
        self.counts: Counter[str] = Counter()
        self.trace: list[dict] = []

    @staticmethod
    def state(directory: Path) -> list[tuple]:
        with sqlite3.connect(directory / "board.sqlite") as connection:
            assert connection.execute("PRAGMA quick_check").fetchall() == [("ok",)]
            return connection.execute("SELECT id,author,title,body,published FROM Posts ORDER BY id COLLATE BINARY").fetchall()

    def sequence(self, number: int) -> None:
        directory = self.directory / f"sequence-{number}"
        directory.mkdir()
        passwords = {user: secrets.token_urlsafe(24) for user in USERS}
        for user, password in passwords.items():
            transport.provision(directory, user, password)
        server = transport.Server(directory)
        clients = {user: transport.Client(server) for user in USERS}
        signed: set[str] = set()
        model: dict[str, tuple[str, str, str, int]] = {}

        def login(user: str) -> None:
            clients[user].login(user, passwords[user])
            signed.add(user)

        def verify() -> None:
            expected = [(identifier, *values) for identifier, values in sorted(model.items())]
            assert self.state(directory) == expected, ("stored state", self.trace, expected, self.state(directory))
            status, _, body = server.raw("GET", "/posts")
            assert status == 200 and Links(body).paths == [f"/posts/{key}" for key, row in sorted(model.items()) if row[3]]
            for user, client in clients.items():
                status, _, body = client.request("GET", "/")
                assert status == (200 if user in signed else 401), (user, status)
                if user in signed:
                    assert Links(body).paths == [f"/posts/{key}" for key, row in sorted(model.items()) if row[0] == user]
            self.counts["independent state and list checks"] += 1

        def create(user: str, title: str, body: str) -> None:
            if self.trace:
                self.trace[-1].update(user=user, title=title, body=body)
            fields = {"title": title, "body": body, "_diamond_csrf": clients[user].csrf}
            status, headers, _ = clients[user].request("POST", "/posts", fields)
            if user not in signed:
                assert status == 401
                self.counts["unauthenticated mutation rejections"] += 1
                return
            assert status == 303 and re.fullmatch(r"/posts/[0-9a-f]{64}", headers["Location"])
            key = headers["Location"].rsplit("/", 1)[1]
            assert key not in model
            model[key] = (user, title, body, 0)
            status, _, rendered = clients[user].request("GET", headers["Location"])
            assert status == 200 and title in "".join(transport.Page(rendered).text) and body in "".join(transport.Page(rendered).text)
            self.counts["accepted creations"] += 1

        try:
            server.start()
            for user in USERS:
                login(user)
                self.trace.append({"sequence": number, "action": "initial-create", "user": user})
                create(user, f"Start {user} 雪", f"Literal <script>{user}</script> & body")
                verify()
            actions = ["create", "read", "edit", "publish", "delete", "invalid", "csrf", "logout", "login", "restart", "missing", "foreign"]
            schedule = []
            for _ in range(4):
                shuffled = actions.copy()
                self.rng.shuffle(shuffled)
                schedule.extend(shuffled)
            for index, action in enumerate(schedule):
                user = self.rng.choice(USERS)
                client = clients[user]
                key = self.rng.choice(sorted(model)) if model else "0" * 64
                row = model.get(key)
                self.trace.append({"sequence": number, "step": index, "action": action, "user": user, "id": key})
                if action == "login":
                    login(user)
                    self.counts["login transitions"] += 1
                elif action == "logout":
                    status, _, _ = client.request("POST", "/_diamond/logout", {"_diamond_csrf": client.csrf})
                    assert status == (303 if user in signed else 404), (action, user, status)
                    signed.discard(user)
                    self.counts["logout transitions"] += 1
                elif action == "restart":
                    before = self.state(directory)
                    server.stop()
                    server.start()
                    signed.clear()
                    assert self.state(directory) == before
                    for stale in clients.values():
                        assert stale.request("GET", "/")[0] == 401
                    # New process reads only published rows with no principal.
                    for identifier, saved in model.items():
                        status, _, body = server.raw("GET", "/posts/" + identifier)
                        assert status == (200 if saved[3] else 404)
                        if saved[3]: assert saved[1] in "".join(transport.Page(body).text)
                    self.counts["restart and stale-session transitions"] += 1
                elif action == "create":
                    values = ["' ); -- <b>雪</b>", "é" * 120, 'A "quoted" & title', "Line\nnext", "e\u0301"]
                    title = self.rng.choice(values)
                    create(user, title, f"sequence {number} step {index}: <script>literal</script> 雪")
                elif action == "read":
                    status, _, body = client.request("GET", "/posts/" + key)
                    allowed = row is not None and (row[3] or (user in signed and row[0] == user))
                    assert status == (200 if allowed else 404), ("read", row, user, signed, status)
                    if allowed:
                        assert row[1] in "".join(transport.Page(body).text) and row[2] in "".join(transport.Page(body).text)
                        Links(body)
                    self.counts["modeled record reads"] += 1
                elif action == "invalid":
                    if user not in signed: login(user)
                    before = self.state(directory)
                    title = self.rng.choice(["", "x" * 121, "é" * 121])
                    status, _, body = client.request("POST", "/posts", {"title": title, "body": "invalid", "_diamond_csrf": client.csrf})
                    assert status == 400 and b"Invalid post" in body
                    assert self.state(directory) == before
                    self.counts["validation boundary rejections"] += 1
                elif action == "csrf":
                    if user not in signed: login(user)
                    other = clients["bob" if user == "alice" else "alice"]
                    status, _, _ = client.request("POST", "/posts", {"title": "must reject", "body": "unchanged", "_diamond_csrf": other.csrf or "0" * 64})
                    assert status == 404
                    self.counts["cross-session CSRF rejections"] += 1
                else:
                    suffix = {"edit": "/edit", "publish": "/publication", "delete": "/delete"}.get(action, "/edit")
                    if action == "missing":
                        key, row = "f" * 64, None
                    elif action == "foreign":
                        # Establish the wrong user's real authenticated context.
                        if row:
                            user = "bob" if row[0] == "alice" else "alice"
                            client = clients[user]
                            if user not in signed: login(user)
                    fields = {"title": f"Edit {number}:{index} 雪", "body": "<b>edited</b> & literal", "_diamond_csrf": client.csrf}
                    proposed = self.rng.choice([True, False])
                    if action == "publish": fields = {"published": str(proposed).lower(), "_diamond_csrf": client.csrf}
                    if action == "delete": fields = {"_diamond_csrf": client.csrf}
                    self.trace[-1].update(user=user, id=key, suffix=suffix,
                                           fields={key: value for key, value in fields.items() if key != "_diamond_csrf"})
                    allowed = user in signed and row is not None and row[0] == user
                    status, headers, _ = client.request("POST", "/posts/" + key + suffix, fields)
                    assert status == (303 if allowed else 404 if user in signed else 401), (action, user, row, status)
                    if allowed:
                        if action == "delete":
                            del model[key]
                            assert headers["Location"] == "/"
                        elif action == "publish": model[key] = (*row[:3], int(proposed))
                        else: model[key] = (row[0], fields["title"], fields["body"], row[3])
                        self.counts["accepted updates and deletions"] += 1
                    else: self.counts["modeled mutation denials"] += 1
                self.counts["generated actions"] += 1
                verify()
            self.counts["HTTP requests"] += server.requests
            self.counts["generated sequences"] += 1
        finally:
            server.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=lambda value: int(value, 0), default=0xD1A05)
    parser.add_argument("--binary", type=Path, default=ROOT / "target/debug/diamond")
    options = parser.parse_args()
    transport.BINARY = options.binary.resolve()
    original = hashlib.sha256(transport.BINARY.read_bytes()).hexdigest()
    directory = Path(tempfile.mkdtemp(prefix="diamond-cp5-stateful-"))
    campaign = Campaign(directory, options.seed)
    try:
        for sequence in range(3): campaign.sequence(sequence)
        assert hashlib.sha256(transport.BINARY.read_bytes()).hexdigest() == original
    except BaseException:
        (directory / "actions.json").write_text(json.dumps(campaign.trace, indent=2) + "\n")
        print(f"Concrete actions and isolated failure state retained at {directory}", file=sys.stderr)
        raise
    else:
        shutil.rmtree(directory)
        print(json.dumps({"result": "PASS", "seed": options.seed, "binary_sha256": original,
                          "observations": dict(campaign.counts)}, indent=2))


if __name__ == "__main__":
    main()
