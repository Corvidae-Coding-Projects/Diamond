#!/usr/bin/env python3
"""Qualify installation, CLI startup, host grants, and loopback readiness."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import socket
import sqlite3
import subprocess
import tempfile
import time
import tomllib
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VERSION = tomllib.loads((ROOT / "Cargo.toml").read_text())["package"]["version"]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(binary: Path, arguments: list[str], *, cwd: Path, input_text: str | None = None) -> dict[str, object]:
    started = time.monotonic()
    completed = subprocess.run(
        [str(binary), *arguments],
        cwd=cwd,
        input=input_text,
        text=True,
        capture_output=True,
        timeout=300,
    )
    return {
        "arguments": arguments,
        "cwd": str(cwd),
        "exit": completed.returncode,
        "seconds": round(time.monotonic() - started, 6),
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--report",
        type=Path,
        default=ROOT / "verification/startup_installation_results.json",
    )
    options = parser.parse_args()
    cases: list[dict[str, object]] = []
    failures: list[str] = []

    def record(name: str, observed: object, checks: list[tuple[bool, str]]) -> None:
        failed = [message for accepted, message in checks if not accepted]
        cases.append({"name": name, "observed": observed, "accepted": not failed, "failures": failed})
        failures.extend(f"{name}: {message}" for message in failed)

    with tempfile.TemporaryDirectory(prefix="diamond-cp6-install-") as temporary:
        directory = Path(temporary)
        install_root = directory / "install"
        install_root.mkdir()
        started = time.monotonic()
        installed = subprocess.run(
            [
                "cargo",
                "install",
                "--locked",
                "--path",
                str(ROOT),
                "--root",
                str(install_root),
            ],
            cwd=directory,
            text=True,
            capture_output=True,
            timeout=300,
        )
        install_observation = {
            "exit": installed.returncode,
            "seconds": round(time.monotonic() - started, 6),
            "stdout": installed.stdout,
            "stderr": installed.stderr,
            "files": sorted(str(path.relative_to(install_root)) for path in install_root.rglob("*") if path.is_file()),
        }
        binary = install_root / "bin/diamond"
        record(
            "cargo_install",
            install_observation,
            [
                (installed.returncode == 0, "cargo install failed"),
                (binary.is_file(), "installed binary missing"),
            ],
        )
        if not binary.is_file():
            options.report.write_text(json.dumps({"result": "FAIL", "cases": cases}, indent=2) + "\n")
            return 1

        observed = run(binary, ["--version"], cwd=directory)
        record(
            "installed_version",
            observed,
            [
                (observed["exit"] == 0, "installed version failed"),
                (
                    observed["stdout"]
                    == f"diamond {VERSION} (LP8 qualified language profile)\n",
                    "installed version mismatch",
                ),
            ],
        )
        observed = run(binary, ["--help"], cwd=directory)
        record(
            "installed_help",
            observed,
            [
                (observed["exit"] == 0, "installed help failed"),
                ("diamond explain CODE" in str(observed["stdout"]), "explain absent from help"),
                (
                    "production-readiness language profile"
                    in str(observed["stdout"]),
                    "checkpoint label mismatch",
                ),
                ("diamond build" in str(observed["stdout"]) and "diamond serve" in str(observed["stdout"]), "project commands absent from help"),
            ],
        )

        for template in ["workspace", "catalog"]:
            project = directory / ("installed-" + template)
            observed = run(binary, ["new", str(project), "--template", template], cwd=directory)
            reference = ROOT / "examples" / ("live-workspace" if template == "workspace" else "catalog")
            reference_hashes = {
                str(path.relative_to(reference)): digest(path)
                for path in sorted(reference.rglob("*"))
                if path.is_file()
            }
            scaffold_hashes = {
                str(path.relative_to(project)): digest(path)
                for path in sorted(project.rglob("*"))
                if path.is_file()
            }
            record(
                "installed_new_" + template,
                {"command": observed, "reference_sha256": reference_hashes, "scaffold_sha256": scaffold_hashes},
                [
                    (observed["exit"] == 0, "installed scaffold failed"),
                    ((project / "diamond.toml").is_file(), "embedded manifest missing"),
                    (scaffold_hashes == reference_hashes, "installed scaffold differs from the qualified reference application"),
                ],
            )
            observed = run(binary, ["build", str(project / "diamond.toml"), "--out", str(directory / (template + "-build"))], cwd=directory)
            record("installed_build_" + template, observed, [(observed["exit"] == 0, "installed scaffold build failed")])

        copied_hello = directory / "hello.dia"
        copied_hello.write_bytes((ROOT / "examples/hello.dia").read_bytes())
        observed = run(binary, ["run", "hello.dia"], cwd=directory)
        record(
            "installed_run_outside_tree",
            observed,
            [
                (observed["exit"] == 0, "installed pure run failed"),
                (observed["stdout"] == "Hello, Diamond!\n", "installed pure output mismatch"),
            ],
        )

        for example in sorted((ROOT / "examples").glob("*.dia")):
            observed = run(binary, ["check", str(example)], cwd=directory)
            record(
                f"installed_check_{example.stem}",
                observed,
                [(observed["exit"] == 0, f"installed binary rejected {example.name}")],
            )

        side_effect_directory = directory / "check-side-effects"
        side_effect_directory.mkdir()
        checked_source = side_effect_directory / "program.dia"
        checked_source.write_text("fn main() -> Int:\n  42\n")
        before = sorted(path.name for path in side_effect_directory.iterdir())
        observed = run(binary, ["check", "program.dia"], cwd=side_effect_directory)
        after = sorted(path.name for path in side_effect_directory.iterdir())
        record(
            "check_side_effects",
            {"command": observed, "before": before, "after": after},
            [
                (observed["exit"] == 0, "check failed"),
                (after == before, "check created filesystem state"),
            ],
        )

        database = directory / "storage.sqlite"
        observed = run(
            binary,
            ["db-init", str(ROOT / "examples/storage.dia"), "--database", str(database)],
            cwd=directory,
        )
        tables = []
        if database.exists():
            with sqlite3.connect(database) as connection:
                tables = [row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                )]
        record(
            "database_initialization",
            {"command": observed, "tables": tables},
            [
                (observed["exit"] == 0, "db-init failed"),
                (tables == ["Notes"], f"unexpected initialized tables: {tables}"),
            ],
        )

        observed = run(
            binary,
            ["run", str(ROOT / "examples/web.dia"), "--listen", "0.0.0.0:0", "--max-requests", "1"],
            cwd=directory,
        )
        record(
            "non_loopback_refusal",
            observed,
            [
                (observed["exit"] != 0, "non-loopback listener accepted"),
                ("non-loopback or production serving" in str(observed["stderr"]), "refusal reason missing"),
                (float(observed["seconds"]) < 5.0, "non-loopback failure exceeded five seconds"),
            ],
        )

        absent_database = directory / "absent.sqlite"
        observed = run(
            binary,
            ["run", str(ROOT / "examples/message_board.dia"), "--listen", "127.0.0.1:0", "--max-requests", "1"],
            cwd=directory,
        )
        record(
            "missing_database_grant",
            {"command": observed, "database_exists": absent_database.exists()},
            [
                (observed["exit"] != 0, "database-backed routes started without a host grant"),
                ("require an explicit --database PATH host grant" in str(observed["stderr"]), "host-grant reason missing"),
                (float(observed["seconds"]) < 5.0, "missing-grant failure exceeded five seconds"),
            ],
        )

        accounts = directory / "accounts.sqlite"
        observed_user = run(
            binary,
            ["user-add", "--auth", str(accounts), "--user", "alice", "--password-stdin"],
            cwd=directory,
            input_text="installation-password\n",
        )
        observed = run(
            binary,
            [
                "run",
                str(ROOT / "examples/message_board.dia"),
                "--database",
                str(accounts),
                "--auth",
                str(accounts),
                "--listen",
                "127.0.0.1:0",
                "--max-requests",
                "1",
            ],
            cwd=directory,
        )
        record(
            "conflicting_database_paths",
            {"user_add": observed_user, "run": observed},
            [
                (observed_user["exit"] == 0, "account fixture creation failed"),
                (observed["exit"] != 0, "same account/application path accepted"),
                ("must be separate files" in str(observed["stderr"]), "separation reason missing"),
                (float(observed["seconds"]) < 5.0, "path-conflict failure exceeded five seconds"),
            ],
        )

        held = socket.socket()
        held.bind(("127.0.0.1", 0))
        held.listen(1)
        occupied_port = held.getsockname()[1]
        occupied_artifacts = directory / "occupied"
        occupied_artifacts.mkdir()
        observed = run(
            binary,
            [
                "run",
                str(ROOT / "examples/web.dia"),
                "--listen",
                f"127.0.0.1:{occupied_port}",
                "--max-requests",
                "1",
            ],
            cwd=occupied_artifacts,
        )
        held.close()
        record(
            "occupied_port",
            {"command": observed, "files": list(occupied_artifacts.iterdir())},
            [
                (observed["exit"] != 0, "occupied listener unexpectedly started"),
                ("Address already in use" in str(observed["stderr"]), "occupied-port reason missing"),
                (not list(occupied_artifacts.iterdir()), "occupied-port failure created files"),
                (float(observed["seconds"]) < 5.0, "occupied-port failure exceeded five seconds"),
            ],
        )

        process = subprocess.Popen(
            [
                str(binary),
                "run",
                str(ROOT / "examples/web.dia"),
                "--listen",
                "127.0.0.1:0",
                "--max-requests",
                "1",
            ],
            cwd=directory,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        server_started = time.monotonic()
        line = process.stderr.readline() if process.stderr is not None else ""
        match = re.search(r"localhost:(\d+)", line)
        response = b""
        if match:
            port = int(match.group(1))
            with socket.create_connection(("127.0.0.1", port), timeout=5) as client:
                client.sendall(
                    f"GET / HTTP/1.1\r\nHost: localhost:{port}\r\nConnection: close\r\n\r\n".encode()
                )
                while True:
                    chunk = client.recv(65536)
                    if not chunk:
                        break
                    response += chunk
        try:
            process_stdout, process_stderr = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process_stdout, process_stderr = process.communicate()
        loopback_observed = {
            "startup_line": line,
            "seconds_to_line": round(time.monotonic() - server_started, 6),
            "exit": process.returncode,
            "stdout": process_stdout,
            "stderr": process_stderr,
            "response_sha256": hashlib.sha256(response).hexdigest(),
            "response_prefix": response[:160].decode("utf-8", "replace"),
        }
        record(
            "loopback_readiness",
            loopback_observed,
            [
                (match is not None, "startup address was not reported"),
                (process.returncode == 0, "bounded server did not exit cleanly"),
                (response.startswith(b"HTTP/1.1 200 OK"), "loopback request did not return 200"),
                (b"Diamond &lt;checkpoint one&gt;" in response, "typed HTML response missing"),
                (float(loopback_observed["seconds_to_line"]) < 5.0, "loopback readiness exceeded five seconds"),
            ],
        )

        report = {
            "schema": 1,
            "finished_utc": datetime.now(timezone.utc).isoformat(),
            "binary_sha256": digest(binary),
            "host_binary_sha256": digest(ROOT / "target/debug/diamond"),
            "install_root_file_count": len(install_observation["files"]),
            "cases": cases,
            "case_count": len(cases),
            "failures": failures,
            "result": "PASS" if not failures else "FAIL",
        }
    options.report.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n")
    print(json.dumps({"status": report["result"], "cases": len(cases), "failures": len(failures)}))
    if failures:
        for failure in failures:
            print(failure, file=__import__("sys").stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
