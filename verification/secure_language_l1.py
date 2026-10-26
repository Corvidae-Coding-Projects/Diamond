#!/usr/bin/env python3
"""Exercise L1 safe-document behavior through the real Diamond CLI."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]


def source(markdown: str) -> str:
    literal = json.dumps(markdown, ensure_ascii=False)
    return f"fn main() -> Html:\n  markdown({literal})\n"


def invoke(binary: Path, path: Path, command: str = "run") -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(binary), command, str(path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, default=ROOT / "target/debug/diamond")
    parser.add_argument("--report", type=Path)
    arguments = parser.parse_args()
    binary = arguments.binary.resolve()
    observations: list[dict[str, object]] = []

    cases = [
        ("formatting", "# Heading\n\n**strong** and `code`", ["<h1>Heading</h1>", "<strong>strong</strong>", "<code>code</code>"], []),
        ("raw-script", "<script>alert(1)</script>", [], ["<script", "</script>", "alert(1)"]),
        ("raw-event", '<img src=x onerror="alert(1)">', [], ["<img", "onerror", "src="]),
        ("markdown-image", "![tracking](https://example.invalid/pixel)", ["tracking"], ["<img", "example.invalid"]),
        ("javascript-link", "[link](javascript:alert(1))", ['href="#"'], ["javascript:"]),
        ("mixed-case-link", "[link](JAVASCRIPT:alert(1))", ['href="#"'], ["JAVASCRIPT:"]),
        ("data-link", "[link](data:text/html,attack)", ['href="#"'], ["data:text"]),
        ("network-path", "[link](//example.invalid/path)", ['href="#"'], ["example.invalid"]),
        ("https-link", "[link](https://example.invalid/path)", ['href="https://example.invalid/path"'], []),
        ("root-link", "[link](/projects/example)", ['href="/projects/example"'], []),
        ("relative-link", "[link](docs/readme)", ['href="docs/readme"'], []),
        ("fragment-link", "[link](#heading)", ['href="#heading"'], []),
    ]

    with tempfile.TemporaryDirectory(prefix="diamond-l1-") as temporary:
        directory = Path(temporary)
        for index, (name, markdown, required, forbidden) in enumerate(cases):
            path = directory / f"{index}.dia"
            path.write_text(source(markdown))
            result = invoke(binary, path)
            passed = result.returncode == 0 and all(value in result.stdout for value in required)
            passed = passed and all(value not in result.stdout for value in forbidden)
            observations.append(
                {
                    "name": name,
                    "exit": result.returncode,
                    "pass": passed,
                    "stdout_sha256": hashlib.sha256(result.stdout.encode()).hexdigest(),
                    "diagnostic": result.stderr.strip(),
                }
            )

        for name, markdown, diagnostic in [
            ("input-ceiling", "a" * (256 * 1024 + 1), "Markdown input"),
            ("output-ceiling", "&" * (256 * 1024), "rendered Markdown"),
        ]:
            path = directory / f"{name}.dia"
            path.write_text(source(markdown))
            result = invoke(binary, path)
            observations.append(
                {
                    "name": name,
                    "exit": result.returncode,
                    "pass": result.returncode != 0 and diagnostic in result.stderr,
                    "stdout_sha256": hashlib.sha256(result.stdout.encode()).hexdigest(),
                    "diagnostic": result.stderr.strip(),
                }
            )

        wrong_type = directory / "wrong-type.dia"
        wrong_type.write_text('fn main() -> Html:\n  "not trusted markup"\n')
        result = invoke(binary, wrong_type, "check")
        observations.append(
            {
                "name": "text-cannot-construct-html",
                "exit": result.returncode,
                "pass": result.returncode != 0 and "expected Html" in result.stderr,
                "stdout_sha256": hashlib.sha256(result.stdout.encode()).hexdigest(),
                "diagnostic": result.stderr.strip(),
            }
        )

        authority = ROOT / "examples/functions/authority"
        manifest = (authority / "diamond.toml").read_text()
        server = (authority / "server.dia").read_text()
        entry = 'export fn main() -> Text:\n  "Authority function examples"'
        secret_shapes = [
            (
                "secret-record-entry",
                'type SecretBox:\n  credential: Secret<"inventory">\n\n'
                'export fn main() -> SecretBox:\n  SecretBox(credential: host.secret("inventory"))',
                manifest,
            ),
            (
                "secret-enum-entry",
                'enum SecretState:\n  Leaked(value: Secret<"inventory">)\n\n'
                'export fn main() -> SecretState:\n  Leaked(host.secret("inventory"))',
                manifest,
            ),
            (
                "secret-record-rpc",
                entry
                + '\n\ntype SecretBox:\n  credential: Secret<"inventory">\n\n'
                + 'export fn leak() -> SecretBox:\n  SecretBox(credential: host.secret("inventory"))',
                manifest.replace("[rpc]\n", '[rpc]\nleak = "server.leak"\n'),
            ),
        ]
        for index, (name, replacement, project_manifest) in enumerate(secret_shapes):
            project = directory / f"secret-project-{index}"
            shutil.copytree(authority, project)
            (project / "diamond.toml").write_text(project_manifest)
            (project / "server.dia").write_text(server.replace(entry, replacement))
            result = invoke(binary, project / "diamond.toml", "check")
            observations.append(
                {
                    "name": name,
                    "exit": result.returncode,
                    "pass": result.returncode != 0
                    and ("opaque secrets" in result.stderr or "wire-safe" in result.stderr),
                    "stdout_sha256": hashlib.sha256(result.stdout.encode()).hexdigest(),
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
    print(json.dumps({"status": report["status"], "observations": len(observations), "failures": [row for row in observations if not row["pass"]]}))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
