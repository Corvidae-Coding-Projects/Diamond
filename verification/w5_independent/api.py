#!/usr/bin/env python3
"""Actual bearer API calls against independent scope/policy/wire expectations."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import random
import shutil
import tempfile

from accounts import begin_window, client, issue_token, provision
from codec_cases import invalid as invalid_codecs, valid as valid_codecs
from common import Receipt, app_id, issue_wire, nominal, project_wire, quote, restrict_runtime, seed
from fixture import APP, ISSUES, PROJECTS, table, write
from model import Account, Credential, IdentityModel, PASSWORD, SEED
from service import ENV_NAME, Service, free_port, sha256

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    options = parser.parse_args()
    root = Path(tempfile.mkdtemp(prefix="diamond-w5-independent-api-"))
    binary = root / "diamond"
    shutil.copy2(options.binary, binary)
    project = write(root / "project")
    service = Service(root, binary, project)
    service.extra_serve = ("--identity-mailbox", str(service.mailbox))
    receipt, fatal = Receipt(), None
    receipt.secret(PASSWORD)
    model = IdentityModel()
    for username in ["alice", "bob", "carol", "outsider"]:
        model.accounts[username] = Account(username, username + "@qualification.invalid", "active")
    operations = ["read", "page", "count", "joined", "create", "move", "edit", "delete", "write_then_hidden", "codec", "global_count_allowed"]

    def compare(name, result, expected, *, code=200):
        observed_code, value = result
        okay = observed_code == code and (value == {"api_version": 1, "value": expected} if code == 200 else value == {"error": expected})
        receipt.record(name, okay, {"status": observed_code, "value": value}, {"status": code, "value": expected})
        return okay

    def rank(key):
        return service.sql(f"SELECT rank FROM {table('Issues')} WHERE id=" + quote(key))

    try:
        service.postgres()
        contract, build = service.build()
        rpc = contract["rpc_id"]
        service.cli("project-init", project, "--postgres-env", ENV_NAME)
        for username in model.accounts:
            provision(service, username)
        seed(service)
        privileges = restrict_runtime(service)
        receipt.record("restricted-runtime-schema-authority", all(not row["can_create"] for row in privileges), privileges,
                       "identity/data work as DML role without schema CREATE or object ownership")
        service.start_node("A")
        service.start_node("B")
        owners = {username: client(service, rpc, username, node="B" if index % 2 else "A")
                  for index, username in enumerate(model.accounts)}
        alice = owners["alice"]
        begin_window(service, receipt, "api-functional")
        bearer, identity = issue_token(alice, receipt, operations=operations, label="red-all-operations")
        compare("public-declared-read", alice.api("public_value", None), "Public declared read")
        public_mutation = alice.api("create", None, [{"workspace": nominal("workspace", "red"), "project": nominal("project", "project-red-visible"), "rank": "1", "title": "anonymous write"}])
        receipt.record("public-cannot-mutate", public_mutation[0] == 401, {"status": public_mutation[0], "value": public_mutation[1]}, "missing bearer cannot invoke protected mutation")
        compare("scope-authorized-get", alice.api("read", bearer, [nominal("issue", "issue-red-one")], node="B"), issue_wire(ISSUES[0]))
        reader_bearer, _ = issue_token(owners["carol"], receipt, operations=["read", "edit"], label="reader-policy-attenuation")
        compare("reader-current-policy-read", owners["carol"].api("read", reader_bearer, [nominal("issue", "issue-red-one")], node="B"), issue_wire(ISSUES[0]))
        reader_write = owners["carol"].api("edit", reader_bearer, [nominal("issue", "issue-red-one"), {"rank": "77"}])
        receipt.record("operation-allowlist-cannot-expand-reader-role", reader_write[0] == 403 and rank("issue-red-one") == "11", {"status": reader_write[0], "value": reader_write[1], "rank": rank("issue-red-one")}, "credential permits operation/scope but cannot expand owner's current Reader resource policy, even for a provisioned account administrator")
        compare("scope-denied-get", alice.api("read", bearer, [nominal("issue", "issue-blue-one")]), "denied", code=403)
        compare("scope-before-aggregate", alice.api("count", bearer), "2")
        compare("explicit-unscoped-operation-grant", alice.api("global_count_allowed", bearer), "1")
        compare("scope-before-page", alice.api("page", bearer, [nominal("workspace", "red"), ""]), [issue_wire(row) for row in ISSUES[:2]])
        compare("scope-excludes-filtered-other-workspace", alice.api("page", bearer, [nominal("workspace", "blue"), ""], node="B"), [])
        compare("right-base-policy-before-join", alice.api("joined", bearer, [nominal("workspace", "red")]),
                [{"left": issue_wire(ISSUES[0]), "right": project_wire(PROJECTS[0])}])
        compare("scope-before-join", alice.api("joined", bearer, [nominal("workspace", "blue")], node="B"), [])
        move_before = service.sql_json(f"SELECT * FROM {table('Issues')} WHERE id='issue-red-one'")
        move = alice.api("move", bearer, [nominal("issue", "issue-red-one"), {"workspace": nominal("workspace", "blue"), "project": nominal("project", "project-blue-visible")}])
        receipt.record("mutation-poststate-scope-denial", move[0] == 403 and service.sql_json(f"SELECT * FROM {table('Issues')} WHERE id='issue-red-one'") == move_before,
                       {"status": move[0], "value": move[1]}, "owner has blue membership but red token cannot move record outside its fixed scope")
        before = rank("issue-blue-one")
        edit_blue = alice.api("edit", bearer, [nominal("issue", "issue-blue-one"), {"rank": "22"}], node="B")
        receipt.record("mutation-prestate-scope-denial", edit_blue[0] == 403 and rank("issue-blue-one") == before,
                       {"status": edit_blue[0], "value": edit_blue[1]}, "no write to an owner-readable row outside token scope")
        for operation in ["edit", "write_then_hidden"]:
            before = rank("issue-red-one")
            arguments = [nominal("issue", "issue-red-one"), {"rank": "1001"}] if operation == "edit" else [nominal("issue", "issue-red-one")]
            outcome = alice.api(operation, bearer, arguments)
            receipt.record("read-return-rollback-" + operation, outcome[0] == 403 and rank("issue-red-one") == before,
                           {"status": outcome[0], "value": outcome[1], "committed_rank": rank("issue-red-one")},
                           "hidden return-read denies and all writes in the serializable operation roll back")
        edited = list(ISSUES[0]); edited[3] = 44
        compare("permitted-mutation-readback", alice.api("edit", bearer, [nominal("issue", "issue-red-one"), {"rank": "44"}], node="B"), issue_wire(edited))
        compare("fresh-node-mutation-readback", alice.api("read", bearer, [nominal("issue", "issue-red-one")]), issue_wire(edited))
        # Restore only ordinary synthetic fixture values before model comparisons.
        service.sql(f"UPDATE {table('Issues')} SET rank=11 WHERE id='issue-red-one'")
        new_value = {"workspace": nominal("workspace", "red"), "project": nominal("project", "project-red-visible"), "rank": "73", "title": "Created via external API 雪"}
        created = alice.api("create", bearer, [new_value])
        result = created[1].get("value") if isinstance(created[1], dict) else None
        okay = created[0] == 200 and isinstance(result, dict) and result.get("id", {}).get("tag") == "issue" and {key: value for key, value in result.items() if key != "id"} == new_value
        receipt.record("external-api-create", okay, {"status": created[0], "value": result}, "server-generated ID and exact typed committed record")
        if okay:
            compare("external-api-created-fresh-read", alice.api("read", bearer, [result["id"]], node="B"), result)
            compare("external-api-delete", alice.api("delete", bearer, [result["id"]]), "Deleted")
            persisted = service.sql(f"SELECT count(*) FROM {table('Issues')} WHERE id=" + quote(result["id"]["value"]))
            receipt.record("external-api-delete-committed", persisted == "0", persisted, "deleted row absent in independent SQL")
        status, listed = alice.account("list_tokens", {}, node="B")
        metadata = json.loads(listed["value"]) if status == 200 and isinstance(listed, dict) and isinstance(listed.get("value"), str) else None
        listed_text = json.dumps(listed)
        receipt.record("credential-list-metadata-only", status == 200 and isinstance(metadata, list) and any(item.get("id") == identity for item in metadata)
                       and bearer not in listed_text and "dia_" not in listed_text, {"status": status, "metadata": metadata}, "credential IDs/scopes/expiry may be listed; raw secret cannot be read back")
        other_revoke = owners["bob"].account("revoke_token", {"id": identity}, node="B")
        owner_still_valid = alice.api("read", bearer, [nominal("issue", "issue-red-one")])
        receipt.record("other-user-cannot-revoke-credential", other_revoke[0] in [400, 403] and owner_still_valid[0] == 200,
                       {"other_user": other_revoke, "owner_after_status": owner_still_valid[0]}, "credential management limited to its owner")

        begin_window(service, receipt, "token-management")
        credential_snapshot = service.sql_json("SELECT id,username,epoch,label,operations,scopes,revoked FROM diamond_identity.credentials WHERE app=" + quote(app_id()) + " ORDER BY id")
        for name, replacement in [
            ("empty-operation-allowlist", {"operations": []}),
            ("undeclared-operation", {"operations": ["hidden"]}),
            ("forged-scope-tag", {"scopes": {"administrator": ["alice"]}}),
            ("invalid-scope-id", {"scopes": {"workspace": ["red/../blue"]}}),
            ("numeric-lifetime-coercion", {"lifetime_seconds": 3600}),
            ("negative-lifetime", {"lifetime_seconds": "-1"}),
            ("unknown-credential-field", {"principal": "alice"}),
        ]:
            body = {"label": "Malformed issue", "operations": ["read"], "scopes": {"workspace": ["red"]}, "lifetime_seconds": "3600", **replacement}
            outcome = alice.account("create_token", body)
            expected_status = 403 if name == "undeclared-operation" else 400
            receipt.record("token-issuance-" + name, outcome[0] == expected_status, {"status": outcome[0], "value": outcome[1]}, "undeclared operation is an authority denial; malformed credential fields are shape errors; neither issues a credential")
        credential_after_invalid = service.sql_json("SELECT id,username,epoch,label,operations,scopes,revoked FROM diamond_identity.credentials WHERE app=" + quote(app_id()) + " ORDER BY id")
        receipt.record("malformed-credential-issuance-commits-no-changes", credential_after_invalid == credential_snapshot, {"rows_before": len(credential_snapshot), "rows_after": len(credential_after_invalid), "state_equal": credential_after_invalid == credential_snapshot}, "all rejected issuance shapes leave independent committed credential metadata unchanged")
        to_revoke, revoke_id = issue_token(alice, receipt, operations=["read"], label="explicit-revoke")
        revoked = alice.account("revoke_token", {"id": revoke_id}, node="B")
        revocation_reads = [alice.api("read", to_revoke, [nominal("issue", "issue-red-one")], node=node) for node in ["A", "B"]]
        receipt.record("explicit-token-revocation-on-both-nodes", revoked[0] == 200 and all(code == 401 for code, _ in revocation_reads),
                       {"revoke": revoked, "new_operations": revocation_reads}, "owner revocation is shared by new operations on either node")
        foreign = write(root / "foreign", app="IndependentForeignAPI")
        service.cli("project-init", foreign, "--postgres-env", ENV_NAME, configuration=service.owner)
        service.ports["C"] = free_port(); service.start_node("C", project=foreign)
        wrong_app = alice.api("read", bearer, [nominal("issue", "issue-red-one")], node="C")
        receipt.record("api-credential-application-binding", wrong_app[0] == 401, {"status": wrong_app[0], "value": wrong_app[1]}, "credential from another app cannot authenticate even with identical operation/resource declarations")
        service.stop_node("C")
        begin_window(service, receipt, "strict-wire")
        for index, value in enumerate(valid_codecs()):
            compare("valid-codec-" + str(index), alice.api("codec", bearer, [value], node="A" if index % 2 else "B"), value)
        for name, value in invalid_codecs():
            outcome = alice.api("codec", bearer, [value])
            receipt.record("strict-codec-" + name, outcome[0] == 400, {"status": outcome[0], "value": outcome[1]}, "strict declared wire type rejects without coercion or authority")
        bad_envelopes = [
            ("unknown-envelope-field", {"arguments": [nominal("issue", "issue-red-one")], "principal": {"user": "alice", "role": "Admin"}}),
            ("missing-arguments", {}), ("wrong-arguments-shape", {"arguments": {"id": "issue-red-one"}}),
            ("wrong-arity", {"arguments": []}),
        ]
        for name, body in bad_envelopes:
            outcome = alice.api("read", bearer, envelope=body)
            receipt.record(name, outcome[0] == 400, {"status": outcome[0], "value": outcome[1]}, "strict envelope rejects malformed/extra fields")
        for name, header in [("wrong-version", {"X-Diamond-API-Version": "2"}),
                             ("origin-expansion", {"Origin": "https://attacker.invalid"}),
                             ("forwarded-authority", {"X-Forwarded-User": "alice", "X-Forwarded-Host": "attacker.invalid"})]:
            outcome = alice.api("read", bearer if name != "forwarded-authority" else None, [nominal("issue", "issue-blue-one")], headers=header)
            receipt.record(name, outcome[0] in ([400] if name == "wrong-version" else [400, 401, 403]),
                           {"status": outcome[0], "value": outcome[1]}, "version/origin/forwarded data cannot expand identity or scope")
        for name, secret in [("random-bearer", "dia_" + "0" * 64), ("wrong-prefix", bearer.replace("dia_", "DIA_")), ("username-is-not-bearer", "alice")]:
            outcome = alice.api("read", secret, [nominal("issue", "issue-red-one")])
            receipt.record(name, outcome[0] == 401, {"status": outcome[0], "value": outcome[1]}, "only actual opaque current credentials authenticate")
        cookie_only = alice.api("read", None, [nominal("issue", "issue-red-one")], headers={"Cookie": alice.cookie})
        mixed = alice.api("read", bearer, [nominal("issue", "issue-red-one")], headers={"Cookie": alice.cookie})
        receipt.record("api-cookie-profile-rejected", cookie_only[0] in [400, 401, 403] and mixed[0] in [400, 401, 403],
                       {"cookie_only": cookie_only, "mixed": mixed}, "cookie and mixed profiles cannot authenticate the bearer API")
        anonymous = client(service, rpc)
        rpc_bearer = anonymous.request("B", "/_diamond/rpc/read", {"contract": rpc, "arguments": [nominal("issue", "issue-red-one")]}, {"Authorization": "Bearer " + bearer})
        receipt.record("api-bearer-not-cookie-rpc", rpc_bearer[0] == 403, {"status": rpc_bearer[0], "value": rpc_bearer[1]}, "valid API credential does not replace cookie/RPC session")
        for path in ["/api/v1/hidden", "/api/v1/missing", "/api/v2/read"]:
            status, _, raw = service.request("A", path, {"arguments": []}, {"X-Diamond-API-Version": "1", "Authorization": "Bearer " + bearer})
            receipt.record("no-reflection-" + path, status == 404, {"status": status, "response": raw.decode()}, "undeclared helpers and unsupported route versions are not reflected")
        for method in ["GET", "PUT", "DELETE", "OPTIONS"]:
            status, headers, raw = service.request("A", "/api/v1/read", None, {"X-Diamond-API-Version": "1", "Authorization": "Bearer " + bearer}, method=method)
            receipt.record("strict-method-" + method, status == 405 and "access-control-allow-origin" not in headers,
                           {"status": status, "headers": headers, "response": raw.decode()}, "API operation is explicit POST, no automatic credentialed CORS")
        raw = b'{"arguments":[],"arguments":[{"tag":"issue","value":"issue-red-one"}]}'
        status, _, value = service.request("B", "/api/v1/read", raw, {"X-Diamond-API-Version": "1", "Authorization": "Bearer " + bearer})
        receipt.record("duplicate-envelope-field", status == 400, {"status": status, "response": value.decode()}, "duplicate JSON fields reject before protected dispatch")

        begin_window(service, receipt, "generated-attenuation")
        rng = random.Random(SEED)
        dimensions = {"owners": set(), "scopes": set(), "scope_sets": set(), "operation_sets": set(), "membership_changes": 0, "generated_cases": []}
        for index in range(24):
            username = ["alice", "bob", "carol", "outsider"][index % 4]
            scopes = frozenset(rng.sample(["red", "blue", "private"], rng.randint(1, 3)))
            allowed_operations = frozenset(rng.sample(["read", "count", "joined"], rng.randint(1, 3)))
            token, token_id = issue_token(owners[username], receipt, operations=sorted(allowed_operations), scopes={"workspace": sorted(scopes)}, label="generated-" + str(index), node="A" if index % 2 else "B")
            credential = Credential(username, allowed_operations, scopes, 0, 1000)
            target = ISSUES[index % len(ISSUES)]
            expected_allowed = model.api_allowed(credential, "read", target[1], now=1)
            actual = owners[username].api("read", token, [nominal("issue", target[0])], node="A" if index % 2 else "B")
            compare("generated-get-" + str(index), actual, issue_wire(target) if expected_allowed else "denied", code=200 if expected_allowed else 403)
            expected_count = sum(model.api_allowed(credential, "count", row[1], now=1) for row in ISSUES)
            actual = owners[username].api("count", token, node="B")
            compare("generated-count-" + str(index), actual, str(expected_count) if "count" in allowed_operations else "denied", code=200 if "count" in allowed_operations else 403)
            dimensions["owners"].add(username); dimensions["scopes"].update(scopes); dimensions["scope_sets"].add(tuple(sorted(scopes))); dimensions["operation_sets"].add(tuple(sorted(allowed_operations)))
            dimensions["generated_cases"].append({"index": index, "owner": username, "scopes": sorted(scopes), "operations": sorted(allowed_operations), "target_issue": target[0], "get_expected_allowed": expected_allowed, "count_expected": expected_count if "count" in allowed_operations else None})
        # Current membership, never token issuance authority, determines access.
        before_role = service.sql_json(f"SELECT * FROM {table('Memberships')} WHERE \"user\"='alice' AND workspace='red'")
        service.sql(f"DELETE FROM {table('Memberships')} WHERE \"user\"='alice' AND workspace='red'")
        for node in ["A", "B"]:
            denied = alice.api("read", bearer, [nominal("issue", "issue-red-one")], node=node)
            compare("new-operation-after-role-revocation-" + node, denied, "denied", code=403)
        dimensions["membership_changes"] += 1
        service.sql(f"INSERT INTO {table('Memberships')}(id,workspace,\"user\",role) VALUES ('member-alice-red','red','alice','Editor')")
        compare("role-restore-does-not-expand-fixed-blue-scope", alice.api("read", bearer, [nominal("issue", "issue-blue-one")]), "denied", code=403)
        # Credential expiry uses actual PG time, not Python's wall clock.
        service.sql("UPDATE diamond_identity.credentials SET expires=clock_timestamp()-interval '1 second' WHERE app=" + quote(app_id()) + " AND id=" + quote(identity))
        for node in ["A", "B"]:
            expired = alice.api("read", bearer, [nominal("issue", "issue-red-one")], node=node)
            receipt.record("database-expired-api-" + node, expired[0] == 401, {"status": expired[0], "value": expired[1]}, "database-time expiry denies current API credential on either node")
        native_scopes = service.sql_json("SELECT id,username,operations,scopes,revoked,digest<>" + quote(bearer) + " AS not_plaintext FROM diamond_identity.credentials WHERE app=" + quote(app_id()) + " AND id=" + quote(identity))
        receipt.record("api-digest-and-fixed-scopes-persist", len(native_scopes) == 1 and native_scopes[0]["not_plaintext"], native_scopes,
                       "committed digest metadata, fixed operation allowlist and nominal scope IDs")
        public_files = [path for path in build.rglob('*') if path.is_file() and path.suffix in ['.js','.html','.css','.map']]
        leaked = [str(path.relative_to(build)) for path in public_files if b'W5_SERVER_ONLY_CANARY_9cb3e0a4' in path.read_bytes()]
        receipt.record("server-source-canary-excluded-from-public-build", bool(public_files) and not leaked, {"public_artifacts": [str(path.relative_to(build)) for path in public_files], "leaked": leaked},
                       "server-only helper/canary text is absent from browser-facing artifacts")
        dimensions = {key: sorted(value) if isinstance(value, set) else value for key, value in dimensions.items()}
    except Exception as failure:
        fatal = repr(failure)
        dimensions = None
    finally:
        try:
            service.close()
        except Exception as cleanup:
            fatal = (fatal or "") + " cleanup=" + repr(cleanup)
        report = receipt.report(service, sha256(binary), error=fatal, seed=SEED, dimensions=dimensions,
                                temporary_root=str(root),
                                scope="actual scoped HTTP API and strict codecs; generated operation/scope/role attenuation against independent fixture; no public TLS/capacity claim")
        options.report.write_text(json.dumps(report, indent=2, ensure_ascii=True) + "\n")
        if report["status"] == "PASS":
            shutil.rmtree(root)
    print(json.dumps({"status": report["status"], "observations": len(receipt.results), "error": report["error"]}))
    return report["status"] != "PASS"


if __name__ == "__main__":
    raise SystemExit(main())
