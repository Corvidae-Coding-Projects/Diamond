#!/usr/bin/env python3
"""Validate the retained L8 evidence against one exact Diamond binary."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load(relative: str):
    path = ROOT / relative
    return path, json.loads(path.read_text())


def count(report) -> int:
    observations = report.get("observations", [])
    if isinstance(observations, int):
        return observations
    if observations:
        return len(observations)
    if report.get("results"):
        return len(report["results"])
    return int(report.get("total", 0))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, default=ROOT / "target/debug/diamond")
    parser.add_argument("--report", type=Path,
                        default=ROOT / "verification/secure_language_l8_results.json")
    options = parser.parse_args()
    binary_hash = hashlib.sha256(options.binary.resolve().read_bytes()).hexdigest()
    paths = [
        *(f"verification/secure_language_l8_components/l{level}_static.json"
          for level in range(1, 8)),
        *(f"verification/secure_language_l8_components/l{level}_runtime.json"
          for level in range(4, 8)),
        "verification/secure_language_l8_runtime_results.json",
        "verification/secure_language_l8_w6_results.json",
        "verification/secure_language_l8_w7w8/jobs_events.json",
        "verification/secure_language_l8_w7w8/job_versions.json",
        "verification/secure_language_l8_w7w8/deployment.json",
        "verification/secure_language_l8_w7w8/events_limits.json",
        "verification/secure_language_l8_w7w8/checker.json",
        "verification/live-workspace_browser_results.json",
        "verification/secure_language_l8_components/examples.json",
        "verification/git_compiler_boundaries_results.json",
    ]
    evidence = []
    failures = []
    for relative in paths:
        path, report = load(relative)
        row = {"path": str(path.relative_to(ROOT)), "status": report.get("status"),
               "binary_sha256": report.get("binary_sha256"),
               "observations": count(report)}
        evidence.append(row)
        if row["status"] != "PASS" or row["binary_sha256"] != binary_hash:
            failures.append(row)
    result = {
        "status": "PASS" if not failures else "FAIL",
        "binary_sha256": binary_hash,
        "evidence": evidence,
        "failures": failures,
        "totals": {
            "compiler_cases": sum(row["observations"] for row in evidence
                                  if row["path"].endswith("_static.json")),
            "new_runtime_observations": sum(row["observations"] for row in evidence
                                            if "l8_w6" not in row["path"] and
                                            (row["path"].endswith("_runtime.json") or
                                             row["path"].endswith("l8_runtime_results.json"))),
            "legacy_transport_observations": next(
                row["observations"] for row in evidence if "l8_w6_results" in row["path"]),
            "durability_recovery_observations": sum(
                row["observations"] for row in evidence if "l8_w7w8/" in row["path"]),
        },
    }
    options.report.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"status": result["status"], "binary_sha256": binary_hash,
                      "totals": result["totals"], "failures": failures}))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
