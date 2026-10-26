#!/usr/bin/env python3
"""Exercise L2 opaque Blob typing through the real project compiler."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "examples/files-workspace"


def invoke(binary: Path, project: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(binary), *arguments, str(project / "diamond.toml")],
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
    original_manifest = (FIXTURE / "diamond.toml").read_text()
    original_server = (FIXTURE / "server.dia").read_text()
    original_browser = (FIXTURE / "tracker.dia").read_text()
    cases: list[tuple[str, str, str, str, bool, str]] = []

    cases.append(("opaque-blob-control", original_manifest, original_server, original_browser, True, ""))
    cases.append(
        (
            "plain-text-cannot-replace-blob",
            original_manifest,
            original_server,
            original_browser.replace(
                'state attachment: Option<Blob<"issue_files">> = None',
                'state attachment: Text = ""',
            ),
            False,
            "state type",
        )
    )
    cases.append(
        (
            "plain-text-cannot-call-file-operation",
            original_manifest,
            original_server,
            original_browser.replace(
                'Some(file) => Some(files.download("issue_files", file))',
                'Some(file) => Some(files.download("issue_files", selected))',
            ),
            False,
            "remote argument type mismatch",
        )
    )
    second_binding = original_manifest + (
        '\n[files.bindings.archive_files]\nresource = "Issues"\nmax_bytes = 8388608\n'
    )
    cases.append(
        (
            "cross-binding-blob-denied",
            second_binding,
            original_server,
            original_browser.replace(
                'files.download("issue_files", file)',
                'files.download("archive_files", file)',
            ),
            False,
            "remote argument type mismatch",
        )
    )
    cases.append(
        (
            "blob-cannot-be-rendered",
            original_manifest,
            original_server,
            original_browser.replace(
                "  html:\n",
                "  html:\n    <p>{attachment}</p>\n",
                1,
            ),
            False,
            "render compound values",
        )
    )
    cases.append(
        (
            "blob-cannot-be-compared",
            original_manifest,
            original_server,
            original_browser.replace(
                'Some(file) => Some(files.download("issue_files", file))',
                'Some(file) => match file == file:\n        true => Some(files.download("issue_files", file))\n        false => None',
            ),
            False,
            "operator is not defined",
        )
    )
    rpc_server = original_server + (
        '\nexport fn echo_blob(value: Blob<"issue_files">) -> Blob<"issue_files">:\n  value\n'
    )
    rpc_manifest = original_manifest.replace(
        "[rpc]\n", '[rpc]\necho_blob = "server.echo_blob"\n', 1
    )
    cases.append(
        (
            "blob-cannot-cross-generic-rpc",
            rpc_manifest,
            rpc_server,
            original_browser,
            False,
            "wire-safe",
        )
    )
    cases.append(
        (
            "blob-cannot-be-persisted",
            original_manifest,
            original_server.replace(
                "type Issue:\n",
                'type Issue:\n  attachment: Blob<"issue_files">\n',
                1,
            ),
            original_browser,
            False,
            "database field",
        )
    )
    cases.append(
        (
            "blob-has-no-source-constructor",
            original_manifest,
            original_server,
            original_browser.replace(
                "state attachment: Option<Blob<\"issue_files\">> = None",
                'state attachment: Option<Blob<"issue_files">> = Some(Blob("forged"))',
                1,
            ),
            False,
            "unknown function Blob",
        )
    )

    observations: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(prefix="diamond-l2-") as temporary:
        root = Path(temporary)
        for index, (name, manifest, server, browser, accepted, diagnostic) in enumerate(cases):
            project = root / str(index)
            shutil.copytree(FIXTURE, project)
            (project / "diamond.toml").write_text(manifest)
            (project / "server.dia").write_text(server)
            (project / "tracker.dia").write_text(browser)
            result = invoke(binary, project, "check")
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

        build = root / "build"
        control = root / "control"
        shutil.copytree(FIXTURE, control)
        result = subprocess.run(
            [str(binary), "build", str(control / "diamond.toml"), "--out", str(build)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        runtime = next(build.glob("assets/*.js"), None)
        runtime_text = runtime.read_text() if runtime else ""
        observations.append(
            {
                "name": "generated-runtime-validates-opaque-blob",
                "exit": result.returncode,
                "pass": result.returncode == 0
                and "case'Blob'" in runtime_text
                and "opaque Blob" in runtime_text
                and "{$:'blob',binding,id:ready.id}" in runtime_text,
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
