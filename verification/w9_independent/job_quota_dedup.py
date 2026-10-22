#!/usr/bin/env python3
"""Check stable-key job deduplication at the 10,000-row quota boundary."""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
import time

BASE = Path(__file__).resolve().parent
PREVIOUS = BASE.parent / "w7_w8_independent"
sys.path.insert(0, str(PREVIOUS))
from fixture import PASSWORD, app_id, identifier, write  # noqa: E402
from receiver import Receiver  # noqa: E402
from service import Client, ENV_NAME, Service, sha256  # noqa: E402

spec = importlib.util.spec_from_file_location("_w9_quota_common", BASE.parent / "w5_independent/common.py")
previous = importlib.util.module_from_spec(spec)
spec.loader.exec_module(previous)
Receipt, quote, seed = previous.Receipt, previous.quote, previous.seed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--report", type=Path, default=BASE / "job_quota_dedup_results.json")
    options = parser.parse_args()
    root = Path(tempfile.mkdtemp(prefix="diamond-w9-job-quota-"))
    binary = root / "diamond"
    shutil.copy2(options.binary, binary)
    project = write(root / "project")
    service = Service(root, binary, project)
    receiver = None
    receipt = Receipt()
    receipt.secret(PASSWORD)
    fatal = None
    started = time.monotonic()

    def row(job_id: str):
        rows = service.sql_json(
            "SELECT id,binding,parent,owner,payload,dedup,state,attempts,fence,outcome "
            "FROM diamond_w7.jobs WHERE app=" + quote(app_id()) + " AND id=" + quote(job_id)
        )
        return rows[0] if len(rows) == 1 else None

    try:
        receiver = Receiver()
        service.runtime["http"] = {
            "inventory": {
                "origin": receiver.origin,
                "network": "loopback",
                "paths": ["/v1/"],
                "methods": ["POST"],
                "timeout_ms": 5000,
            }
        }
        service.postgres()
        contract, _build = service.build()
        service.cli("project-init", project, "--postgres-env", ENV_NAME)
        service.files_cli(initialize=True)
        service.cli(
            "project-admin-add",
            project,
            "--postgres-env",
            ENV_NAME,
            "--user",
            "alice",
            "--email",
            "alice@qualification.invalid",
            "--password-stdin",
            input=PASSWORD + "\n",
        )
        seed(service)
        service.start_node("A")
        alice = Client(service, contract["rpc_id"])
        alice.login("alice", PASSWORD)
        parent = identifier("issue-red-one")
        first = alice.rpc("submit", parent, "quota-stable-payload", "quota-stable-key")
        if first[0] != 200:
            raise AssertionError(("initial job rejected", first))
        job_id = first[1]["value"]
        original = row(job_id)
        service.sql(
            "INSERT INTO diamond_w7.jobs(app,id,binding,version,parent,owner,payload,dedup,restrictions,unscoped,state,attempts,fence,lease,due,outcome) "
            "SELECT app,pg_catalog.lpad(pg_catalog.to_hex(g::bigint),64,'0'),binding,version,parent,owner,payload,"
            "'quota-fill-'||g::text,restrictions,unscoped,'done',1,1,NULL,clock_timestamp(),'completed' "
            "FROM diamond_w7.jobs CROSS JOIN pg_catalog.generate_series(1,9999) AS g "
            "WHERE app=" + quote(app_id()) + " AND id=" + quote(job_id)
        )
        count_before = int(service.sql("SELECT count(*) FROM diamond_w7.jobs WHERE app=" + quote(app_id())))
        repeated = alice.rpc("submit", parent, "quota-stable-payload", "quota-stable-key", node="A")
        count_after = int(service.sql("SELECT count(*) FROM diamond_w7.jobs WHERE app=" + quote(app_id())))
        after = row(job_id)
        receipt.record(
            "identical-stable-key-retry-survives-full-row-quota",
            count_before == 10000
            and repeated[0] == 200
            and repeated[1].get("value") == job_id
            and count_after == count_before
            and after == original,
            {
                "count_before": count_before,
                "retry": repeated,
                "count_after": count_after,
                "original_unchanged": after == original,
                "job": after,
            },
            "an identical submission at the application row cap returns its existing opaque ID without consuming capacity or mutating the original",
        )
        conflicting = alice.rpc("submit", parent, "changed-quota-payload", "quota-stable-key", node="A")
        conflict_count = int(service.sql("SELECT count(*) FROM diamond_w7.jobs WHERE app=" + quote(app_id())))
        receipt.record(
            "conflicting-stable-key-retry-remains-conflict-at-full-quota",
            count_before == 10000
            and conflicting[0] == 409
            and conflicting[1].get("error") == "aborted"
            and conflict_count == count_before
            and row(job_id) == original,
            {
                "count_before": count_before,
                "retry": conflicting,
                "count_after": conflict_count,
                "original_unchanged": row(job_id) == original,
            },
            "a changed payload under an existing owner/binding/key remains a confirmed conflict at quota rather than being misclassified as capacity loss",
        )
        fresh_read = alice.rpc("read", {"tag": "issue", "value": parent})
        receipt.record(
            "quota-rejection-or-dedup-does-not-poison-server",
            fresh_read[0] == 200 and count_after == 10000,
            {"read": fresh_read, "job_count": count_after},
            "the quota-edge transaction leaves the node and durable registry usable",
        )
    except Exception as error:
        fatal = repr(error)
    finally:
        service.close()
        if receiver is not None:
            receiver.close()
    report = receipt.report(
        service,
        sha256(binary),
        error=fatal,
        elapsed_seconds=time.monotonic() - started,
        command=f"python3 verification/w9_independent/job_quota_dedup.py --binary {options.binary}",
        oracle="ORACLES.md J3",
        temporary_root_removed=True,
    )
    options.report.parent.mkdir(parents=True, exist_ok=True)
    options.report.write_text(json.dumps(report, indent=2) + "\n")
    shutil.rmtree(root)
    print(json.dumps({"status": report["status"], "observations": len(receipt.results), "error": fatal, "elapsed_seconds": report["elapsed_seconds"]}))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
