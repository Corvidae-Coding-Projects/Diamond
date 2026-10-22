#!/usr/bin/env python3
"""Freeze one artifact and run the independent W7/W8 real-interface gates."""
from concurrent.futures import ThreadPoolExecutor
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

from service import sha256

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--caddy", type=Path, required=True)
    parser.add_argument("--report", type=Path, default=BASE / "results.json")
    options = parser.parse_args()
    root = Path(tempfile.mkdtemp(prefix="diamond-w7-w8-independent-final-"))
    binary, caddy = root / "diamond", root / "caddy"
    shutil.copy2(options.binary, binary)
    shutil.copy2(options.caddy, caddy)
    fingerprint = sha256(binary)
    started = time.monotonic()
    sources = list(BASE.glob("*.py")) + [BASE / "ORACLES.md", BASE / "ORACLE_CORRECTIONS.md"]
    sources.extend(BASE.parent / "w5_independent" / name for name in ["common.py", "service.py", "fixture.py"])
    sources.append(BASE.parent / "w6_independent/service.py")
    source_hashes = {str(path.relative_to(ROOT)): sha256(path) for path in sources}
    definitions = [("jobs_events", ["--short-ids"]), ("deployment", []), ("checker", []),
                   ("events_limits", []), ("tls_proxy", ["--caddy", str(caddy)]), ("job_versions", [])]

    def run(definition):
        name, extra = definition
        path = BASE / (name + "_results.json")
        command = [sys.executable, str(BASE / (name + ".py")), "--binary", str(binary), "--report", str(path), *extra]
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=150)
        report = json.loads(path.read_text()) if path.exists() else {}
        receipt = {"name": name, "exit": result.returncode, "status": report.get("status"),
                   "binary_sha256": report.get("binary_sha256"), "observations": len(report.get("results", [])),
                   "http_requests": report.get("http_requests", 0), "shutdowns": len(report.get("shutdowns", [])),
                   "report": str(path.relative_to(ROOT)), "stdout": result.stdout.strip(), "stderr": result.stderr.strip()}
        print(json.dumps({key: receipt[key] for key in ["name", "status", "observations", "http_requests"]}), flush=True)
        return receipt

    results, fatal = [], None
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(run, definitions))
    except Exception as error:
        fatal = repr(error)
    report = {"status": "PASS" if not fatal and len(results) == len(definitions) and
              all(r["exit"] == 0 and r["status"] == "PASS" and r["binary_sha256"] == fingerprint for r in results) else "FAIL",
              "binary_sha256": fingerprint, "caddy_sha256": sha256(caddy), "error": fatal,
              "elapsed_seconds": time.monotonic() - started, "observations": sum(r["observations"] for r in results),
              "http_requests": sum(r["http_requests"] for r in results), "shutdowns": sum(r["shutdowns"] for r in results),
              "steps": results, "source_sha256": source_hashes, "temporary_root": str(root)}
    options.report.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({key: report[key] for key in ["status", "binary_sha256", "observations", "http_requests", "shutdowns", "elapsed_seconds", "error"]}), flush=True)
    if report["status"] == "PASS":
        shutil.rmtree(root)
    return report["status"] != "PASS"


if __name__ == "__main__":
    raise SystemExit(main())
