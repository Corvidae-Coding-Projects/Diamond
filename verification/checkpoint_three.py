#!/usr/bin/env python3
"""Real CLI/HTTP/SQLite evidence for the completed interpreted MVP surface.

The oracle models declared visibility and explicit state transitions in Python.
It imports only earlier interface drivers, never production implementation code.
"""
from __future__ import annotations

from html.parser import HTMLParser
import json
from pathlib import Path
import random
import secrets
import shutil
import sqlite3
import subprocess
import sys
import tempfile

from checkpoint_two import BINARY, ROOT, Client, Page, Server, provision, rows

SEED = 0xD1A03


class Items(HTMLParser):
    def __init__(self, source: str) -> None:
        super().__init__(convert_charrefs=True)
        self.values: list[str] = []
        self.current: list[str] | None = None
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        if tag == "li":
            self.current = []

    def handle_data(self, text):
        if self.current is not None:
            self.current.append(text)

    def handle_endtag(self, tag):
        if tag == "li":
            self.values.append("".join(self.current or []))
            self.current = None


class Campaign:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.counts: dict[str, int] = {}

    def count(self, label: str) -> None:
        self.counts[label] = self.counts.get(label, 0) + 1

    def cli(self, name: str, source: str, expected: str | None = None, reject: str | None = None, database: Path | None = None, command: str = "run") -> subprocess.CompletedProcess[str]:
        path = self.directory / f"{name}.dia"
        path.write_text(source)
        arguments = [str(BINARY), command, str(path)]
        if database:
            arguments += ["--database", str(database)]
        result = subprocess.run(arguments, cwd=ROOT, capture_output=True, text=True, timeout=8)
        assert "panicked at" not in result.stderr and result.returncode >= 0
        if reject is not None:
            assert result.returncode != 0 and reject in result.stderr, (name, result.stderr)
        else:
            assert result.returncode == 0, (name, result.stderr)
            if expected is not None:
                assert result.stdout == expected + "\n", (name, result.stdout, expected)
        return result

    def language(self) -> None:
        enum = 'enum State:\n  Active\n  Suspended(reason: Text)\n  Closed\n\n'
        description = 'fn describe(state: State) -> Text:\n  match state:\n    Active => "active"\n    Suspended(reason) => "suspended: " + reason\n    Closed => "closed"\n\n'
        for index, (value, expected) in enumerate([("Active", "active"), ('Suspended("café 雪")', "suspended: café 雪"), ("Closed()", "closed")]):
            self.cli(f"variant-{index}", enum + description + f"fn main() -> Text:\n  describe({value})\n", expected)
            self.count("direct language journeys")
        self.cli("result-recovery", 'fn fail() -> Result<Int, DataError>:\n  Err("expected failure")\nfn main() -> Text:\n  match fail():\n    Ok(_) => "unexpected"\n    Err(error) => error_message(error)\n', "expected failure")
        self.cli("option-none", 'fn choose(value: Option<Int>) -> Int:\n  match value:\n    Some(number) => number\n    None => 73\nfn main() -> Int:\n  choose(None)\n', "73")
        self.cli("empty-list", 'fn main() -> Int:\n  len([])\n', "0")
        self.cli("unicode-length", 'fn main() -> Int:\n  len("café 雪")\n', "6")
        self.cli("request-shadow", (ROOT / 'verification/regressions/request_shadow.dia').read_text(), "true")
        self.counts["direct language journeys"] += 5
        generator = random.Random(SEED)
        for index in range(24):
            values = [generator.randrange(-100, 100) for _ in range(generator.randrange(0, 8))]
            source = 'fn transform(value: Option<Int>) -> Int:\n  match value:\n    Some(number) =>\n      let doubled = number * 2\n      doubled + 1\n    None => 0\nfn main() -> Html:\n  let rendered = for number in ' + str(values) + ':\n    let result = transform(Some(number))\n    html:\n      <li>{result}</li>\n  html:\n    <ul>{rendered}</ul>\n'
            result = self.cli(f"generated-language-{index}", source)
            assert Items(result.stdout).values == [str(number * 2 + 1) for number in values]
            self.count("generated language journeys")
        for name, source, diagnostic in [
            ("incomplete", enum + 'fn main() -> Int:\n  match Active:\n    Active => 1\n', "D123"),
            ("duplicate-case", enum + 'fn main() -> Int:\n  match Active:\n    Active => 1\n    Active => 2\n    _ => 0\n', "D123"),
            ("wrong-payload", enum + 'fn main() -> Text:\n  describe(Suspended(7))\n' + description, "D110"),
            ("missing-binding", enum + 'fn main() -> Int:\n  match Active:\n    Suspended => 1\n    _ => 0\n', "D123"),
            ("wrong-pattern", enum + 'fn main() -> Int:\n  match Active:\n    None => 0\n    _ => 1\n', "D123"),
            ("branch-types", enum + 'fn main() -> Int:\n  match Active:\n    Active => 1\n    Suspended(_) => "bad"\n    Closed => 0\n', "D110"),
            ("unreachable", 'fn main() -> Int:\n  match true:\n    _ => 1\n    false => 2\n', "D123"),
            ("scope", 'fn main() -> Int:\n  let value = match Some(1):\n    Some(inner) => inner\n    None => 0\n  inner\n', "D112"),
            ("list-types", 'fn main() -> Int:\n  len([1, "two"])\n', "D110"),
            ("html-list", 'fn main() -> Html:\n  let values = ["<script>"]\n  html:\n    <p>{values}</p>\n', "D121"),
            ("early-return", 'fn main() -> Int:\n  match true:\n    true =>\n      return 1\n    false => 2\n', "D900"),
            ("transaction-return", 'app ReturnCheck:\n  database SQLite\nfn main() -> Int:\n  transaction db:\n    return 1\n  2\n', "D900"),
            ("duplicate-variant", 'enum First:\n  Duplicate\nenum Second:\n  Duplicate\n', "D101"),
            ("nested-pattern", 'fn main() -> Int:\n  match Some(Some(1)):\n    Some(Some(value)) => value\n    _ => 0\n', "D010"),
            ("oversized-list", 'fn main() -> Int:\n  len([' + ','.join(['1'] * 1001) + '])\n', "limit"),
        ]:
            self.cli(name, source, reject=diagnostic, command="check")
            self.count("language compile rejections")

    def selections_and_transactions(self) -> None:
        prefix = (ROOT / "examples/storage.dia").read_text().split("fn main()", 1)[0]
        database = self.directory / "selection.sqlite"
        self.cli("selection-init", prefix, database=database, command="db-init")
        model = [(f"n{index:03}", "Robert'); DROP TABLE Notes; --" if index == 9 else f"title {index % 4} 雪", int(index % 3 != 0)) for index in range(20)]
        with sqlite3.connect(database) as connection:
            connection.executemany('INSERT INTO Notes (id,title,visible) VALUES (?,?,?)', model)
        render = '\nfn render(values: List<Note>) -> Html:\n  let entries = for note in values:\n    html:\n      <li>{note.id}</li>\n  html:\n    <ul>{entries}</ul>\n'
        generator = random.Random(SEED + 1)
        for index in range(24):
            limit, offset = generator.randrange(1, 6), generator.randrange(0, 18)
            title = model[9][1] if index % 4 == 0 else None
            arguments = f"limit: {limit}, offset: {offset}" + (", title: " + json.dumps(title, ensure_ascii=False) if title else "")
            expected = [row[0] for row in sorted(model) if row[2] and (title is None or row[1] == title)][offset:offset + limit]
            source = prefix + render + f'\nfn main() -> Result<Html, DataError>:\n  let values = notes.select({arguments})?\n  Ok(render(values))\n'
            result = self.cli(f"selection-{index}", source, database=database)
            assert Items(result.stdout).values == expected, (index, Items(result.stdout).values, expected)
            self.count("generated authorized selections")
        for name, call, expected in [("missing-limit", "notes.select()", "D125"), ("unknown-filter", 'notes.select(limit: 2, where: "anything")', "D125"), ("wrong-filter", "notes.select(limit: 2, visible: 7)", "D110"), ("duplicate-limit", "notes.select(limit: 2, limit: 3)", "D101")]:
            self.cli(name, prefix + f'fn main() -> Result<List<Note>, DataError>:\n  {call}\n', reject=expected, command="check")
            self.count("selection compile rejections")
        for limit, offset in [(0, 0), (101, 0), (1, -1), (1, 10001)]:
            self.cli(f"selection-bounds-{limit}-{offset}", prefix + f'fn main() -> Result<List<Note>, DataError>:\n  notes.select(limit: {limit}, offset: {offset})\n', reject="invalid input", database=database)
            self.count("selection runtime rejections")
        self.cli("caught-missing", prefix + 'fn main() -> Text:\n  match notes.get(NoteId("missing")):\n    Ok(_) => "unexpected"\n    Err(error) => error_message(error)\n', "Access denied or resource not found.", database=database)
        transaction_database = self.directory / "transactions.sqlite"
        bodies = [('returned_transaction_error', "cancel this transaction"), ('caught_transaction_failure', "transaction aborted")]
        for name, expected in bodies:
            source = (ROOT / f'verification/regressions/{name}.dia').read_text()
            self.cli(name, source, reject=expected, database=transaction_database)
            with sqlite3.connect(transaction_database) as connection:
                assert connection.execute('SELECT count(*) FROM Notes').fetchone() == (0,)
            self.count("transaction rollback replays")
        success = prefix + 'fn main() -> Result<NoteId, DataError>:\n  transaction db:\n    let saved = notes.create(NewNote(title: "Committed"))?\n    Ok(saved.id)\n'
        self.cli("successful-transaction", success, database=transaction_database)
        with sqlite3.connect(transaction_database) as connection:
            assert connection.execute('SELECT title FROM Notes').fetchall() == [("Committed",)]
        self.count("transaction commit readbacks")
        nullable = prefix.replace('  visible: Bool = true', '  visible: Bool = true\n  subtitle: Option<Text> = None')
        nullable_database = self.directory / 'nullable.sqlite'
        self.cli("nullable-init", nullable, database=nullable_database, command="db-init")
        with sqlite3.connect(nullable_database) as connection:
            connection.executemany('INSERT INTO Notes (id,title,visible,subtitle) VALUES (?,?,?,?)', [('a','hidden',0,None),('b','visible',1,None),('c','visible',1,'雪')])
        for value, expected in [('None', ['b']), ('Some("雪")', ['c'])]:
            source = nullable + render + f'fn main() -> Result<Html, DataError>:\n  let values = notes.select(subtitle: {value}, limit: 5)?\n  Ok(render(values))\n'
            assert Items(self.cli("nullable-select", source, database=nullable_database).stdout).values == expected
            self.count("nullable authorized selections")

    def board(self) -> None:
        alice_password, bob_password = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
        provision(self.directory, "alice", alice_password)
        provision(self.directory, "bob", bob_password)
        server = Server(self.directory)
        try:
            server.start()
            alice, bob = Client(server), Client(server)
            alice.login("alice", alice_password)
            bob.login("bob", bob_password)
            title, text = 'Original <draft> 雪', 'Body <script> stays text.'
            path = alice.post(title, text)
            identifier = path.rsplit('/', 1)[1]
            assert title in ''.join(Page(alice.request('GET', '/')[2]).text)
            assert title not in ''.join(Page(bob.request('GET', '/')[2]).text)
            assert title not in ''.join(Page(server.raw('GET', '/posts')[2]).text)
            assert bob.request('GET', path + '/edit')[0] == 404
            status, _, edit = alice.request('GET', path + '/edit')
            assert status == 200 and b'value="Original &lt;draft&gt;' in edit
            before = rows(self.directory)
            status, _, failure = alice.request('POST', path + '/edit', {'title':'', 'body':'invalid', '_diamond_csrf':alice.csrf})
            assert status == 400 and 'Invalid post' in ''.join(Page(failure).text)
            assert rows(self.directory) == before
            new_title, new_body = 'Edited "title" & café', 'New body 雪\n<script>literal</script>'
            fields = {'title':new_title, 'body':new_body, '_diamond_csrf':alice.csrf}
            status, headers, _ = alice.request('POST', path + '/edit', fields)
            assert status == 303 and headers['Location'] == path
            assert rows(self.directory) == [(identifier, 'alice', new_title, new_body, 0)]
            assert new_title in ''.join(Page(alice.request('GET', path)[2]).text)
            status, _, public_body = alice.request('POST', path + '/publication', {'published':'true', '_diamond_csrf':alice.csrf})
            assert status == 303 and rows(self.directory)[0][-1] == 1
            assert server.raw('GET', path)[0] == 200
            assert new_title in ''.join(Page(server.raw('GET', '/posts')[2]).text)
            assert bob.request('GET', path)[0] == 200
            assert 'Edit post' not in ''.join(Page(bob.request('GET', path)[2]).text)
            before = rows(self.directory)
            for suffix, form in [('/edit', fields), ('/publication', {'published':'false'}), ('/delete', {})]:
                form = {**form, '_diamond_csrf':bob.csrf}
                assert bob.request('POST', path + suffix, form)[0] == 404
                assert rows(self.directory) == before
                self.count('rejected cross-user mutations')
            assert alice.request('POST', path + '/publication', {'published':'false','_diamond_csrf':alice.csrf})[0] == 303
            assert server.raw('GET', path)[0] == 404 and bob.request('GET', path)[0] == 404
            assert new_title not in ''.join(Page(server.raw('GET', '/posts')[2]).text)
            assert alice.request('POST', path + '/publication', {'published':'true','_diamond_csrf':alice.csrf})[0] == 303
            server.stop()
            server.start()
            assert new_title in ''.join(Page(server.raw('GET', path)[2]).text)
            alice.login('alice', alice_password)
            assert rows(self.directory) == [(identifier, 'alice', new_title, new_body, 1)]
            assert alice.request('POST', path + '/delete', {'_diamond_csrf':alice.csrf})[0] == 303
            assert rows(self.directory) == [] and server.raw('GET', path)[0] == 404
            assert new_title not in ''.join(Page(server.raw('GET', '/posts')[2]).text)
            assert alice.request('POST', '/_diamond/logout', {'_diamond_csrf':alice.csrf})[0] == 303
            assert alice.request('GET', '/')[0] == 401
            self.counts['complete board journeys'] = 1
            self.counts['board HTTP requests'] = server.requests
        finally:
            server.stop()


def main() -> None:
    directory = Path(tempfile.mkdtemp(prefix='diamond-checkpoint-three-'))
    campaign = Campaign(directory)
    try:
        campaign.language()
        campaign.selections_and_transactions()
        campaign.board()
    except Exception:
        print(f'Failure inputs and isolated data retained at {directory}', file=sys.stderr)
        raise
    else:
        shutil.rmtree(directory)
        print(json.dumps({'status':'PASS', 'seed':SEED, 'observations':campaign.counts}, indent=2))


if __name__ == '__main__':
    main()
