#!/usr/bin/env python3
"""Real HTTP/PostgreSQL qualification for checked graph-query budgets and policy."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SUPPORT = ROOT / "verification" / "w7_w8_independent"
sys.path.insert(0, str(SUPPORT))
from receiver import Receiver  # noqa: E402
from service import Client, ENV_NAME, RUNTIME_NAME, Service, sha256  # noqa: E402

PASSWORD = "Synthetic L7 query qualification password 97!"


def value(outcome):
    if outcome[0] != 200 or not isinstance(outcome[1], dict) or "value" not in outcome[1]:
        raise AssertionError(("expected successful RPC", outcome))
    return outcome[1]["value"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, default=ROOT / "target/debug/diamond")
    parser.add_argument("--report", type=Path,
                        default=ROOT / "verification" / "secure_language_l7_runtime_results.json")
    options = parser.parse_args()
    workspace = ROOT / ".diamond"
    workspace.mkdir(mode=0o700, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="l7-query-runtime-", dir=workspace))
    binary, project_dir, mail_root = root / "diamond", root / "project", root / "mail"
    shutil.copy2(options.binary.resolve(), binary)
    shutil.copytree(ROOT / "examples" / "functions" / "authority", project_dir)
    mail_root.mkdir(mode=0o700)
    project = project_dir / "diamond.toml"
    service = Service(root, binary, project)
    receiver = None
    observations, fatal = [], None

    def record(name, okay, observed, expected):
        observations.append({"name": name, "status": "PASS" if okay else "FAIL",
                             "observed": observed, "expected": expected})
        if not okay:
            raise AssertionError((name, observed, expected))

    try:
        receiver = Receiver()
        service.runtime["http"] = {"inventory": {"origin": receiver.origin, "network": "loopback",
            "paths": ["/v1/"], "methods": ["POST"], "credential_env": "DIAMOND_L7_INVENTORY"}}
        service.environment["DIAMOND_L7_INVENTORY"] = "synthetic-l7-bearer"
        service.runtime["mail"] = {"document_mail": {"root": str(mail_root),
                                                       "from": "noreply@example.invalid"}}
        service.postgres()
        contract, build = service.build()
        contract_json = json.loads((build / "contract.json").read_text())
        declared = contract_json["queries"]["document_graph"]
        record("artifact-publishes-query-budget", declared["selection"] == ["id", "owner", "title"] and
               declared["max_rows"] == 10 and declared["max_queries"] == 1 and
               declared["max_response_bytes"] == 32768,
               {"query": declared},
               "contract exposes the exact selected fields and every enforced budget")
        service.cli("project-init", project, "--postgres-env", ENV_NAME)
        service.files_cli(initialize=True)
        service.cli("project-mail-init", project, "--postgres-env", ENV_NAME,
                    "--runtime-env", RUNTIME_NAME)
        for username in ("alice", "bob"):
            service.cli("project-admin-add", project, "--postgres-env", ENV_NAME, "--user", username,
                        "--email", username + "@qualification.invalid", "--password-stdin",
                        input=PASSWORD + "\n")
        service.start_node("A")
        alice, bob, anonymous = (Client(service, contract["rpc_id"]) for _ in range(3))
        alice.login("alice", PASSWORD)
        bob.login("bob", PASSWORD)
        for title in ("Alice one", "Alice two", "Alice three"):
            value(alice.rpc("create_document", title))
        value(bob.rpc("create_document", "Bob only"))

        first = value(alice.rpc("document_graph", "", "2"))
        record("row-selection-and-request-limit", len(first) == 2 and
               all(set(row) == {"id", "owner", "title"} for row in first) and
               all(row["owner"] == {"tag": "user", "value": "alice"} for row in first),
               {"rows": first},
               "single checked query returns no more than the requested rows and exactly its declared fields")
        bob_rows = value(bob.rpc("document_graph", "", "10"))
        record("policy-is-pushed-into-base-resource", len(bob_rows) == 1 and
               bob_rows[0]["title"] == "Bob only" and
               bob_rows[0]["owner"] == {"tag": "user", "value": "bob"},
               {"rows": bob_rows},
               "the same query document executes under the current principal's resource read policy")

        excessive_rows = alice.rpc("document_graph", "", "11")
        long_cursor = alice.rpc("document_graph", "x" * 513, "1")
        malformed_limit = alice.rpc("document_graph", "", "01")
        record("runtime-cursor-row-and-codec-bounds", excessive_rows[0] == 400 and
               long_cursor[0] == 400 and malformed_limit[0] == 400,
               {"row_status": excessive_rows[0], "cursor_status": long_cursor[0],
                "codec_status": malformed_limit[0]},
               "requests outside the declared row/cursor bounds or canonical Int codec never execute the plan")

        anonymous.bootstrap()
        unauthenticated = anonymous.rpc("document_graph", "", "1")
        record("query-does-not-bypass-rpc-authentication", unauthenticated[0] == 403,
               {"status": unauthenticated[0]},
               "query document retains the same authenticated RPC boundary")
    except Exception as error:
        fatal = repr(error)
    finally:
        if receiver is not None:
            receiver.close()
        service.close()
        report = {"status": "FAIL" if fatal or service.cleanup_errors or
                  any(item["status"] != "PASS" for item in observations) else "PASS",
                  "binary_sha256": sha256(binary), "observations": observations,
                  "fatal": fatal, "shutdowns": service.shutdowns,
                  "cleanup_errors": service.cleanup_errors,
                  "scope": "Disposable loopback HTTP, owned PostgreSQL, and real authenticated RPC; no Internet destination."}
        options.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        shutil.rmtree(root)
    print(json.dumps({"status": report["status"], "observations": len(observations), "fatal": fatal}))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
