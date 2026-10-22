#!/usr/bin/env python3
"""Replay the bounded W9 hostile/failure campaigns on one W10 executable."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time


ROOT = Path(__file__).resolve().parents[2]
BASE = Path(__file__).resolve().parent


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=BASE / "w9")
    parser.add_argument("--report", type=Path, default=BASE / "w9_campaign_results.json")
    parser.add_argument("--aggregate-only", action="store_true")
    options = parser.parse_args()
    output = options.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix="diamond-w10-w9-campaign-")) if not options.aggregate_only else None
    binary = temporary / "diamond" if temporary else options.binary.resolve()
    if temporary:
        shutil.copy2(options.binary.resolve(), binary)
    binary_hash = digest(binary)
    definitions = [
        ("transport", ROOT / "verification/w9/transport.py"),
        ("health_starvation", ROOT / "verification/w9/health_starvation.py"),
        ("storage_root_replacement", ROOT / "verification/w9/storage_root_replacement.py"),
        ("artifact_boundaries", ROOT / "verification/w9/artifact_boundaries.py"),
        ("edge_campaign", ROOT / "verification/w9_independent/edge_campaign.py"),
        ("websocket_malformed", ROOT / "verification/w9_independent/websocket_malformed.py"),
        ("auth_policy_races", ROOT / "verification/w9_independent/auth_policy_races.py"),
        ("running_cancel", ROOT / "verification/w9_independent/running_cancel.py"),
        ("stale_holder", ROOT / "verification/w9_independent/stale_holder.py"),
        ("recover_cancel", ROOT / "verification/w9_independent/recover_cancel.py"),
        ("job_quota_dedup", ROOT / "verification/w9_independent/job_quota_dedup.py"),
        ("storage_loss", ROOT / "verification/w9_independent/storage_loss.py"),
    ]

    def run(definition: tuple[str, Path]) -> dict[str, object]:
        name, driver = definition
        report_path = output / f"{name}.json"
        command = [sys.executable, str(driver), "--binary", str(binary), "--report", str(report_path)]
        started = time.monotonic()
        completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, timeout=900)
        value = json.loads(report_path.read_text()) if report_path.is_file() else {}
        row = {
            "name": name,
            "command": command,
            "exit": completed.returncode,
            "status": value.get("status"),
            "binary_sha256": value.get("binary_sha256"),
            "observations": len(value.get("results", [])),
            "shutdowns": len(value.get("shutdowns", [])),
            "temporary_root_removed": value.get("temporary_root_removed"),
            "seconds": round(time.monotonic() - started, 3),
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "report": str(report_path.relative_to(ROOT)),
        }
        print(json.dumps({key: row[key] for key in ["name", "status", "observations", "seconds"]}), flush=True)
        return row

    started = time.monotonic()
    rows = []
    error = None
    try:
        if options.aggregate_only:
            for name, driver in definitions:
                report_path = output / f"{name}.json"
                value = json.loads(report_path.read_text())
                observed = value.get("results", value.get("observations", []))
                rows.append({
                    "name": name,
                    "command": [sys.executable, str(driver), "--binary", str(binary), "--report", str(report_path)],
                    "exit": 0 if value.get("status") == "PASS" else 1,
                    "status": value.get("status"),
                    "binary_sha256": value.get("binary_sha256"),
                    "observations": len(observed),
                    "shutdowns": len(value.get("shutdowns", [])),
                    "temporary_root_removed": value.get("temporary_root_removed"),
                    "seconds": None,
                    "stdout": "",
                    "stderr": "",
                    "report": str(report_path.relative_to(ROOT)),
                })
        else:
            with ThreadPoolExecutor(max_workers=2) as pool:
                rows = list(pool.map(run, definitions))
    except Exception as failure:
        error = repr(failure)
    passed = (
        error is None
        and len(rows) == len(definitions)
        and all(row["exit"] == 0 and row["status"] == "PASS" and row["binary_sha256"] == binary_hash for row in rows)
    )
    source_paths = [path for _, path in definitions]
    source_paths.extend((ROOT / "verification/w9").glob("*.md"))
    source_paths.extend((ROOT / "verification/w9_independent").glob("*.md"))
    report = {
        "status": "PASS" if passed else "FAIL",
        "binary_sha256": binary_hash,
        "campaigns": rows,
        "campaign_count": len(rows),
        "observations": sum(int(row["observations"]) for row in rows),
        "shutdowns": sum(int(row["shutdowns"]) for row in rows),
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "error": error,
        "driver_sha256": {str(path.relative_to(ROOT)): digest(path) for path in sorted(source_paths)},
        "temporary_root_removed": passed and (temporary is None or temporary.exists()),
        "boundary": "W9 bounded hostile/failure replay; the separate W10 capacity and Chrome receipts cover sustained load and browser behavior.",
    }
    options.report.write_text(json.dumps(report, indent=2) + "\n")
    if passed and temporary:
        shutil.rmtree(temporary)
        report["temporary_root_removed"] = not temporary.exists()
        options.report.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"status": report["status"], "campaigns": len(rows), "observations": report["observations"], "shutdowns": report["shutdowns"], "elapsed_seconds": report["elapsed_seconds"], "error": error}))
    return int(not passed)


if __name__ == "__main__":
    raise SystemExit(main())
