#!/usr/bin/env python3
"""Exercise L3 opaque Stream typing through the real project compiler."""

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
    original_browser = (FIXTURE / "ui.dia").read_text()
    watch = '<watch stream={stream("document_changes", document)} into="live" cursor="cursor"></watch>'
    cases: list[tuple[str, str, str, str, bool, str]] = []

    cases.append(("opaque-stream-control", original_manifest, original_server, original_browser, True, ""))
    cases.append(
        (
            "dynamic-channel-denied",
            original_manifest,
            original_server,
            original_browser.replace('stream("document_changes", document)', "stream(title, document)"),
            False,
            "stream channel must be a static name",
        )
    )
    cases.append(
        (
            "undeclared-channel-denied",
            original_manifest,
            original_server,
            original_browser.replace('stream("document_changes", document)', 'stream("missing", document)'),
            False,
            "unknown event channel",
        )
    )
    cases.append(
        (
            "non-text-parent-denied",
            original_manifest,
            original_server,
            original_browser.replace('stream("document_changes", document)', 'stream("document_changes", true)'),
            False,
            "stream requires a static channel and parent Text",
        )
    )
    cases.append(
        (
            "text-cannot-replace-stream",
            original_manifest,
            original_server,
            original_browser.replace('stream("document_changes", document)', "document"),
            False,
            "watch requires a typed Stream",
        )
    )
    cases.append(
        (
            "cross-channel-assignment-denied",
            original_manifest,
            original_server,
            original_browser.replace(
                '  state cursor: Text = ""',
                '  state cursor: Text = ""\n  state feed: Stream<"document_changes"> = stream("other", "parent")',
            ),
            False,
            "state initializer type mismatch",
        )
    )
    cases.append(
        (
            "stream-cannot-be-rendered",
            original_manifest,
            original_server,
            original_browser.replace(
                "  html:\n",
                '  html:\n    <p>{stream("document_changes", "parent")}</p>\n',
                1,
            ),
            False,
            "render compound values",
        )
    )
    cases.append(
        (
            "stream-cannot-be-compared",
            original_manifest,
            original_server,
            original_browser.replace(
                '  state cursor: Text = ""',
                '  state cursor: Text = ""\n  state equal: Bool = stream("document_changes", "parent") == stream("document_changes", "parent")',
            ),
            False,
            "operator is not defined",
        )
    )
    rpc_server = original_server + (
        '\nexport fn echo_stream(value: Stream<"document_changes">) -> Stream<"document_changes">:\n  value\n'
    )
    rpc_manifest = original_manifest.replace(
        "[rpc]\n", '[rpc]\necho_stream = "server.echo_stream"\n', 1
    )
    cases.append(
        (
            "stream-cannot-cross-generic-rpc",
            rpc_manifest,
            rpc_server,
            original_browser,
            False,
            "wire-safe",
        )
    )
    cases.append(
        (
            "stream-cannot-be-persisted",
            original_manifest,
            original_server.replace(
                "type Document:\n",
                'type Document:\n  feed: Stream<"document_changes">\n',
                1,
            ),
            original_browser,
            False,
            "database field",
        )
    )
    cases.append(
        (
            "legacy-untyped-watch-denied",
            original_manifest,
            original_server,
            original_browser.replace(
                watch,
                '<watch channel="document_changes" parent={document} into="live" cursor="cursor"></watch>',
            ),
            False,
            "HTML attribute channel",
        )
    )
    cases.append(
        (
            "stream-has-no-public-constructor",
            original_manifest,
            original_server,
            original_browser.replace(
                'stream("document_changes", document)',
                'Stream("document_changes", document)',
            ),
            False,
            "unknown function Stream",
        )
    )

    observations: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(prefix="diamond-l3-") as temporary:
        root = Path(temporary)
        for index, (name, manifest, server, browser, accepted, diagnostic) in enumerate(cases):
            project = root / str(index)
            shutil.copytree(FIXTURE, project)
            (project / "diamond.toml").write_text(manifest)
            (project / "server.dia").write_text(server)
            (project / "ui.dia").write_text(browser)
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
        required = [
            "case'Stream'",
            "opaque Stream",
            "stream.channel",
            "if(d.parent==='')continue",
            "event.data.length>65536",
            "if(++w.tries>3)",
            "w.socket?.close()",
            "credentials:'same-origin'",
        ]
        observations.append(
            {
                "name": "generated-runtime-owns-stream-safety",
                "exit": result.returncode,
                "pass": result.returncode == 0 and all(marker in runtime_text for marker in required),
                "diagnostic": result.stderr.strip(),
                "missing_markers": [marker for marker in required if marker not in runtime_text],
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
