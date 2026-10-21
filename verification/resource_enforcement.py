#!/usr/bin/env python3
"""Run actual Diamond value-limit, SQLite, and rollback/reuse journeys.

All child interpreters are bounded to 512 MiB address space, 5 CPU seconds, no core
files. These OS bounds protect the verifier; a signal/abort is always a failure
on the current interpreter. Expectations are ordinary results for modest values,
a returned Error::Limit for excessive values/copy work, unchanged SQLite state
when an explicit transaction fails, and successful same-server reuse afterward.
No implementation is substituted. Temporary databases/accounts are disposable.
"""
from __future__ import annotations

import argparse
import hashlib
import http.client
import json
from pathlib import Path
import random
import re
import resource
import select
import shutil
import sqlite3
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SEED = 0xD1A05
TREE = 'type Tree:\n  left: Option<Tree>\n  right: Option<Tree>\n'
PREFIX = '''app ResourceReplay:
  database SQLite
  provides DbRead<Notes>, DbWrite<Notes>
type NoteId = Id<"note">
type Note:
  id: NoteId
  title: Text
  visible: Bool = true
type NewNote:
  title: Text
resource Notes: Note
policy Notes:
  read(note, by principal):
    true
  create(proposed, by principal):
    true
'''


def bounded() -> None:
    resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024,) * 2)
    resource.setrlimit(resource.RLIMIT_CPU, (5, 5))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def invoke(binary: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([str(binary), *arguments], cwd=ROOT, text=True,
                          capture_output=True, timeout=10, preexec_fn=bounded)


def tree_lines(count: int, indent: str = '  ') -> str:
    result = [indent + 'let node0 = Tree(left: None, right: None)']
    result += [indent + f'let node{n} = Tree(left: Some(node{n-1}), right: Some(node{n-1}))'
               for n in range(1, count)]
    return '\n'.join(result) + '\n'


def text_lines(size: int, doublings: int = 0, indent: str = '  ') -> str:
    result = indent + f'let text0 = {json.dumps("x" * size)}\n'
    for n in range(1, doublings + 1):
        result += indent + f'let text{n} = text{n-1} + text{n-1}\n'
    return result


class Campaign:
    def __init__(self, binary: Path, directory: Path):
        self.binary, self.directory = binary, directory
        self.cli = self.sqlite = self.http = 0
        self.cases: list[str] = []
        self.runtime_work_cases: list[str] = []

    def run_source(self, name: str, source: str, expected: str | None,
                   database: Path | None = None, *, limit_reason: str | None = None) -> None:
        path = self.directory / f'{name}.dia'
        path.write_text(source)
        if limit_reason:
            checked = invoke(self.binary, 'check', str(path))
            self.cli += 1
            assert checked.returncode == 0, (name, 'must compile before runtime-limit verification', checked)
        arguments = ['run', str(path)]
        if database:
            arguments += ['--database', str(database)]
        observed = invoke(self.binary, *arguments)
        self.cli += 1
        self.cases.append(name)
        assert observed.returncode >= 0, (name, 'process signal', observed.returncode, observed.stderr)
        if expected is None:
            assert observed.returncode == 1 and not observed.stdout, (name, observed)
            assert 'resource limit exceeded:' in observed.stderr, (name, observed.stderr)
            if limit_reason:
                assert f'resource limit exceeded: {limit_reason}' in observed.stderr, (name, observed.stderr)
                self.runtime_work_cases.append(name)
            assert 'panicked at' not in observed.stderr, (name, observed.stderr)
        else:
            assert (observed.returncode, observed.stdout, observed.stderr) == (0, expected + '\n', ''), (name, observed)

    def init(self, name: str, source: str) -> tuple[Path, Path]:
        path, database = self.directory / f'{name}.dia', self.directory / f'{name}.sqlite'
        path.write_text(source)
        result = invoke(self.binary, 'db-init', str(path), '--database', str(database))
        assert result.returncode == 0, result.stderr
        self.cli += 1
        return path, database

    def generated(self) -> None:
        rng = random.Random(SEED)
        for count in rng.sample(list(range(1, 13)), 12):
            self.run_source(f'tree-{count}', TREE + 'fn main() -> Int:\n' + tree_lines(count) + '  7\n', '7')
        fixture = ROOT / 'verification/regressions/cp5_resource/recursive_record.dia'
        self.run_source('recursive-record-regression', fixture.read_text(), None)
        for count, expected in [(32, '9'), (33, None)]:
            lines = ['type Link:', '  next: Option<Link>', 'fn main() -> Int:', '  let node0 = Link(next: None)']
            lines += [f'  let node{n} = Link(next: Some(node{n-1}))' for n in range(1, count)]
            self.run_source(f'linear-depth-{count}', '\n'.join(lines) + '\n  9\n', expected)
        for count, expected in [(0, '0'), (1, '1'), (3, '3'), (7, '7'), (8, None)]:
            source = 'fn main() -> Int:\n' + text_lines(1024, 9)
            # Empty lists have no inferable element type in this MVP; the zero
            # case exercises the empty iterator result through a typed source.
            if count == 0:
                source += '  let values = for item in [1]:\n    0\n  len(values) - 1\n'
            else:
                source += '  let values = [' + ', '.join(['text9'] * count) + ']\n  len(values)\n'
            self.run_source(f'list-payload-{count}', source, expected)
        source = 'fn main() -> Int:\n' + text_lines(1024, 6)
        source += '  let indices = [' + ', '.join(map(str, range(100))) + ']\n'
        source += '  let values = for batch in [0, 1, 2, 3, 4, 5, 6, 7, 8, 9]:\n    for item in indices:\n      let copied = text6\n      item\n  len(values)\n'
        self.run_source('iteration-scope-copy-work', source, None, limit_reason='managed value work')
        source = TREE + 'fn identity(value: Tree) -> Tree:\n  value\nfn main() -> Int:\n' + tree_lines(11)
        source += '  let values = for item in [' + ', '.join(map(str, range(150))) + ']:\n    let copied = identity(node10)\n    item\n  len(values)\n'
        self.run_source('parameter-and-scope-copy-work', source, None, limit_reason='managed value work')
        source = 'fn main() -> Int:\n' + text_lines(1024, 6)
        source += '  let values = for item in [' + ', '.join(map(str, range(180))) + ']:\n    match true:\n      true => len(text6)\n      false => 0\n  len(values)\n'
        self.run_source('branch-scope-copy-work', source, None, limit_reason='managed value work')
        source = TREE + 'fn grow() -> Result<Int, DataError>:\n' + tree_lines(18) + '  Ok(1)\nfn main() -> Int:\n  match grow():\n    Ok(value) => value\n    Err(problem) => 99\n'
        self.run_source('result-cannot-catch-resource-limit', source, None)
        source = TREE + 'fn main() -> Int:\n' + tree_lines(8) + '  match Some(node7):\n    Some(value) => match value.left:\n      Some(child) => 31\n      None => 32\n    None => 33\n'
        self.run_source('owned-option-patterns', source, '31')
        source = 'enum Choice:\n  Number(value: Int)\n  Missing\nfn main() -> Int:\n  match Number(37):\n    Number(value) => value\n    Missing => 0\n'
        self.run_source('owned-enum-patterns', source, '37')
        # Err's inner type cannot be inferred on its own; use a typed producer.
        source = 'fn ordinary() -> Result<Int, DataError>:\n  Err("ordinary")\nfn main() -> Int:\n  match ordinary():\n    Ok(value) => 1\n    Err(problem) => len(error_message(problem))\n'
        self.run_source('ordinary-result-remains-catchable', source, '8')

    def database(self) -> None:
        source = PREFIX + 'fn main() -> Result<Int, DataError>:\n  let values = notes.select(limit: 100)?\n  Ok(len(values))\n'
        _, database = self.init('selection-size', source)
        with sqlite3.connect(database) as connection:
            connection.executemany('INSERT INTO Notes VALUES(?,?,?)', [(f'{n:064x}', 'a' * 65_536, 1) for n in range(80)])
        self.run_source('selection-aggregate-size', source, None, database)
        self.sqlite += 1
        with sqlite3.connect(database) as connection:
            assert connection.execute('SELECT count(*),sum(length(title)) FROM Notes').fetchone() == (80, 80 * 65_536)
            connection.execute('DELETE FROM Notes WHERE id > ?', (f'{9:064x}',))
        self.run_source('selection-modest-readback', source, '10', database)
        self.sqlite += 1
        row_source = '''app Wide:
  database SQLite
  provides DbRead<Items>
type ItemId = Id<"item">
type Item:
  id: ItemId
  a: Text
  b: Text
  c: Text
  d: Text
  e: Option<Text>
resource Items: Item
policy Items:
  read(item, by principal):
    true
fn main() -> Result<Int, DataError>:
  let found = items.get(ItemId("''' + '1' * 64 + '''"))?
  Ok(len(found.a))
'''
        _, database = self.init('wide-stored-row', row_source)
        with sqlite3.connect(database) as connection:
            connection.execute('INSERT INTO Items(id,a,b,c,d,e) VALUES(?,?,?,?,?,?)', ('1' * 64, *(['z' * 900_000] * 5)))
        self.run_source('stored-row-size-preflight', row_source, None, database)
        self.sqlite += 1
        create_source = '''app WideCreate:
  database SQLite
  provides DbRead<Items>, DbWrite<Items>
type ItemId = Id<"item">
type Item:
  id: ItemId
  a: Text
  b: Text
  c: Text
  d: Text
type NewItem:
  a: Text
  b: Text
  c: Text
  d: Text
resource Items: Item
policy Items:
  read(item, by principal):
    true
  create(item, by principal):
    true
fn main() -> Result<Int, DataError>:
''' + text_lines(1023, 10)
        create_source += '  let payload = text10 + ' + json.dumps('x' * 848) + '\n'
        create_source += '  let saved = items.create(NewItem(a: payload, b: payload, c: payload, d: payload))?\n  Ok(1)\n'
        _, create_database = self.init('created-row-size', create_source)
        self.run_source('created-row-size-before-commit', create_source, None, create_database)
        with sqlite3.connect(create_database) as connection:
            assert connection.execute('SELECT count(*) FROM Items').fetchone() == (0,)
        self.sqlite += 1
        update_source = create_source.replace('  create(item, by principal):\n    true\n', '  create(item, by principal):\n    true\n  update(before, after, by principal):\n    true\n')
        update_source = update_source.replace('items.create(NewItem(', 'items.update(ItemId("' + '1' * 64 + '"), NewItem(')
        _, update_database = self.init('updated-row-size', update_source)
        with sqlite3.connect(update_database) as connection:
            connection.execute('INSERT INTO Items(id,a,b,c,d) VALUES(?,?,?,?,?)', ('1' * 64, 'old-a', 'old-b', 'old-c', 'old-d'))
        self.run_source('updated-row-size-before-commit', update_source, None, update_database)
        with sqlite3.connect(update_database) as connection:
            assert connection.execute('SELECT a,b,c,d FROM Items').fetchall() == [('old-a', 'old-b', 'old-c', 'old-d')]
        self.sqlite += 1
        _, database = self.init('relation-work', source)
        with sqlite3.connect(database) as connection:
            connection.executemany('INSERT INTO Notes VALUES(?,?,?)', ((f'{n:064x}', 'b' * 7000, 1) for n in range(10_000)))
        self.run_source('stored-relation-materialization-work', source, None, database)
        self.sqlite += 1
        with sqlite3.connect(database) as connection:
            assert connection.execute('SELECT count(*) FROM Notes').fetchone() == (10_000,)
            assert connection.execute('PRAGMA integrity_check').fetchone() == ('ok',)

    def rollback(self) -> None:
        source = PREFIX + TREE + 'public route POST "/limited":\n  transaction db:\n    let saved = notes.create(NewNote(title: "must-roll-back"))?\n' + tree_lines(18, '    ') + '    "unreachable"\n'
        source += '\npublic route POST "/save":\n  let saved = notes.create(NewNote(title: "committed-after-limit"))?\n  "saved"\npublic route GET "/read":\n  let values = notes.select(limit: 100)?\n  html:\n    <p>{len(values)}</p>\n'
        path, database = self.init('rollback-reuse', source)
        process = subprocess.Popen([str(self.binary), 'run', str(path), '--database', str(database), '--listen', '127.0.0.1:0'], cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, preexec_fn=bounded)
        try:
            assert process.stderr and select.select([process.stderr], [], [], 8)[0], 'startup timeout'
            line = process.stderr.readline()
            match = re.search(r'http://localhost:(\d+)', line)
            assert match, line
            port = int(match[1])
            def request(method: str, path: str) -> tuple[int, str]:
                connection = http.client.HTTPConnection('127.0.0.1', port, timeout=8)
                try:
                    connection.request(method, path, headers={'Host':f'localhost:{port}', 'Origin':f'http://localhost:{port}'})
                    response = connection.getresponse()
                    self.http += 1
                    return response.status, response.read().decode().strip()
                finally:
                    connection.close()
            for _ in range(3):
                assert request('POST', '/limited') == (413, 'Resource limit exceeded')
                with sqlite3.connect(database) as connection:
                    assert connection.execute('SELECT count(*) FROM Notes').fetchone() == (0,)
                self.sqlite += 1
                assert request('GET', '/read') == (200, '<p>0</p>')
            assert request('POST', '/save') == (200, 'saved')
            assert request('GET', '/read') == (200, '<p>1</p>')
            with sqlite3.connect(database) as connection:
                assert connection.execute('SELECT title FROM Notes').fetchall() == [('committed-after-limit',)]
                assert connection.execute('PRAGMA integrity_check').fetchone() == ('ok',)
            self.sqlite += 1
        finally:
            if process.poll() is None:
                process.terminate()
            out, err = process.communicate(timeout=3)
            assert not out and 'panicked at' not in err, (process.returncode, out, err)
        self.run_source('committed-state-fresh-process', PREFIX + 'fn main() -> Result<Int, DataError>:\n  let values = notes.select(limit: 100)?\n  Ok(len(values))\n', '1', database)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=Path, default=ROOT/'target/debug/diamond')
    parser.add_argument('--baseline-only', action='store_true')
    options = parser.parse_args()
    binary = options.binary.resolve()
    if options.baseline_only:
        observed = invoke(binary, 'run', str(ROOT/'verification/regressions/cp5_resource/recursive_record.dia'))
        print(json.dumps({'binary_sha256':hashlib.sha256(binary.read_bytes()).hexdigest(), 'exit':observed.returncode, 'stdout':observed.stdout, 'stderr':observed.stderr}, indent=2))
        assert observed.returncode < 0 and 'memory allocation' in observed.stderr, observed
        return
    directory = Path(tempfile.mkdtemp(prefix='diamond-resource-enforcement-'))
    try:
        campaign = Campaign(binary, directory)
        campaign.generated()
        campaign.database()
        campaign.rollback()
        print(json.dumps({'seed':SEED, 'binary_sha256':hashlib.sha256(binary.read_bytes()).hexdigest(), 'cli_checks':campaign.cli, 'sqlite_checks':campaign.sqlite, 'http_requests':campaign.http, 'generated_and_regression_cases':campaign.cases, 'compile_pass_then_runtime_work_limit':campaign.runtime_work_cases, 'bounds':{'child_address_space_mib':512,'child_cpu_seconds':5,'managed_value_mib':4,'managed_depth':64,'evaluation_value_work_mib':64}},indent=2))
    except BaseException:
        print(f'Failure data retained at {directory}')
        raise
    else:
        shutil.rmtree(directory)


if __name__ == '__main__':
    main()
