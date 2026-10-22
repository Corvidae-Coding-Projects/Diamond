#!/usr/bin/env python3
"""Collect the final checkpoint-six quality and real-interface evidence."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
BINARY = ROOT / "target/debug/diamond"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inputs() -> dict[str, str]:
    paths = [
        ROOT / "Cargo.toml",
        ROOT / "Cargo.lock",
        ROOT / "deny.toml",
        ROOT / "LICENSE",
        ROOT / ".gitignore",
        ROOT / "README.md",
        ROOT / "CHECKPOINTS.md",
        ROOT / "IMPLEMENTATION_PLAN.md",
        ROOT / "design.md",
    ]
    for directory, pattern in [
        ("src", "*.rs"),
        ("src", "*.js"),
        ("examples", "*"),
        ("docs", "*.md"),
        ("verification", "*.py"),
        ("verification", "*.rs"),
        ("verification", "*.c"),
        ("verification", "*.md"),
    ]:
        paths += sorted((ROOT / directory).rglob(pattern))
    paths += sorted((ROOT / ".design").glob("general-web-apps*"))
    paths += sorted((ROOT / "verification/regressions").rglob("*"))
    return {str(path.relative_to(ROOT)): digest(path) for path in paths if path.is_file()}


def documentation_audit() -> dict[str, object]:
    documents = [ROOT / "README.md", ROOT / "examples/README.md", *sorted((ROOT / "docs").glob("*.md"))]
    missing = []
    links = 0
    for document in documents:
        for target in re.findall(r"\[[^]]+\]\(([^)]+)\)", document.read_text()):
            if target.startswith(("http://", "https://", "#")):
                continue
            path_text = target.split("#", 1)[0]
            if not path_text:
                continue
            links += 1
            linked = (document.parent / path_text).resolve()
            if not linked.exists():
                missing.append(f"{document.relative_to(ROOT)} -> {target}")
    source = "\n".join(path.read_text() for path in sorted((ROOT / "src").rglob("*.rs")))
    authored_unsafe = re.findall(r"\bunsafe\s+(?:fn|impl|trait|extern)|\bunsafe\s*\{", source)
    lint_suppressions = re.findall(r"#\s*!?\s*\[\s*allow\s*\(", source)
    return {
        "documents": [str(path.relative_to(ROOT)) for path in documents],
        "local_links": links,
        "missing_links": missing,
        "authored_unsafe_constructs": authored_unsafe,
        "lint_suppressions": lint_suppressions,
        "accepted": not missing and not authored_unsafe and not lint_suppressions,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "verification",
                        help="Place child receipts here, preserving earlier checkpoint evidence.")
    parser.add_argument(
        "--report",
        type=Path,
        default=ROOT / "verification/checkpoint_six_results.json",
    )
    options = parser.parse_args()
    options.output_dir.mkdir(parents=True, exist_ok=True)
    def receipt(name: str) -> str:
        return str(options.output_dir / name)
    report: dict[str, object] = {
        "schema": 1,
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "full": options.full,
        "inputs_sha256": inputs(),
        "documentation_audit": documentation_audit(),
        "commands": [],
    }
    report["rustc"] = subprocess.check_output(["rustc", "--version", "--verbose"], text=True).strip()
    report["cargo"] = subprocess.check_output(["cargo", "--version"], text=True).strip()
    report["python"] = sys.version
    commands: list[tuple[list[str], dict[str, str], int]] = []
    if options.full:
        commands += [
            (["cargo", "fmt", "--all", "--check"], {}, 300),
            (["rustfmt", "--check", "--edition", "2024", "verification/session_lifecycle.rs", "verification/session_clock.rs"], {}, 300),
            (["cargo", "build", "--locked"], {}, 300),
            (["cargo", "clippy", "--locked", "--all-targets", "--all-features", "--", "-D", "warnings"], {}, 300),
            (["cargo", "test", "--locked"], {}, 300),
            (["cargo", "doc", "--locked", "--no-deps"], {"RUSTDOCFLAGS": "-D warnings"}, 300),
            (["cargo", "run", "--locked", "--example", "embedding"], {}, 300),
            ([
                sys.executable,
                "verification/checkpoint_five.py",
                "--full",
                "--report",
                receipt("checkpoint_six_checkpoint_five.json"),
                "--transaction-report",
                receipt("checkpoint_six_transactions.json"),
                "--cancellation-report",
                receipt("checkpoint_six_cancellation.json"),
            ], {}, 1_800),
        ]
    commands += [
        ([sys.executable, "verification/diagnostic_exploration.py", "--report", receipt("checkpoint_six_diagnostics.json")], {}, 300),
        ([sys.executable, "verification/startup_installation.py", "--report", receipt("checkpoint_six_startup_installation.json")], {}, 600),
        ([sys.executable, "verification/performance.py", "--report", receipt("checkpoint_six_performance.json")], {}, 300),
    ]

    result = 0
    binary_hash = None
    library_hash = None
    try:
        audit = report["documentation_audit"]
        if not audit["accepted"]:  # type: ignore[index]
            raise AssertionError(f"documentation/organization audit failed: {audit}")
        for command, environment, timeout in commands:
            started = time.monotonic()
            completed = subprocess.run(
                command,
                cwd=ROOT,
                env=os.environ | environment,
                text=True,
                capture_output=True,
                timeout=timeout,
            )
            row: dict[str, object] = {
                "command": command,
                "cwd": str(ROOT),
                "environment_overrides": environment,
                "exit": completed.returncode,
                "seconds": round(time.monotonic() - started, 3),
                "stdout": completed.stdout,
                "stderr": completed.stderr,
            }
            last_line = completed.stdout.strip().splitlines()[-1:] or [""]
            try:
                row["observations"] = json.loads(last_line[0])
            except json.JSONDecodeError:
                pass
            observed = row.get("observations", {})
            accepted = completed.returncode == 0 and observed.get(  # type: ignore[union-attr]
                "status", observed.get("result", "PASS")  # type: ignore[union-attr]
            ) == "PASS"
            row["accepted"] = accepted
            report["commands"].append(row)  # type: ignore[union-attr]
            print(f'{"PASS" if accepted else "FAIL"}: {" ".join(command)}', flush=True)
            if not accepted:
                print(completed.stdout + completed.stderr, file=sys.stderr)
                result = 1
                break
            if BINARY.is_file():
                current = digest(BINARY)
                binary_hash = binary_hash or current
                if current != binary_hash:
                    raise AssertionError("executable changed during final verification")
        if report["inputs_sha256"] != inputs():
            raise AssertionError("verification inputs changed during execution")
        if binary_hash is None or digest(BINARY) != binary_hash:
            raise AssertionError("final executable fingerprint is unavailable or changed")
        library_hash = digest(ROOT / "target/debug/libdiamond.rlib")
    except (AssertionError, OSError, subprocess.SubprocessError) as error:
        report["runner_error"] = str(error)
        print(str(error), file=sys.stderr)
        result = 1

    report.update(
        result="PASS" if result == 0 else "FAIL",
        binary_sha256=binary_hash,
        library_sha256=library_hash,
        finished_utc=datetime.now(timezone.utc).isoformat(),
    )
    options.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(f"Receipt: {options.report}")
    return result


if __name__ == "__main__":
    raise SystemExit(main())
