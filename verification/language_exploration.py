#!/usr/bin/env python3
"""Bounded, seeded exploration through the real CLI and public native library.

Expectations come from integer mathematics, explicit immutable-value examples,
HTML parsing and documented MVP limits. No production module is imported. Each
CLI child is disposable, with CPU/address-space/core limits; successful temporary
programs are removed and any unexpected result retains the exact input and receipt.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import random
import resource
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "verification/regressions/cp5_language"
DEFAULT_SEED = 0xD1A05
I64_MIN, I64_MAX = -(2**63), 2**63 - 1


def fingerprint(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def child_limits() -> None:
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_CPU, (5, 5))
    resource.setrlimit(resource.RLIMIT_AS, (512 * 1024**2, 512 * 1024**2))


def integer(value: int) -> str:
    # The signed minimum is an expression because positive integer tokens are i64.
    return "(-9223372036854775807 - 1)" if value == I64_MIN else str(value)


def trunc_div(a: int, b: int) -> int:
    quotient = abs(a) // abs(b)
    return -quotient if (a < 0) != (b < 0) else quotient


class Html(HTMLParser):
    def __init__(self, source: str) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: list[str] = []
        self.attributes: list[dict[str, str | None]] = []
        self.text: list[str] = []
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        self.attributes.append(dict(attrs))

    def handle_data(self, value):
        self.text.append(value)


class Campaign:
    def __init__(self, binary: Path, directory: Path, seed: int) -> None:
        self.binary, self.directory = binary, directory
        self.random = random.Random(seed)
        self.counts: Counter[str] = Counter()
        self.case_hashes: list[str] = []

    def cli(self, name: str, source: str, *, expected: str | None = None,
            reject: str | None = None, command: str = "run",
            family: str = "language acceptance") -> subprocess.CompletedProcess[str]:
        path = self.directory / (name + ".dia")
        path.write_text(source)
        self.case_hashes.append(fingerprint(path))
        result = subprocess.run([str(self.binary), command, str(path)], cwd=ROOT,
                                capture_output=True, text=True, timeout=8,
                                preexec_fn=child_limits)
        receipt = {"name": name, "command": command, "returncode": result.returncode,
                   "stdout": result.stdout, "stderr": result.stderr,
                   "expected": expected, "reject_contains": reject,
                   "source_sha256": self.case_hashes[-1]}
        self.directory.joinpath("last_observation.json").write_text(json.dumps(receipt, indent=2))
        assert result.returncode >= 0 and "panicked at" not in result.stderr, receipt
        if reject is None:
            assert result.returncode == 0, receipt
            if expected is not None:
                assert result.stdout == expected + "\n", receipt
        else:
            assert result.returncode != 0 and reject in result.stderr, receipt
        self.counts[family] += 1
        return result

    def arithmetic(self) -> None:
        # Signed division truncates toward zero, including negative nonmultiples.
        operands = [(-29, 7), (29, -7), (-29, -7), (0, 17), (I64_MIN, 1), (I64_MAX, -1)]
        for index, (a, b) in enumerate(operands):
            self.cli(f"division-{index}", f"fn main() -> Int:\n  {integer(a)} / {integer(b)}\n",
                     expected=str(trunc_div(a, b)), family="signed arithmetic")
        for index in range(24):
            a, b, c = [self.random.randrange(-5000, 5001) for _ in range(3)]
            divisor = self.random.choice([-31, -7, -2, 2, 5, 37])
            expression = f"(({a}) * ({b}) + ({c})) / ({divisor})"
            self.cli(f"arithmetic-{index}", f"fn main() -> Int:\n  {expression}\n",
                     expected=str(trunc_div(a * b + c, divisor)), family="signed arithmetic")
        for index, (expression, result) in enumerate([
            (f"{I64_MAX} - {I64_MAX}", 0),
            (f"{integer(I64_MIN)} + 1", I64_MIN + 1),
            (f"{integer(I64_MIN)} * 0", 0),
            (f"-{I64_MAX}", -I64_MAX),
        ]):
            self.cli(f"integer-boundary-{index}", f"fn main() -> Int:\n  {expression}\n",
                     expected=str(result), family="signed arithmetic")
        for index, expression in enumerate([
            f"{I64_MAX} + 1", f"{integer(I64_MIN)} - 1", f"{I64_MAX} * 2",
            f"{integer(I64_MIN)} / -1", f"-({integer(I64_MIN)})", "17 / 0",
        ]):
            self.cli(f"arithmetic-rejected-{index}", f"fn main() -> Int:\n  {expression}\n",
                     reject="invalid input", family="arithmetic failure boundaries")
        prefix = "fn explode() -> Bool:\n  (9 / 0) == 0\n"
        for index, (expression, result) in enumerate([
            ("false and explode()", "false"), ("true or explode()", "true"),
            ("not false and (3 < 8) or false", "true"),
            ("not true or (8 <= 8 and 3 != 4)", "true"),
            ("(3 > 8) or (8 >= 9)", "false"),
        ]):
            self.cli(f"boolean-{index}", prefix + f"fn main() -> Bool:\n  {expression}\n",
                     expected=result, family="Boolean acceptance")
        for index, expression in enumerate(["true and explode()", "false or explode()"]):
            self.cli(f"boolean-eager-{index}", prefix + f"fn main() -> Bool:\n  {expression}\n",
                     reject="invalid input", family="Boolean required evaluation")

    def immutable_values(self) -> None:
        prefix = ("type Count = Int\ntype Label = Text<length: 1..32>\n"
                  "type Entry:\n  value: Count\n  label: Label = \"default 雪\"\n  active: Bool = true\n"
                  "enum State:\n  Waiting\n  Ready(entry: Entry)\n  Failed(message: Text)\n"
                  "fn recover(result: Result<Entry, DataError>) -> Option<Entry>:\n"
                  "  match result:\n    Ok(entry) => Some(entry)\n    Err(_) => None\n"
                  "fn score(value: Option<Entry>) -> Int:\n"
                  "  match value:\n    Some(entry) => entry.value * 3 + len(entry.label)\n    None => -71\n")
        for index in range(16):
            value = self.random.randrange(-300, 301)
            label = self.random.choice(["café", "雪 & <tag>", "e\u0301", "plain"])
            construction = f"Entry(value: {value})" if index % 2 else f"Entry(label: {json.dumps(label, ensure_ascii=False)}, value: {value})"
            final_label = "default 雪" if index % 2 else label
            state = "Waiting" if index % 4 == 0 else f"Ready({construction})"
            body = (f"fn main() -> Int:\n  let state = {state}\n  match state:\n"
                    "    Waiting => score(None)\n    Ready(entry) => score(recover(Ok(entry)))\n"
                    "    Failed(_) => 99\n")
            expected = -71 if index % 4 == 0 else value * 3 + len(final_label)
            self.cli(f"immutable-{index}", prefix + body, expected=str(expected), family="record enum Option Result compositions")
        self.cli("result-error-recovery", prefix + "fn main() -> Int:\n  score(recover(Err(\"handled\")))\n",
                 expected="-71", family="record enum Option Result compositions")
        self.cli("immutable-copy", "type Entry:\n  value: Int\nfn main() -> Int:\n  let original = Entry(value: 17)\n  let other = original\n  original.value + other.value\n",
                 expected="34", family="record enum Option Result compositions")

    def lists_and_html(self) -> None:
        for index, length in enumerate([0, 1, 2, 7, 19, 31]):
            values = [self.random.randrange(-19, 20) for _ in range(length)]
            body = ("fn square(value: Int) -> Int:\n  value * value\nfn main() -> Html:\n  let items = for value in " + str(values) + ":\n"
                    "    let result = square(value)\n    html:\n      <li>{result}</li>\n"
                    "  html:\n    <ul>{items}</ul>\n")
            rendered = self.cli(f"list-render-{index}", body, family="list iteration HTML compositions").stdout
            parsed = Html(rendered)
            assert parsed.tags == ["ul"] + ["li"] * length
            assert [s.strip() for s in parsed.text if s.strip()] == [str(value * value) for value in values]
        texts = ["<script>alert('x')</script>", '"quoted" & café 雪', "line\nbreak", "e\u0301", "{literal}"]
        for index, text in enumerate(texts):
            literal = json.dumps(text, ensure_ascii=False)
            source = f"fn main() -> Html:\n  let message = {literal}\n  html:\n    <article><p title={{message}}>{{message}}</p></article>\n"
            parsed = Html(self.cli(f"escape-{index}", source, family="typed HTML and Unicode").stdout)
            assert parsed.tags == ["article", "p"] and parsed.attributes[1]["title"] == text
            assert "".join(parsed.text).strip() == text
            self.cli(f"unicode-length-{index}", f"fn main() -> Int:\n  len({literal})\n", expected=str(len(text)), family="typed HTML and Unicode")
        for index, path in enumerate(["/", "/posts/a-b_7", "/雪", "/search?x=1&y=2"]):
            source = f'fn main() -> Html:\n  html:\n    <a href="{path}">go</a>\n'
            parsed = Html(self.cli(f"url-valid-{index}", source, family="internal URL acceptance").stdout)
            assert parsed.attributes == [{"href": path}]

    def malformed(self) -> None:
        cases = {
            "missing-close": "fn main() -> Int:\n  (3 + 2\n",
            "missing-colon": "fn main() -> Int\n  3\n",
            "bad-indent": "fn main() -> Int:\n  let x = 1\n x\n",
            "tabs": "fn main() -> Int:\n\t3\n",
            "invalid-token": "fn main() -> Int:\n  @\n",
            "unterminated-text": 'fn main() -> Text:\n  "open\n',
            "unsupported-escape": 'fn main() -> Text:\n  "a\\q"\n',
            "integer-token-overflow": f"fn main() -> Int:\n  {2**63}\n",
            "wrong-return": 'fn main() -> Int:\n  "three"\n',
            "wrong-record-field": "type Entry:\n  value: Int\nfn main() -> Int:\n  Entry(wrong: 1).value\n",
            "mixed-list": 'fn main() -> Int:\n  len([1, "two"])\n',
            "bad-branch": "fn main() -> Int:\n  match true:\n    true => 1\n",
            "duplicate-pattern": "fn main() -> Int:\n  match true:\n    true => 1\n    true => 2\n    false => 3\n",
            "alias-cycle": "type Left = Right\ntype Right = Left\nfn main() -> Int:\n  1\n",
            "raw-script": 'fn main() -> Html:\n  html:\n    <script>bad</script>\n',
            "event-attribute": 'fn main() -> Html:\n  html:\n    <p onclick="bad">text</p>\n',
            "mismatched-tag": 'fn main() -> Html:\n  html:\n    <p>text</span>\n',
            "duplicate-attribute": 'fn main() -> Html:\n  html:\n    <p id="a" id="b">text</p>\n',
            "text-url": 'fn main() -> Html:\n  let path = "/safe"\n  html:\n    <a href={path}>go</a>\n',
        }
        for index, path in enumerate(["javascript:alert(1)", "//outside/path", "/a b", "/a\\b", "/%2foutside", "https://outside/"]):
            cases[f"url-invalid-{index}"] = f'fn main() -> Html:\n  html:\n    <a href="{path}">go</a>\n'
        for name, source in cases.items():
            # These distinct malformed/type/sink mutations must be rejected by checking.
            result = self.cli(name, source, command="check", reject="", family="structured source rejections")
            assert any(marker in result.stderr for marker in ["D0", "D1", "D9", "invalid input", "resource limit"]), (name, result.stderr)

    def boundaries(self) -> None:
        source = "fn main() -> Int:\n  7\n#"
        at_limit = source + "x" * (1_048_576 - len(source))
        self.cli("source-at-limit", at_limit, expected="7", family="documented resource boundaries")
        self.cli("source-over-limit", at_limit + "x", command="check", reject="source size", family="documented resource boundaries")
        for count, accepted in [(16, True), (70, False)]:
            source = "fn main() -> Int:\n  " + "(" * count + "7" + ")" * count + "\n"
            self.cli(f"expression-nesting-{count}", source, expected="7" if accepted else None,
                     reject=None if accepted else "expression nesting", family="documented resource boundaries")
        for count, accepted in [(12, True), (33, False)]:
            ty = "Option<" * count + "Int" + ">" * count
            self.cli(f"type-nesting-{count}", f"fn unused(value: {ty}) -> Int:\n  9\nfn main() -> Int:\n  7\n",
                     expected="7" if accepted else None, command="run" if accepted else "check",
                     reject=None if accepted else "type nesting", family="documented resource boundaries")
        for count, accepted in [(12, True), (34, False)]:
            markup = "<div>" * count + "safe" + "</div>" * count
            self.cli(f"html-nesting-{count}", f"fn main() -> Html:\n  html:\n    {markup}\n",
                     reject=None if accepted else "HTML nesting", family="documented resource boundaries")
        self.cli("recursive-call-bound", "fn recurse() -> Int:\n  recurse()\nfn main() -> Int:\n  recurse()\n",
                 reject="call depth", family="documented resource boundaries")
        for count, accepted in [(100, True), (1001, False)]:
            self.cli(f"list-length-{count}", "fn main() -> Int:\n  len([" + ",".join(["7"] * count) + "])\n",
                     expected=str(count) if accepted else None,
                     reject=None if accepted else "resource limit", family="documented resource boundaries")
        for count, accepted in [(12, True), (21, False)]:
            source = 'fn main() -> Int:\n  let text0 = "a"\n'
            source += "".join(f"  let text{index} = text{index-1} + text{index-1}\n" for index in range(1, count + 1))
            source += f"  len(text{count})\n"
            self.cli(f"text-growth-{count}", source, expected=str(2**count) if accepted else None,
                     reject=None if accepted else "resource limit", family="documented resource boundaries")
        source = 'fn main() -> Html:\n  let text0 = "&"\n'
        source += "".join(f"  let text{index} = text{index-1} + text{index-1}\n" for index in range(1, 19))
        source += "  html:\n    <p>{text18}</p>\n"
        self.cli("escaped-output-bound", source, reject="resource limit", family="documented resource boundaries")

    def native(self, library: Path) -> dict:
        source = ROOT / "verification/session_lifecycle.rs"
        executable = self.directory / "session_lifecycle"
        arguments = ["rustc", "--edition=2024", "-Dwarnings", str(source), "-L", f"dependency={library.parent / 'deps'}",
                     "--extern", f"diamond={library}", "-o", str(executable)]
        subprocess.run(arguments, cwd=ROOT, capture_output=True, text=True, check=True, timeout=30)
        result = subprocess.run([str(executable)], cwd=ROOT, capture_output=True, text=True,
                                timeout=10, preexec_fn=child_limits)
        assert result.returncode == 0, (result.returncode, result.stdout, result.stderr)
        observed = json.loads(result.stdout)
        assert observed["status"] == "PASS"
        return {**observed, "source_sha256": fingerprint(source), "library_sha256": fingerprint(library),
                "consumer_sha256": fingerprint(executable)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, default=ROOT / "target/debug/diamond")
    parser.add_argument("--library", type=Path, default=ROOT / "target/debug/libdiamond.rlib")
    parser.add_argument("--seed", type=lambda value: int(value, 0), default=DEFAULT_SEED)
    parser.add_argument("--skip-native", action="store_true", help="Do not conflate an alternate binary with the current native library")
    parser.add_argument("--replay-resource-regression", action="store_true", help="Replay the saved allocation failure only after a repair exists")
    arguments = parser.parse_args()
    directory = Path(tempfile.mkdtemp(prefix="diamond-language-exploration-"))
    campaign = Campaign(arguments.binary.resolve(), directory, arguments.seed)
    try:
        campaign.arithmetic()
        campaign.immutable_values()
        campaign.lists_and_html()
        campaign.malformed()
        campaign.boundaries()
        native = None if arguments.skip_native else campaign.native(arguments.library.resolve())
        if arguments.replay_resource_regression:
            campaign.cli("record-clone-saved-regression", (FIXTURES / "record_clone.dia").read_text(),
                         reject="resource limit", family="saved resource regression replay")
    except Exception:
        print(f"FAIL: exact programs and last observation retained at {directory}", file=sys.stderr)
        raise
    else:
        receipt = {"status": "PASS", "seed": arguments.seed,
                   "binary_sha256": fingerprint(arguments.binary.resolve()),
                   "driver_sha256": fingerprint(Path(__file__)),
                   "generated_inputs_sha256": hashlib.sha256("".join(campaign.case_hashes).encode()).hexdigest(),
                   "observations": dict(campaign.counts), "native": native,
                   "child_limits": {"address_space_bytes": 512 * 1024**2, "cpu_seconds": 5, "wall_seconds": 8, "core_bytes": 0}}
        shutil.rmtree(directory)
        print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    main()
