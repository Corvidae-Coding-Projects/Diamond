#!/usr/bin/env python3
"""Validate the W10 production-module, CLI, operation and AC coverage ledger."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import tomllib


ROOT = Path(__file__).resolve().parents[2]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


MODULE_GROUPS = {
    "language and checking": {
        "lexer.rs", "parser.rs", "syntax.rs", "hir.rs", "checker.rs", "checker/integrations.rs", "template.rs",
    },
    "single-file runtime": {
        "accounts.rs", "auth.rs", "authority.rs", "browser_auth.rs", "data.rs", "diagnostic_help.rs", "error.rs",
        "eval.rs", "forms.rs", "host.rs", "http.rs", "integrations.rs", "policy.rs", "policy/postgres.rs", "standard.rs",
        "value.rs", "web.rs",
    },
    "public API and CLI": {"lib.rs", "main.rs"},
    "project compiler and frontend": {
        "project/api.rs", "project/artifact.rs", "project/assets.rs", "project/browser.rs", "project/lower.rs",
        "project/lower/asynchronous.rs", "project/lower/reach.rs", "project/mod.rs", "project/render.rs",
        "project/resolve.rs", "project/rpc.rs", "project/server.rs", "project/wire.rs",
    },
    "connected transport and deployment": {
        "project/connected.rs", "project/connected/profiles.rs", "project/deployment.rs", "project/deployment/backup.rs",
    },
    "shared identity": {
        "project/accounts/credentials.rs", "project/accounts/delivery.rs", "project/accounts/enrollment.rs",
        "project/accounts/mod.rs", "project/accounts/rates.rs", "project/accounts/routes.rs", "project/accounts/schema.rs",
        "project/accounts/sessions.rs", "project/identity.rs",
    },
    "relational storage": {
        "project/storage.rs", "relational/config.rs", "relational/migration.rs", "relational/mod.rs", "relational/pool.rs",
        "relational/query.rs", "relational/restriction.rs", "relational/runtime.rs", "relational/schema.rs", "relational/types.rs",
    },
    "private files": {"project/files.rs", "project/files/operations.rs", "project/files/transport.rs"},
    "jobs and integrations": {"project/jobs.rs", "project/jobs/worker.rs"},
    "live events": {"project/events.rs"},
}

CLI_COMMANDS = {
    "--help", "--version", "explain", "new", "check", "build", "serve", "run", "db-init", "user-add",
    "project-init", "project-files-init", "project-files-clean", "project-work", "project-deploy-check",
    "project-recover", "project-backup", "project-restore", "project-user-add", "project-admin-add", "project-status",
    "project-migrate", "project-backfill", "project-audit",
}

EVIDENCE = {
    "checkpoint-six": "verification/w10/checkpoint_six_results.json",
    "w3": "verification/w10/w3_results.json",
    "w5": "verification/w10/w5_results.json",
    "w6": "verification/w10/w6/results.json",
    "w7-w8": "verification/w10/w7_w8_results.json",
    "w9-hostile": "verification/w10/w9_campaign_results.json",
    "capacity": "verification/w10/capacity_results.json",
    "accessibility": "verification/w10/accessibility_results.json",
    "dependencies": "verification/w10/dependency_results.json",
    "workspace-browser": "verification/w10/workspace_browser_results.json",
    "catalog-browser": "verification/w10/catalog_browser_results.json",
}

AC_EVIDENCE = {
    1: ["checkpoint-six", "w9-hostile"], 2: ["w3", "checkpoint-six"], 3: ["w3", "workspace-browser", "catalog-browser"],
    4: ["w3"], 5: ["w3", "w5", "w9-hostile"], 6: ["w5", "w6", "w7-w8", "w9-hostile"],
    7: ["w7-w8", "capacity", "w9-hostile"], 8: ["w5", "w7-w8", "w9-hostile"], 9: ["w7-w8", "w9-hostile"],
    10: ["w5", "w9-hostile", "workspace-browser"], 11: ["w3", "accessibility", "workspace-browser", "catalog-browser"],
    12: ["w6", "w9-hostile", "workspace-browser"], 13: ["w6", "w9-hostile"], 14: ["w7-w8", "w9-hostile", "capacity"],
    15: ["w7-w8", "w9-hostile", "capacity"], 16: ["w6", "checkpoint-six"], 17: ["w7-w8", "w9-hostile", "capacity"],
    18: ["checkpoint-six", "accessibility", "workspace-browser", "catalog-browser"],
    19: ["w7-w8", "workspace-browser", "catalog-browser"], 20: list(EVIDENCE),
}


def operations() -> list[str]:
    values = []
    for application in ["live-workspace", "catalog"]:
        manifest = tomllib.loads((ROOT / "examples" / application / "diamond.toml").read_text())
        for name in manifest.get("rpc", {}):
            values.append(f"{application}:rpc:{name}")
        for name in manifest.get("http_api", {}).get("operations", {}):
            values.append(f"{application}:api:{name}")
        for name in manifest.get("files", {}).get("bindings", {}):
            values.append(f"{application}:file:{name}")
        for name in manifest.get("jobs", {}):
            values.append(f"{application}:job:{name}")
        for name in manifest.get("events", {}):
            values.append(f"{application}:event:{name}")
    return sorted(values)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--library", type=Path, required=True)
    parser.add_argument("--require-evidence", action="store_true")
    parser.add_argument("--report", type=Path, default=ROOT / "verification/w10/coverage_results.json")
    options = parser.parse_args()
    source_modules = {str(path.relative_to(ROOT / "src")) for path in (ROOT / "src").rglob("*.rs")}
    assigned = set().union(*MODULE_GROUPS.values())
    duplicates = sorted(path for path in assigned if sum(path in group for group in MODULE_GROUPS.values()) != 1)
    missing_modules = sorted(source_modules - assigned)
    nonexistent_modules = sorted(assigned - source_modules)

    main_source = (ROOT / "src/main.rs").read_text()
    absent_commands = sorted(command for command in CLI_COMMANDS if f'"{command}"' not in main_source)
    operation_list = operations()
    evidence_rows = {}
    failures = []
    binary_hash = digest(options.binary)
    library_hash = digest(options.library)
    for name, relative in EVIDENCE.items():
        path = ROOT / relative
        row: dict[str, object] = {"path": relative, "present": path.is_file()}
        if path.is_file():
            value = json.loads(path.read_text())
            row.update(status=value.get("status", value.get("result")), sha256=digest(path))
            if row["status"] != "PASS":
                failures.append(f"{name} evidence did not pass")
            receipt_binary = value.get("binary_sha256")
            receipt_library = value.get("library_sha256")
            if receipt_binary and receipt_binary != binary_hash:
                failures.append(f"{name} executable hash differs")
            if receipt_library and receipt_library != library_hash:
                failures.append(f"{name} library hash differs")
        elif options.require_evidence:
            failures.append(f"{name} evidence is missing")
        evidence_rows[name] = row
    if missing_modules or nonexistent_modules or duplicates:
        failures.append("production module ledger is incomplete or ambiguous")
    if absent_commands:
        failures.append("CLI command ledger contains absent commands")
    if sorted(AC_EVIDENCE) != list(range(1, 21)) or any(not names for names in AC_EVIDENCE.values()):
        failures.append("acceptance-criterion evidence map is incomplete")
    report = {
        "status": "PASS" if not failures else "FAIL",
        "binary_sha256": binary_hash,
        "library_sha256": library_hash,
        "production_modules": {name: sorted(paths) for name, paths in MODULE_GROUPS.items()},
        "production_module_count": len(source_modules),
        "missing_modules": missing_modules,
        "nonexistent_modules": nonexistent_modules,
        "duplicate_module_assignments": duplicates,
        "cli_commands": sorted(CLI_COMMANDS),
        "absent_cli_commands": absent_commands,
        "reference_operations": operation_list,
        "acceptance_criteria": {f"AC-{number}": AC_EVIDENCE[number] for number in range(1, 21)},
        "evidence": evidence_rows,
        "failures": failures,
    }
    options.report.parent.mkdir(parents=True, exist_ok=True)
    options.report.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"status": report["status"], "modules": len(source_modules), "commands": len(CLI_COMMANDS), "operations": len(operation_list), "evidence": len(EVIDENCE), "failures": failures}))
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
