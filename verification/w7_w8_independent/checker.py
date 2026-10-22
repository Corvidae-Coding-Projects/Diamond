#!/usr/bin/env python3
"""Actual compiler rejection campaign derived from W7 declared contracts."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

from fixture import write
from service import sha256

BASE = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--report", type=Path, default=BASE / "checker_results.json")
    options = parser.parse_args()
    root = Path(tempfile.mkdtemp(prefix="diamond-w7-independent-checker-"))
    binary = root / "diamond"
    shutil.copy2(options.binary, binary)
    scenarios = [
        ("valid-real-job-and-event-calls", None, None, None, True),
        ("unknown-static-job-binding", "server.dia", 'host.job_submit<Text>("notify", parent, payload, key)',
            'host.job_submit<Text>("unknown_job", parent, payload, key)', False),
        ("wrong-job-payload-type", "server.dia", 'host.job_submit<Text>("notify", parent, payload, key)',
            'host.job_submit<Int>("notify", parent, 42, key)', False),
        ("unknown-static-event-binding", "server.dia", 'host.event_emit<Text>("issue_changes", parent, payload)',
            'host.event_emit<Text>("unknown_event", parent, payload)', False),
        ("wrong-event-payload-type", "server.dia", 'host.event_emit<Text>("issue_changes", parent, payload)',
            'host.event_emit<Int>("issue_changes", parent, 42)', False),
        ("external-cannot-claim-transactional-delivery", "diamond.toml", 'delivery = "deduplicated"',
            'delivery = "transactional"', False),
        ("handler-cannot-enqueue-recursively", "diamond.toml", '"Http<inventory>", "JobKey", "Events"]\nmax_attempts',
            '"Http<inventory>", "JobKey", "Events", "Jobs"]\nmax_attempts', False),
        ("handler-missing-parent-write-authority", "diamond.toml", 'grants = ["DbRead<Issues>", "DbWrite<Issues>", "Http<inventory>"',
            'grants = ["DbRead<Issues>", "Http<inventory>"', False),
        ("zero-attempt-bound", "diamond.toml", "max_attempts = 3", "max_attempts = 0", False),
        ("unbounded-attempt-count", "diamond.toml", "max_attempts = 3", "max_attempts = 1000000", False),
    ]
    results = []
    for name, filename, old, new, success in scenarios:
        project = write(root / name)
        if filename:
            path = project.parent / filename
            source = path.read_text()
            if old not in source:
                raise AssertionError(("fixture mutation does not exercise its named scenario", name))
            path.write_text(source.replace(old, new))
        run = subprocess.run([str(binary), "check", str(project)], capture_output=True, text=True, timeout=20)
        results.append({"name": name, "status": "PASS" if (run.returncode == 0) == success else "FAIL",
                        "expected": "accept valid declared handlers and generic calls" if success else "reject checked boundary violation",
                        "exit": run.returncode, "stdout": run.stdout.strip(), "stderr": run.stderr.strip()})
    report = {"status": "PASS" if all(row["status"] == "PASS" for row in results) else "FAIL",
              "binary_sha256": sha256(binary), "results": results,
              "source_sha256": {path.name: sha256(path) for path in [Path(__file__), BASE / "fixture.py"]}}
    options.report.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"status": report["status"], "observations": len(results)}), flush=True)
    if report["status"] == "PASS":
        shutil.rmtree(root)
    return report["status"] != "PASS"


if __name__ == "__main__":
    raise SystemExit(main())
