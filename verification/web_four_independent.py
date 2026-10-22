#!/usr/bin/env python3
"""Pin and operate independently specified W4 CLI/HTTP/PG/native workflows."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
DRIVERS = ROOT / "verification/w4_independent"


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, default=ROOT / "target/debug/diamond")
    parser.add_argument("--library", type=Path, default=ROOT / "target/debug/libdiamond.rlib")
    parser.add_argument("--dependencies", type=Path, default=ROOT / "target/debug/deps")
    parser.add_argument("--report", type=Path, default=ROOT / "verification/web_four_independent_results.json")
    options = parser.parse_args()
    root = Path(tempfile.mkdtemp(prefix="diamond-w4-independent-run-"))
    binary, library = root / "diamond", root / "libdiamond.rlib"
    shutil.copy2(options.binary, binary)
    shutil.copy2(options.library, library)
    reports, regressions, error = {}, [], None
    try:
        fixture = DRIVERS / "findings/join_server_type/diamond.toml"
        run = subprocess.run([str(binary), "check", str(fixture)], cwd=ROOT, capture_output=True, text=True, timeout=30)
        regressions.append({"name": "source-level-server-join-output", "status": "PASS" if run.returncode == 0 else "FAIL",
                            "expected": "valid native server record resolves as configured join output",
                            "exit": run.returncode, "stdout": run.stdout, "stderr": run.stderr,
                            "fixture": str(fixture.relative_to(ROOT))})
        for name in ["checker", "scalars", "queries", "migrations", "faults"]:
            report_path = root / (name + ".json")
            command = ["python3", str(DRIVERS / (name + ".py")), "--binary", str(binary), "--report", str(report_path)]
            if name == "migrations":
                command += ["--library", str(library), "--dependencies", str(options.dependencies.resolve())]
            run = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=360)
            if not report_path.is_file():
                raise AssertionError((name, "missing result receipt", run.returncode, run.stdout, run.stderr))
            reports[name] = json.loads(report_path.read_text())
            if reports[name]["binary_sha256"] != digest(binary):
                raise AssertionError((name, "binary fingerprint mismatch"))
            if run.returncode != 0 or reports[name]["status"] != "PASS":
                raise AssertionError((name, run.returncode, run.stdout, run.stderr))
        if any(result["status"] != "PASS" for result in regressions):
            raise AssertionError("retained regression replay failed")
    except Exception as failure:
        error = repr(failure)
    finally:
        receipt = {"status": "FAIL" if error else "PASS", "binary_sha256": digest(binary), "library_sha256": digest(library),
                   "error": error, "reports": reports, "regressions": regressions,
                   "driver_sha256": {str(path.relative_to(ROOT)): digest(path) for path in sorted(DRIVERS.rglob("*"))
                                     if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".json"},
                   "scope": "current-schema relational-v1, CLI, restricted-role real PG/two real app nodes, model/actual-plan comparisons and external public API; browser operated separately; no exhaustive-security or production-capacity claim",
                   "temporary_root": str(root) if error else None}
        options.report.write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + "\n")
        if error is None:
            shutil.rmtree(root)
    print(json.dumps({"status": receipt["status"], "observations": {name: len(report["results"]) for name, report in reports.items()},
                      "rows": reports.get("queries", {}).get("rows"), "regressions": len(regressions), "error": error, "report": str(options.report)}))
    return error is not None


if __name__ == "__main__":
    raise SystemExit(main())
