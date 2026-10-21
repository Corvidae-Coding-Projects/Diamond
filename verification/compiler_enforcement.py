#!/usr/bin/env python3
"""Bounded real-CLI checks for checkpoint-four compiler enforcement.

Expectations come from the authority budgets, read-only route contract, lexical
scope rules, and explicitly unsupported nested transactions/schema/form shapes.
No production module is imported. Every case invokes the actual Diamond binary.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import random
import shutil
import sqlite3
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
BINARY = Path(os.environ.get("DIAMOND_BINARY", ROOT / "target/debug/diamond")).resolve()
FIXTURES = ROOT / "verification/regressions/cp4_compiler"
SEED = 0xD1A04

SCHEMA = '''type ItemId = Id<"item">
type Item:
  id: ItemId
  title: Text
type NewItem:
  title: Text
type Number:
  value: Int
enum Amount:
  Count(value: Int)
resource Items: Item
policy Items:
  read(item, by principal):
    true
  create(proposed, by principal):
    true
  update(before, after, by principal):
    true
  delete(item, by principal):
    true
'''
WRITE = '''fn consume() -> Result<Int, DataError>:
  let saved = items.create(NewItem(title: "effect"))?
  Ok(7)
'''
READ = '''fn consume() -> Result<Int, DataError>:
  let selected = items.select(limit: 10)?
  Ok(len(selected))
'''


def application(grants: str = "DbRead<Items>, DbWrite<Items>") -> str:
    return "app Enforcement:\n  database SQLite\n" + (
        f"  provides {grants}\n" if grants else ""
    )


class Campaign:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.counts: Counter[str] = Counter()

    def invoke(self, label: str, source: str, *, diagnostic: str | None = None,
               command: str = "check", expected: str | None = None,
               database: Path | None = None, group: str = "compiler checks") -> None:
        path = self.directory / f"{label}.dia"
        path.write_text(source)
        arguments = [str(BINARY), command, str(path)]
        if database is not None:
            arguments += ["--database", str(database)]
        result = subprocess.run(arguments, cwd=ROOT, text=True, capture_output=True, timeout=8)
        assert result.returncode >= 0 and "panicked at" not in result.stderr, (label, result)
        if diagnostic is not None:
            assert result.returncode != 0 and diagnostic in result.stderr, (
                label, diagnostic, result.returncode, result.stdout, result.stderr
            )
        else:
            assert result.returncode == 0, (label, result.stderr)
            if expected is not None:
                assert result.stdout == expected + "\n", (label, expected, result.stdout)
        self.counts[group] += 1

    def regressions(self) -> None:
        for name, diagnostic in [
            ("no_database", "D301"),
            ("nested_direct", "nested transaction"),
            ("nested_call", "nested transaction"),
            ("nested_recursive", "nested transaction"),
            ("bad_schema", "database field"),
            ("bad_form", "form field"),
            ("csrf_form", "D117"),
            ("created_by_form", "D117"),
            ("case_columns", "distinct names"),
            ("derived_id", "generated id"),
        ]:
            self.invoke(name, (FIXTURES / f"{name}.dia").read_text(),
                        diagnostic=diagnostic, group="saved compile regressions")

    def effect_contexts(self) -> None:
        contexts = {
            "direct": "  consume()?\n",
            "argument": "  identity(consume()?)\n",
            "unary": "  -consume()?\n",
            "binary_left": "  consume()? + 2\n",
            "binary_right": "  2 + consume()?\n",
            "record_and_field": "  Number(value: consume()?).value\n",
            "variant_payload": "  match Count(consume()?):\n    Count(value) => value\n",
            "option_payload": "  match Some(consume()?):\n    Some(value) => value\n    None => 0\n",
            "result_payload": "  match Ok(consume()?):\n    Ok(value) => value\n    Err(_) => 0\n",
            "result_match": "  match consume():\n    Ok(value) => value\n    Err(_) => 0\n",
            "unused_match_arm": "  match false:\n    true => consume()?\n    false => 0\n",
            "list": "  len([consume()?])\n",
            "iteration_source": "  let values = for value in [consume()?]:\n    value\n  len(values)\n",
            "iteration_body": "  let values = for value in [1, 2]:\n    consume()?\n  len(values)\n",
            "empty_iteration_body": "  let values = for value in []:\n    consume()?\n  len(values)\n",
            "html_content": "  html:\n    <p>{consume()?}</p>\n",
            "html_attribute": "  html:\n    <p title={consume()?}>checked</p>\n",
            "route_url": "  route_url(Target, consume()?)\n",
            "selection_argument": "  let selected = items.select(limit: consume()?)?\n  len(selected)\n",
            "transaction": "  transaction db:\n    consume()?\n",
        }
        support = 'fn identity(value: Int) -> Int:\n  value\npublic route GET "/target/{id:Int}" as Target:\n  id\n'
        modes = [
            ("get", "GET", "DbRead<Items>, DbWrite<Items>", "D302"),
            ("head", "HEAD", "DbRead<Items>, DbWrite<Items>", "D302"),
            ("no_budget", "POST", "", "D301"),
            ("read_budget", "POST", "DbRead<Items>", "D301"),
            ("write_budget", "POST", "DbWrite<Items>", "D301"),
            ("permitted", "POST", "DbRead<Items>, DbWrite<Items>", None),
        ]
        for name, body in contexts.items():
            for mode, method, grants, diagnostic in modes:
                source = application(grants) + SCHEMA + WRITE + support
                source += f'public route {method} "/":\n' + body
                self.invoke(f"effect_{name}_{mode}", source, diagnostic=diagnostic,
                            group="expression and route authority checks")
            source = application("DbRead<Items>") + SCHEMA + READ + support
            source += 'public route GET "/":\n' + body
            self.invoke(f"read_context_{name}", source, group="permitted read-only expression contexts")
            opens = 'fn consume() -> Result<Int, DataError>:\n  transaction db:\n    Ok(7)\n'
            source = application() + SCHEMA + opens + support
            source += 'public route POST "/":\n  transaction db:\n'
            source += "".join("  " + line for line in body.splitlines(keepends=True))
            self.invoke(f"transaction_context_{name}", source, diagnostic="nested transaction",
                        group="transaction expression context checks")
        for method in ["GET", "HEAD"]:
            source = application("DbRead<Items>") + SCHEMA + READ
            source += f'public route {method} "/":\n  consume()?\n'
            self.invoke(f"read_{method}", source, group="permitted read-only routes")

    def operation_budgets(self) -> None:
        operations = {
            "get": ('  let found = items.get(ItemId("fixture"))?\n  Ok(len(found.title))\n', False, True),
            "select": ('  let selected = items.select(limit: 10)?\n  Ok(len(selected))\n', False, True),
            "create": ('  let saved = items.create(NewItem(title: "created"))?\n  Ok(len(saved.title))\n', True, True),
            "update": ('  let saved = items.update(ItemId("fixture"), NewItem(title: "updated"))?\n  Ok(len(saved.title))\n', True, True),
            "delete": ('  items.delete(ItemId("fixture"))?\n  Ok(7)\n', True, False),
        }
        for name, (body, writes, reads) in operations.items():
            for grants, grants_read, grants_write in [
                ("", False, False), ("DbRead<Items>", True, False),
                ("DbWrite<Items>", False, True), ("DbRead<Items>, DbWrite<Items>", True, True),
            ]:
                for method in ["GET", "HEAD", "POST"]:
                    diagnostic = "D301" if (reads and not grants_read) or (writes and not grants_write) else (
                        "D302" if writes and method != "POST" else None
                    )
                    source = application(grants) + SCHEMA + 'fn operation() -> Result<Int, DataError>:\n' + body
                    source += f'public route {method} "/":\n  operation()?\n'
                    self.invoke(f"operation_{name}_{grants_read}_{grants_write}_{method}", source,
                                diagnostic=diagnostic, group="CRUD operation authority checks")

    def call_graphs(self) -> None:
        generator = random.Random(SEED)
        for index in range(32):
            depth = generator.randrange(1, 13)
            names = [f"layer_{index}_{number}" for number in range(depth)]
            declarations = [WRITE]
            for number, name in enumerate(names):
                called = names[number + 1] if number + 1 < depth else "consume"
                # Every fourth graph includes a cycle with a reachable mutation.
                body = f"  {called}()\n"
                if index % 4 == 0 and number == depth - 1:
                    body = f"  match true:\n    true => consume()\n    false => {names[0]}()\n"
                declarations.append(f"fn {name}() -> Result<Int, DataError>:\n" + body)
            generator.shuffle(declarations)
            prefix = SCHEMA + "".join(declarations)
            for method in ["GET", "HEAD"]:
                source = application() + prefix + f'public route {method} "/":\n  {names[0]}()?\n'
                self.invoke(f"graph_{index}_{method}", source, diagnostic="D302",
                            group="generated transitive call graph checks")
            source = application("") + prefix
            self.invoke(f"graph_{index}_budget", source, diagnostic="D301",
                        group="generated transitive call graph checks")

    def scopes_and_sinks(self) -> None:
        for name, source, diagnostic in [
            ("match_escape", 'fn main() -> Int:\n  let value = match Some(7):\n    Some(inner) => inner\n    None => 0\n  inner\n', "D112"),
            ("iteration_escape", 'fn main() -> Int:\n  let values = for item in [7]:\n    item\n  item\n', "D112"),
            ("transaction_escape", application("") + 'fn main() -> Int:\n  transaction db:\n    let inside = 7\n  inside\n', "D112"),
            ("function_capture", 'fn helper() -> Int:\n  7\nfn main() -> Int:\n  let callable = helper\n  1\n', "D112"),
            ("scope_capture", 'fn main() -> Int:\n  let authority = [db]\n  1\n', "D112"),
            ("request_capture", 'public route GET "/":\n  let value = request\n  "unsafe"\n', "D112"),
            ("request_in_function", 'fn main() -> Bool:\n  request.authenticated\n', "D112"),
            ("html_record", 'type Payload:\n  value: Text\nfn main() -> Html:\n  html:\n    <p>{Payload(value: "secret")}</p>\n', "D121"),
            ("html_result", 'fn main() -> Html:\n  html:\n    <p>{Ok("hidden")}</p>\n', "D121"),
            ("html_scalar_list", 'fn main() -> Html:\n  html:\n    <p>{[1, 2]}</p>\n', "D121"),
            ("dynamic_link", 'fn main() -> Html:\n  let link = "/safe"\n  html:\n    <a href={link}>link</a>\n', "D121"),
            ("dynamic_redirect", 'public route POST "/":\n  let target = "/safe"\n  redirect target\n', "D111"),
        ]:
            self.invoke(name, source, diagnostic=diagnostic, group="scope and restricted sink checks")

        for ty in ["List<Int>", "Option<List<Int>>", "Option<Option<Int>>", "Html", "Result<Int, DataError>"]:
            source = application() + f'type Item:\n  id: Id<"item">\n  payload: {ty}\nresource Items: Item\n'
            self.invoke(f"schema_type_{len(self.counts)}_{ty[:4]}", source,
                        diagnostic="database field", group="schema and form shape checks")
            form = f'type Input:\n  payload: {ty}\npublic route POST "/":\n  let form = request.form<Input>()?\n  "ok"\n'
            self.invoke(f"form_type_{len(self.counts)}_{ty[:4]}", form,
                        diagnostic="form field", group="schema and form shape checks")
        self.invoke("ordinary_records", 'type Payload:\n  nested: List<Option<Int>>\n  id: Int\n  ID: Text\nfn main() -> Int:\n  7\n',
                    expected="7", command="run", group="permitted ordinary values")
        scalar_form = 'type Input:\n  number: Int\n  flag: Bool\n  text: Text<length: 1..5>\n  identifier: Id<"item">\n  optional: Option<Text>\npublic route POST "/":\n  let form = request.form<Input>()?\n  "ok"\n'
        self.invoke("scalar_form", scalar_form, group="permitted form shapes")

    def transaction_graphs(self) -> None:
        generator = random.Random(SEED + 1)
        for index in range(16):
            depth = generator.randrange(1, 9)
            functions = ['fn opens() -> Int:\n  transaction db:\n    7\n']
            called = "opens"
            for number in range(depth):
                name = f"wrapper_{number}"
                functions.append(f"fn {name}() -> Int:\n  {called}()\n")
                called = name
            generator.shuffle(functions)
            prefix = application("") + "".join(functions)
            self.invoke(f"transaction_graph_{index}", prefix + f'fn main() -> Int:\n  transaction db:\n    {called}()\n',
                        diagnostic="nested transaction", group="generated transaction graph checks")
            self.invoke(f"route_transaction_graph_{index}", prefix + f'public route POST "/":\n  transaction db:\n    {called}()\n',
                        diagnostic="nested transaction", group="generated transaction graph checks")
        database = self.directory / "transactions.sqlite"
        prefix = application() + SCHEMA
        # Empty transactions require a host database but no invented resource effect.
        source = application("") + 'fn once() -> Int:\n  transaction db:\n    7\nfn main() -> Int:\n  let first = once()\n  first + once()\n'
        self.invoke("sequential_empty_transactions", source, command="run", expected="14",
                    database=database, group="real transaction readbacks")
        self.invoke("empty_transaction_without_host", source, command="run", diagnostic="access denied",
                    group="host authority rejection")
        source = application("") + 'fn recurse(value: Bool) -> Int:\n  match value:\n    true => recurse(false)\n    false =>\n      transaction db:\n        9\nfn main() -> Int:\n  recurse(true)\n'
        self.invoke("nonnested_recursive_transaction", source, command="run", expected="9",
                    database=database, group="real transaction readbacks")
        source = prefix + WRITE + 'fn main() -> Result<Int, DataError>:\n  transaction db:\n    let value = consume()?\n    Ok(value)\n'
        self.invoke("permitted_transaction_call", source, command="run", expected="7",
                    database=database, group="real transaction readbacks")
        with sqlite3.connect(database) as connection:
            assert connection.execute('SELECT title FROM Items').fetchall() == [("effect",)]
        source = application("DbRead<Items>") + SCHEMA + READ + 'fn main() -> Result<Int, DataError>:\n  transaction db:\n    let count = consume()?\n    Ok(count)\n'
        self.invoke("read_only_transaction", source, command="run", expected="1",
                    database=database, group="real transaction readbacks")
        self.invoke("missing_host_database", source, command="run", diagnostic="access denied",
                    group="host authority rejection")
        for source_path in sorted((ROOT / "examples").glob("*.dia")):
            self.invoke(f"example_{source_path.stem}", source_path.read_text(),
                        group="existing example compile checks")


def main() -> None:
    directory = Path(tempfile.mkdtemp(prefix="diamond-compiler-enforcement-"))
    campaign = Campaign(directory)
    try:
        campaign.regressions()
        campaign.effect_contexts()
        campaign.operation_budgets()
        campaign.call_graphs()
        campaign.scopes_and_sinks()
        campaign.transaction_graphs()
    except BaseException:
        print(f"Failure inputs retained at {directory}")
        raise
    else:
        shutil.rmtree(directory)
        print(json.dumps({
            "status": "PASS", "seed": SEED,
            "binary_sha256": hashlib.sha256(BINARY.read_bytes()).hexdigest(),
            "compiler_source_sha256": {
                name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                for name in ["src/checker.rs", "src/hir.rs", "verification/compiler_enforcement.py"]
            },
            "observations": campaign.counts, "total_cli_invocations": sum(campaign.counts.values()),
        }, indent=2))


if __name__ == "__main__":
    main()
