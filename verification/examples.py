#!/usr/bin/env python3
"""Check and execute the public Diamond example/function catalog."""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
from pathlib import Path
import re
import socket
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
FUNCTIONS = ROOT / "examples/functions"


def run(binary: Path, arguments: list[str], *, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [str(binary), *arguments],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"command failed: {binary} {' '.join(arguments)}\n{result.stdout}{result.stderr}"
        )
    return result


def assert_catalog_coverage() -> dict[str, list[str]]:
    standard = (ROOT / "src/standard.rs").read_text()
    names_block = re.search(r"pub\(crate\) const NAMES:.*?= &\[(.*?)\];", standard, re.S)
    if names_block is None:
        raise AssertionError("could not read the standard function registry")
    standard_names = re.findall(r'"([a-z_]+)"', names_block.group(1))
    function_source = "\n".join(path.read_text() for path in sorted(FUNCTIONS.rglob("*.dia")))
    example_source = "\n".join(
        path.read_text() for path in sorted((ROOT / "examples").rglob("*.dia"))
    )

    groups = {
        "standard": standard_names,
        "constructors_and_helpers": [
            "Some(", "None", "Ok(", "Err(", "len(", "error_message(",
            "route_url(", "bad_request(", "request.form<", "request.is_user(",
        ],
        "repositories": [
            ".create(", ".get(", ".update(", ".delete(", ".select(",
            ".page(", ".cursor(", ".count(", ".sum(", ".join(",
        ],
        "host": [
            "host.postgres_version(", "host.postgres_echo(", "host.secret(",
            "host.http_get<", "host.http_post<", "host.job_submit<",
            "host.job_status(", "host.job_cancel(", "host.job_key(",
            "host.event_emit<",
        ],
        "session": [
            "session.login(", "session.user(", "session.logout(",
            "session.register(", "session.verify(", "session.resend(",
            "session.recover(", "session.reset(", "session.change_password(",
            "session.logout_all(", "session.invite(", "session.disable(",
            "session.create_token(", "session.list_tokens(",
            "session.revoke_token(",
        ],
        "files_and_browser": [
            "files.upload(", "files.status(", "files.download(",
            "files.delete(", "rpc.", "<watch ",
        ],
        "policies": [
            "principal.is_user(", "principal.has_role(",
            "principal.is_related(", "principal.has_role_in(",
        ],
    }
    missing: dict[str, list[str]] = {}
    for group, entries in groups.items():
        corpus = function_source if group != "policies" else example_source
        absent = []
        for entry in entries:
            needle = f"{entry}(" if group == "standard" else entry
            if needle not in corpus:
                absent.append(entry)
        if absent:
            missing[group] = absent
    if missing:
        raise AssertionError(f"example callable coverage is incomplete: {missing}")
    return groups


def assert_links() -> int:
    documents = [
        ROOT / "README.md",
        ROOT / "docs/language.md",
        *sorted((ROOT / "examples").rglob("README.md")),
    ]
    checked = 0
    missing = []
    for document in documents:
        for target in re.findall(r"\[[^]]+\]\(([^)]+)\)", document.read_text()):
            if target.startswith(("http://", "https://", "#")):
                continue
            local = target.split("#", 1)[0]
            if not local:
                continue
            checked += 1
            if not (document.parent / local).resolve().exists():
                missing.append(f"{document.relative_to(ROOT)} -> {target}")
    if missing:
        raise AssertionError(f"missing local documentation links: {missing}")
    return checked


def http_example(binary: Path) -> dict[str, object]:
    with socket.socket() as selection:
        selection.bind(("127.0.0.1", 0))
        port = selection.getsockname()[1]
    process = subprocess.Popen(
        [
            str(binary),
            "run",
            "examples/functions/web_helpers.dia",
            "--listen",
            f"127.0.0.1:{port}",
            "--max-requests",
            "3",
        ],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    observations = []
    try:
        assert process.stderr is not None
        startup = process.stderr.readline()
        if f"http://127.0.0.1:{port}" not in startup:
            raise AssertionError(f"web helper did not start: {startup}")

        def request(method: str, path: str, body: str | None = None, headers: dict[str, str] | None = None) -> tuple[int, bytes]:
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
            try:
                connection.request(method, path, body=body, headers=headers or {})
                response = connection.getresponse()
                return response.status, response.read()
            finally:
                connection.close()

        status, body = request("GET", "/")
        observations.append(status == 200 and b'href="/people/demo"' in body)
        status, body = request("GET", "/people/demo")
        observations.append(status == 200 and b"User demo" in body)
        status, body = request(
            "POST",
            "/echo",
            "message=hello+from+Diamond",
            {"Content-Type": "application/x-www-form-urlencoded"},
        )
        observations.append(status == 200 and b"hello from Diamond" in body)
        process.wait(timeout=5)
        if process.returncode != 0 or not all(observations):
            stdout, stderr = process.communicate()
            raise AssertionError(f"web helper behavior failed: {observations}\n{stdout}{stderr}")
        return {"requests": 3, "observations": len(observations)}
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, default=ROOT / "target/debug/diamond")
    parser.add_argument("--report", type=Path)
    options = parser.parse_args()
    binary = options.binary.resolve()
    if not binary.is_file():
        raise SystemExit("build Diamond first with: cargo build --locked")

    expected = {
        "basics.dia": "Hello, Ada (36) | ada | enabled | example failure | suspended: review\n",
        "text.dia": "雪ia | true | ready | blue green blue | 2\n",
        "lists.dia": "3 | 3 | 4,2\n",
        "decimals.dia": "1.500000 | 2.000000 | 1.000000 | 0.750000 | 3.000000\n",
        "time.dia": "1700000000000 | 2500 | 1700000002500 | 2500\n",
    }
    checked = []
    executed = []
    for path in sorted(FUNCTIONS.glob("*.dia")):
        relative = str(path.relative_to(ROOT))
        run(binary, ["check", relative])
        checked.append(relative)
        if path.name in expected:
            result = run(binary, ["run", relative])
            if result.stdout != expected[path.name]:
                raise AssertionError(
                    f"unexpected output for {relative}: {result.stdout!r}"
                )
            executed.append(relative)

    projects = []
    with tempfile.TemporaryDirectory(prefix="diamond-examples-") as temporary:
        root = Path(temporary)
        database = root / "storage.sqlite"
        source = "examples/functions/storage_methods.dia"
        run(binary, ["db-init", source, "--database", str(database)])
        for _ in range(2):
            result = run(binary, ["run", source, "--database", str(database)])
            if result.stdout != "Updated title | selected 1\n":
                raise AssertionError(f"unexpected storage output: {result.stdout!r}")

        for name in ["relational", "authority"]:
            manifest = f"examples/functions/{name}/diamond.toml"
            output = root / f"{name}-build"
            run(binary, ["check", manifest])
            run(binary, ["build", manifest, "--out", str(output)])
            run(binary, ["check", str(output)])
            projects.append(manifest)

    coverage = assert_catalog_coverage()
    links = assert_links()
    web = http_example(binary)
    report = {
        "status": "PASS",
        "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
        "checked_standalone": checked,
        "executed_pure": executed,
        "storage_runs": 2,
        "projects": projects,
        "web": web,
        "callable_groups": {name: len(entries) for name, entries in coverage.items()},
        "local_links": links,
    }
    if options.report:
        options.report.parent.mkdir(parents=True, exist_ok=True)
        options.report.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
