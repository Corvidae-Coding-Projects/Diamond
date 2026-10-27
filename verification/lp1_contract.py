#!/usr/bin/env python3
"""Observe LP1's existing contract through real Diamond CLI/build/HTTP interfaces.

Expected values and failure classes below are declared independently of Diamond.
This is a bounded contract sample, not the later LP8 qualification campaign.
"""

from __future__ import annotations

import argparse
import hashlib
import http.client
from html.parser import HTMLParser
import json
from pathlib import Path
import random
import shutil
import socket
import sqlite3
import subprocess
import tempfile
import time


ROOT = Path(__file__).resolve().parents[1]
SUMMARY = "9007199254740993|3|-2|-0.000002|😀|·a·b·"
SEED = 0xD1A1


class Page(HTMLParser):
    def __init__(self, html: str) -> None:
        super().__init__(convert_charrefs=True)
        self.active: str | None = None
        self.text: dict[str, str] = {}
        self.scripts = 0
        self.feed(html)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "p":
            self.active = dict(attrs).get("id")
        if tag == "script":
            self.scripts += 1

    def handle_data(self, text: str) -> None:
        if self.active:
            self.text[self.active] = self.text.get(self.active, "") + text

    def handle_endtag(self, tag: str) -> None:
        if tag == "p":
            self.active = None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, default=ROOT / "target/debug/diamond")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--language-version", default="0.16.0")
    parser.add_argument("--format-version", type=int, default=1)
    parser.add_argument("--wire-version", type=int, default=3)
    args = parser.parse_args()
    binary = args.binary.resolve()
    observations: list[dict[str, object]] = []

    def observe(name: str, passed: bool, **detail: object) -> None:
        observations.append({"name": name, "pass": passed, **detail})

    def invoke(*arguments: object) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(binary), *map(str, arguments)], cwd=ROOT, text=True,
            capture_output=True, timeout=25, check=False,
        )

    pure = [
        ("S01-precedence", "Int", "2 + 3 * 4", "14"),
        ("S02-large-exact-int", "Int", "9007199254740993", "9007199254740993"),
        ("S03-minimum-int-expression", "Int", "-9223372036854775807 - 1", "-9223372036854775808"),
        ("S04-negative-division", "Int", "-7 / 3", "-2"),
        ("S05-lazy-and", "Bool", "false and (1 / 0 == 0)", "false"),
        ("S06-lazy-or", "Bool", "true or (1 / 0 == 0)", "true"),
        ("S07-scalar-length", "Int", 'len("A😀é")', "3"),
        ("S08-no-normalization", "Bool", '"é" == "é"', "false"),
        ("S09-scalar-order", "Bool", '"😀" > "\ue000"', "true"),
        ("S10-scalar-slice", "Text", 'text_slice("A😀é", 1, 1)', "😀"),
        ("S11-empty-replacement", "Text", 'text_replace("ab", "", "·")', "·a·b·"),
        ("S12-half-even", "Text", 'decimal_text(decimal_multiply(decimal_units(1), decimal_units(1500000), "half_even"))', "0.000002"),
        ("S13-negative-half-even", "Text", 'decimal_text(decimal_multiply(decimal_units(-1), decimal_units(1500000), "half_even"))', "-0.000002"),
        ("S14-half-away", "Text", 'decimal_text(decimal_multiply(decimal_units(1), decimal_units(500000), "half_away"))', "0.000001"),
        ("S15-time-units", "Int", "time_millis(timestamp_add(timestamp(1000), duration(250)))", "1250"),
        ("S16-list-none", "Int", "match list_get([8, 13], -1):\n    Some(value) => value\n    None => 55", "55"),
        ("S17-lazy-match", "Int", "match true:\n    true => 42\n    false => 1 / 0", "42"),
        ("S18-empty-slice", "Text", 'text_slice("abc", 3, 0)', ""),
        ("S19-list-length", "Int", "len([8, 13])", "2"),
    ]
    failures = [
        ("N01-overflow", "run", "fn main() -> Int:\n  9223372036854775807 + 1\n", "integer overflow"),
        ("N02-division-zero", "run", "fn main() -> Int:\n  1 / 0\n", "division by zero"),
        ("N03-strict-slice", "run", 'fn main() -> Text:\n  text_slice("abc", 0, 4)\n', "invalid utility"),
        ("N04-minimum-literal-limit", "check", "fn main() -> Int:\n  -9223372036854775808\n", "D004"),
        ("N05-nested-return", "check", "fn main() -> Int:\n  match true:\n    true =>\n      return 1\n    false => 2\n", "D900"),
        ("N06-nonexhaustive", "check", "fn main() -> Int:\n  match true:\n    true => 1\n", "D123"),
        ("N07-nominal-id", "check", 'type A = Id<"a">\ntype B = Id<"b">\nfn take(value: A) -> Int:\n  1\nfn main() -> Int:\n  take(B("x"))\n', "D110"),
        ("N08-html-forgery", "check", 'fn main() -> Html:\n  "<script>bad()</script>"\n', "expected Html"),
        ("N09-nested-secret-return", "check", 'type Hidden:\n  value: Secret<"inventory">\nfn main() -> Html:\n  let x = Hidden(value: "forged")\n  html:\n    <p>{x}</p>\n', "D110"),
        ("N10-dynamic-id-single", "check", 'type A = Id<"a">\nfn convert(value: Text) -> A:\n  A(value)\nfn main() -> Int:\n  1\n', "dynamic Id construction"),
        ("N11-named-function-args", "check", "fn take(value: Int) -> Int:\n  value\nfn main() -> Int:\n  take(value: 1)\n", "positional"),
        ("N12-argument-order", "run", 'fn take(a: Int, b: Int) -> Int:\n  a + b\nfn main() -> Int:\n  take(len(text_slice("a", 0, 2)), 1 / 0)\n', "invalid utility"),
        ("N13-argument-order-reversed", "run", 'fn take(a: Int, b: Int) -> Int:\n  a + b\nfn main() -> Int:\n  take(1 / 0, len(text_slice("a", 0, 2)))\n', "division by zero"),
    ]
    temp_parent = ROOT / ".diamond"
    temp_parent.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="lp1-", dir=temp_parent) as temporary:
        directory = Path(temporary)
        for name, ty, expression, expected in pure:
            path = directory / f"{name}.dia"
            path.write_text(f"fn main() -> {ty}:\n  {expression}\n", encoding="utf-8")
            result = invoke("run", path)
            observe(name, result.returncode == 0 and result.stdout == expected + "\n",
                    expected=expected, stdout=result.stdout.rstrip("\n"), stderr=result.stderr.strip())
        for name, command, source, diagnostic in failures:
            path = directory / f"{name}.dia"
            path.write_text(source, encoding="utf-8")
            result = invoke(command, path)
            observe(name, result.returncode > 0 and diagnostic in result.stderr
                    and "panicked at" not in result.stderr,
                    expected_diagnostic=diagnostic, stderr=result.stderr.strip())

        rng = random.Random(SEED)
        for index in range(16):
            a, b, c = (rng.randrange(-1000, 1000) for _ in range(3))
            path = directory / f"generated-{index}.dia"
            path.write_text(f"fn main() -> Int:\n  ({a} + {b}) * {c}\n", encoding="utf-8")
            result = invoke("run", path)
            expected = str((a + b) * c)
            observe(f"G{index:02}-arithmetic", result.returncode == 0 and result.stdout == expected + "\n",
                    inputs=[a, b, c], expected=expected, stdout=result.stdout.rstrip("\n"))

        for name in ("returned_transaction_error", "caught_transaction_failure"):
            database = directory / f"{name}.sqlite"
            source = ROOT / "verification/regressions" / f"{name}.dia"
            result = invoke("run", source, "--database", database)
            with sqlite3.connect(database) as connection:
                count = connection.execute("SELECT count(*) FROM Notes").fetchone()[0]
            observe(f"T-{name}", result.returncode > 0 and count == 0,
                    rows=count, stderr=result.stderr.strip())

        project = ROOT / "verification/lp1/browser/diamond.toml"
        result = invoke("run", project)
        observe("P01-shared-server-semantics", result.returncode == 0 and result.stdout == SUMMARY + "\n",
                expected=SUMMARY, stdout=result.stdout.rstrip("\n"), stderr=result.stderr.strip())
        build = directory / "build"
        result = invoke("build", project, "--out", build)
        observe("P02-build", result.returncode == 0, stderr=result.stderr.strip())
        if result.returncode == 0:
            contract = json.loads((build / "contract.json").read_text())
            observe("P03-versions", contract["format_version"] == args.format_version and contract["wire_version"] == args.wire_version
                    and contract["language_version"] == args.language_version,
                    format=contract["format_version"], wire=contract["wire_version"], language=contract["language_version"])
            result = invoke("check", build)
            observe("P04-rechecked-load", result.returncode == 0, stderr=result.stderr.strip())
            page = Page((build / "index.html").read_text())
            observe("P05-ssr-semantics-and-escaping", page.text.get("initial") == SUMMARY
                    and page.text.get("hostile") == "<script>lp1()</script>" and page.scripts == 1,
                    initial=page.text.get("initial"), script_nodes=page.scripts)
            public = list((build / "assets").iterdir())
            observe("P06-server-source-private", all("lp1-server-only-canary" not in path.read_text()
                    for path in public), public_assets=len(public))
            changed = directory / "changed-build"
            shutil.copytree(build, changed)
            (changed / "contract.json").write_text((changed / "contract.json").read_text() + " ")
            result = invoke("check", changed)
            observe("P07-contract-tamper-denied", result.returncode > 0 and "artifact mismatch" in result.stderr,
                    stderr=result.stderr.strip())
            server_artifact = json.loads((changed / "server.json").read_text())
            server_artifact["language_version"] = "0.0.0"
            (changed / "server.json").write_text(json.dumps(server_artifact))
            result = invoke("check", changed)
            observe("P08-language-mismatch-denied", result.returncode > 0 and "incompatible" in result.stderr,
                    stderr=result.stderr.strip())

            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                port = listener.getsockname()[1]
            process = subprocess.Popen([str(binary), "serve", str(build), "--listen", f"127.0.0.1:{port}"],
                                       cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                for _ in range(100):
                    if process.poll() is not None:
                        break
                    try:
                        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
                        connection.request("GET", "/")
                        response = connection.getresponse()
                        html = response.read().decode()
                        status = response.status
                        connection.close()
                        observed = Page(html)
                        observe("P09-real-http", status == 200 and observed.text.get("initial") == SUMMARY,
                                status=status, initial=observed.text.get("initial"))
                        break
                    except OSError:
                        time.sleep(.05)
                else:
                    observe("P09-real-http", False, error="startup deadline")
                if not any(row["name"] == "P09-real-http" for row in observations):
                    observe("P09-real-http", False, error="server exited")
            finally:
                process.send_signal(2) if process.poll() is None else None
                try:
                    process.communicate(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate(timeout=5)
                    observe("P10-owned-shutdown", False, error="forced termination")
                else:
                    observe("P10-owned-shutdown", process.returncode == 0, exit=process.returncode)

    report = {
        "status": "PASS" if all(row["pass"] for row in observations) else "FAIL",
        "scope": "LP1 current language baseline; no future LP2-LP8 feature qualification",
        "expected_versions": [args.language_version, args.format_version, args.wire_version],
        "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
        "driver_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "fixture_sha256": {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted((ROOT / "verification/lp1/browser").iterdir())
            if path.is_file()
        },
        "seed": SEED,
        "observations": observations,
    }
    encoded = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    if args.report:
        args.report.write_text(encoded, encoding="utf-8")
    print(json.dumps({"status": report["status"], "observations": len(observations),
                      "failures": [row for row in observations if not row["pass"]]}, ensure_ascii=False))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
