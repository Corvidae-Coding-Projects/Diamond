#!/usr/bin/env python3
"""Exercise L5 typed workflow contracts through the real compiler."""
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


def invoke(binary: Path, project: Path):
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
    workflow = manifest[manifest.index("[workflows.document_flow]"):]
    cases = [
        ("workflow-control", manifest, server, True, ""),
        ("capability-required", manifest.replace(', "Workflows"', ""), server, False,
         "exceed the application's declared authority"),
        ("known-state-enum", manifest.replace('state = "DocumentFlow"', 'state = "Missing"'),
         server, False, "state must name a declared enum"),
        ("fieldless-initial", manifest.replace('initial = "Queued"', 'initial = "Completed"'),
         server, False, "fieldless enum variant"),
        ("terminal-required", manifest.replace('terminal = ["Completed", "Cancelled"]',
                                                'terminal = []'),
         server, False, "explicit terminal states"),
        ("terminal-cannot-be-timer", manifest.replace('timers = { Waiting = 1 }',
                                                       'timers = { Completed = 1 }'),
         server, False, "bounded timers"),
        ("timer-bounded", manifest.replace('timers = { Waiting = 1 }',
                                            'timers = { Waiting = 86401 }'),
         server, False, "bounded timers"),
        ("attempts-bounded", manifest.replace(workflow, workflow.replace(
            "max_attempts = 3", "max_attempts = 9")), server, False, "bounded timers/retries"),
        ("transitions-bounded", manifest.replace("max_transitions = 8", "max_transitions = 257"),
         server, False, "bounded timers/retries"),
        ("transactional-retry-only", manifest.replace('retry = "transactional"',
                                                       'retry = "automatic"'),
         server, False, "transactional retry"),
        ("parent-concurrency-owned", manifest.replace('concurrency = "parent"',
                                                       'concurrency = "none"'),
         server, False, "parent concurrency"),
        ("typed-handler-state", manifest, server.replace(
            "export fn advance_document_flow(parent: Text, input: Text, state: DocumentFlow) -> Result<DocumentFlow, DataError>",
            "export fn advance_document_flow(parent: Text, input: Text, state: Text) -> Result<Text, DataError>"),
         False, "pattern is not a variant"),
        ("external-effects-prohibited", manifest, server.replace(
            "  let document = documents.get(DocumentId(parent))?\n  match state:",
            "  let document = documents.get(DocumentId(parent))?\n  let sent = host.http_post<Text, Text>(\"inventory\", \"/v1/workflow\", input)?\n  match state:", 1),
         False, "may not perform uncertain external effects"),
        ("generic-input-bound", manifest, server.replace(
            'host.workflow_submit<Text>("document_flow", parent, input, key)',
            'host.workflow_submit<Bool>("document_flow", parent, input, key)'),
         False, "generic type differs"),
        ("binding-static", manifest, server.replace(
            'host.workflow_status("document_flow", id)', 'host.workflow_status(id, id)'),
         False, "static identifier"),
    ]
    observations = []
    with tempfile.TemporaryDirectory(prefix="diamond-l5-", dir=ROOT / ".diamond") as temporary:
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
