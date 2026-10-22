#!/usr/bin/env python3
"""Drive actual CLI checks for independently specified W4 schema boundaries."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

from schema_fixture import MANIFEST, SERVER, write

ROOT = Path(__file__).resolve().parents[2]


def substitute(text, before, after):
    if text.count(before) != 1:
        raise AssertionError(("fixture mutation must apply exactly once", before, text.count(before)))
    return text.replace(before, after)


def stored_field(before, after):
    declaration, rest = SERVER.split("resource Rows:", 1)
    return substitute(declaration, before, after) + "resource Rows:" + rest


def cases():
    yield "supported-scalar-schema", MANIFEST, SERVER, True
    for name, shape in [("list-column", "List<Int>"), ("nested-option-column", "Option<Option<Int>>"),
                        ("result-column", "Result<Int, DataError>")]:
        yield name, MANIFEST, stored_field("  number: Int", "  number: " + shape), False
    yield "payload-enum-column", MANIFEST, substitute(SERVER, "  Closed", "  Closed(reason: Text)"), False
    yield "unsupported-profile", substitute(MANIFEST, 'profile = "relational-v1"', 'profile = "relational-v999"'), SERVER, False
    yield "version-zero", substitute(MANIFEST, "\nversion = 1\n", "\nversion = 0\n"), SERVER, False
    yield "removed-compatibility-range-option", substitute(MANIFEST, "\nversion = 1\n", "\nversion = 1\ncompatible_max = 2\n"), SERVER, False
    yield "unknown-resource", substitute(MANIFEST, "[storage.resources.Rows]", "[storage.resources.Missing]"), SERVER, False
    yield "missing-resource-configuration", MANIFEST.split("[storage.resources.Rows]")[0] + "resources = {}\n", SERVER, False
    yield "unknown-index-field", substitute(MANIFEST, 'indexes = [["workspace", "number", "id"]]', 'indexes = [["workspace", "missing"]]'), SERVER, False
    yield "duplicate-index-field", substitute(MANIFEST, 'indexes = [["workspace", "number", "id"]]', 'indexes = [["workspace", "number", "number"]]'), SERVER, False
    yield "empty-index", substitute(MANIFEST, 'indexes = [["workspace", "number", "id"]]', 'indexes = [[]]'), SERVER, False
    yield "unbounded-index-key", MANIFEST, stored_field("  title: Text<length: 1..80>", "  title: Text"), False
    yield "oversize-encoded-index-key", MANIFEST, stored_field("  title: Text<length: 1..80>", "  title: Text<length: 1..210>"), False
    if SERVER.count("  title: Text<length: 1..80>") != 3:
        raise AssertionError("three stored/create/update title declarations expected")
    yield "maximum-encoded-index-key", MANIFEST, SERVER.replace("  title: Text<length: 1..80>", "  title: Text<length: 1..209>"), True
    yield "unscoped-unique-disclosure", substitute(MANIFEST, 'unique = [["workspace", "title"]]', 'unique = [["title"]]'), SERVER, False
    yield "explicit-global-unique-disclosure", substitute(MANIFEST, 'unique = [["workspace", "title"]]', 'unique = [["title"]]\nglobal_unique = true'), SERVER, True
    yield "wrong-scope-type", substitute(MANIFEST, 'scope = "workspace"', 'scope = "number"'), SERVER, False
    yield "query-parameter-field-collision", MANIFEST, stored_field("  active: Bool", "  offset: Bool"), False
    yield "query-comparison-field-collision", MANIFEST, stored_field("  active: Bool", "  number__gte: Bool"), False
    yield "unknown-reference-resource", MANIFEST + '\n[storage.resources.Rows.references.parent]\nresource = "Missing"\nfields = {workspace = "id"}\n', SERVER, False
    cyclic = substitute(MANIFEST, 'unique = [["workspace", "title"]]', 'unique = [["workspace", "id"]]')
    yield "cyclic-self-reference", cyclic + '\n[storage.resources.Rows.references.parent]\nresource = "Rows"\nfields = {workspace = "workspace", id = "id"}\n', SERVER, False
    yield "unknown-storage-option", MANIFEST + '\n[storage.unknown]\nunsafe = true\n', SERVER, False
    lookup = '''
[storage.lookups.membership]
resource = "Rows"
scope = "workspace"
principal = "owner"
role = "phase"
purpose = "Independent scope membership qualification"
'''
    indexed = substitute(MANIFEST, 'indexes = [["workspace", "number", "id"]]', 'indexes = [["workspace", "number", "id"], ["workspace", "owner"]]')
    yield "declared-indexed-lookup", indexed + lookup, SERVER, True
    yield "lookup-missing-index", MANIFEST + lookup, SERVER, False
    yield "lookup-wrong-principal-type", indexed + substitute(lookup, 'principal = "owner"', 'principal = "number"'), SERVER, False
    yield "lookup-wrong-role-type", indexed + substitute(lookup, 'role = "phase"', 'role = "number"'), SERVER, False
    yield "lookup-missing-purpose", indexed + substitute(lookup, 'purpose = "Independent scope membership qualification"', 'purpose = ""'), SERVER, False
    yield "lookup-unknown-resource", indexed + substitute(lookup, 'resource = "Rows"', 'resource = "Missing"'), SERVER, False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, default=ROOT / "target/debug/diamond")
    parser.add_argument("--report", type=Path, required=True)
    options = parser.parse_args()
    root = Path(tempfile.mkdtemp(prefix="diamond-w4-independent-check-"))
    binary = root / "diamond"
    shutil.copy2(options.binary, binary)
    results, error = [], None
    try:
        for name, manifest, server, expected in cases():
            project = write(root / name, manifest, server)
            run = subprocess.run([str(binary), "check", str(project)], cwd=ROOT,
                                 capture_output=True, text=True, timeout=20)
            observed = run.returncode == 0
            results.append({"name": name, "status": "PASS" if observed == expected else "FAIL",
                            "expected_check_success": expected, "exit": run.returncode,
                            "stdout": run.stdout, "stderr": run.stderr,
                            "fixture_manifest_sha256": hashlib.sha256(manifest.encode()).hexdigest(),
                            "fixture_server_sha256": hashlib.sha256(server.encode()).hexdigest()})
    except Exception as failure:
        error = repr(failure)
    status = "FAIL" if error or any(result["status"] == "FAIL" for result in results) else "PASS"
    report = {"status": status, "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
              "error": error, "results": results, "temporary_root": str(root) if status == "FAIL" else None}
    options.report.write_text(json.dumps(report, indent=2) + "\n")
    if status == "PASS":
        shutil.rmtree(root)
    print(json.dumps({"status": status, "cases": len(results), "error": error,
                      "failures": [result["name"] for result in results if result["status"] == "FAIL"],
                      "report": str(options.report)}))
    return status != "PASS"


if __name__ == "__main__":
    raise SystemExit(main())
