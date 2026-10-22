#!/usr/bin/env python3
"""Portable runner for the independent verifier's W2 HTTP/SQL and CLI oracles.

The child drivers retain their independently chosen requirements and cases.
This wrapper pins an executable, supplies real fixture setup, checks child exit
codes/cases, redacts ephemeral CSRF values and cleans only owned temporary data.
"""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def scrub(value):
    if isinstance(value, dict):
        return {key: "[redacted]" if key in {"csrf", "password", "password_hash", "cookie"} else scrub(item) for key, item in value.items()}
    if isinstance(value, list):
        return [scrub(item) for item in value]
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, default=ROOT / "target/debug/diamond")
    parser.add_argument("--report", type=Path, default=ROOT / "verification/web_two_independent_results.json")
    options = parser.parse_args()
    root = Path(tempfile.mkdtemp(prefix="diamond-w2-independent-run-"))
    binary = root / "diamond"
    shutil.copy2(options.binary.resolve(), binary)
    digest = hashlib.sha256(binary.read_bytes()).hexdigest()
    spec = importlib.util.spec_from_file_location("w2_setup", ROOT / "verification/web_two.py")
    setup = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(setup)
    setup.BINARY = binary
    fixture_root = root / "fixture"
    fixture_root.mkdir()
    fixture = setup.Fixture(fixture_root)
    reports = {}
    child_directories = []
    error = None
    try:
        fixture.start()
        for name in ["http_cases", "cli", "transitive"]:
            script = ROOT / f"verification/w2_independent/{name}.py"
            arguments = [str(fixture_root / "state.json")] if name == "http_cases" else []
            result = subprocess.run(["python3", str(script), *arguments], cwd=ROOT,
                                    env=os.environ | {"DIAMOND_W2_BINARY": str(binary)},
                                    capture_output=True, text=True, timeout=180)
            if not result.stdout.strip():
                raise AssertionError((name,result.returncode,result.stderr))
            metadata = json.loads(result.stdout)
            directory = Path(metadata["root"])
            assert directory.parent == Path(tempfile.gettempdir()) and directory.name.startswith("diamond-w2-independent-")
            child_directories.append(directory)
            report = json.loads((directory / "report.json").read_text())
            reports[name] = scrub(report)
            cases = report["results"]
            assert result.returncode == 0 and all(case["status"] == "PASS" for case in cases), (name, result.stderr, [c for c in cases if c["status"] != "PASS"])
            assert report["binary_sha256"] == digest
    except Exception as failure:
        error = repr(failure)
    finally:
        fixture.close()
        receipt = {"status": "FAIL" if error else "PASS", "binary_sha256": digest,
                   "error": error, "reports": reports, "driver_sha256": {
                       path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                       for path in (ROOT / "verification/w2_independent").glob("*.py")},
                   "retained_directory": str(root) if error else None}
        options.report.write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + "\n")
        if error is None:
            for directory in child_directories:
                shutil.rmtree(directory)
            shutil.rmtree(root)
    print(json.dumps({"status": receipt["status"], "cases": {name: len(report["results"]) for name, report in reports.items()}, "error": error, "report": str(options.report)}))
    return int(error is not None)


if __name__ == "__main__":
    raise SystemExit(main())
