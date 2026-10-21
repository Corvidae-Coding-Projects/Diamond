#!/usr/bin/env python3
"""Replay the independent W1 module/artifact exploration through the actual CLI.

Derived from the independent verifier's disposable exploration, including its
discovered qualification/visibility failures. Expected outcomes are language
requirements, not a comparison against the compiler's own checker.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
MIB = 1024 * 1024
MANIFEST = '''schema_version = 1
name = "Verify"
server = "server"
browser = "ui"
component = "Counter"

[modules.shared]
path = "shared.dia"
target = "shared"

[modules.server]
path = "server.dia"
target = "server"
imports = ["shared"]

[modules.ui]
path = "ui.dia"
target = "browser"
imports = ["shared"]
'''
SHARED = '''export fn amount(value: Int) -> Int:
  value + 7

fn private_value() -> Int:
  99183
'''
SERVER = '''export fn main() -> Int:
  amount(10)

fn server_secret() -> Text:
  "INDEPENDENT_SERVER_CANARY_34282"
'''
UI = '''export component Counter:
  state count: Int = amount(2)
  on increment:
    count = amount(count)
  html:
    <main><strong>{count}</strong><button type="button" on-click={increment}>Add</button></main>
'''


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def replace(folder: Path, file: str, old: str, new: str) -> None:
    path = folder / file
    text = path.read_text()
    assert old in text, (file, old)
    path.write_text(text.replace(old, new))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, default=ROOT / "target/debug/diamond")
    parser.add_argument("--report", type=Path, default=ROOT / "verification/web_one_modules_results.json")
    options = parser.parse_args()
    binary = options.binary.resolve()
    binary_hash = sha(binary)
    rows: list[dict] = []
    directory = Path(tempfile.mkdtemp(prefix="diamond-w1-modules-"))

    def observe(label: str, passed: bool, **details) -> None:
        rows.append(dict(label=label, accepted=passed, **details))
        if not passed:
            raise AssertionError(f"unexpected outcome: {label}")

    def cli(label: str, path: Path, success=True, command="check", args=(), output=None) -> None:
        argv = [str(binary), command, str(path), *map(str, args)]
        value = subprocess.run(argv, text=True, capture_output=True, timeout=20)
        observe(label, (value.returncode == 0) == success and (output is None or value.stdout == output),
                command=argv, expected_success=success, expected_stdout=output,
                exit=value.returncode, stdout=value.stdout, stderr=value.stderr)

    base = directory / "base"
    base.mkdir()
    for name, content in [("diamond.toml", MANIFEST), ("shared.dia", SHARED),
                          ("server.dia", SERVER), ("ui.dia", UI)]:
        (base / name).write_text(content)

    def fixture(label: str, mutate, success=False) -> Path:
        folder = directory / label
        shutil.copytree(base, folder)
        mutate(folder)
        cli(label, folder / "diamond.toml", success)
        return folder

    def manifest(folder, old, new):
        replace(folder, "diamond.toml", old, new)

    def authority(folder, app=True, module=True, browser=False):
        if app:
            manifest(folder, 'component = "Counter"', 'component = "Counter"\nprovides = ["PostgresRead"]')
        if module:
            manifest(folder, 'target = "server"', 'target = "server"\nprovides = ["PostgresRead"]')
        if browser:
            manifest(folder, 'target = "browser"', 'target = "browser"\nprovides = ["PostgresRead"]')
        (folder / "server.dia").write_text('export fn main() -> Text:\n  host.postgres_version()\n')

    error = None
    try:
        cli("base-check", base / "diamond.toml")
        cli("base-run", base / "diamond.toml", command="run", output="17\n")
        for label, file, old, new in [
            ("private-export", "server.dia", "amount(10)", "private_value()"),
            ("missing-function", "server.dia", "amount(10)", "missing_value()"),
            ("missing-import", "diamond.toml", 'imports = ["shared"]', 'imports = []'),
            ("duplicate-import", "diamond.toml", 'imports = ["shared"]', 'imports = ["shared", "shared"]'),
            ("unknown-module-import", "diamond.toml", 'imports = ["shared"]', 'imports = ["absent"]'),
            ("cycle", "diamond.toml", 'target = "shared"', 'target = "shared"\nimports = ["shared"]'),
            ("browser-server-boundary", "diamond.toml", 'target = "browser"\nimports = ["shared"]', 'target = "browser"\nimports = ["server", "shared"]'),
            ("server-browser-boundary", "diamond.toml", 'target = "server"\nimports = ["shared"]', 'target = "server"\nimports = ["ui", "shared"]'),
            ("invalid-target", "diamond.toml", 'target = "shared"', 'target = "worker"'),
            ("wrong-entry-target", "diamond.toml", 'server = "server"', 'server = "shared"'),
            ("private-component", "ui.dia", 'export component', 'component'),
            ("private-main", "server.dia", 'export fn main', 'fn main'),
            ("module-budget-exceeds-app", "diamond.toml", 'target = "server"', 'target = "server"\nprovides = ["PostgresRead"]'),
            ("browser-host-source", "ui.dia", 'state count: Int = amount(2)', 'state count: Text = host.postgres_version()'),
            ("shared-host-source", "shared.dia", 'fn private_value() -> Int:\n  99183', 'fn private_value() -> Text:\n  host.postgres_version()'),
            ("unknown-app-budget", "diamond.toml", 'component = "Counter"', 'component = "Counter"\nprovides = ["ProcessExec"]'),
            ("parent-path", "diamond.toml", 'path = "shared.dia"', 'path = "../shared.dia"'),
            ("absolute-path", "diamond.toml", 'path = "shared.dia"', 'path = "/tmp/shared.dia"'),
            ("backslash-path", "diamond.toml", 'path = "shared.dia"', "path = 'folder\\shared.dia'"),
            ("reserved-module", "diamond.toml", '[modules.shared]', '[modules.__diamond_shared]'),
            ("reserved-function", "shared.dia", 'private_value', '__diamond_private_value'),
            ("reserved-state", "ui.dia", 'count', '__diamond_state'),
            ("host-state", "ui.dia", 'count', 'host'),
            ("duplicate-state", "ui.dia", 'state count: Int = amount(2)', 'state count: Int = amount(2)\n  state count: Int = 77'),
            ("duplicate-event", "ui.dia", '  html:', '  on increment:\n    count = 5\n  html:'),
            ("duplicate-function", "shared.dia", 'fn private_value() -> Int:', 'fn private_value() -> Int:\n  1\n\nfn private_value() -> Int:'),
            ("local-import-ambiguity", "server.dia", 'export fn main()', 'fn amount(value: Int) -> Int:\n  value + 100\n\nexport fn main()'),
        ]:
            fixture(label, lambda p, f=file, a=old, b=new: replace(p, f, a, b))

        for label, kwargs, success in [
            ("browser-host-budget", dict(browser=True), False),
            ("missing-module-budget", dict(module=False), False),
            ("missing-app-budget", dict(app=False, module=False), False),
            ("host-source-check", {}, True),
        ]:
            folder = fixture(label, lambda p, kw=kwargs: authority(p, **kw), success)
            if success:
                cli("missing-runtime-host-grant", folder / "diamond.toml", False, command="run")

        outside = directory / "outside.dia"
        outside.write_text(SHARED)
        def special_source(folder, kind):
            path = folder / "shared.dia"
            path.unlink()
            if kind == "symlink": path.symlink_to(outside)
            elif kind == "fifo": os.mkfifo(path)
            else: path.mkdir()
        for kind in ["symlink", "fifo", "directory"]:
            fixture(kind + "-source", lambda p, k=kind: special_source(p, k))
        fixture("invalid-source-utf8", lambda p: (p / "shared.dia").write_bytes(b"\xff"))

        def ambiguous(folder):
            (folder / "other.dia").write_text('export fn amount(value: Int) -> Int:\n  value + 90\n')
            manifest(folder, 'imports = ["shared"]', 'imports = ["shared", "other"]')
            with (folder / "diamond.toml").open("a") as stream:
                stream.write('\n[modules.other]\npath = "other.dia"\ntarget = "shared"\n')
        fixture("import-import-ambiguity", ambiguous)

        for name in ["constructor", "prototype", "__proto__", "toString", "valueOf", "then",
                     "null", "undefined", "arguments", "eval", "await", "class"]:
            fixture("state-name-" + name, lambda p, n=name: replace(p, "ui.dia", "count", n), True)
        for name in ["constructor", "prototype", "__proto__", "toString", "valueOf", "then",
                     "arguments", "eval", "await", "class"]:
            def rename(folder, n=name):
                for file in ["shared.dia", "server.dia", "ui.dia"]:
                    replace(folder, file, "amount", n)
            fixture("function-name-" + name, rename, True)

        def pad(path, size):
            content = path.read_bytes()
            assert size >= len(content) + 2
            path.write_bytes(content + b"#" + b"x" * (size - len(content) - 2) + b"\n")
        for size in [MIB - 1, MIB, MIB + 1]:
            fixture(f"source-size-{size}", lambda p, n=size: pad(p / "shared.dia", n), size <= MIB)
        for size in [MIB, MIB + 1]:
            fixture(f"manifest-size-{size}", lambda p, n=size: pad(p / "diamond.toml", n), size <= MIB)
        for size in [128, 129]:
            def long_name(folder, n="m" * size):
                manifest(folder, '[modules.shared]', '[modules.' + n + ']')
                manifest(folder, '["shared"]', '["' + n + '"]')
            fixture(f"module-name-{size}", long_name, size <= 128)
        def add_modules(folder, count):
            names = [f"f{i}" for i in range(count)]
            manifest(folder, 'imports = ["shared"]', 'imports = ' + json.dumps(["shared"] + names))
            with (folder / "diamond.toml").open("a") as stream:
                for name in names:
                    stream.write(f'\n[modules.{name}]\npath = "{name}.dia"\ntarget = "shared"\n')
                    (folder / (name + ".dia")).write_text('fn filler() -> Int:\n  1\n')
        for count in [64, 65]:
            fixture(f"module-count-{count}", lambda p, n=count: add_modules(p, n - 3), count <= 64)
        for size in [8 * MIB, 8 * MIB + 1]:
            def aggregate(folder, total=size):
                add_modules(folder, 6)
                current = sum(f.stat().st_size for f in folder.iterdir())
                for file in sorted(folder.glob("*.dia")):
                    grow = min(MIB - file.stat().st_size, total - current)
                    if grow >= 2:
                        pad(file, file.stat().st_size + grow)
                        current += grow
                assert current == total
            fixture(f"project-total-{size}", aggregate, size <= 8 * MIB)
        for count in [4096, 4097]:
            def declarations(folder, n=count):
                # Base has five declarations including the component.
                with (folder / "shared.dia").open("a") as stream:
                    for i in range(n - 5): stream.write(f'\nfn filler_{i}() -> Int:\n  1\n')
            fixture(f"declarations-{count}", declarations, count <= 4096)

        for label, shared, success, output in [
            ("let-shadowing", 'export fn amount(value: Int) -> Int:\n  let x = value + 1\n  let x = x + 2\n  x + 4\n', False, None),
            ("param-shadowing", 'export fn amount(value: Int) -> Int:\n  let value = value + 7\n  value\n', False, None),
            ("call-shadowing", 'export fn amount(value: Int) -> Int:\n  let amount = value + 7\n  amount\n', True, "17\n"),
            ("duplicate-param", 'export fn amount(value: Int, value: Int) -> Int:\n  value + 7\n', False, None),
        ]:
            folder = directory / label
            shutil.copytree(base, folder)
            (folder / "shared.dia").write_text(shared)
            if label == "duplicate-param": replace(folder, "server.dia", "amount(10)", "amount(10, 20)")
            for command in ["check", "run", "build"]:
                cli(label + "-" + command, folder / "diamond.toml", success, command,
                    args=("--out", folder / "build") if command == "build" else (),
                    output=output if command == "run" else None)

        private = directory / "own-private-normal"
        shutil.copytree(base, private)
        replace(private, "shared.dia", "value + 7", "value + private_value()")
        cli("own-private-normal", private / "diamond.toml", command="run", output="99193\n")
        transitive = directory / "transitive-effect-budget"
        shutil.copytree(base, transitive)
        manifest(transitive, 'component = "Counter"', 'component = "Counter"\nprovides = ["PostgresRead"]')
        manifest(transitive, 'target = "server"\nimports = ["shared"]', 'target = "server"\nimports = ["shared", "worker"]')
        with (transitive / "diamond.toml").open("a") as stream:
            stream.write('\n[modules.worker]\npath = "worker.dia"\ntarget = "server"\nprovides = ["PostgresRead"]\n')
        (transitive / "worker.dia").write_text('export fn version() -> Text:\n  host.postgres_version()\n')
        (transitive / "server.dia").write_text('export fn main() -> Text:\n  version()\n')
        cli("transitive-effect-denied", transitive / "diamond.toml", False)
        manifest(transitive, 'target = "server"\nimports', 'target = "server"\nprovides = ["PostgresRead"]\nimports')
        cli("transitive-effect-allowed", transitive / "diamond.toml")
        cli("transitive-runtime-grant-missing", transitive / "diamond.toml", False, command="run")

        one, two = directory / "build-one", directory / "build-two"
        for folder in [one, two]:
            cli(folder.name, base / "diamond.toml", command="build", args=("--out", folder))
        def tree(folder):
            return {str(f.relative_to(folder)): sha(f) for f in folder.rglob("*") if f.is_file()}
        observe("deterministic-tree", tree(one) == tree(two), files=tree(one))
        contract = json.loads((one / "contract.json").read_text())
        hashes = {key: sha(one / key) == value for key, value in contract["artifacts"].items()}
        observe("artifact-hashes", all(hashes.values()), files=hashes)
        server = json.loads((one / "server.json").read_text())
        encoded = json.dumps([server["manifest"], dict(sorted(server["sources"].items()))],
                             ensure_ascii=False, separators=(",", ":")).encode()
        observe("input-hash", hashlib.sha256(encoded).hexdigest() == contract["input_hash"])
        js = next((one / "assets").glob("*.js")).read_text()
        observe("browser-excludes-server", "INDEPENDENT_SERVER_CANARY_34282" not in js)
        observe("browser-excludes-unused-shared", "private_value" not in js and "99183" not in js)
        cli("load-build-check", one)
        cli("load-build-run", one, command="run", output="17\n")
        cli("existing-output-refused", base / "diamond.toml", False, command="build", args=("--out", one))
        def edit_json(folder, key, value):
            path = folder / "server.json"
            data = json.loads(path.read_text())
            data[key] = value
            path.write_text(json.dumps(data))
        for label, mutate in [
            ("tamper-page", lambda p: (p / "index.html").write_text("tampered")),
            ("tamper-js", lambda p: next((p / "assets").glob("*.js")).write_text("tampered")),
            ("tamper-contract", lambda p: (p / "contract.json").write_text("{}")),
            ("server-version", lambda p: edit_json(p, "language_version", "0.0.0")),
            ("server-format", lambda p: edit_json(p, "format_version", 99)),
            ("server-unknown-field", lambda p: edit_json(p, "checked", True)),
            ("tamper-source", lambda p: edit_json(p, "sources", {**server["sources"], "server.dia": "export fn main() -> Int:\n  9187\n"})),
            ("invalid-source", lambda p: edit_json(p, "sources", {**server["sources"], "server.dia": "export fn main() -> Int:\n  host.postgres_version()\n"})),
            ("missing-page", lambda p: (p / "index.html").unlink()),
            ("artifact-fifo", lambda p: ((p / "contract.json").unlink(), os.mkfifo(p / "contract.json"))),
            ("artifact-symlink", lambda p: ((p / "contract.json").unlink(), (p / "contract.json").symlink_to(one / "contract.json"))),
        ]:
            folder = directory / label
            shutil.copytree(one, folder)
            mutate(folder)
            cli(label, folder, False)
        regression = subprocess.run(["python3", str(ROOT / "verification/regressions/w1_resolution/replay.py"), str(binary)],
                                    capture_output=True, text=True, timeout=30)
        observe("retained-resolution-regressions", regression.returncode == 0,
                cases=[json.loads(line) for line in regression.stdout.splitlines()], stderr=regression.stderr)
    except (AssertionError, OSError, subprocess.SubprocessError) as failure:
        error = str(failure)
    result = "PASS" if error is None else "FAIL"
    report = dict(result=result, binary_sha256=binary_hash, error=error, cases=rows,
                  retained_directory=str(directory) if error else None)
    options.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    if error is None: shutil.rmtree(directory)
    print(json.dumps(dict(result=result, observations=len(rows), cli_invocations=sum("command" in r for r in rows),
                          report=str(options.report), error=error)))
    return int(error is not None)


if __name__ == "__main__":
    raise SystemExit(main())
