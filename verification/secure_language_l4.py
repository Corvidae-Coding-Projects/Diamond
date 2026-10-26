#!/usr/bin/env python3
"""Exercise L4 manifest-bound mail through the real project compiler."""

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


def invoke(binary: Path, project: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(binary), "check", str(project / "diamond.toml")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, default=ROOT / "target/debug/diamond")
    parser.add_argument("--report", type=Path)
    arguments = parser.parse_args()
    binary = arguments.binary.resolve()
    manifest = (FIXTURE / "diamond.toml").read_text()
    server = (FIXTURE / "server.dia").read_text()
    cases: list[tuple[str, str, str, bool, str]] = [
        ("mail-control", manifest, server, True, ""),
        (
            "missing-mail-capability",
            manifest.replace(', "Mail<document_mail>"', ""),
            server,
            False,
            "exceed the application's declared authority",
        ),
        (
            "dynamic-binding-denied",
            manifest,
            server.replace(
                'host.mail_submit("document_mail", parent, recipient, "notice", body, key)',
                'host.mail_submit(parent, parent, recipient, "notice", body, key)',
            ),
            False,
            "mail binding requires a static name",
        ),
        (
            "unknown-binding-denied",
            manifest,
            server.replace('host.mail_status("document_mail", id)', 'host.mail_status("missing", id)'),
            False,
            "unknown mail binding",
        ),
        (
            "dynamic-template-denied",
            manifest,
            server.replace(
                'host.mail_submit("document_mail", parent, recipient, "notice", body, key)',
                'host.mail_submit("document_mail", parent, recipient, key, body, key)',
            ),
            False,
            "mail template requires a static name",
        ),
        (
            "unknown-template-denied",
            manifest,
            server.replace(
                'host.mail_submit("document_mail", parent, recipient, "notice", body, key)',
                'host.mail_submit("document_mail", parent, recipient, "missing", body, key)',
            ),
            False,
            "unknown mail template",
        ),
        (
            "header-injection-denied",
            manifest.replace(
                'subject = "Document notification"',
                'subject = "Document notification\\nBcc: victim@example.invalid"',
            ),
            server,
            False,
            "invalid plain-text mail template",
        ),
        (
            "recipient-type-denied",
            manifest,
            server.replace(
                'host.mail_submit("document_mail", parent, recipient, "notice", body, key)',
                'host.mail_submit("document_mail", parent, true, "notice", body, key)',
            ),
            False,
            "mail arguments do not match",
        ),
        (
            "secret-body-denied",
            manifest,
            server.replace(
                'host.mail_submit("document_mail", parent, recipient, "notice", body, key)',
                'host.mail_submit("document_mail", parent, recipient, "notice", host.secret("inventory"), key)',
            ),
            False,
            "mail arguments do not match",
        ),
        (
            "binding-resource-denied",
            manifest.replace('resource = "Documents"\nmax_attempts = 3', 'resource = "Missing"\nmax_attempts = 3'),
            server,
            False,
            "invalid durable binding resource",
        ),
        (
            "attempt-bound-denied",
            manifest.replace("max_attempts = 3", "max_attempts = 9"),
            server,
            False,
            "absent mail/resource capabilities",
        ),
    ]

    observations: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(prefix="diamond-l4-", dir=ROOT / ".diamond") as temporary:
        root = Path(temporary)
        for index, (name, project_manifest, project_server, accepted, diagnostic) in enumerate(cases):
            project = root / str(index)
            shutil.copytree(FIXTURE, project)
            (project / "diamond.toml").write_text(project_manifest)
            (project / "server.dia").write_text(project_server)
            result = invoke(binary, project)
            passed = (result.returncode == 0) == accepted
            if diagnostic:
                passed = passed and diagnostic in result.stderr
            observations.append(
                {
                    "name": name,
                    "expected_accept": accepted,
                    "exit": result.returncode,
                    "pass": passed,
                    "diagnostic": result.stderr.strip(),
                }
            )

    report = {
        "status": "PASS" if all(row["pass"] for row in observations) else "FAIL",
        "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
        "observations": observations,
    }
    if arguments.report:
        arguments.report.write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {
                "status": report["status"],
                "observations": len(observations),
                "failures": [row for row in observations if not row["pass"]],
            }
        )
    )
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
