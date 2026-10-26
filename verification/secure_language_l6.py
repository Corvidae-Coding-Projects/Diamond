#!/usr/bin/env python3
"""Exercise L6 declarative adapter-owned secret consumption through the compiler."""
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
    secret = '''[secrets.inventory_bearer]
adapter = "http"
binding = "inventory"
kind = "bearer"
audience = "inventory"
'''
    cases = [
        ("declarative-auto-injection-control", manifest, server, True, ""),
        ("declaration-required-for-automatic-auth", manifest.replace(secret, ""), server, False,
         "requires a declarative secret binding"),
        ("adapter-closed", manifest.replace('adapter = "http"', 'adapter = "smtp"'), server,
         False, "adapter, kind, audience"),
        ("kind-closed", manifest.replace('kind = "bearer"', 'kind = "password"'), server,
         False, "adapter, kind, audience"),
        ("audience-bound", manifest.replace('audience = "inventory"', 'audience = "other"'),
         server, False, "invalid secret name, binding, or audience"),
        ("consumer-capability-required", manifest.replace('binding = "inventory"\nkind = "bearer"\naudience = "inventory"',
                                                           'binding = "missing"\nkind = "bearer"\naudience = "missing"'),
         server, False, "adapter, kind, audience"),
        ("one-secret-per-consumer", manifest + secret.replace("inventory_bearer", "duplicate"),
         server, False, "one secret declaration"),
        ("unknown-secret-field-denied", manifest.replace('kind = "bearer"',
                                                          'kind = "bearer"\nvalue = "never"'),
         server, False, "invalid project manifest"),
        ("automatic-post-body-typed", manifest, server.replace(
            'host.http_post<Text, Text>("inventory", path, body)',
            'host.http_post<Bool, Text>("inventory", path, body)'),
         False, "HTTP body type mismatch"),
        ("dynamic-destination-denied", manifest, server.replace(
            'host.http_get<Text>("inventory", path)', 'host.http_get<Text>(path, path)'),
         False, "destination requires a static name"),
    ]
    observations = []
    with tempfile.TemporaryDirectory(prefix="diamond-l6-", dir=ROOT / ".diamond") as temporary:
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
