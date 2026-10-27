#!/usr/bin/env python3
"""Exercise LP7 through the real formatter, CLI, LSP, and browser compiler."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import select
import shutil
import subprocess
import tempfile
import time


ROOT = Path(__file__).resolve().parents[1]
MAX_MESSAGE = 1_048_576


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, default=ROOT / "target/debug/diamond")
    parser.add_argument("--report", type=Path, default=ROOT / "verification/lp7/tools.json")
    args = parser.parse_args()
    binary = args.binary.resolve()
    observations: list[dict[str, object]] = []

    def observe(name: str, passed: bool, **detail: object) -> None:
        observations.append({"name": name, "pass": passed, **detail})

    def invoke(*arguments: object) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(binary), *map(str, arguments)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )

    temporary_parent = ROOT / ".diamond"
    temporary_parent.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="lp7-", dir=temporary_parent) as raw_directory:
        directory = Path(raw_directory)
        valid = directory / "format.dia"
        original = (
            "# café formatter fixture\r\n"
            "fn message() -> Text:\r\n"
            "    # nested comment stays with this block\r\n"
            "    \"snowman ☃ and escaped \\\\n text\"\r\n"
            "\r\n"
            "fn view() -> Html:\r\n"
            "    html:\r\n"
            "        <main>\r\n"
            "          <p>Unicode 雪 and template spacing</p>\r\n"
            "        </main>\r\n"
        ).encode()
        valid.write_bytes(original)
        result = invoke("fmt", "--check", valid)
        observe(
            "F2-check-detects-change-without-write",
            result.returncode != 0 and valid.read_bytes() == original and "would be reformatted" in result.stderr,
            stderr=result.stderr.strip(),
        )
        result = invoke("fmt", valid)
        first = valid.read_bytes()
        preserve = all(
            value in first
            for value in [
                "# café formatter fixture".encode(),
                "# nested comment stays with this block".encode(),
                "snowman ☃ and escaped \\\\n text".encode(),
                "Unicode 雪 and template spacing".encode(),
            ]
        )
        checked = invoke("check", valid)
        observe(
            "F1-format-valid-preserves-content-and-checks",
            result.returncode == 0 and checked.returncode == 0 and preserve and b"\r" not in first,
            formatter_stderr=result.stderr.strip(),
            checker_stderr=checked.stderr.strip(),
            output_sha256=hashlib.sha256(first).hexdigest(),
        )
        second_result = invoke("fmt", valid)
        second = valid.read_bytes()
        final_check = invoke("fmt", "--check", valid)
        observe(
            "F1-format-idempotent",
            second_result.returncode == 0 and first == second and final_check.returncode == 0,
            stderr=(second_result.stderr + final_check.stderr).strip(),
        )

        malformed = directory / "malformed.dia"
        malformed_source = b"fn main() -> Int:\n  (1 +\n"
        malformed.write_bytes(malformed_source)
        result = invoke("fmt", malformed)
        observe(
            "F2-malformed-unchanged",
            result.returncode != 0 and malformed.read_bytes() == malformed_source and "error[D010]" in result.stderr,
            stderr=result.stderr.strip(),
        )

        diagnostic = directory / "unicode-diagnostic.dia"
        diagnostic.write_text(
            'fn main() -> Int:\n  let private_value = "DO_NOT_PRINT_雪"\n  private_value\n',
            encoding="utf-8",
        )
        result = invoke("check", diagnostic)
        observe(
            "D1-single-file-code-location-caret-remedy",
            result.returncode != 0
            and "error[D110]" in result.stderr
            and f"{diagnostic}:3:3" in result.stderr
            and "^" in result.stderr
            and "help:" in result.stderr
            and "DO_NOT_PRINT_雪" not in result.stderr,
            stderr=result.stderr.strip(),
        )

        project = directory / "project"
        shutil.copytree(ROOT / "examples/foundations", project)
        (project / "shared.dia").write_text(
            'export fn bump(value: Int) -> Int:\n  "wrong"\n\n'
            'export fn welcome() -> Text:\n  "project fixture"\n',
            encoding="utf-8",
        )
        result = invoke("check", project / "diamond.toml")
        observe(
            "D1-project-module-path-code-caret-remedy",
            result.returncode != 0
            and "shared.dia" in result.stderr
            and "error[D110]" in result.stderr
            and "^" in result.stderr
            and "help:" in result.stderr,
            stderr=result.stderr.strip(),
        )

        lsp_observations = exercise_lsp(binary, directory)
        observations.extend(lsp_observations)

        build = directory / "browser-build"
        result = invoke("build", ROOT / "verification/lp1/browser/diamond.toml", "--out", build)
        if result.returncode == 0:
            javascript = "\n".join(path.read_text(encoding="utf-8") for path in (build / "assets").glob("*.js"))
            maps = "\n".join(path.read_text(encoding="utf-8") for path in (build / "assets").glob("*.map"))
        else:
            javascript = maps = ""
        observe(
            "B1-handler-and-component-source-map-private-source-absent",
            result.returncode == 0
            and 'Contract.fail' in javascript
            and '__handlerLocations' in javascript
            and "diamond-source:" in javascript
            and '"ui.dia"' in maps
            and "SERVER_ONLY_CANARY" not in javascript
            and "SERVER_ONLY_CANARY" not in maps,
            build_stderr=result.stderr.strip(),
        )

        example_failures: list[dict[str, object]] = []
        example_commands: list[list[str]] = []
        for manifest in sorted((ROOT / "examples").rglob("diamond.toml")):
            example_commands.append(["check", str(manifest)])
        for source in sorted((ROOT / "examples").glob("*.dia")):
            example_commands.append(["check", str(source)])
        for command in example_commands:
            result = invoke(*command)
            if result.returncode != 0:
                example_failures.append({"command": command, "stderr": result.stderr.strip()})
        observe(
            "C1-current-examples-check",
            not example_failures,
            commands=len(example_commands),
            failures=example_failures,
        )

    report = {
        "status": "PASS" if all(row["pass"] for row in observations) else "FAIL",
        "binary_sha256": sha256(binary),
        "driver_sha256": sha256(Path(__file__)),
        "scenarios_sha256": sha256(ROOT / "verification/lp7/SCENARIOS.md"),
        "observations": observations,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return int(report["status"] != "PASS")


def exercise_lsp(binary: Path, directory: Path) -> list[dict[str, object]]:
    observations: list[dict[str, object]] = []
    process = subprocess.Popen(
        [str(binary), "lsp"],
        cwd=ROOT,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert process.stdin is not None and process.stdout is not None and process.stderr is not None
    next_id = 1

    def send(method: str, params: object | None = None, request: bool = False) -> int | None:
        nonlocal next_id
        message: dict[str, object] = {"jsonrpc": "2.0", "method": method}
        request_id = None
        if params is not None:
            message["params"] = params
        if request:
            request_id = next_id
            next_id += 1
            message["id"] = request_id
        body = json.dumps(message, separators=(",", ":")).encode()
        process.stdin.write(f"Content-Length: {len(body)}\r\n\r\n".encode() + body)
        process.stdin.flush()
        return request_id

    def receive(timeout: float = 5.0) -> dict[str, object]:
        deadline = time.monotonic() + timeout
        header = bytearray()
        while not header.endswith(b"\r\n\r\n"):
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([process.stdout], [], [], remaining)[0]:
                raise TimeoutError("LSP response header")
            byte = os.read(process.stdout.fileno(), 1)
            if not byte:
                raise EOFError(process.stderr.read().decode(errors="replace"))
            header.extend(byte)
        length = None
        for line in header.decode().splitlines():
            if line.lower().startswith("content-length:"):
                length = int(line.split(":", 1)[1].strip())
        if length is None:
            raise AssertionError(header)
        body = bytearray()
        while len(body) < length:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([process.stdout], [], [], remaining)[0]:
                raise TimeoutError("LSP response body")
            body.extend(os.read(process.stdout.fileno(), length - len(body)))
        return json.loads(body)

    def response(request_id: int) -> dict[str, object]:
        while True:
            message = receive()
            if message.get("id") == request_id:
                return message

    try:
        request_id = send("initialize", {"capabilities": {}}, True)
        assert request_id is not None
        initialized = response(request_id)
        capabilities = initialized.get("result", {}).get("capabilities", {})
        observations.append(
            {
                "name": "E1-initialize-bounded-compiler-capabilities",
                "pass": capabilities.get("positionEncoding") == "utf-32"
                and capabilities.get("hoverProvider") is True
                and capabilities.get("definitionProvider") is True
                and capabilities.get("referencesProvider") is True,
                "capabilities": capabilities,
            }
        )
        send("initialized", {})
        source = (
            "type Person:\n"
            "  name: Text\n"
            "fn greet(person: Person) -> Text:\n"
            "  person.name\n"
            "fn main() -> Text:\n"
            "  greet(Person(name: \"Ada 雪\"))\n"
        )
        uri = (directory / "editor.dia").resolve().as_uri()
        send("textDocument/didOpen", {"textDocument": {"uri": uri, "languageId": "diamond", "version": 1, "text": source}})
        opened = receive()
        diagnostics = opened.get("params", {}).get("diagnostics", [])

        hover_id = send("textDocument/hover", {"textDocument": {"uri": uri}, "position": {"line": 2, "character": 5}}, True)
        definition_id = send("textDocument/definition", {"textDocument": {"uri": uri}, "position": {"line": 5, "character": 4}}, True)
        references_id = send("textDocument/references", {"textDocument": {"uri": uri}, "position": {"line": 5, "character": 4}, "context": {"includeDeclaration": True}}, True)
        completion_id = send("textDocument/completion", {"textDocument": {"uri": uri}, "position": {"line": 3, "character": 9}}, True)
        assert None not in (hover_id, definition_id, references_id, completion_id)
        hover = response(int(hover_id))
        definition = response(int(definition_id))
        references = response(int(references_id))
        completion = response(int(completion_id))
        labels = [item.get("label") for item in completion.get("result", {}).get("items", [])]
        observations.append(
            {
                "name": "E1-current-document-hover-definition-references-completion",
                "pass": not diagnostics
                and "greet" in json.dumps(hover.get("result"))
                and definition.get("result", {}).get("range", {}).get("start", {}).get("line") == 2
                and len(references.get("result", [])) == 2
                and "name" in labels,
                "hover": hover.get("result"),
                "definition": definition.get("result"),
                "reference_count": len(references.get("result", [])),
                "completion_labels": labels,
            }
        )

        malformed = 'fn main() -> Int:\n  "wrong 雪"\n'
        send("$/cancelRequest", {"id": 9999})
        send("textDocument/didChange", {"textDocument": {"uri": uri, "version": 2}, "contentChanges": [{"text": malformed}]})
        changed = receive()
        changed_params = changed.get("params", {})
        changed_diagnostics = changed_params.get("diagnostics", [])
        send("textDocument/didClose", {"textDocument": {"uri": uri}})
        closed = receive()
        observations.append(
            {
                "name": "E1-E2-versioned-diagnostic-cancel-close",
                "pass": changed_params.get("version") == 2
                and [row.get("code") for row in changed_diagnostics] == ["D110"]
                and closed.get("params", {}).get("diagnostics") == [],
                "changed": changed_params,
                "closed": closed.get("params"),
            }
        )
        shutdown_id = send("shutdown", request=True)
        assert shutdown_id is not None
        response(shutdown_id)
        send("exit")
        process.stdin.close()
        process.wait(timeout=5)
        observations.append({"name": "E2-clean-shutdown", "pass": process.returncode == 0, "exit": process.returncode})
    except Exception as error:
        observations.append({"name": "E1-E2-protocol-client", "pass": False, "error": repr(error)})
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)

    oversized = subprocess.Popen(
        [str(binary), "lsp"], cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    assert oversized.stdin is not None and oversized.stderr is not None
    oversized.stdin.write(f"Content-Length: {MAX_MESSAGE + 1}\r\n\r\n".encode())
    oversized.stdin.close()
    try:
        oversized.wait(timeout=5)
        stderr = oversized.stderr.read().decode(errors="replace")
        observations.append(
            {
                "name": "E2-oversized-message-bounded",
                "pass": oversized.returncode != 0 and "resource limit exceeded" in stderr and "panicked at" not in stderr,
                "exit": oversized.returncode,
                "stderr": stderr.strip(),
            }
        )
    except subprocess.TimeoutExpired:
        oversized.kill()
        oversized.wait(timeout=5)
        observations.append({"name": "E2-oversized-message-bounded", "pass": False, "error": "timeout"})
    return observations


if __name__ == "__main__":
    raise SystemExit(main())
