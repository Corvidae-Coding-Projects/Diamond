#!/usr/bin/env python3
"""Bounded policy enforcement campaign through the real CLI, HTTP, and SQLite.

Oracle: design.md 9.2.1 and 12.2 plus the supported scalar subset. The Python
predicates below are separately stated business contracts, not translations of
production policy/evaluator/SQL code. No production module is imported.

Replay: python3 verification/policy_enforcement.py
Minimal stored-data replays: add --regressions-only. --binary selects an exact
previous executable for before/after comparison. Successful temporary databases
are deleted; failing inputs remain in the printed isolated directory.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from html.parser import HTMLParser
import hashlib
import json
from pathlib import Path
import random
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from typing import Callable

import checkpoint_two as transport

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "verification/regressions/cp4_policy"
SEED = 0xD1A04
FIELDS = ("id", "label", "note", "owner", "score", "tenant", "visible")
Row = dict[str, object]


class Identifiers(HTMLParser):
    def __init__(self, body: bytes) -> None:
        super().__init__(convert_charrefs=True)
        self.values: list[str] = []
        self.inside = False
        self.feed(body.decode())

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.inside = tag == "li"

    def handle_endtag(self, tag: str) -> None:
        if tag == "li":
            self.inside = False

    def handle_data(self, value: str) -> None:
        if self.inside:
            self.values.append(value)


class Client(transport.Client):
    def home(self) -> None:
        status, _, body = self.request("GET", "/")
        tokens = transport.Page(body).csrf
        assert status == 200 and len(tokens) == 1, (status, body)
        self.csrf = tokens[0]


@dataclass(frozen=True)
class Contract:
    name: str
    source: tuple[str, str, str, str]
    read: Callable[[Row, str | None], bool]
    create: Callable[[Row, str | None], bool]
    update: Callable[[Row, Row, str | None], bool]
    delete: Callable[[Row, str | None], bool]


def owner(row: Row, user: str | None) -> bool:
    return user is not None and row["owner"] == user


# Independent, explicit business contracts. Mutation results require read access
# to their proposed/returned row in addition to the operation's action policy.
CONTRACTS = (
    Contract(
        "ownership-and-tenant",
        (
            '(principal.is_user(item.owner) and item.tenant == "red") or (item.visible and item.score >= 2)',
            'principal.authenticated and principal.is_user(proposed.owner) and proposed.tenant != "blocked" and proposed.score >= 0',
            'principal.is_user(before.owner) and before.owner == after.owner and before.tenant == after.tenant and after.score >= before.score and after.visible',
            'principal.is_user(item.owner) and not item.visible',
        ),
        lambda row, user: (owner(row, user) and row["tenant"] == "red") or (bool(row["visible"]) and row["score"] >= 2),
        lambda row, user: owner(row, user) and row["tenant"] != "blocked" and row["score"] >= 0,
        lambda before, after, user: owner(before, user) and before["owner"] == after["owner"] and before["tenant"] == after["tenant"] and after["score"] >= before["score"] and bool(after["visible"]),
        lambda row, user: owner(row, user) and not row["visible"],
    ),
    Contract(
        "scalar-order-and-not",
        (
            'not item.visible and item.score < 7 and item.label >= "B" and item.label <= "雪"',
            'principal.is_user(proposed.owner) and proposed.label != "forbidden" and proposed.score <= 12',
            'principal.is_user(before.owner) and before.owner == after.owner and after.tenant == "red" and after.score > before.score and after.label != "forbidden"',
            'principal.is_user(item.owner) and item.score <= 5',
        ),
        lambda row, user: not row["visible"] and row["score"] < 7 and "B" <= row["label"] <= "雪",
        lambda row, user: owner(row, user) and row["label"] != "forbidden" and row["score"] <= 12,
        lambda before, after, user: owner(before, user) and before["owner"] == after["owner"] and after["tenant"] == "red" and after["score"] > before["score"] and after["label"] != "forbidden",
        lambda row, user: owner(row, user) and row["score"] <= 5,
    ),
    Contract(
        "authentication-and-changing-tenant",
        (
            'principal.authenticated and (item.visible == true or item.score != 3) and (item.tenant == "blue" or item.label < "C")',
            'principal.authenticated and proposed.visible == false and proposed.score > 0',
            'principal.authenticated and principal.is_user(before.owner) and before.owner == after.owner and after.score >= before.score and before.tenant != after.tenant',
            'principal.is_user(item.owner) or principal.has_role(.admin)',
        ),
        lambda row, user: user is not None and (bool(row["visible"]) or row["score"] != 3) and (row["tenant"] == "blue" or row["label"] < "C"),
        lambda row, user: user is not None and not row["visible"] and row["score"] > 0,
        lambda before, after, user: owner(before, user) and before["owner"] == after["owner"] and after["score"] >= before["score"] and before["tenant"] != after["tenant"],
        lambda row, user: owner(row, user),  # local provider grants no roles
    ),
)


PREFIX = '''app PolicyCampaign:
  database SQLite
  session SecureCookie
  provides DbRead<Items>, DbWrite<Items>

type ItemId = Id<"item">
type UserId = Id<"user">

type Item:
  id: ItemId
  label: Text<length: 1..16>
  note: Option<Text>
  owner: UserId
  score: Int
  tenant: Text<length: 1..12>
  visible: Bool

type Input:
  label: Text<length: 1..16>
  note: Option<Text>
  owner: UserId
  score: Int
  tenant: Text<length: 1..12>
  visible: Bool

resource Items: Item
'''


def quoted(value: object) -> str:
    if value is None:
        return "None"
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, int):
        return str(value)
    return json.dumps(value, ensure_ascii=False)


def selection_arguments(filters: Row, limit: int, offset: int) -> str:
    arguments = [f"limit: {limit}", f"offset: {offset}"]
    for field, value in filters.items():
        literal = quoted(value)
        if field == "owner":
            literal = f"UserId({literal})"
        elif field == "note" and value is not None:
            literal = f"Some({literal})"
        arguments.append(f"{field}: {literal}")
    return ", ".join(arguments)


def app_source(contract: Contract, selections: list[tuple[Row, int, int]]) -> str:
    read, create, update, delete = contract.source
    source = PREFIX + f'''
policy Items:
  read(item, by principal):
    {read}
  create(proposed, by principal):
    {create}
  update(before, after, by principal):
    {update}
  delete(item, by principal):
    {delete}

route GET "/":
  html:
    <form method="POST" action="/create"><button type="submit">Create</button></form>

public route GET "/item/{{id:ItemId}}":
  let item = items.get(id)?
  html:
    <ul><li>{{item.id}}</li></ul>

route POST "/create":
  let input = request.form<Input>()?
  let saved = items.create(input)?
  html:
    <ul><li>{{saved.id}}</li></ul>

route POST "/update/{{id:ItemId}}":
  let input = request.form<Input>()?
  let saved = items.update(id, input)?
  html:
    <ul><li>{{saved.id}}</li></ul>

route POST "/delete/{{id:ItemId}}":
  items.delete(id)?
  "deleted"
'''
    for index, (filters, limit, offset) in enumerate(selections):
        source += f'''
public route GET "/select/{index}":
  let selected = items.select({selection_arguments(filters, limit, offset)})?
  let entries = for item in selected:
    html:
      <li>{{item.id}}</li>
  html:
    <ul>{{entries}}</ul>
'''
    return source


class Campaign:
    def __init__(self, directory: Path, binary: Path) -> None:
        self.directory = directory
        self.binary = binary
        self.binary_sha256 = hashlib.sha256(binary.read_bytes()).hexdigest()
        self.counts: dict[str, int] = {}
        transport.BINARY = binary

    def count(self, name: str, number: int = 1) -> None:
        self.counts[name] = self.counts.get(name, 0) + number

    def source(self, name: str, source: str) -> Path:
        path = self.directory / f"{name}.dia"
        path.write_text(source)
        return path

    def cli(self, command: str, source: Path, database: Path | None = None) -> subprocess.CompletedProcess[str]:
        arguments = [str(self.binary), command, str(source)]
        if database is not None:
            arguments += ["--database", str(database)]
        result = subprocess.run(arguments, cwd=ROOT, capture_output=True, text=True, timeout=15)
        assert result.returncode >= 0 and "panicked at" not in result.stderr, (arguments, result.stderr)
        return result

    @staticmethod
    def invalid_stored_failure(name: str, result: subprocess.CompletedProcess[str]) -> None:
        assert result.returncode != 0 and not result.stdout and "main returned an error" in result.stderr and "invalid input" in result.stderr, (name, "malformed stored data must be a typed failure", result.returncode, result.stdout, result.stderr)

    def stored_regressions(self) -> None:
        original = (FIXTURES / "invalid_stored_selection.dia").read_text()
        # Each case has a different SQL mechanism that previously prevented the
        # malformed stored row from reaching the runtime's validation function.
        cases = [
            ("policy-masked", original, [("1" * 64, "okay", 1), ("2" * 64, "oversized", 0)]),
            ("equality-masked", original.replace("limit: 1", 'label: "okay", limit: 1'), [("1" * 64, "okay", 1), ("2" * 64, "oversized", 1)]),
            ("limit-masked", original, [("1" * 64, "okay", 1), ("2" * 64, "oversized", 1)]),
            ("offset-masked", original.replace("limit: 1", "limit: 1, offset: 1"), [("1" * 64, "oversized", 1), ("2" * 64, "okay", 1)]),
            ("empty-policy", original.replace("item.visible", "false"), [("1" * 64, "oversized", 1)]),
        ]
        for name, source, rows in cases:
            path = self.source(name, source)
            database = self.directory / f"{name}.sqlite"
            result = self.cli("db-init", path, database)
            assert result.returncode == 0, result.stderr
            with sqlite3.connect(database) as connection:
                connection.executemany("INSERT INTO Items VALUES (?,?,?)", rows)
            result = self.cli("run", path, database)
            self.invalid_stored_failure(name, result)
            with sqlite3.connect(database) as connection:
                assert connection.execute("SELECT * FROM Items ORDER BY id").fetchall() == rows
            self.count("malformed stored selection failures")

        # Malformed opaque identifiers are invalid in every field, recursively
        # through Option, and including an id that is not the lookup target.
        original = (FIXTURES / "invalid_stored_id.dia").read_text()
        for index, bad in enumerate(["", "has/slash", "has space", "雪", "a" * 129]):
            path = self.source(f"stored-id-{index}", original)
            database = self.directory / f"stored-id-{index}.sqlite"
            assert self.cli("db-init", path, database).returncode == 0
            with sqlite3.connect(database) as connection:
                connection.execute("INSERT INTO Items VALUES (?,?)", ("1" * 64, bad))
            result = self.cli("run", path, database)
            self.invalid_stored_failure(repr(bad), result)
            self.count("malformed stored ID failures")
        optional = original.replace("owner: UserId", "owner: Option<UserId>").replace("Result<UserId,", "Result<Int,").replace("Ok(item.owner)", "Ok(1)")
        path = self.source("stored-optional-id", optional)
        database = self.directory / "optional-id.sqlite"
        assert self.cli("db-init", path, database).returncode == 0
        with sqlite3.connect(database) as connection:
            connection.execute("INSERT INTO Items VALUES (?,?)", ("1" * 64, "bad/optional"))
        result = self.cli("run", path, database)
        self.invalid_stored_failure("optional ID", result)
        self.count("malformed stored optional ID failures")

        selection = original.split("fn main()", 1)[0] + 'fn main() -> Result<Int, DataError>:\n  let selected = items.select(limit: 1)?\n  Ok(len(selected))\n'
        path = self.source("stored-primary-id", selection)
        database = self.directory / "primary-id.sqlite"
        assert self.cli("db-init", path, database).returncode == 0
        with sqlite3.connect(database) as connection:
            connection.execute("INSERT INTO Items VALUES (?,?)", ("bad/primary", "alice"))
        self.invalid_stored_failure("primary ID", self.cli("run", path, database))
        self.count("malformed stored primary ID failures")

        # The scan bound is an operation error, even when the authorized output
        # page would be empty or contain only one valid record.
        path = self.source("selection-budget", selection)
        database = self.directory / "budget.sqlite"
        assert self.cli("db-init", path, database).returncode == 0
        with sqlite3.connect(database) as connection:
            connection.executemany("INSERT INTO Items VALUES (?,?)", ((f"item_{index:05}", "alice") for index in range(10_000)))
        result = self.cli("run", path, database)
        assert result.returncode == 0 and result.stdout == "1\n", ("selection at scan budget", result.stdout, result.stderr)
        self.count("selection budget boundary successes")
        with sqlite3.connect(database) as connection:
            connection.execute("INSERT INTO Items VALUES (?,?)", ("item_10000", "alice"))
        result = self.cli("run", path, database)
        assert result.returncode != 0 and not result.stdout and "resource limit exceeded" in result.stderr, ("selection scan budget", result.stdout, result.stderr)
        with sqlite3.connect(database) as connection:
            assert connection.execute("SELECT count(*) FROM Items").fetchone() == (10_001,)
        self.count("selection budget failures")

    def compile_boundaries(self) -> None:
        actions = {
            "read": ('items.get(ItemId("one"))', "Item"),
            "select": ("items.select(limit: 1)", "List<Item>"),
            "create": ('items.create(Input(label: "a", note: None, owner: UserId("alice"), score: 2, tenant: "red", visible: true))', "Item"),
            "update": ('items.update(ItemId("one"), Input(label: "a", note: None, owner: UserId("alice"), score: 2, tenant: "red", visible: true))', "Item"),
            "delete": ('items.delete(ItemId("one"))', "Unit"),
        }
        rules = {"read": "  read(item, by principal):\n    true\n", "create": "  create(item, by principal):\n    true\n", "update": "  update(before, after, by principal):\n    true\n", "delete": "  delete(item, by principal):\n    true\n"}
        for action, (call, output) in actions.items():
            omitted = "read" if action == "select" else action
            policy = "policy Items:\n" + "".join(rule for name, rule in rules.items() if name != omitted)
            path = self.source("missing-" + action, PREFIX + policy + f"\nfn main() -> Result<{output}, DataError>:\n  {call}\n")
            result = self.cli("check", path)
            assert result.returncode != 0 and "D204" in result.stderr, (action, result.stderr)
            self.count("missing action compile rejections")
        for action in ["create", "update"]:
            call, output = actions[action]
            path = self.source("missing-read-for-" + action, PREFIX + "policy Items:\n" + rules[action] + f"\nfn main() -> Result<{output}, DataError>:\n  {call}\n")
            result = self.cli("check", path)
            assert result.returncode != 0 and "D204" in result.stderr and "read policy" in result.stderr, result.stderr
            self.count("returned row read-policy rejections")

        # A direct delete may authorize its action without read permission.
        source = PREFIX.replace("  session SecureCookie\n", "").replace("DbRead<Items>, ", "") + 'policy Items:\n  delete(item, by principal):\n    item.tenant == "red"\n\nfn main() -> Result<Unit, DataError>:\n  items.delete(ItemId("one"))\n'
        path = self.source("no-read-delete", source)
        database = self.directory / "no-read-delete.sqlite"
        assert self.cli("check", path).returncode == 0
        assert self.cli("db-init", path, database).returncode == 0
        with sqlite3.connect(database) as connection:
            connection.execute("INSERT INTO Items VALUES (?,?,?,?,?,?,?)", ("one", "hidden", None, "alice", 0, "red", 0))
        assert self.cli("run", path, database).returncode == 0
        with sqlite3.connect(database) as connection:
            assert connection.execute("SELECT count(*) FROM Items").fetchone() == (0,)
        self.count("direct delete without read journeys")

        for name, predicate in [
            ("application-call", "arbitrary(item.score)"),
            ("optional-operand", "item.note == item.note"),
            ("principal-attribute", 'principal.tenant == "red"'),
        ]:
            path = self.source("unsupported-policy-" + name, PREFIX + f"policy Items:\n  read(item, by principal):\n    {predicate}\n")
            result = self.cli("check", path)
            assert result.returncode != 0 and "D900" in result.stderr, (name, result.stderr)
            self.count("unsupported policy compile rejections")
        for name, call in [("join", "items.join(items)"), ("arbitrary-filter", 'items.select(where: "score > 1", limit: 3)'), ("aggregate", "items.count()")]:
            path = self.source("unsupported-query-" + name, PREFIX + "policy Items:\n" + rules["read"] + f"\nfn main() -> Int:\n  {call}\n")
            result = self.cli("check", path)
            assert result.returncode != 0 and ("D900" in result.stderr or "D125" in result.stderr), (name, result.stderr)
            self.count("unsupported query compile rejections")

    def transaction_regressions(self) -> None:
        source = FIXTURES / "caught_invalid_selection.dia"
        database = self.directory / "caught-invalid-selection.sqlite"
        assert self.cli("db-init", source, database).returncode == 0
        seeded = [("1" * 64, "oversized", 0)]
        with sqlite3.connect(database) as connection:
            connection.executemany("INSERT INTO Items VALUES (?,?,?)", seeded)
        result = self.cli("run", source, database)
        assert result.returncode != 0 and not result.stdout and "transaction aborted" in result.stderr, ("caught selection failure", result.returncode, result.stdout, result.stderr)
        with sqlite3.connect(database) as connection:
            assert connection.execute("SELECT * FROM Items ORDER BY id").fetchall() == seeded
        self.count("caught invalid selection rollback journeys")

        # A successful selection inside an explicit transaction must not commit
        # that transaction early. A later deliberate error rolls back the create.
        prefix = source.read_text().split("fn main()", 1)[0]
        later_error = self.source("selection-before-abort", prefix + '''fn main() -> Result<Int, DataError>:
  transaction db:
    let saved = items.create(Input(label: "good", visible: true))?
    let selected = items.select(limit: 1)?
    Err("abort after selection")
''')
        database = self.directory / "selection-before-abort.sqlite"
        assert self.cli("db-init", later_error, database).returncode == 0
        result = self.cli("run", later_error, database)
        assert result.returncode != 0 and "abort after selection" in result.stderr, (result.returncode, result.stdout, result.stderr)
        with sqlite3.connect(database) as connection:
            assert connection.execute("SELECT count(*) FROM Items").fetchone() == (0,)
        self.count("selection preserves explicit transaction rollback journeys")

    def stored_http_regressions(self) -> None:
        directory = self.directory / "invalid-http"
        directory.mkdir()
        database = directory / "board.sqlite"
        source = FIXTURES / "invalid_stored_http.dia"
        result = self.cli("db-init", source, database)
        assert result.returncode == 0, result.stderr
        transport.provision(directory, "observer", secrets.token_urlsafe(20))
        server = transport.Server(directory, str(source))
        try:
            server.start()
            # Full-relation validation errors never disclose a returned page,
            # the invalid value, the SQLite error, or a rendered typed URL.
            rows = [("1" * 64, "okay", "alice", 1), ("2" * 64, "oversized", "bob", 0)]
            with sqlite3.connect(database) as connection:
                connection.executemany("INSERT INTO Items VALUES (?,?,?,?)", rows)
            status, _, body = server.raw("GET", "/")
            assert status == 400 and body == b"Invalid input", ("HTTP invalid hidden row", status, body)
            with sqlite3.connect(database) as connection:
                assert connection.execute("SELECT * FROM Items ORDER BY id").fetchall() == rows
                connection.execute("DELETE FROM Items")
                connection.execute("INSERT INTO Items VALUES (?,?,?,?)", ("1" * 64, "okay", "alice/more", 1))
            for route in ["/", "/link"]:
                status, _, body = server.raw("GET", route)
                assert status == 400 and body == b"Invalid input", ("HTTP stored ID", route, status, body)
            with sqlite3.connect(database) as connection:
                assert connection.execute("SELECT * FROM Items").fetchall() == [("1" * 64, "okay", "alice/more", 1)]
                connection.execute("UPDATE Items SET owner='alice'")
            # A valid follow-up on the same process demonstrates recovery after
            # the failed automatic read transactions and normal typed URL output.
            status, _, body = server.raw("GET", "/link")
            assert status == 200 and b'href="/people/alice"' in body, (status, body)
            status, _, body = server.raw("GET", "/")
            assert status == 200 and b"<p>1</p>" in body, (status, body)
            self.count("sanitized stored-data HTTP failures", 3)
            self.count("same-process read recovery journeys", 2)
            self.count("HTTP requests", server.requests)
        finally:
            server.stop()

    @staticmethod
    def state(database: Path) -> list[Row]:
        with sqlite3.connect(database) as connection:
            return [dict(zip(FIELDS, row)) for row in connection.execute("SELECT " + ",".join(FIELDS) + " FROM Items ORDER BY id COLLATE BINARY")]

    @staticmethod
    def seed(database: Path, model: list[Row]) -> None:
        with sqlite3.connect(database) as connection:
            connection.execute("DELETE FROM Items")
            connection.executemany("INSERT INTO Items (" + ",".join(FIELDS) + ") VALUES (?,?,?,?,?,?,?)", ([row[field] for field in FIELDS] for row in model))

    def generated(self) -> None:
        generator = random.Random(SEED)
        model: list[Row] = []
        for index in range(18):
            model.append(dict(id=f"{'Z' if index % 4 == 0 else 'a'}_{index:02}", label=["A<one", "B & two", "Café", "Ωmega", "雪", "forbidden"][index % 6], note=None if index % 3 == 0 else ["雪", "bound'); --"][index % 2], owner=["alice", "bob", "other"][index % 3], score=[0, 1, 2, 3, 5, 6, 7, 12][index % 8], tenant=["red", "blue", "blocked"][(index // 3) % 3], visible=bool(index % 2)))
        # Physical row insertion is deliberately unrelated to the required id order.
        generator.shuffle(model)
        selections: list[tuple[Row, int, int]] = [({}, 3, 0), ({}, 4, 2), ({}, 100, 0), ({}, 2, 30), ({"owner": "alice"}, 5, 0), ({"tenant": "blue"}, 2, 1), ({"score": 3}, 10, 0), ({"visible": True}, 3, 0), ({"label": "B & two"}, 4, 0), ({"note": None}, 10, 0), ({"note": "bound'); --"}, 2, 0), ({"owner": "bob", "tenant": "blocked", "visible": False}, 5, 0)]
        for contract in CONTRACTS:
            directory = self.directory / contract.name
            directory.mkdir()
            source = self.source(contract.name, app_source(contract, selections))
            database = directory / "board.sqlite"
            result = self.cli("db-init", source, database)
            assert result.returncode == 0, result.stderr
            self.seed(database, model)
            accounts = {user: secrets.token_urlsafe(20) for user in ["alice", "bob"]}
            for user, password in accounts.items():
                transport.provision(directory, user, password)
            server = transport.Server(directory, str(source))
            try:
                server.start()
                clients = {None: Client(server), "alice": Client(server), "bob": Client(server)}
                for user, password in accounts.items():
                    clients[user].login(user, password)
                initial = self.state(database)
                for user, client in clients.items():
                    for row in model:
                        status, _, body = client.request("GET", "/item/" + str(row["id"]))
                        expected = contract.read(row, user)
                        assert status == (200 if expected else 404), (contract.name, user, "get", row, status, body)
                        if expected:
                            assert Identifiers(body).values == [row["id"]]
                        self.count("reference get comparisons")
                    missing = client.request("GET", "/item/missing")
                    assert missing[0] == 404
                    for row in model:
                        if not contract.read(row, user):
                            hidden = client.request("GET", "/item/" + str(row["id"]))
                            assert hidden[0] == missing[0] and hidden[2] == missing[2]
                            break
                    for index, (filters, limit, offset) in enumerate(selections):
                        expected = [row["id"] for row in sorted(model, key=lambda row: row["id"]) if contract.read(row, user) and all(row[key] == value for key, value in filters.items())][offset:offset + limit]
                        status, _, body = client.request("GET", f"/select/{index}")
                        assert status == 200 and Identifiers(body).values == expected, (contract.name, user, index, expected, status, body)
                        self.count("reference select comparisons")
                assert self.state(database) == initial

                # Full before/after state is checked for every attempted mutation;
                # fixture reset gives each comparison the same independent prestate.
                for user in ["alice", "bob"]:
                    client = clients[user]
                    candidates = [dict(label=label, note=note, owner=assigned, score=score, tenant=tenant, visible=visible) for label, note, assigned, score, tenant, visible in [
                        ("B new", None, user, 2, "red", True),
                        ("B hidden", "雪", user, 3, "blue", False),
                        ("forbidden", None, user, 13, "red", False),
                        ("A foreign", "bound'); --", "other", 1, "blue", False),
                        ("Z blocked", None, user, 0, "blocked", True),
                        ("B plain", None, user, 1, "red", False),
                    ]]
                    for candidate in candidates:
                        self.seed(database, model)
                        before = self.state(database)
                        action_allowed = contract.create(candidate, user)
                        readable = contract.read(candidate, user)
                        expected = action_allowed and readable
                        status, _, body = client.request("POST", "/create", self.form(candidate, client.csrf))
                        assert status == (200 if expected else 404), (contract.name, user, "create", candidate, expected, status, body)
                        after = self.state(database)
                        if expected:
                            added = [row for row in after if row not in before]
                            assert len(added) == 1 and re.fullmatch("[0-9a-f]{64}", str(added[0]["id"]))
                            assert {key: value for key, value in added[0].items() if key != "id"} == candidate
                            assert len(after) == len(before) + 1 and all(row in after for row in before)
                            assert Identifiers(body).values == [added[0]["id"]]
                            assert client.request("GET", "/item/" + str(added[0]["id"]))[0] == 200
                        else:
                            assert after == before, (contract.name, "denied create changed state")
                            if action_allowed and not readable:
                                self.count("returned mutation read-denial rollbacks")
                        self.count("reference create comparisons")

                    # A known owner row is varied across the pre-state; updates
                    # include action-allowed/read-denied cases as well as transfer,
                    # cross-tenant, score, and visibility boundaries.
                    targets = [row for row in model if row["owner"] in [user, "other"]][:4]
                    for target in targets:
                        changes = [dict(target, score=target["score"] + 1, visible=True), dict(target, score=target["score"] + 1, visible=False, label="B changed", tenant="red"), dict(target, owner="bob" if user == "alice" else "alice"), dict(target, tenant="blue" if target["tenant"] != "blue" else "red", score=target["score"] + 2), dict(target, score=0, label="forbidden")]
                        for proposed in changes:
                            self.seed(database, model)
                            before = self.state(database)
                            action_allowed = contract.update(target, proposed, user)
                            readable = contract.read(proposed, user)
                            expected = action_allowed and readable
                            status, _, body = client.request("POST", "/update/" + str(target["id"]), self.form(proposed, client.csrf))
                            assert status == (200 if expected else 404), (contract.name, user, "update", target, proposed, expected, status, body)
                            after = self.state(database)
                            expected_state = [proposed if row["id"] == target["id"] else row for row in before] if expected else before
                            assert after == expected_state, (contract.name, "update state", target, proposed, after, expected_state)
                            if action_allowed and not readable:
                                self.count("returned mutation read-denial rollbacks")
                            if expected:
                                assert Identifiers(body).values == [target["id"]]
                                assert client.request("GET", "/item/" + str(target["id"]))[0] == 200
                            self.count("reference update comparisons")
                    for target in model:
                        self.seed(database, model)
                        before = self.state(database)
                        expected = contract.delete(target, user)
                        status, _, body = client.request("POST", "/delete/" + str(target["id"]), {"_diamond_csrf": client.csrf})
                        assert status == (200 if expected else 404), (contract.name, user, "delete", target, expected, status, body)
                        expected_state = [row for row in before if row["id"] != target["id"]] if expected else before
                        assert self.state(database) == expected_state
                        if expected:
                            assert client.request("GET", "/item/" + str(target["id"]))[0] == 404
                        self.count("reference delete comparisons")
                self.count("HTTP requests", server.requests)
            finally:
                server.stop()

    @staticmethod
    def form(row: Row, csrf: str) -> dict[str, str]:
        return {key: ("" if value is None else str(value).lower() if isinstance(value, bool) else str(value)) for key, value in row.items() if key != "id"} | {"_diamond_csrf": csrf}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, default=ROOT / "target/debug/diamond")
    parser.add_argument("--regressions-only", action="store_true")
    options = parser.parse_args()
    directory = Path(tempfile.mkdtemp(prefix="diamond-policy-enforcement-"))
    campaign = Campaign(directory, options.binary.resolve())
    try:
        campaign.stored_regressions()
        campaign.transaction_regressions()
        campaign.stored_http_regressions()
        if not options.regressions_only:
            campaign.compile_boundaries()
            campaign.generated()
        assert hashlib.sha256(campaign.binary.read_bytes()).hexdigest() == campaign.binary_sha256, "executable changed during verification; rerun against a stable build"
    except Exception:
        print(f"Failure input and isolated state retained at {directory}", file=sys.stderr)
        raise
    else:
        shutil.rmtree(directory)
        print(json.dumps({"status": "PASS", "seed": SEED, "binary_sha256": campaign.binary_sha256, "observations": campaign.counts}, indent=2))


if __name__ == "__main__":
    main()
