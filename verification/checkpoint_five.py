#!/usr/bin/env python3
"""Collect final checkpoint-five quality and real-interface observations.

The default runs this pass's campaigns. --full also builds the current artifact,
runs every required Rust gate and replays checkpoints one through four without
overwriting their historical receipts. Chrome is observed separately.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
BINARY = ROOT / "target/debug/diamond"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inputs() -> dict[str, str]:
    paths = [ROOT / "Cargo.toml", ROOT / "Cargo.lock", ROOT / "README.md",
             ROOT / "design.md", ROOT / "verification/checkpoint_five.md"]
    for directory, pattern in [("src", "*.rs"), ("examples", "*"),
                               ("verification", "*.py"), ("verification", "*.rs"), ("verification", "*.c")]:
        paths += sorted((ROOT / directory).glob(pattern))
    paths += sorted((ROOT / "verification/regressions").rglob("*"))
    return {str(path.relative_to(ROOT)): digest(path) for path in paths if path.is_file()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--report", type=Path, default=ROOT / "verification/checkpoint_five_results.json")
    parser.add_argument(
        "--transaction-report",
        type=Path,
        default=ROOT / "verification/checkpoint_five_transactions.json",
    )
    parser.add_argument(
        "--cancellation-report",
        type=Path,
        default=ROOT / "verification/checkpoint_five_cancellation.json",
    )
    options = parser.parse_args()
    report = {"started_utc": datetime.now(timezone.utc).isoformat(), "full": options.full,
              "inputs_sha256": inputs(), "commands": []}
    report["rustc"] = subprocess.check_output(["rustc", "--version", "--verbose"], text=True).strip()
    report["cargo"] = subprocess.check_output(["cargo", "--version"], text=True).strip()
    report["python"] = sys.version
    report["cc"] = subprocess.check_output(["cc", "--version"], text=True).splitlines()[0]
    commands: list[tuple[list[str], dict[str, str]]] = []
    if options.full:
        commands += [
            (["cargo", "fmt", "--all", "--check"], {}),
            (["rustfmt", "--check", "--edition", "2024",
              "verification/session_lifecycle.rs", "verification/session_clock.rs"], {}),
            (["cargo", "build", "--locked"], {}),
            (["cargo", "clippy", "--locked", "--all-targets", "--all-features", "--", "-D", "warnings"], {}),
            (["cargo", "test", "--locked"], {}),
            (["cargo", "doc", "--locked", "--no-deps"], {"RUSTDOCFLAGS": "-D warnings"}),
            (["cargo", "run", "--locked", "--example", "embedding"], {}),
        ]
        commands += [([sys.executable, f"verification/checkpoint_{name}.py"], {}) for name in ["one", "two", "three"]]
        commands += [([sys.executable, f"verification/{name}.py"], {}) for name in
                     ["compiler_enforcement", "web_enforcement", "policy_enforcement"]]
        commands += [([sys.executable, "verification/transaction_faults.py", "--report",
                       str(options.transaction_report)], {})]
    commands += [
        ([sys.executable, "verification/language_exploration.py", "--replay-resource-regression"], {}),
        ([sys.executable, "verification/language_exploration.py", "--seed", "858630"], {}),
        ([sys.executable, "verification/resource_enforcement.py"], {}),
        ([sys.executable, "verification/stateful_exploration.py"], {}),
        ([sys.executable, "verification/stateful_exploration.py", "--seed", "858630"], {}),
        ([sys.executable, "verification/transport_exploration.py", "--wait-window"], {}),
        ([sys.executable, "verification/cancellation_recovery.py", "--report",
          str(options.cancellation_report)], {}),
        ([sys.executable, "verification/session_clock.py"], {}),
    ]
    binary_hash = None
    result = 0
    try:
        for command, extra in commands:
            if "verification/" in " ".join(command) or command[:2] == ["cargo", "run"]:
                current = digest(BINARY)
                binary_hash = binary_hash or current
                assert current == binary_hash, "executable changed during verification"
            started = time.monotonic()
            completed = subprocess.run(command, cwd=ROOT, env=os.environ | extra,
                                       text=True, capture_output=True, timeout=300)
            row = {"command": command, "cwd": str(ROOT), "environment_overrides": extra,
                   "exit": completed.returncode, "seconds": round(time.monotonic() - started, 3),
                   "stdout": completed.stdout, "stderr": completed.stderr}
            try:
                row["observations"] = json.loads(completed.stdout)
            except json.JSONDecodeError:
                pass
            observed = row.get("observations", {})
            accepted = completed.returncode == 0 and observed.get("status", observed.get("result", "PASS")) == "PASS"
            row["accepted"] = accepted
            report["commands"].append(row)
            print(f'{"PASS" if accepted else "FAIL"}: {" ".join(command)}', flush=True)
            if not accepted:
                print(completed.stdout + completed.stderr, file=sys.stderr)
                result = 1
                break
        assert report["inputs_sha256"] == inputs(), "verification inputs changed during execution"
        assert binary_hash and digest(BINARY) == binary_hash, "executable changed during verification"
        report["library_sha256"] = digest(ROOT / "target/debug/libdiamond.rlib")
    except (AssertionError, OSError, subprocess.SubprocessError) as error:
        report["runner_error"] = str(error)
        print(str(error), file=sys.stderr)
        result = 1
    report.update(result="PASS" if result == 0 else "FAIL", binary_sha256=binary_hash,
                  finished_utc=datetime.now(timezone.utc).isoformat())
    options.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(f"Receipt: {options.report}")
    return result


if __name__ == "__main__":
    raise SystemExit(main())
