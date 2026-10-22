#!/usr/bin/env python3
"""Real Diamond checks against prewritten explicit-API boundary requirements."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

from fixture import MANIFEST, SERVER, api_config, write
from service import sha256

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    options = parser.parse_args()
    root = Path(tempfile.mkdtemp(prefix="diamond-w5-independent-checker-"))
    binary = root / "diamond"
    shutil.copy2(options.binary, binary)
    results, fatal = [], None
    # Expected acceptance is fixed here before invoking the compiler.
    cases = [
        ("explicit-protected-public-api", MANIFEST + api_config(), SERVER, True),
        ("public-mutation-export", MANIFEST + api_config().replace('[http_api.operations.create]\nfunction = "server.create"\nauth = "token"', '[http_api.operations.create]\nfunction = "server.create"\nauth = "public"'), SERVER, False),
        ("unscoped-resource-needs-explicit-grant", MANIFEST + api_config().replace("allow_unscoped = true\n", ""), SERVER, False),
        ("reserved-api-metadata-operation", MANIFEST + api_config().replace("[http_api.operations.read]", "[http_api.operations._contract]", 1), SERVER, False),
        ("unknown-api-version", MANIFEST + api_config().replace('[http_api]\nversion = 1', '[http_api]\nversion = 2'), SERVER, False),
        ("cookie-is-not-api-auth-profile", MANIFEST + api_config().replace('auth = "token"', 'auth = "cookie"', 1), SERVER, False),
        ("unresolved-operation-binding", MANIFEST + api_config().replace('function = "server.read"', 'function = "server.missing"', 1), SERVER, False),
        ("implicit-unexported-handler", MANIFEST + api_config().replace('function = "server.read"', 'function = "server.internal"', 1), SERVER + '\nfn internal() -> Text:\n  "private"\n', False),
        ("api-needs-explicit-identity", MANIFEST.replace('[identity]\nprofile = "accounts-v1"\nenrollment = "open"\n', '') + api_config(), SERVER, False),
        ("enrollment-not-an-arbitrary-mode", MANIFEST.replace('enrollment = "open"', 'enrollment = "automatic-admin"') + api_config(), SERVER, False),
        ("unknown-api-operation-fields", MANIFEST + api_config().replace('function = "server.read"', 'function = "server.read"\nprincipal = "admin"', 1), SERVER, False),
    ]
    try:
        for name, manifest, server, expected in cases:
            project = write(root / name, api="")
            project.write_text(manifest)
            (project.parent / "server.dia").write_text(server)
            run = subprocess.run([str(binary), "check", str(project)], cwd=ROOT, capture_output=True, text=True, timeout=30)
            okay = (run.returncode == 0) == expected
            results.append({"name": name, "status": "PASS" if okay else "FAIL", "expected": "accept" if expected else "reject",
                            "observed": {"exit": run.returncode, "stdout": run.stdout, "stderr": run.stderr},
                            "manifest_sha256": hashlib.sha256(manifest.encode()).hexdigest(),
                            "server_sha256": hashlib.sha256(server.encode()).hexdigest()})
    except Exception as failure:
        fatal = repr(failure)
    report = {"status": "PASS" if fatal is None and all(item["status"] == "PASS" for item in results) else "FAIL",
              "binary_sha256": sha256(binary), "error": fatal, "results": results, "temporary_root": str(root)}
    options.report.write_text(json.dumps(report, indent=2) + "\n")
    if report["status"] == "PASS":
        shutil.rmtree(root)
    print(json.dumps({"status": report["status"], "observations": len(results), "error": fatal}))
    return report["status"] != "PASS"


if __name__ == "__main__":
    raise SystemExit(main())
