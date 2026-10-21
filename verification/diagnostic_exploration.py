#!/usr/bin/env python3
"""Exercise compiler diagnostics and parser edges through the real CLI."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BINARY = ROOT / "target/debug/diamond"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def invoke(arguments: list[str], timeout: float = 5.0) -> dict[str, object]:
    started = time.monotonic()
    completed = subprocess.run(
        [str(BINARY), *arguments],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=timeout,
    )
    return {
        "arguments": arguments,
        "exit": completed.returncode,
        "seconds": round(time.monotonic() - started, 6),
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--report",
        type=Path,
        default=ROOT / "verification/diagnostic_exploration_results.json",
    )
    options = parser.parse_args()
    cases: list[dict[str, object]] = []
    failures: list[str] = []

    def record(name: str, observed: dict[str, object], checks: list[tuple[bool, str]]) -> None:
        failed = [message for accepted, message in checks if not accepted]
        cases.append({"name": name, "observed": observed, "accepted": not failed, "failures": failed})
        failures.extend(f"{name}: {message}" for message in failed)

    with tempfile.TemporaryDirectory(prefix="diamond-cp6-diagnostics-") as temporary:
        directory = Path(temporary)

        def source_case(
            name: str,
            source: str | bytes,
            *,
            command: str = "check",
            code: str | None = None,
            location: tuple[int, int] | None = None,
            output: str | None = None,
            no_raw_tab: bool = False,
            forbidden_stderr: str | None = None,
        ) -> None:
            path = directory / f"{name}.dia"
            if isinstance(source, bytes):
                path.write_bytes(source)
            else:
                path.write_text(source)
            observed = invoke([command, str(path)])
            stderr = str(observed["stderr"])
            stdout = str(observed["stdout"])
            checks: list[tuple[bool, str]] = []
            if code is None:
                checks.append((observed["exit"] == 0, "valid source failed"))
                if output is not None:
                    checks.append((stdout.strip() == output, f"expected stdout {output!r}"))
            else:
                checks.extend(
                    [
                        (observed["exit"] != 0, "invalid source succeeded"),
                        (f"error[{code}]" in stderr, f"missing rendered {code}"),
                    ]
                )
                if location is not None:
                    line, column = location
                    checks.append(
                        (
                            f" --> {path}:{line}:{column}" in stderr,
                            f"missing path span {line}:{column}",
                        )
                    )
                    if line <= len(path.read_text().splitlines()):
                        checks.append(("^" in stderr, "missing source caret"))
            if no_raw_tab:
                checks.append(("\t" not in stderr, "raw tab leaked into diagnostic excerpt"))
            if forbidden_stderr is not None:
                checks.append((forbidden_stderr not in stderr, "terminal formatting character leaked"))
            record(name, observed, checks)

        source_case("valid_separator", "fn main() -> Int:\n  1_000 + 2\n", command="run", output="1002")
        source_case("valid_crlf", b"fn main() -> Int:\r\n  42\r\n")
        source_case("valid_comments", "fn main() -> Int:\n    # retained block\n\n  42 # end\n")
        source_case("empty", "", code="D010", location=(1, 1))
        source_case("tab", "fn main() -> Int:\n\t42\n", code="D001", location=(2, 1), no_raw_tab=True)
        source_case("indent", "fn main() -> Int:\n    1\n   2\n", code="D002", location=(3, 4))
        source_case("repeated_separator", "fn main() -> Int:\n  1__2\n", code="D004", location=(2, 4))
        source_case("trailing_separator", "fn main() -> Int:\n  1_\n", code="D004", location=(2, 4))
        source_case("separator_before_name", "fn main() -> Int:\n  1_foo\n", code="D004", location=(2, 4))
        source_case(
            "integer_overflow",
            "fn main() -> Int:\n  9223372036854775808\n",
            code="D004",
            location=(2, 3),
        )
        source_case("unterminated_string", 'fn main() -> Text:\n  "open\n', code="D005", location=(2, 3))
        source_case("unsupported_token", "fn main() -> Int:\n  1 & 2\n", code="D900", location=(2, 5))
        source_case("unicode_column", 'fn main() -> Text:\n  "café" @\n', code="D010", location=(2, 10))
        source_case(
            "bidi_excerpt",
            'fn main() -> Text:\n  "\u202e" @\n',
            code="D010",
            location=(2, 7),
            forbidden_stderr="\u202e",
        )
        source_case("unclosed_group", "fn main() -> Int:\n  (1 + 2\n", code="D010", location=(2, 9))
        source_case(
            "html_interpolation",
            "fn main() -> Html:\n  html:\n    <p>café {missing}</p>\n",
            code="D112",
            location=(3, 14),
        )
        source_case(
            "html_closing_tag",
            "fn main() -> Html:\n  html:\n    <p>text</div>\n",
            code="D020",
            location=(3, 12),
        )
        source_case(
            "empty_interpolation",
            "fn main() -> Html:\n  html:\n    <p>{}</p>\n",
            code="D010",
            location=(3, 9),
        )
        source_case(
            "multiline_interpolation_location",
            "fn main() -> Html:\n  html:\n    <p>{\n      missing}</p>\n",
            code="D112",
            location=(4, 7),
        )
        source_case(
            "html_tab",
            "fn main() -> Html:\n  html:\n    <p>before\tafter</p>\n",
            code="D001",
            location=(3, 14),
            no_raw_tab=True,
        )
        source_case("comment_only", "# no declaration\n\n", code="D010", location=(3, 1))
        source_case("unknown_type", "type MissingAlias = Missing\n", code="D103", location=(1, 1))
        source_case("unsupported_type", "type Mapping = Map<Int>\n", code="D900", location=(1, 1))
        source_case(
            "invalid_route_path",
            'public route GET "relative":\n  "no"\n',
            code="D402",
            location=(1, 1),
        )
        source_case(
            "unsupported_route_parameter",
            'public route GET "/items/{value:Text}":\n  value\n',
            code="D900",
            location=(1, 1),
        )
        source_case(
            "invalid_id_literal",
            'type ItemId = Id<"item">\nfn main() -> ItemId:\n  ItemId("bad/value")\n',
            code="D117",
            location=(3, 3),
        )
        source_case(
            "invalid_static_link",
            'fn main() -> Html:\n  html:\n    <a href="https://example.test">outside</a>\n',
            code="D020",
            location=(3, 13),
        )
        source_case(
            "encoded_static_link",
            'fn main() -> Html:\n  html:\n    <a href="/items%2fhidden">encoded</a>\n',
            code="D900",
            location=(3, 13),
        )

        for example in sorted((ROOT / "examples").glob("*.dia")):
            observed = invoke(["check", str(example)])
            record(
                f"example_{example.stem}",
                observed,
                [(observed["exit"] == 0, f"shipped example {example.name} failed")],
            )

    codes = [
        "D001", "D002", "D003", "D004", "D005", "D010", "D020", "D101", "D102",
        "D103", "D110", "D111", "D112", "D113", "D114", "D115", "D116", "D117",
        "D118", "D119", "D120", "D121", "D122", "D123", "D124", "D125", "D200",
        "D201", "D204", "D301", "D302", "D401", "D402", "D403", "D405", "D900",
    ]
    for spelling in [*codes, "d004"]:
        observed = invoke(["explain", spelling])
        record(
            f"explain_{spelling}",
            observed,
            [
                (observed["exit"] == 0, "known diagnostic explanation failed"),
                (spelling.upper() in str(observed["stdout"]), "canonical code missing"),
                (len(str(observed["stdout"]).splitlines()) >= 2, "diagnostic remedy missing"),
            ],
        )
    observed = invoke(["explain", "D999"])
    record(
        "explain_unknown",
        observed,
        [
            (observed["exit"] != 0, "unknown diagnostic code succeeded"),
            ("unknown diagnostic code D999" in str(observed["stderr"]), "unknown-code error missing"),
        ],
    )

    report = {
        "schema": 1,
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "binary_sha256": digest(BINARY),
        "cases": cases,
        "case_count": len(cases),
        "failures": failures,
        "result": "PASS" if not failures else "FAIL",
    }
    options.report.parent.mkdir(parents=True, exist_ok=True)
    options.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"status": report["result"], "cases": len(cases), "failures": len(failures)}))
    if failures:
        for failure in failures:
            print(failure, file=__import__("sys").stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
