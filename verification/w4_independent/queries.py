#!/usr/bin/env python3
"""Actual generated RPC versus a prewritten independent workspace data model."""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time

from model import (GRANTS, SEED, WORKSPACES, aggregate, authorized, corpus,
                   join_parents, projects, revoke)
from query_fixture import write
from scalars import constrain_runtime
from service import Client, ENV_NAME, PASSWORD, Service, sha256

ROOT = Path(__file__).resolve().parents[2]
APP = "IndependentRelationalQueries"


def table(resource):
    return 'diamond_w4."r_' + hashlib.sha256(APP.encode()).hexdigest()[:16] + '_' + hashlib.sha256(resource.encode()).hexdigest()[:16] + '"'


def issue_wire(row):
    maybe = lambda value: {"tag": "None"} if value is None else {"tag": "Some", "value": value}
    return {"id": {"tag": "issue", "value": row.id}, "workspace": {"tag": "workspace", "value": row.workspace},
            "owner": maybe(None if row.owner is None else {"tag": "user", "value": row.owner}),
            "rank": str(row.rank), "title": row.title, "optional": maybe(row.optional),
            "mirror": maybe(row.mirror),
            "amount": {"tag": "Decimal", "value": str(int(row.amount * 1_000_000))},
            "timestamp": {"tag": "Timestamp", "value": str(row.timestamp)},
            "parent": maybe(None if row.parent is None else {"tag": "project", "value": row.parent})}


def project_wire(row):
    return {"id": {"tag": "project", "value": row.id}, "workspace": {"tag": "workspace", "value": row.workspace},
            "owner": {"tag": "user", "value": row.owner}, "public_to_members": row.public_to_members}


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"))


def fingerprint(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def seed(service, rows):
    def copy(resource, columns, values):
        command = f"COPY {table(resource)} ({','.join(chr(34) + name + chr(34) for name in columns)}) FROM STDIN"
        data = "\n".join("\t".join("\\N" if value is None else str(value) for value in row) for row in values) + "\n"
        subprocess.run(["psql", service.owner, "-X", "-q", "-v", "ON_ERROR_STOP=1", "-c", command],
                       input=data, text=True, check=True, capture_output=True, timeout=90)
    memberships = [(f"membership-{user}-{workspace}", workspace, user, role.capitalize())
                   for user, scopes in GRANTS.items() if user is not None for workspace, role in scopes.items()]
    copy("Memberships", ["id", "workspace", "principal", "role"], memberships)
    copy("Projects", ["id", "workspace", "owner", "public_to_members"],
         [(row.id, row.workspace, row.owner, "t" if row.public_to_members else "f") for row in projects()])
    copy("Issues", ["id", "workspace", "owner", "rank", "title", "optional", "mirror", "amount", "timestamp", "parent"],
         [(row.id, row.workspace, row.owner, row.rank, row.title.encode().hex(),
           None if row.optional is None else row.optional.encode().hex(), None if row.mirror is None else row.mirror.encode().hex(),
           int(row.amount * 1_000_000), row.timestamp, row.parent)
          for row in rows])
    service.sql("ANALYZE")


def actual_page_plan(service, client):
    """EXPLAIN a SELECT captured from the real server's PostgreSQL execution.

    Only the isolated owner's PG log setting changes. The SQL is captured, not
    recreated from the production query builder; safe literal parameters come
    from the same PG log and are retained as synthetic app identity/scope data.
    """
    service.sql("ALTER SYSTEM SET log_statement='all'")
    service.sql("SELECT pg_reload_conf()")
    time.sleep(.2)
    for node in ["A", "B"]:
        service.stop_node(node)
        service.start_node(node)
    current_log = service.sql("SELECT pg_current_logfile()")
    log = (Path(current_log) if Path(current_log).is_absolute() else service.data / current_log) if current_log else service.root / "postgres.log"
    offset = log.stat().st_size
    status, value = client.rpc("page", {"tag": "workspace", "value": "workspace-1"}, "", "17", False)
    time.sleep(.2)
    service.sql("ALTER SYSTEM SET log_statement='none'")
    service.sql("SELECT pg_reload_conf()")
    time.sleep(.1)
    if status != 200:
        raise AssertionError(("actual plan request", status, value))
    content = log.read_text()[offset:]
    lines = content.splitlines()
    captured, bindings = None, None
    for index, line in enumerate(lines):
        match = re.search(r"execute [^:]*: (SELECT .*ORDER BY .*LIMIT 17 OFFSET 0)$", line)
        if match and table("Issues") in match.group(1):
            captured = match.group(1)
            if index + 1 < len(lines) and "parameters:" in lines[index + 1].lower():
                bindings = dict(re.findall(r"\$(\d+) = (NULL|'(?:[^']|'')*')", lines[index + 1]))
            break
    if captured is None or not bindings:
        settings = service.sql("SELECT current_setting('log_statement'),current_setting('logging_collector'),current_setting('log_directory'),current_setting('log_destination')")
        raise AssertionError(("actual generated SQL not captured", settings, content[-8000:]))
    sql = re.sub(r"\$(\d+)", lambda match: bindings[match.group(1)], captured)
    plan = json.loads(service.sql("EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) " + sql))
    relation = table("Issues").split('"')[1]
    nodes = []
    sorts = []
    def walk(node):
        if node.get("Node Type") in ["Sort", "Incremental Sort"]:
            sorts.append(node["Node Type"])
        if node.get("Relation Name") == relation:
            scan = {key: node.get(key) for key in ["Node Type", "Index Name", "Index Cond", "Filter", "Actual Rows", "Actual Loops", "Rows Removed by Filter"] if key in node}
            scan["index_children"] = [child.get("Index Name") for child in node.get("Plans", []) if "Index" in child.get("Node Type", "")]
            nodes.append(scan)
        for child in node.get("Plans", []):
            walk(child)
    walk(plan[0]["Plan"])
    indexed = any("Index" in node["Node Type"] or node["index_children"] for node in nodes)
    bounded = not sorts and all(node.get("Actual Rows", 0) * node.get("Actual Loops", 1) <= 100 for node in nodes)
    return {"captured_sql": captured, "synthetic_bindings": bindings, "plan": plan,
            "issue_scan_nodes": nodes, "indexed": indexed, "ordered_bounded": bounded, "sort_nodes": sorts}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, default=ROOT / "target/debug/diamond")
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--rows", type=int, default=100_003)
    parser.add_argument("--keep", action="store_true")
    options = parser.parse_args()
    if options.rows < 103 or options.rows > 100_003:
        raise SystemExit("rows must be 103..100003; only the full default qualifies 100k")
    root = Path(tempfile.mkdtemp(prefix="diamond-w4-independent-query-"))
    binary = root / "diamond"
    shutil.copy2(options.binary, binary)
    project = write(root / "project")
    service = Service(root, binary, project)
    results, fatal, clients, rows = [], None, {}, corpus(options.rows)
    nullable_pairs = {name: 0 for name in ["None_None", "None_Some", "Some_None", "Some_Some_equal", "Some_Some_unequal"]}
    for row in rows:
        category = "None_None" if row.optional is None and row.mirror is None else "None_Some" if row.optional is None else "Some_None" if row.mirror is None else "Some_Some_equal" if row.optional == row.mirror else "Some_Some_unequal"
        nullable_pairs[category] += 1
    if any(count == 0 for count in nullable_pairs.values()):
        raise AssertionError(("all independent nullable-policy input classes must be populated", nullable_pairs))

    def record(name, okay, observed, expected):
        results.append({"name": name, "status": "PASS" if okay else "FAIL", "expected": expected, "observed": observed})

    def compare(name, client, operation, arguments, expected, node="A"):
        status, observed = client.rpc(operation, *arguments, node=node)
        actual = observed.get("value") if isinstance(observed, dict) else None
        # The connected profile requires authenticated RPC even when a resource
        # policy itself could evaluate an anonymous principal. Anonymous model
        # denials therefore cross the earlier fixed HTTP gate, not SQL results.
        anonymous = client is clients.get(None)
        okay = status == 403 and observed == {"error": "denied"} if anonymous else status == 200 and actual == expected
        record(name, okay, {"status": status, "sha256": fingerprint(actual), "sample": actual[:2] if isinstance(actual, list) else actual},
               {"http_status": 403 if anonymous else 200, "sha256": fingerprint(expected), "count": len(expected) if isinstance(expected, list) else None,
                "sample": expected[:2] if isinstance(expected, list) else expected})
        return actual if status == 200 else None

    def scope(workspace):
        return {"tag": "workspace", "value": workspace}

    try:
        service.postgres()
        contract, _ = service.build()
        service.cli("project-init", project, "--postgres-env", ENV_NAME)
        foreign_keys = service.sql_json("SELECT pg_get_constraintdef(oid) AS definition,confrelid='" + table("Projects") + "'::regclass AS correct_parent FROM pg_constraint WHERE conrelid='" + table("Issues") + "'::regclass AND contype='f'")
        record("physical-scoped-foreign-key", len(foreign_keys) == 1 and foreign_keys[0]["correct_parent"]
               and "parent" in foreign_keys[0]["definition"] and "workspace" in foreign_keys[0]["definition"],
               foreign_keys, "one composite parent+workspace FK targeting Projects")
        for user in ["alice", "bob", "carol", "outsider"]:
            service.cli("project-user-add", project, "--postgres-env", ENV_NAME, "--user", user,
                        "--password-stdin", input=PASSWORD + "\n")
        seed(service, rows)
        record("100k-real-rows-seeded", int(service.sql(f"SELECT count(*) FROM {table('Issues')}")) == len(rows),
               {"count": service.sql(f"SELECT count(*) FROM {table('Issues')}")}, {"count": len(rows), "full_capacity_case": len(rows) >= 100_000})
        privileges, elevated = constrain_runtime(service)
        record("runtime-no-owner", all(not row["can_create"] for row in privileges) and elevated == "f",
               {"schemas": privileges, "elevated_role": elevated}, "no owner/admin/DDL role")
        service.start_node("A")
        service.start_node("B")
        for user in ["alice", "bob", "carol", "outsider", None]:
            client = clients[user] = Client(service, contract)
            if user is not None:
                client.login(user, node="B" if user == "bob" else "A")
            else:
                client.request("A", "/_diamond/session")
            for index, workspace in enumerate(WORKSPACES):
                model = aggregate(rows, user, workspace)
                compare(f"count-{user}-{workspace}", client, "count", [scope(workspace)], str(model["count"]), node="A" if index % 2 else "B")
                compare(f"sum-{user}-{workspace}", client, "sum_rank", [scope(workspace)], str(model["sum_rank"]), node="B")
                expected = sorted(authorized(rows, user, workspace), key=lambda row: (row.rank, row.id))[:17]
                compare(f"first-page-{user}-{workspace}", client, "page", [scope(workspace), "", "17", False], [issue_wire(row) for row in expected])
        # Complete pagination through one populated authorized workspace. The
        # order oracle was fixed before requests and does not parse cursors.
        expected = sorted(authorized(rows, "alice", "workspace-1"), key=lambda row: (row.rank, row.id))
        seen, cursor, page_count, pagination_error, page_boundaries = [], "", 0, None, []
        while page_count <= len(expected) // 100 + 2:
            status, observed = clients["alice"].rpc("page", scope("workspace-1"), cursor, "100", False,
                                                     node="A" if page_count % 2 else "B")
            page = observed.get("value") if isinstance(observed, dict) else None
            if status != 200 or not isinstance(page, list):
                pagination_error = {"status": status, "value": observed, "page": page_count}
                break
            if not page:
                break
            seen.extend(page)
            page_count += 1
            page_boundaries.append({"page": page_count, "first_id": page[0]["id"]["value"], "last_id": page[-1]["id"]["value"], "count": len(page)})
            status, observed = clients["alice"].rpc("cursor", page[-1], False, node="B")
            if status != 200 or not isinstance(observed.get("value"), str):
                pagination_error = {"cursor_status": status, "value": observed, "page": page_count}
                break
            next_cursor = observed["value"]
            if next_cursor == cursor:
                pagination_error = "cursor did not advance"
                break
            cursor = next_cursor
        expected_wire = [issue_wire(row) for row in expected]
        record("complete-exact-once-pagination", pagination_error is None and seen == expected_wire,
               {"pages": page_count, "records": len(seen), "sha256": fingerprint(seen), "error": pagination_error,
                "page_boundaries": page_boundaries, "observed_ids": [row["id"]["value"] for row in seen],
                "unique_id_count": len({row["id"]["value"] for row in seen})},
               {"records": len(expected), "sha256": fingerprint(expected_wire)})
        plan = actual_page_plan(service, clients["alice"])
        record("actual-indexed-generated-page-plan", plan["indexed"] and plan["ordered_bounded"] or len(rows) < 100_000, plan,
               "actual captured generated page SELECT uses an ordered Issues index with bounded row retrieval on the 100k fixture; small probes are diagnostic only")
        compare("reverse-duplicate-sort-keys", clients["alice"], "page", [scope("workspace-1"), "", "37", True],
                [issue_wire(row) for row in reversed(expected[-37:])])
        for user, workspace in [("alice", "workspace-0"), ("alice", "workspace-3"), ("bob", "workspace-0"), ("carol", "workspace-1"), (None, "workspace-1")]:
            expected_join = sorted(join_parents(rows, user, workspace), key=lambda pair: (pair[0].rank, pair[0].id))[:100]
            compare(f"both-base-policy-join-{user}-{workspace}", clients[user], "joined", [scope(workspace), "", "100"],
                    [{"left": issue_wire(left), "right": project_wire(right)} for left, right in expected_join], node="B")
        for user, workspace in [("alice", "workspace-0"), ("bob", "workspace-0"), ("alice", "workspace-1"), ("outsider", "workspace-1")]:
            selected = sorted(authorized(rows, user, workspace, lambda row: -13 <= row.rank < 6), key=lambda row: row.id)[:100]
            compare(f"indexed-ranges-{user}-{workspace}", clients[user], "filtered", [scope(workspace), "-13", "6"], [issue_wire(row) for row in selected])
            for optional in [None, "", "雪", "a\0b", "é", "e\u0301"]:
                value = {"tag": "None"} if optional is None else {"tag": "Some", "value": optional}
                selected = sorted(authorized(rows, user, workspace, lambda row: row.optional == optional), key=lambda row: row.id)[:100]
                compare(f"optional-filter-{user}-{workspace}-{canonical(optional)}", clients[user], "optional_rows", [scope(workspace), value],
                        [issue_wire(row) for row in selected])
        # Position cursors may be reused across identities/scopes, but cannot
        # transfer read authority. Foreign scope remains governed by its policy.
        for user, workspace in [("outsider", "workspace-1"), (None, "workspace-1"), ("bob", "workspace-1")]:
            compare(f"cursor-not-authority-{user}", clients[user], "page", [scope(workspace), cursor, "17", False], [])
        cross_scope = sorted(authorized(rows, "alice", "workspace-3"), key=lambda row: (row.rank, row.id))
        cross_scope = [row for row in cross_scope if (row.rank, row.id) > (expected[-1].rank, expected[-1].id)][:17]
        compare("cursor-position-new-scope-still-authorized", clients["alice"], "page", [scope("workspace-3"), cursor, "17", False], [issue_wire(row) for row in cross_scope])
        status, value = clients["alice"].rpc("page", scope("workspace-1"), cursor, "17", True)
        record("cursor-direction-mismatch-refused", status == 400, {"status": status, "value": value}, "query-bound ordering direction rejects incompatible cursor")
        cursor_value = json.loads(bytes.fromhex(cursor))
        for field, value in [("resource", "Projects"), ("order", "title"), ("id", "bad/id")]:
            forged = dict(cursor_value, **{field: value})
            encoded = json.dumps(forged, separators=(",", ":")).encode().hex()
            status, value = clients["alice"].rpc("page", scope("workspace-1"), encoded, "17", False)
            record("cursor-forged-" + field, status == 400, {"status": status, "value": value}, "malformed incompatible cursor rejects before disclosure")
        for index, malformed in enumerate(["g0", "00", "aa", "0", "a" * 16_386, "00" * 300]):
            status, observed = clients["alice"].rpc("page", scope("workspace-1"), malformed, "17", False)
            record("malformed-cursor-" + str(index), status in [400, 413], {"status": status, "value": observed}, "bounded malformed-input rejection (HTTP400 or earlier envelope limit413), then recovery")
        compare("good-after-malformed-cursors", clients["alice"], "page", [scope("workspace-1"), "", "7", False], [issue_wire(row) for row in expected[:7]])
        # Reader, editor, cross-workspace reference and denied post-state writes.
        target = next(row for row in rows if row.workspace == "workspace-0" and row.parent is not None and row.owner is None)
        before = issue_wire(target)
        edit = {"workspace": scope(target.workspace), "title": "Independent edit 雪\0", "rank": "83", "parent": before["parent"]}
        status, observed = clients["alice"].rpc("edit_issue", target.id, edit, node="A")
        record("reader-update-denied", status == 403, {"status": status, "value": observed}, "HTTP 403")
        compare("denied-write-state-unchanged", clients["bob"], "read_issue", [target.id], before, node="B")
        cross_reference = dict(edit, parent={"tag": "Some", "value": {"tag": "project", "value": "project-001"}})
        status, observed = clients["bob"].rpc("edit_issue", target.id, cross_reference, node="B")
        record("cross-scope-fk-rejected", status >= 400, {"status": status, "value": observed}, "write rejected by scoped relationship")
        compare("cross-scope-fk-state-unchanged", clients["bob"], "read_issue", [target.id], before)
        other = next(row for row in rows if row.workspace == "workspace-1" and row.parent is not None)
        relink = {"workspace": scope("workspace-3"), "title": "Cross-scope relink", "rank": "31", "parent": {"tag": "None"}}
        status, observed = clients["alice"].rpc("edit_issue", other.id, relink)
        record("post-state-cross-scope-relink-denied", status == 403, {"status": status, "value": observed}, "deny relink even when the user can edit both scopes")
        compare("post-state-denial-unchanged", clients["alice"], "read_issue", [other.id], issue_wire(other), node="B")
        status, observed = clients["outsider"].rpc("page", scope("workspace-1"), "", "7", False,
                                                  extra={"principal": "alice", "role": "Admin"})
        record("forged-envelope-authority-denied", status == 400, {"status": status, "value": observed}, "unknown principal/role fields rejected before authority reconstruction")
        changed = replace(target, title=edit["title"], rank=83)
        compare("editor-update-allowed", clients["bob"], "edit_issue", [target.id, edit], issue_wire(changed), node="B")
        status, observed = clients["bob"].rpc("write_then_hidden", target.id, node="A")
        record("failed-return-read-aborts-whole-request", status == 403 and observed == {"error": "denied"},
               {"status": status, "value": observed}, "second write's proposed unreadable value fails, including earlier write")
        compare("return-read-failure-earlier-write-rolled-back", clients["bob"], "read_issue", [target.id], issue_wire(changed), node="B")
        rows[rows.index(target)] = changed
        target = changed
        # Same target read-modify-write from independent clients and both nodes.
        before_rank = target.rank
        extra = Client(service, contract)
        extra.login("bob", node="B")
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(client.rpc, "increment", target.id, node=node)
                       for client, node in [(clients["bob"], "A"), (extra, "B")]]
            outcomes = [future.result(timeout=25) for future in futures]
        confirmed = sum(status == 200 for status, value in outcomes)
        rank = int(service.sql(f"SELECT rank FROM {table('Issues')} WHERE id='{target.id}'"))
        record("concurrent-no-lost-update", confirmed >= 1 and rank == before_rank + confirmed and all(status in [200, 409] for status, _ in outcomes),
               {"outcomes": outcomes, "committed_rank": rank}, {"rank": before_rank + confirmed, "legal_outcomes": "confirmed serial increments or distinct serialization abort"})
        target = replace(target, rank=rank)
        # Revocation is committed independently, then new requests on both nodes
        # must deny without affinity or logout.
        service.sql(f"DELETE FROM {table('Memberships')} WHERE principal='bob' AND workspace='workspace-0'")
        grants = revoke(GRANTS, "bob", "workspace-0")
        for node in ["A", "B"]:
            compare("post-revocation-count-" + node, clients["bob"], "count", [scope("workspace-0")], "0", node=node)
            status, observed = clients["bob"].rpc("read_issue", target.id, node=node)
            record("post-revocation-id-" + node, status == 403, {"status": status, "value": observed}, "HTTP 403 after committed revocation")
        record("runtime-connection-bound-observed", int(service.sql("SELECT count(*) FROM pg_stat_activity WHERE usename='diamond_w4_runtime'")) <= 8,
               {"connections": service.sql("SELECT count(*) FROM pg_stat_activity WHERE usename='diamond_w4_runtime'")}, "bounded observed connections for two nodes; root separately tests admission pressure")
    except Exception as failure:
        fatal = repr(failure)
    finally:
        service.close()
        status = "FAIL" if fatal or service.cleanup_errors or any(result["status"] == "FAIL" for result in results) else "PASS"
        report = {"status": status, "binary_sha256": sha256(binary), "seed": SEED, "rows": len(rows),
                  "nullable_pair_input_counts": nullable_pairs,
                  "error": fatal, "results": results, "http_requests": len(service.trace),
                  "shutdowns": service.shutdowns, "cleanup_errors": service.cleanup_errors,
                  "temporary_root": str(root) if status == "FAIL" or options.keep else None,
                  "scope": "real PG, constrained role, actual generated RPC, independent Python reference; no browser, migration, fault or actual-plan claim"}
        options.report.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
        if status == "PASS" and not options.keep:
            shutil.rmtree(root)
    print(json.dumps({"status": status, "cases": len(results), "requests": len(service.trace), "rows": len(rows), "error": fatal,
                      "failures": [result["name"] for result in results if result["status"] == "FAIL"], "report": str(options.report)}))
    return status != "PASS"


if __name__ == "__main__":
    raise SystemExit(main())
