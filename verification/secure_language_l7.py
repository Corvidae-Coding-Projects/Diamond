#!/usr/bin/env python3
"""Exercise L7 checked graph-query documents through the real compiler."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "examples/functions/authority"


def invoke(binary, project):
    return subprocess.run([str(binary), "check", str(project / "diamond.toml")], cwd=ROOT,
                          capture_output=True, text=True, timeout=20, check=False)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, default=ROOT / "target/debug/diamond")
    parser.add_argument("--report", type=Path)
    options = parser.parse_args()
    binary = options.binary.resolve()
    manifest = (FIXTURE / "diamond.toml").read_text()
    server = (FIXTURE / "server.dia").read_text()
    query_rpc = 'document_graph = "server.document_graph"\n'
    cases = [
        ("query-control", manifest, server, True, ""),
        ("same-named-rpc-required", manifest.replace(query_rpc, ""), server, False,
         "same-named RPC"),
        ("selection-exact", manifest.replace('selection = ["id", "owner", "title"]',
                                             'selection = ["id", "title"]'),
         server, False, "selection or depth"),
        ("selection-no-extra-field", manifest.replace('selection = ["id", "owner", "title"]',
                                                       'selection = ["id", "owner", "title", "secret"]'),
         server, False, "selection or depth"),
        ("resource-effects-exact", manifest.replace('resources = ["Documents"]',
                                                     'resources = []'),
         server, False, "exact read resources"),
        ("arbitrary-resolver-body-denied", manifest, server.replace(
            '  documents.page(order: "id", descending: false, after: after, limit: limit)',
            '  document_graph_helper(after, limit)').replace(
            'export fn document_graph(after: Text, limit: Int)',
            'fn document_graph_helper(after: Text, limit: Int) -> Result<List<Document>, DataError>:\n  documents.page(order: "id", descending: false, after: after, limit: limit)\n\nexport fn document_graph(after: Text, limit: Int)', 1),
         False, "single repository"),
        ("cursor-type-bound", manifest.replace('cursor_argument = "after"',
                                                'cursor_argument = "limit"'),
         server, False, "single repository"),
        ("limit-type-bound", manifest.replace('limit_argument = "limit"',
                                               'limit_argument = "after"'),
         server, False, "single repository"),
        ("depth-budget", manifest.replace("max_depth = 1", "max_depth = 0"), server, False,
         "depth/node/row/cost"),
        ("node-budget", manifest.replace("max_nodes = 40", "max_nodes = 39"), server, False,
         "depth/node/row/cost"),
        ("row-budget", manifest.replace("max_rows = 10", "max_rows = 101"), server, False,
         "depth/node/row/cost"),
        ("cost-budget", manifest.replace("max_cost = 40", "max_cost = 39"), server, False,
         "depth/node/row/cost"),
        ("database-query-budget", manifest.replace("max_queries = 1", "max_queries = 2"),
         server, False, "depth/node/row/cost"),
        ("timeout-budget", manifest.replace("timeout_ms = 5000", "timeout_ms = 4999"),
         server, False, "depth/node/row/cost"),
        ("response-budget", manifest.replace("max_response_bytes = 32768",
                                             "max_response_bytes = 0"),
         server, False, "depth/node/row/cost"),
    ]
    observations = []
    with tempfile.TemporaryDirectory(prefix="diamond-l7-", dir=ROOT / ".diamond") as temporary:
        for index, (name, candidate_manifest, candidate_server, accepted, diagnostic) in enumerate(cases):
            project = Path(temporary) / str(index)
            shutil.copytree(FIXTURE, project)
            (project / "diamond.toml").write_text(candidate_manifest)
            (project / "server.dia").write_text(candidate_server)
            result = invoke(binary, project)
            passed = (result.returncode == 0) == accepted and (not diagnostic or diagnostic in result.stderr)
            observations.append({"name": name, "expected_accept": accepted, "exit": result.returncode,
                                 "pass": passed, "diagnostic": result.stderr.strip()})
    report = {"status": "PASS" if all(item["pass"] for item in observations) else "FAIL",
              "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
              "observations": observations}
    if options.report:
        options.report.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"status": report["status"], "observations": len(observations),
                      "failures": [item for item in observations if not item["pass"]]}))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
