#!/usr/bin/env python3
"""Inspect built and served W9 artifacts for server-only and credential canaries."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[1]
W78 = BASE.parent / "w7_w8_independent"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fixture = load("_w9_artifact_fixture", W78 / "fixture.py")
sys.modules["fixture"] = fixture
service_module = load("_w9_artifact_service", W78 / "service.py")
common = load("_w9_artifact_common", BASE.parent / "w5_independent" / "common.py")
receiver_module = load("_w9_artifact_receiver", W78 / "receiver.py")
SERVER_CANARY = "W5_SERVER_ONLY_CANARY_9cb3e0a4"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    options = parser.parse_args()
    root = Path(tempfile.mkdtemp(prefix="diamond-w9-artifacts-"))
    binary = root / "diamond"
    shutil.copy2(options.binary, binary)
    project = fixture.write(root / "project")
    service = service_module.Service(root, binary, project)
    receiver = receiver_module.Receiver()
    service.runtime["http"] = {
        "inventory": {
            "origin": receiver.origin,
            "network": "loopback",
            "paths": ["/v1/"],
            "methods": ["POST"],
        }
    }
    observations = []
    error = None

    def record(name: str, okay: bool, observed, expected: str) -> None:
        observations.append(
            {"name": name, "status": "PASS" if okay else "FAIL", "observed": observed, "expected": expected}
        )

    try:
        service.postgres()
        contract, build = service.build()
        private_server = build / "server.json"
        public_files = [build / "index.html", build / "contract.json"]
        public_files.extend(sorted((build / "assets").rglob("*")))
        public_files.extend(sorted((build / "pages").rglob("*")))
        public_files = [path for path in public_files if path.is_file()]
        leaked = [str(path.relative_to(build)) for path in public_files if SERVER_CANARY.encode() in path.read_bytes()]
        record(
            "server-source-canary-confined-to-private-artifact",
            private_server.is_file()
            and SERVER_CANARY.encode() in private_server.read_bytes()
            and not leaked,
            {
                "private_server_sha256": digest(private_server),
                "public_file_count": len(public_files),
                "public_canary_hits": leaked,
            },
            "the planted implementation canary exists in the private server artifact and in no browser asset, page, map or public contract",
        )
        service.cli("project-init", project, "--postgres-env", service_module.ENV_NAME)
        service.files_cli(initialize=True)
        service.cli(
            "project-admin-add",
            project,
            "--postgres-env",
            service_module.ENV_NAME,
            "--user",
            "alice",
            "--email",
            "alice@artifact.invalid",
            "--password-stdin",
            input=fixture.PASSWORD + "\n",
        )
        common.seed(service)
        service.start_node("A")
        client = service_module.Client(service, contract["rpc_id"])
        client.login("alice", fixture.PASSWORD)
        paths = ["/server.json", "/server.dia", "/diamond.toml", "/contract.json", "/assets/../server.json"]
        served = {}
        bodies = []
        for path in paths:
            status, _, body = service.request("A", path, headers={"Cookie": client.cookie or ""})
            served[path] = {"status": status, "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}
            bodies.append(body)
        api_contract = service.request("A", "/api/v1/_contract")
        bodies.append(api_contract[2])
        hidden = client.rpc("hidden")
        response_canary = any(SERVER_CANARY.encode() in body for body in bodies) or SERVER_CANARY in json.dumps(hidden)
        record(
            "private-build-inputs-not-served-or-dispatched",
            all(value["status"] >= 400 for value in served.values())
            and api_contract[0] == 404
            and hidden[0] >= 400
            and not response_canary,
            {"paths": served, "api_contract_status": api_contract[0], "hidden_status": hidden[0], "canary_in_response": response_canary},
            "guessed private artifact/source paths, the absent API profile, and undeclared server functions disclose no implementation text",
        )
        logs = "\n".join(path.read_text(errors="replace") for path in service.node_logs.values())
        cookie_value = (client.cookie or "").partition("=")[2]
        secrets = [fixture.PASSWORD, client.csrf, cookie_value]
        found = [index for index, value in enumerate(secrets) if value and value in logs]
        build_secret_hits = [
            str(path.relative_to(build))
            for path in public_files
            if any(value and value.encode() in path.read_bytes() for value in secrets)
        ]
        record(
            "runtime-credentials-absent-from-logs-and-browser-artifacts",
            not found and not build_secret_hits,
            {"log_bytes": len(logs), "log_secret_indexes": found, "browser_artifact_hits": build_secret_hits},
            "synthetic password, CSRF and session values do not enter request logs or compiled browser material",
        )
    except Exception as failure:
        error = repr(failure)
    finally:
        service.close()
        receiver.close()
        report = {
            "status": "PASS" if error is None and not service.cleanup_errors and all(row["status"] == "PASS" for row in observations) else "FAIL",
            "binary_sha256": digest(binary),
            "error": error,
            "observations": observations,
            "shutdowns": service.shutdowns,
            "cleanup_errors": service.cleanup_errors,
            "scope": "real build plus one real connected node; direct artifact-byte inspection and actual HTTP/RPC reachability checks",
        }
        shutil.rmtree(root)
        report["temporary_root_removed"] = not root.exists()
        options.report.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"status": report["status"], "observations": len(observations), "error": error}))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
