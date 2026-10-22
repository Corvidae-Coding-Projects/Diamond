#!/usr/bin/env python3
"""Record the dated W10 Rust dependency, license and source review."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tomllib


ROOT = Path(__file__).resolve().parents[2]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def invoke(arguments: list[str], environment: dict[str, str]) -> dict[str, object]:
    completed = subprocess.run(
        arguments,
        cwd=ROOT,
        env=os.environ | environment,
        text=True,
        capture_output=True,
        timeout=300,
    )
    return {
        "command": arguments,
        "exit": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def advisory_revisions(cargo_home: Path) -> list[dict[str, str]]:
    revisions = []
    for candidate in [cargo_home / "advisory-db", *(cargo_home / "advisory-dbs").glob("*")]:
        if not (candidate / ".git").exists():
            continue
        head = subprocess.run(
            ["git", "-C", str(candidate), "log", "-1", "--format=%H%n%cI%n%s"],
            text=True,
            capture_output=True,
            timeout=30,
        )
        lines = head.stdout.splitlines()
        if head.returncode == 0 and len(lines) >= 3:
            revisions.append({"path": str(candidate), "commit": lines[0], "committed": lines[1], "subject": lines[2]})
    return revisions


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cargo-home", type=Path, required=True)
    parser.add_argument("--report", type=Path, default=ROOT / "verification/w10/dependency_results.json")
    options = parser.parse_args()
    environment = {"CARGO_HOME": str(options.cargo_home.resolve()), "CARGO_TERM_COLOR": "never"}

    audit = invoke(["cargo", "audit", "--json", "--no-fetch"], environment)
    try:
        audit_json = json.loads(str(audit["stdout"]))
    except json.JSONDecodeError:
        audit_json = {}

    deny = invoke(["cargo", "deny", "--offline", "--locked", "check"], environment)
    metadata_result = invoke(
        ["cargo", "metadata", "--offline", "--locked", "--format-version", "1"],
        environment,
    )
    try:
        metadata = json.loads(str(metadata_result["stdout"]))
    except json.JSONDecodeError:
        metadata = {}
    packages = metadata.get("packages", [])
    versions: dict[str, set[str]] = {}
    registries: set[str] = set()
    for package in packages:
        versions.setdefault(package["name"], set()).add(package["version"])
        if package.get("source"):
            registries.add(package["source"])
    duplicates = {name: sorted(values) for name, values in versions.items() if len(values) > 1}

    policy = tomllib.loads((ROOT / "deny.toml").read_text())
    direct = tomllib.loads((ROOT / "Cargo.toml").read_text())["dependencies"]
    wildcard_direct = sorted(
        name for name, value in direct.items()
        if value == "*" or isinstance(value, dict) and value.get("version") == "*"
    )
    deny_text = str(deny["stdout"]) + str(deny["stderr"])
    reported_duplicates = sorted(set(re.findall(r"crate '([^']+)'", deny_text)))
    failures = []
    if audit["exit"] != 0 or audit_json.get("vulnerabilities", {}).get("found") is not False:
        failures.append("RustSec audit did not report a clean locked graph")
    if deny["exit"] != 0:
        failures.append("cargo-deny policy failed")
    if metadata_result["exit"] != 0 or not packages:
        failures.append("locked offline cargo metadata was unavailable")
    if wildcard_direct:
        failures.append("direct wildcard dependencies are present")
    if not advisory_revisions(options.cargo_home):
        failures.append("advisory database revision was unavailable")

    report = {
        "status": "PASS" if not failures else "FAIL",
        "reviewed_utc": datetime.now(timezone.utc).isoformat(),
        "cargo_lock_sha256": digest(ROOT / "Cargo.lock"),
        "cargo_toml_sha256": digest(ROOT / "Cargo.toml"),
        "deny_toml_sha256": digest(ROOT / "deny.toml"),
        "tool_versions": {
            tool: subprocess.check_output([tool, "--version"], text=True).strip()
            for tool in ["cargo", "rustc", "cargo-audit", "cargo-deny"]
        },
        "advisory_databases": advisory_revisions(options.cargo_home),
        "audit": audit_json,
        "cargo_deny": {
            "exit": deny["exit"],
            "summary": deny_text.strip().splitlines()[-1:] or [""],
            "reported_duplicate_names": reported_duplicates,
        },
        "graph": {
            "packages": len(packages),
            "locked_dependencies": audit_json.get("lockfile", {}).get("dependency-count"),
            "registries": sorted(registries),
            "duplicate_versions": duplicates,
            "direct_dependencies": sorted(direct),
            "direct_wildcards": wildcard_direct,
        },
        "policy": {
            "allowed_licenses": policy["licenses"]["allow"],
            "allowed_registries": policy["sources"]["allow-registry"],
            "unknown_git": policy["sources"]["unknown-git"],
            "unknown_registry": policy["sources"]["unknown-registry"],
            "yanked": policy["advisories"]["yanked"],
        },
        "failures": failures,
        "boundary": "Dated locked-graph review for x86_64-unknown-linux-gnu; repeat after lockfile or advisory database changes.",
    }
    options.report.parent.mkdir(parents=True, exist_ok=True)
    options.report.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"status": report["status"], "packages": len(packages), "duplicates": len(duplicates), "vulnerabilities": audit_json.get("vulnerabilities", {}).get("count"), "failures": failures}))
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
