#!/usr/bin/env python3
"""Real CLI/native migrations, killed backfill transactions, audit, and recovery."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import select
import shutil
import signal
import subprocess
import tempfile
import time

from schema_fixture import MANIFEST, SERVER, write
from scalars import constrain_runtime
from service import Client, ENV_NAME, PASSWORD, Service, sha256

ROOT = Path(__file__).resolve().parents[2]


def versions(root):
    current = write(root / "current")
    manifest = MANIFEST.replace("\nversion = 1\n", "\nversion = 2\n") + '''
[storage.backfills.populate_flag]
resource = "Rows"
field = "new_flag"
value = 7
'''
    source = SERVER.replace("type StoredRow:\n", "type StoredRow:\n  new_flag: Option<Int> = None\n")
    next_version = write(root / "next", manifest, source)
    return current, next_version, manifest, source


def actual_table(service):
    names = service.sql("SELECT tablename FROM pg_tables WHERE schemaname='diamond_w4' AND tablename LIKE 'r\\_%' ESCAPE '\\'").splitlines()
    if len(names) != 1:
        raise AssertionError(("one independent Rows relation expected", names))
    return 'diamond_w4."' + names[0].replace('"', '""') + '"'


def native(options, root, results):
    current, next_version, _, _ = versions(root / "native-projects")
    native_root = root / "native-database"
    native_root.mkdir()
    service = Service(native_root, options.binary, current)
    executable = root / "native_consumer"
    command = ["rustc", "--edition", "2024", str(Path(__file__).with_name("native.rs")), "--crate-name", "independent_w4_native",
               "--extern", "diamond=" + str(options.library.resolve()), "-L", "dependency=" + str(options.dependencies.resolve()), "-o", str(executable)]
    compiled = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=60)
    if compiled.returncode != 0:
        raise AssertionError(("native consumer compile", compiled.stdout, compiled.stderr))
    try:
        service.postgres()
        run = subprocess.run([str(executable), str(current), str(next_version)], cwd=ROOT,
                             env=os.environ | {ENV_NAME: service.owner}, capture_output=True, text=True, timeout=40)
        reports = [json.loads(line) for line in run.stdout.splitlines()]
        okay = (run.returncode == 0 and len(reports) == 5 and reports[0]["version"] == 1
                and reports[1]["valid"] is True and reports[1]["rows"] == {"Rows": 0}
                and reports[2]["version"] == 2 and reports[3]["processed"] == 0
                and reports[3]["done"] is True and reports[4]["valid"] is True)
        results.append({"name": "external-public-api-consumer", "status": "PASS" if okay else "FAIL",
                        "expected": "initialize/status/audit/migrate/backfill/audit plus two rejected invalid batches through public API",
                        "observed": {"exit": run.returncode, "reports": reports, "stderr": run.stderr},
                        "consumer_sha256": sha256(executable), "library_sha256": sha256(options.library)})
    finally:
        service.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, default=ROOT / "target/debug/diamond")
    parser.add_argument("--library", type=Path, default=ROOT / "target/debug/libdiamond.rlib")
    parser.add_argument("--dependencies", type=Path, default=ROOT / "target/debug/deps")
    parser.add_argument("--report", type=Path, required=True)
    options = parser.parse_args()
    root = Path(tempfile.mkdtemp(prefix="diamond-w4-independent-migration-"))
    pinned = root / "diamond"
    shutil.copy2(options.binary, pinned)
    options.binary = pinned
    current, next_version, manifest2, source2 = versions(root / "projects")
    database = root / "database"
    database.mkdir()
    service = Service(database, pinned, current)
    results, fatal, blocker, child = [], None, None, None

    def record(name, okay, observed, expected):
        results.append({"name": name, "status": "PASS" if okay else "FAIL", "expected": expected, "observed": observed})

    def admin(command, project=current, success=True, *arguments):
        return service.cli(command, project, "--postgres-env", ENV_NAME, *arguments,
                           success=success, configuration=service.owner)

    try:
        native(options, root, results)
        service.postgres()
        service.cli("check", current)
        service.cli("check", next_version)
        admin("project-init")
        service.cli("project-user-add", current, "--postgres-env", ENV_NAME, "--user", "alice", "--password-stdin",
                    configuration=service.owner, input=PASSWORD + "\n")
        relation = actual_table(service)
        # Trusted setup, no application implementation: 37 valid distinct rows.
        service.sql(f"INSERT INTO {relation} (id,owner,workspace,title,number,active,amount,stamp,wait,maybe,phase) "
                    "SELECT 'row-'||lpad(n::text,3,'0'),'alice','workspace-1',encode(convert_to('Title '||n,'UTF8'),'hex'),n,true,n,-n,n,NULL,'Open' FROM generate_series(0,36) n")
        status = json.loads(admin("project-status").stdout)
        record("explicit-current-schema-status", status["version"] == 1, status, {"version": 1})
        audit = json.loads(admin("project-audit").stdout)
        record("initial-integrity-audit", audit["valid"] is True and audit["rows"] == {"Rows": 37}, audit, {"valid": True, "rows": {"Rows": 37}})
        privileges, elevated = constrain_runtime(service)
        run = service.cli("project-migrate", next_version, "--postgres-env", ENV_NAME, success=False)
        record("runtime-role-cannot-migrate", run.returncode != 0 and service.sql("SELECT version FROM diamond_w4.apps") == "1",
               {"exit": run.returncode, "stderr": run.stderr, "privileges": privileges}, "non-owner migration refused with no schema mutation")
        # Both real coordinators request exactly the same next version. One may
        # commit; the other must observe that committed current version and fail.
        command = [str(pinned), "project-migrate", str(next_version), "--postgres-env", ENV_NAME]
        environment = os.environ | {ENV_NAME: service.owner}
        first = subprocess.Popen(command, env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        second = subprocess.Popen(command, env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        outcomes = []
        for process in [first, second]:
            stdout, stderr = process.communicate(timeout=30)
            outcomes.append({"exit": process.returncode, "stdout": stdout, "stderr": stderr})
        record("serialized-migration-coordinators", sum(item["exit"] == 0 for item in outcomes) == 1
               and service.sql("SELECT version FROM diamond_w4.apps") == "2", outcomes, "one version-2 commit, one already-current refusal")
        # A preexisting Some value is application data; filling NULL must retain it.
        service.sql(f"UPDATE {relation} SET new_flag=91 WHERE id='row-011'")
        first_batch = json.loads(admin("project-backfill", next_version, True, "--batch", "7").stdout)
        record("bounded-first-backfill", first_batch["processed"] == 7 and first_batch["last_id"] == "row-006" and first_batch["done"] is False,
               first_batch, {"processed": 7, "last_id": "row-006", "done": False})
        before_checkpoint = service.sql("SELECT last_id||':'||done::text FROM diamond_w4.backfills")
        before_rows = service.sql(f"SELECT count(*) FROM {relation} WHERE new_flag=7")
        # Hold the next physical row, start a real CLI batch behind that lock,
        # then kill it before commit. The retained checkpoint is independent SQL.
        blocker = subprocess.Popen(["psql", service.owner, "-X", "-qAt", "-v", "ON_ERROR_STOP=1"], stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
        blocker.stdin.write(f"BEGIN; SELECT id FROM {relation} WHERE id='row-007' FOR UPDATE;\n")
        blocker.stdin.flush()
        readable, _, _ = select.select([blocker.stdout], [], [], 5)
        if not readable or blocker.stdout.readline().strip() != "row-007":
            raise AssertionError("backfill blocker did not lock next row")
        child = subprocess.Popen([str(pinned), "project-backfill", str(next_version), "--postgres-env", ENV_NAME, "--batch", "7"],
                                 env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        waited = False
        for _ in range(100):
            count = service.sql("SELECT count(*) FROM pg_stat_activity WHERE wait_event_type='Lock' AND query LIKE '%FOR UPDATE%' AND pid<>pg_backend_pid()")
            if int(count) > 0:
                waited = True
                break
            if child.poll() is not None:
                break
            time.sleep(.025)
        if not waited:
            raise AssertionError(("backfill did not reach row lock", child.poll()))
        child.kill()
        child.communicate(timeout=15)
        child = None
        blocker.stdin.write("ROLLBACK;\n\\q\n")
        blocker.stdin.flush()
        blocker.communicate(timeout=10)
        blocker = None
        checkpoint = service.sql("SELECT last_id||':'||done::text FROM diamond_w4.backfills")
        row_count = service.sql(f"SELECT count(*) FROM {relation} WHERE new_flag=7")
        record("killed-batch-does-not-advance", checkpoint == before_checkpoint and row_count == before_rows,
               {"checkpoint": checkpoint, "filled": row_count}, {"checkpoint": before_checkpoint, "filled": before_rows})
        batches = []
        for _ in range(8):
            batch = json.loads(admin("project-backfill", next_version, True, "--batch", "7").stdout)
            batches.append(batch)
            if batch["done"]:
                break
        stored = service.sql_json(f"SELECT id,new_flag FROM {relation} ORDER BY id")
        expected = [{"id": f"row-{n:03d}", "new_flag": 91 if n == 11 else 7} for n in range(37)]
        record("resumed-complete-exact-backfill", stored == expected and batches[-1]["done"] and all(batch["processed"] <= 7 for batch in batches),
               {"batches": batches, "rows": stored}, {"rows": expected, "maximum_processed": 7})
        replay = json.loads(admin("project-backfill", next_version, True, "--batch", "7").stdout)
        record("completed-backfill-no-replay", replay["done"] and replay["processed"] == 0, replay, {"done": True, "processed": 0})
        for batch in ["0", "1001"]:
            run = admin("project-backfill", next_version, False, "--batch", batch)
            record("invalid-backfill-batch-" + batch, run.returncode != 0, {"exit": run.returncode, "stderr": run.stderr}, "safe input refusal")
        changed_plan = write(root / "changed-plan", manifest2.replace("value = 7", "value = 8"), source2)
        run = admin("project-backfill", changed_plan, False, "--batch", "7")
        record("same-name-changed-backfill-refused", run.returncode != 0, {"exit": run.returncode, "stderr": run.stderr}, "plan fingerprint mismatch, no new writes")
        destructive = write(root / "destructive", manifest2.replace("version = 2", "version = 3"), source2.replace("  new_flag: Option<Int> = None\n", ""))
        run = admin("project-migrate", destructive, False)
        record("destructive-removal-refused", run.returncode != 0 and service.sql("SELECT version FROM diamond_w4.apps") == "2",
               {"exit": run.returncode, "stderr": run.stderr}, "explicit refusal, version remains2")
        audit = json.loads(admin("project-audit", next_version).stdout)
        record("post-backfill-audit", audit["valid"] and audit["rows"] == {"Rows": 37}, audit, {"valid": True, "rows": {"Rows": 37}})
        # Exact shape metadata is part of startup/admin validation. Owner-only
        # corruption must fail closed; restore the exact original afterward.
        old_shape = service.sql("SELECT shape FROM diamond_w4.apps")
        service.sql("UPDATE diamond_w4.apps SET shape='not-the-recorded-digest'")
        run = admin("project-audit", next_version, False)
        record("metadata-digest-corruption-refused", run.returncode != 0, {"exit": run.returncode, "stderr": run.stderr}, "digest mismatch refusal")
        service.sql("UPDATE diamond_w4.apps SET shape='" + old_shape + "'")
        audit = json.loads(admin("project-audit", next_version).stdout)
        record("metadata-recovery-audit", audit["valid"] is True, audit, {"valid": True})
        # A migration is operated through the current application's normal
        # consumer after restart, using the same constrained runtime role.
        contract, _ = service.build(next_version)
        service.start_node("A", project=next_version)
        service.start_node("B", project=next_version)
        alice = Client(service, contract)
        alice.login("alice")
        for node in ["A", "B"]:
            status, value = alice.rpc("read_row", "row-011", node=node)
            record("migrated-current-schema-normal-read-" + node,
                   status == 200 and value["value"]["new_flag"] == {"tag": "Some", "value": "91"}
                   and value["value"]["number"] == "11" and value["value"]["title"] == "Title 11",
                   {"status": status, "value": value}, "constrained-role current server reads preserved Some91 and original data")
    except Exception as failure:
        fatal = repr(failure)
    finally:
        for process in [child, blocker]:
            if process is not None:
                process.kill()
                process.communicate(timeout=10)
        service.close()
        status = "FAIL" if fatal or service.cleanup_errors or any(result["status"] == "FAIL" for result in results) else "PASS"
        report = {"status": status, "binary_sha256": sha256(pinned), "error": fatal, "results": results,
                  "shutdowns": service.shutdowns, "cleanup_errors": service.cleanup_errors,
                  "temporary_root": str(root) if status == "FAIL" else None,
                  "scope": "real current-schema CLI and external native consumer, stopped app servers, actual killed transaction; no version compatibility or browser claim"}
        options.report.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
        if status == "PASS":
            shutil.rmtree(root)
    print(json.dumps({"status": status, "cases": len(results), "error": fatal,
                      "failures": [result["name"] for result in results if result["status"] == "FAIL"], "report": str(options.report)}))
    return status != "PASS"


if __name__ == "__main__":
    raise SystemExit(main())
