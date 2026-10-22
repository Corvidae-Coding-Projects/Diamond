#!/usr/bin/env python3
"""Actual PostgreSQL/HTTP scalar-storage checks, with independent wire values."""
from __future__ import annotations
import argparse
import copy
import hashlib
import json
from pathlib import Path
import shutil
import tempfile

from schema_fixture import write
from service import Client, ENV_NAME, PASSWORD, Service, sha256

ROOT = Path(__file__).resolve().parents[2]


def inputs():
    integers = [-9223372036854775808, -9007199254740993, -1, 0, 1, 9007199254740993, 9223372036854775807]
    text = ["雪", "a\0b", "é", "e\u0301", "🐦\nline", "<script>literal</script>", "trailing \r\n"]
    for index in range(23):
        integer = str(integers[index % len(integers)])
        title = "😀" * 80 if index == 21 else "a\0" * 40 if index == 22 else f"unique-{index}:{text[index % len(text)]}"
        yield {"workspace": {"tag": "workspace", "value": "workspace-1"},
               "title": title, "number": integer,
               "active": index % 2 == 0, "amount": {"tag": "Decimal", "value": integer},
               "stamp": {"tag": "Timestamp", "value": integer},
               "wait": {"tag": "Duration", "value": str(-index)},
               "maybe": {"tag": "None"} if index % 3 == 0 else {"tag": "Some", "value": "" if index % 3 == 1 else text[index % len(text)]},
               "phase": {"tag": "Open" if index % 2 == 0 else "Closed", "fields": []}}


def constrain_runtime(service):
    """Owner-only setup grants DML on the isolated runtime schemas, never DDL."""
    schemas = service.sql("SELECT nspname FROM pg_namespace WHERE nspname LIKE 'diamond%' ORDER BY nspname").splitlines()
    if not schemas:
        raise AssertionError("initialization created no Diamond schema")
    service.sql("CREATE ROLE diamond_w4_runtime LOGIN")
    for schema in schemas:
        # SQL identifiers come from a trusted fixed-name schema inventory, with
        # double quotes escaped rather than interpolating application values.
        identifier = '"' + schema.replace('"', '""') + '"'
        service.sql(f"GRANT USAGE ON SCHEMA {identifier} TO diamond_w4_runtime")
        for relation in service.sql(f"SELECT tablename FROM pg_tables WHERE schemaname='{schema}' ORDER BY tablename").splitlines():
            quoted = identifier + '."' + relation.replace('"', '""') + '"'
            privileges = "SELECT,INSERT,UPDATE,DELETE" if (schema == "diamond_w4" and relation.startswith("r_")) or (schema == "diamond_w2" and relation in ["sessions", "rates"]) else "SELECT"
            service.sql(f"GRANT {privileges} ON TABLE {quoted} TO diamond_w4_runtime")
    service.configuration = service.owner.replace("user=diamond_w4_owner", "user=diamond_w4_runtime")
    schema_privileges = service.sql_json("SELECT nspname, has_schema_privilege('diamond_w4_runtime',oid,'CREATE') AS can_create "
                                         "FROM pg_namespace WHERE nspname LIKE 'diamond%' ORDER BY nspname")
    owner_privilege = service.sql("SELECT rolsuper OR rolcreatedb OR rolcreaterole FROM pg_roles WHERE rolname='diamond_w4_runtime'")
    forbidden = service.sql("SELECT count(*) FROM pg_tables WHERE schemaname IN ('diamond_w4','diamond_w2') "
                            "AND NOT (schemaname='diamond_w4' AND tablename LIKE 'r\\_%' ESCAPE '\\') "
                            "AND NOT (schemaname='diamond_w2' AND tablename IN ('sessions','rates')) "
                            "AND (has_table_privilege('diamond_w4_runtime',quote_ident(schemaname)||'.'||quote_ident(tablename),'INSERT') "
                            "OR has_table_privilege('diamond_w4_runtime',quote_ident(schemaname)||'.'||quote_ident(tablename),'UPDATE') "
                            "OR has_table_privilege('diamond_w4_runtime',quote_ident(schemaname)||'.'||quote_ident(tablename),'DELETE'))")
    if forbidden != "0":
        raise AssertionError(("runtime metadata DML grants", forbidden))
    return schema_privileges, owner_privilege


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, default=ROOT / "target/debug/diamond")
    parser.add_argument("--report", type=Path, required=True)
    options = parser.parse_args()
    root = Path(tempfile.mkdtemp(prefix="diamond-w4-independent-scalars-"))
    binary = root / "diamond"
    shutil.copy2(options.binary, binary)
    project = write(root / "project")
    service = Service(root, binary, project)
    results, fatal = [], None

    def record(name, okay, observed, expected):
        results.append({"name": name, "status": "PASS" if okay else "FAIL", "expected": expected, "observed": observed})

    try:
        service.postgres()
        contract, build = service.build()
        missing = service.cli("serve", project, "--listen", f"127.0.0.1:{service.ports['A']}", "--origin", service.origin,
                              "--postgres-env", ENV_NAME, success=False, configuration=service.owner, timeout=4)
        record("startup-missing-metadata-refused", missing.returncode != 0,
               {"exit": missing.returncode, "stderr": missing.stderr}, "startup does not implicitly initialize absent storage")
        service.cli("project-init", project, "--postgres-env", ENV_NAME)
        relation_name = service.sql("SELECT tablename FROM pg_tables WHERE schemaname='diamond_w4' AND tablename LIKE 'r\\_%' ESCAPE '\\'")
        relation = 'diamond_w4."' + relation_name.replace('"', '""') + '"'
        columns = service.sql_json("SELECT a.attname AS name,format_type(a.atttypid,a.atttypmod) AS type,a.attnotnull AS required,co.collname AS collation "
                                   "FROM pg_attribute a LEFT JOIN pg_collation co ON co.oid=a.attcollation "
                                   "WHERE a.attrelid='" + relation + "'::regclass AND a.attnum>0 AND NOT a.attisdropped ORDER BY a.attname")
        actual_columns = {item["name"]: (item["type"], item["required"], item["collation"]) for item in columns}
        expected_columns = {name: ("text", name != "maybe", "C") for name in ["id", "owner", "workspace", "title", "maybe", "phase"]}
        expected_columns.update({name: ("bigint", True, None) for name in ["number", "amount", "stamp", "wait"]})
        expected_columns["active"] = ("boolean", True, None)
        record("physical-typed-nullable-columns", actual_columns == expected_columns, actual_columns, expected_columns)
        keys = service.sql_json("SELECT indisprimary AS primary,indisunique AS unique,pg_get_indexdef(indexrelid) AS definition FROM pg_index WHERE indrelid='" + relation + "'::regclass ORDER BY indexrelid")
        checks = service.sql("SELECT count(*) FROM pg_constraint WHERE conrelid='" + relation + "'::regclass AND contype='c'")
        record("physical-primary-unique-index-checks", len(keys) == 3 and sum(row["primary"] for row in keys) == 1
               and sum(row["unique"] for row in keys) == 2 and checks == "11", {"indexes": keys, "checks": checks},
               "one PK, one scoped unique, one declared secondary index, eleven typed column checks")
        for user in ["alice", "bob"]:
            service.cli("project-user-add", project, "--postgres-env", ENV_NAME, "--user", user,
                        "--password-stdin", input=PASSWORD + "\n")
        privileges, owner_privilege = constrain_runtime(service)
        record("runtime-no-schema-owner-authority", all(not row["can_create"] for row in privileges) and owner_privilege == "f",
               {"schema_privileges": privileges, "elevated_role": owner_privilege}, "DML role cannot create schema objects or administer roles/databases")
        service.sql("UPDATE diamond_w4.apps SET version=2")
        mismatch = service.cli("serve", project, "--listen", f"127.0.0.1:{service.ports['A']}", "--origin", service.origin,
                               "--postgres-env", ENV_NAME, success=False, timeout=4)
        record("startup-incompatible-current-version-refused", mismatch.returncode != 0,
               {"exit": mismatch.returncode, "stderr": mismatch.stderr}, "constrained-role startup rejects mismatched current version")
        service.sql("UPDATE diamond_w4.apps SET version=1")
        service.start_node("A")
        service.start_node("B")
        alice, bob = Client(service, contract), Client(service, contract)
        alice.login("alice")
        bob.login("bob", node="B")
        committed = []
        for index, value in enumerate(inputs()):
            node = "A" if index % 2 else "B"
            status, observed = alice.rpc("create_row", value, node=node)
            output = observed.get("value") if isinstance(observed, dict) else None
            okay = status == 200 and isinstance(output, dict) and output.get("owner") == {"tag": "user", "value": "alice"}
            if okay:
                row_id = output.get("id")
                okay = row_id is not None and {key: item for key, item in output.items() if key not in ["id", "owner"]} == value
            record("create-exact-value-" + str(index), okay, {"status": status, "value": observed}, value)
            if not okay:
                continue
            committed.append(output)
            status, observed = alice.rpc("read_row", output["id"]["value"], node="A" if node == "B" else "B")
            record("fresh-node-read-" + str(index), status == 200 and observed == {"value": output}, {"status": status, "value": observed}, {"value": output})
            status, observed = bob.rpc("read_row", output["id"]["value"], node="B")
            record("other-user-denied-" + str(index), status == 403, {"status": status, "value": observed}, "HTTP 403, no resource disclosure")
        if len(committed) != 23:
            raise AssertionError(("scalar setup incomplete", len(committed)))
        original = committed[0]
        status, observed = alice.rpc("create_row", next(inputs()), node="B")
        record("duplicate-scoped-unique-reject", status >= 400, {"status": status, "value": observed}, "failed write; original relation unchanged")
        cross_scope = next(inputs())
        cross_scope["workspace"]["value"] = "workspace-2"
        status, observed = alice.rpc("create_row", cross_scope, node="A")
        record("same-title-different-scope-allowed", status == 200, {"status": status, "value": observed}, "same unique value allowed in a different declared scope")
        for name, mutate in [("forge-owner", lambda value: value.update(owner={"tag": "user", "value": "bob"})),
                             ("title-over-scalar-bound", lambda value: value.update(title="😀" * 81)),
                             ("wrong-scope-tag", lambda value: value["workspace"].update(tag="user")),
                             ("number-coercion", lambda value: value.update(number=1)),
                             ("bad-optional", lambda value: value.update(maybe=None)),
                             ("enum-forgery", lambda value: value["phase"].update(tag="Admin"))]:
            value = next(inputs())
            mutate(value)
            status, observed = alice.rpc("create_row", value)
            record(name, status == 400, {"status": status, "value": observed}, "HTTP 400 before protected write")
        status, observed = bob.rpc("edit_row", original["id"]["value"], {"title": "Denied mutation", "number": "72"})
        record("other-user-update-denied", status == 403, {"status": status, "value": observed}, "HTTP 403")
        status, observed = bob.rpc("delete_row", original["id"]["value"])
        record("other-user-delete-denied", status == 403, {"status": status, "value": observed}, "HTTP 403")
        expected = copy.deepcopy(original)
        expected.update(title="Edited 雪\0exact", number="9007199254740993")
        status, observed = alice.rpc("edit_row", original["id"]["value"], {"title": expected["title"], "number": expected["number"]}, node="B")
        record("owner-update-return", status == 200 and observed == {"value": expected}, {"status": status, "value": observed}, {"value": expected})
        service.stop_node("A")
        service.start_node("A")
        fresh = Client(service, contract)
        fresh.login("alice")
        status, observed = fresh.rpc("read_row", original["id"]["value"], node="A")
        record("restart-durable-value", status == 200 and observed == {"value": expected}, {"status": status, "value": observed}, {"value": expected})
        for limit in [0, 101, -1, 9223372036854775807]:
            status, observed = alice.rpc("list_rows", original["workspace"], str(limit))
            record("invalid-list-bound-" + str(limit), status >= 400, {"status": status, "value": observed}, "bounded safe rejection")
        status, observed = alice.rpc("list_rows", original["workspace"], "100", node="B")
        expected_rows = [expected if row["id"] == original["id"] else row for row in committed]
        actual_rows = observed.get("value") if isinstance(observed, dict) else None
        ordered = sorted(expected_rows, key=lambda row: row["id"]["value"])
        record("complete-independent-readback", status == 200 and actual_rows == ordered,
               {"status": status, "value": observed}, {"value": ordered})
        status, observed = bob.rpc("list_rows", original["workspace"], "100")
        record("hidden-relation-list-empty", status == 200 and observed == {"value": []}, {"status": status, "value": observed}, {"value": []})
        constraints = service.sql_json("SELECT conname,pg_get_constraintdef(oid) AS definition FROM pg_constraint WHERE conrelid='" + relation + "'::regclass AND contype='c'")
        title_constraint = next(row for row in constraints if "title" in row["definition"])
        quoted_constraint = '"' + title_constraint["conname"].replace('"', '""') + '"'
        # Isolated owner-only corruption fixture. Runtime DML credentials have
        # no ability to remove this check or change schema metadata.
        service.sql(f"ALTER TABLE {relation} DROP CONSTRAINT {quoted_constraint}; UPDATE {relation} SET title='zz' WHERE id='{original['id']['value']}'")
        status, observed = fresh.rpc("read_row", original["id"]["value"], node="A")
        record("fetched-corrupt-text-fails-closed", status >= 400 and "value" not in observed,
               {"status": status, "value": observed}, "no typed record returned from corrupt text/domain")
        audit = service.cli("project-audit", project, "--postgres-env", ENV_NAME, configuration=service.owner, success=False)
        record("explicit-audit-rejects-corrupt-row-or-catalog", audit.returncode != 0, {"exit": audit.returncode, "stderr": audit.stderr}, "audit refuses owner-injected corrupt data/catalog")
        service.sql(f"UPDATE {relation} SET title='{expected['title'].encode().hex()}' WHERE id='{original['id']['value']}'; ALTER TABLE {relation} ADD CONSTRAINT {quoted_constraint} {title_constraint['definition']}")
        status, observed = fresh.rpc("read_row", original["id"]["value"], node="B")
        record("corruption-recovery-exact-read", status == 200 and observed == {"value": expected}, {"status": status, "value": observed}, {"value": expected})
        audit = service.cli("project-audit", project, "--postgres-env", ENV_NAME, configuration=service.owner)
        audit_value = json.loads(audit.stdout)
        record("corruption-recovery-audit", audit_value.get("valid") is True, audit_value, "repaired exact data and check pass explicit audit")
        status, observed = alice.rpc("delete_row", original["id"]["value"])
        record("owner-delete", status == 200 and observed == {"value": "Deleted"}, {"status": status, "value": observed}, {"value": "Deleted"})
        status, observed = fresh.rpc("read_row", original["id"]["value"], node="B")
        record("deleted-read-denied", status == 403, {"status": status, "value": observed}, "HTTP 403, same forbidden/missing disclosure")
    except Exception as failure:
        fatal = repr(failure)
    finally:
        service.close()
        status = "FAIL" if fatal or service.cleanup_errors or any(result["status"] == "FAIL" for result in results) else "PASS"
        report = {"status": status, "binary_sha256": sha256(binary), "error": fatal,
                  "results": results, "trace": service.trace,
                  "shutdowns": service.shutdowns, "cleanup_errors": service.cleanup_errors,
                  "temporary_root": str(root) if status == "FAIL" else None,
                  "scope": "isolated real PostgreSQL, restricted runtime DML role, generated RPC on two real nodes; no browser claim"}
        options.report.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
        if status == "PASS":
            shutil.rmtree(root)
    print(json.dumps({"status": status, "cases": len(results), "error": fatal,
                      "failures": [result["name"] for result in results if result["status"] == "FAIL"],
                      "report": str(options.report)}))
    return status != "PASS"


if __name__ == "__main__":
    raise SystemExit(main())
