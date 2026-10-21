#!/usr/bin/env python3
"""Run checkpoint-four real-interface replays and write a fingerprinted receipt.

Build first with cargo build --locked. Add --full to also build and run Rust
quality checks, the native embedding, and all three earlier checkpoint replays.
This runner records observed subprocess results; the individual drivers define
their independent behavioral oracles. Chrome is observed separately.
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


def fingerprint(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inputs() -> dict[str, str]:
    paths = [ROOT / "Cargo.toml", ROOT / "Cargo.lock", ROOT / "design.md"]
    paths += sorted((ROOT / "src").glob("*.rs"))
    paths += sorted((ROOT / "examples").glob("*"))
    paths += sorted((ROOT / "verification").glob("*.py"))
    paths += sorted((ROOT / "verification").glob("*.c"))
    paths += sorted((ROOT / "verification/regressions").rglob("*.dia"))
    return {str(path.relative_to(ROOT)): fingerprint(path) for path in paths if path.is_file()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--report", type=Path, default=ROOT / "verification/checkpoint_four_results.json")
    options = parser.parse_args()
    report = {"started_utc": datetime.now(timezone.utc).isoformat(),
              "full": options.full, "inputs_sha256": inputs(), "commands": []}
    report["rustc"] = subprocess.check_output(["rustc", "--version", "--verbose"], text=True).strip()
    report["cargo"] = subprocess.check_output(["cargo", "--version"], text=True).strip()
    commands: list[tuple[list[str], dict[str, str]]] = []
    if options.full:
        commands += [
            (["cargo", "fmt", "--check"], {}),
            (["cargo", "build", "--locked"], {}),
            (["cargo", "clippy", "--locked", "--all-targets", "--all-features", "--", "-D", "warnings"], {}),
            (["cargo", "test", "--locked"], {}),
            (["cargo", "doc", "--locked", "--no-deps"], {"RUSTDOCFLAGS": "-D warnings"}),
            (["cargo", "run", "--locked", "--example", "embedding"], {}),
        ]
        commands += [([sys.executable, f"verification/checkpoint_{name}.py"], {}) for name in ["one", "two", "three"]]
    commands += [([sys.executable, f"verification/{name}.py"], {}) for name in
                 ["compiler_enforcement", "web_enforcement", "policy_enforcement", "transaction_faults"]]
    result = 0
    binary_hash = None
    try:
        for command, extra in commands:
            # Freeze the expected artifact after the initial build, before consumers.
            if "verification/" in " ".join(command) or command[:2] == ["cargo", "run"]:
                current = fingerprint(BINARY)
                if binary_hash is None:
                    binary_hash = current
                assert current == binary_hash, "executable changed during verification"
            started = time.monotonic()
            completed = subprocess.run(command, cwd=ROOT, env=os.environ | extra,
                                       text=True, capture_output=True, timeout=300)
            observation = {"command": command, "environment_overrides": extra,
                           "exit": completed.returncode,
                           "seconds": round(time.monotonic() - started, 3),
                           "stdout": completed.stdout, "stderr": completed.stderr}
            try:
                observation["observations"] = json.loads(completed.stdout)
            except json.JSONDecodeError:
                pass
            report["commands"].append(observation)
            print(f'{"PASS" if completed.returncode == 0 else "FAIL"}: {" ".join(command)}', flush=True)
            if completed.returncode:
                print(completed.stdout + completed.stderr, file=sys.stderr)
                result = 1
                break
        assert report["inputs_sha256"] == inputs(), "verification inputs changed during execution"
        assert binary_hash is not None and fingerprint(BINARY) == binary_hash, "executable changed during verification"
    except (AssertionError, OSError, subprocess.SubprocessError) as error:
        report["runner_error"] = str(error)
        print(str(error), file=sys.stderr)
        result = 1
    report.update(result="PASS" if result == 0 else "FAIL", binary_sha256=binary_hash,
                  finished_utc=datetime.now(timezone.utc).isoformat())
    options.report.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(f'Receipt: {options.report}')
    return result


if __name__ == "__main__":
    raise SystemExit(main())
