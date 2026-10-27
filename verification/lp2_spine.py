#!/usr/bin/env python3
"""Run LP2 through the real compiler, HTTP servers, PostgreSQL, and browser fixture.

Expected rows/outcomes are stated independently below. The optional proxy only
forwards real responses and can delay/drop one completed action response; it
implements no Diamond page, business logic, validation, or authorization.
"""
from __future__ import annotations

import argparse
import copy
from concurrent.futures import ThreadPoolExecutor
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import random
import shutil
import subprocess
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "verification/w5_independent"))
from service import Client, ENV_NAME, Service, free_port, sha256

PASSWORD = "LP2 synthetic fixture password 61!"
APP = hashlib.sha256(b"LanguageSpine").hexdigest()
SEED = 0xD1A20002


def table(resource):
    return 'diamond_w4."r_' + APP[:16] + '_' + hashlib.sha256(resource.encode()).hexdigest()[:16] + '"'


def literal(text):
    return "'" + text.replace("'", "''") + "'"


def nominal(tag, value):
    return {"tag": tag, "value": value}


def rows(service):
    return service.sql_json(f'SELECT id, workspace, convert_from(decode(title,\'hex\'),\'UTF8\') AS title, revision FROM {table("Issues")} ORDER BY id')


def seed(service):
    service.sql(f'DELETE FROM {table("Issues")}; DELETE FROM {table("Members")};')
    service.sql(f'INSERT INTO {table("Members")}(id,workspace,"user",role) VALUES '
                "('member-alice','alpha','alice','Editor'),('member-bob','beta','bob','Editor'),('member-reader','alpha','reader','Reader')")
    for key, workspace, title in [("alpha-a", "alpha", "Old A"), ("alpha-b", "alpha", "Old B"), ("beta-c", "beta", "Private C")]:
        service.sql(f'INSERT INTO {table("Issues")}(id,workspace,title,revision) VALUES ({literal(key)},{literal(workspace)},{literal(title.encode().hex())},0)')


def restrict(service):
    service.sql("CREATE ROLE lp2_runtime LOGIN")
    for schema in service.sql("SELECT nspname FROM pg_namespace WHERE nspname LIKE 'diamond%' ORDER BY nspname").splitlines():
        service.sql(f'GRANT USAGE ON SCHEMA "{schema}" TO lp2_runtime')
        for relation in service.sql("SELECT tablename FROM pg_tables WHERE schemaname=" + literal(schema)).splitlines():
            dml = (schema == "diamond_w4" and relation.startswith("r_")) or schema == "diamond_identity" and relation in ["users", "sessions", "challenges", "credentials", "rates"] or schema == "diamond_actions" and relation == "tickets"
            service.sql(f'GRANT {"SELECT,INSERT,UPDATE,DELETE" if dml else "SELECT"} ON TABLE "{schema}"."{relation}" TO lp2_runtime')
    service.configuration = service.owner.replace("user=diamond_w5_owner", "user=lp2_runtime")


class Gate:
    def __init__(self, port, backend):
        self.lock = threading.Lock()
        self.release = threading.Event()
        self.armed = False
        self.drop = False
        self.held = None
        self.backend = backend
        self.actions_seen = 0
        gate = self

        class Forward(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def forward(self):
                length = int(self.headers.get("Content-Length", "0"))
                if length > 20000:
                    self.close_connection = True
                    return
                body = self.rfile.read(length)
                connection = http.client.HTTPConnection("127.0.0.1", gate.backend, timeout=20)
                try:
                    connection.request(self.command, self.path, body, {key: value for key, value in self.headers.items() if key.lower() not in ["connection", "transfer-encoding"]})
                    response = connection.getresponse()
                    data, headers = response.read(), response.getheaders()
                    if response.status == 400 and self.command == "GET":
                        names = ["host", "origin", "sec-fetch-site", "sec-fetch-mode", "sec-fetch-dest", "content-length", "transfer-encoding", "forwarded", "x-forwarded-for", "x-forwarded-proto"]
                        print(json.dumps({"rejected_request_headers": {name: self.headers.get_all(name) for name in names if self.headers.get_all(name)}}), flush=True)
                    with gate.lock:
                        if self.path == "/_diamond/actions/rename": gate.actions_seen += 1
                        hold = gate.armed and self.path == "/_diamond/actions/rename"
                        if hold:
                            gate.armed = False
                            gate.held = {"path": self.path, "actual_status": response.status, "actual_bytes": len(data)}
                    if hold:
                        gate.release.wait(45)
                        if gate.drop:
                            self.close_connection = True
                            return
                    self.send_response(response.status)
                    for key, value in headers:
                        if key.lower() not in ["content-length", "connection", "transfer-encoding", "server", "date"]:
                            self.send_header(key, value)
                    self.send_header("Content-Length", response.getheader("Content-Length", "0") if self.command == "HEAD" else str(len(data)))
                    self.end_headers()
                    if self.command != "HEAD":
                        self.wfile.write(data)
                except (OSError, http.client.HTTPException):
                    self.close_connection = True
                finally:
                    connection.close()

            do_GET = forward
            do_POST = forward
            do_HEAD = forward

        self.server = ThreadingHTTPServer(("127.0.0.1", port), Forward)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def arm(self):
        with self.lock:
            self.armed, self.drop, self.held = True, False, None
            self.release.clear()

    def close(self):
        self.release.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def action(client, key, title, *, node="A", extra=None):
    body = {"contract": client.contract, "arguments": [nominal("issue", key), {"title": title}], **(extra or {})}
    status, prepared = client.request(node, "/_diamond/actions/rename/prepare", body)
    if status != 200: return status, prepared
    body["ticket"] = prepared["ticket"]
    return client.request(node, "/_diamond/actions/rename", body)


def backend(service, contract, record):
    clients = {}
    for user in ("alice", "bob", "reader"):
        client = Client(service, contract)
        client.login(user, PASSWORD)
        clients[user] = client
    alice = clients["alice"]
    expected = [{"id": "alpha-a", "workspace": "alpha", "title": "Old A", "revision": 0}, {"id": "alpha-b", "workspace": "alpha", "title": "Old B", "revision": 0}, {"id": "beta-c", "workspace": "beta", "title": "Private C", "revision": 0}]
    record("seed", rows(service) == expected, rows(service), expected)
    status, value = action(alice, "alpha-b", "New", node="B")
    record("authorized-action", status == 200 and value["value"]["tag"] == "ActionSucceeded" and value["value"]["fields"][0]["title"] == "New", [status, value], "typed ActionSucceeded(New)")
    expected[1]["title"] = "New"
    expected[1]["revision"] = 1
    record("independent-saved-rows", rows(service) == expected, rows(service), expected)
    for user in ("bob", "reader"):
        status, value = action(clients[user], "alpha-b", "Unauthorized")
        record(user + "-denied", status == 200 and value["value"]["tag"] == "ActionDenied" and rows(service) == expected, [status, value], "ActionDenied; all rows unchanged")
    anonymous = Client(service, contract)
    anonymous.bootstrap()
    outcome = action(anonymous, "alpha-b", "Anonymous")
    record("anonymous-denied", outcome[0] in (401, 403) and rows(service) == expected, outcome, "authentication/policy rejection; no write")
    status, value = action(alice, "alpha-b", "reject")
    record("declared-error-rolls-back", status == 200 and value["value"]["tag"] == "ActionRejected" and rows(service) == expected, [status, value, rows(service)], "declared rejection after attempted write; saved title remains New")
    for label, arguments in [
        ("wrong-nominal", [nominal("user", "alpha-b"), {"title": "Wrong"}]),
        ("extra-field", [nominal("issue", "alpha-b"), {"title": "Wrong", "workspace": nominal("workspace", "beta")}]),
        ("empty-title", [nominal("issue", "alpha-b"), {"title": ""}]),
        ("overlong-scalars", [nominal("issue", "alpha-b"), {"title": "😀" * 121}]),
    ]:
        outcome = alice.request("A", "/_diamond/actions/rename", {"contract": contract, "arguments": arguments})
        record(label, outcome[0] == 400 and rows(service) == expected, outcome, "strict authoritative rejection; no write")
    for label, path, body in [
        ("raw-rpc-bypass", "/_diamond/rpc/rename", {"contract": contract, "arguments": [nominal("issue", "alpha-b"), {"title": "Wrong"}]}),
        ("wrong-contract", "/_diamond/actions/rename", {"contract": "0" * 64, "arguments": [nominal("issue", "alpha-b"), {"title": "Wrong"}]}),
        ("unsupported-replay", "/_diamond/actions/rename", {"contract": contract, "arguments": [nominal("issue", "alpha-b"), {"title": "Wrong"}], "replay": "durable"}),
    ]:
        outcome = alice.request("A", path, body)
        record(label, outcome[0] == {"raw-rpc-bypass": 404, "wrong-contract": 409, "unsupported-replay": 400}[label] and rows(service) == expected, outcome, "explicit rejection; no write")
    fresh = Client(service, contract)
    fresh.login("alice", PASSWORD, node="B")
    outcome = fresh.rpc("load", nominal("issue", "alpha-b"), node="B")
    record("fresh-client-readback", outcome[0] == 200 and outcome[1]["value"]["title"] == "New", outcome, "New through a fresh client and second application server")
    outcome = clients["reader"].rpc("load", nominal("issue", "alpha-b"))
    record("reader-can-read", outcome[0] == 200 and outcome[1]["value"]["title"] == "New", outcome, "reader may read while writes stay denied")
    for path in ("/", "/issues/alpha-b"):
        status, _, data = service.request("A", path, headers={"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document"})
        record("external-document-navigation-" + path, status == 200 and data.startswith(b"<!doctype html>"), status, "public document navigation works")
    status, _, _ = service.request("A", "/", headers={"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "no-cors", "Sec-Fetch-Dest": "script"})
    record("cross-site-subresource-denied", status == 400, status, "cross-site subresource remains denied")
    outcome = alice.request("A", "/_diamond/actions/rename", {"contract": contract, "arguments": [nominal("issue", "alpha-b"), {"title": "Cross site"}]}, {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "cors", "Sec-Fetch-Dest": "empty"})
    record("cross-site-mutation-denied", outcome[0] == 400 and rows(service) == expected, outcome, "cross-site action denied even with valid cookie/CSRF; no write")
    outcome = alice.account("create_token", {"label": "LP2 disposable scoped API", "operations": ["rename", "load"], "scopes": {"workspace": ["alpha"]}, "lifetime_seconds": "3600"})
    if outcome[0] != 200: raise AssertionError(("credential setup", outcome[0], sorted(outcome[1])))
    bearer = outcome[1]["value"]
    outcome = api_action(alice, bearer, "alpha-b", "New", node="B")
    expected[1]["revision"] += 1
    record("api-shares-action-outcome", outcome[0] == 200 and outcome[1]["value"]["tag"] == "ActionSucceeded", outcome, "same typed action dispatcher via attenuated bearer API")
    outcome = api_action(alice, bearer, "beta-c", "Wrong scope")
    record("api-scope-denied", outcome[0] == 200 and outcome[1]["value"]["tag"] == "ActionDenied" and rows(service) == expected, outcome, "attenuated token cannot write another workspace")
    rng = random.Random(SEED)
    for index in range(12):
        title = "Case-" + str(index) + ":" + "".join(rng.choice(["a", "Z", "雪", "😀", "é", "<", "&"]) for _ in range(9))
        outcome = action(alice, "alpha-b", title, node="A" if index % 2 else "B")
        expected[1]["title"] = title
        expected[1]["revision"] += 1
        record(f"generated-valid-title-{index}", outcome[0] == 200 and outcome[1]["value"]["tag"] == "ActionSucceeded" and rows(service) == expected, [outcome[0], rows(service)], expected)
    for path, wanted in [("/issues/alpha-b", 200), ("/issues/alpha-b?extra=1", 400), ("/issues/a%2fb", 400), ("/issues/" + "a" * 129, 404)]:
        status, _, data = service.request("A", path)
        okay = status == wanted and (status != 200 or b'id="route-id">alpha-b</p>' in data)
        record("direct-route-" + path, okay, status, wanted)
    return clients


def api_action(client, bearer, key, title, *, node="A"):
    arguments = [nominal("issue", key), {"title": title}]
    status, prepared = client.api("rename/prepare", bearer, arguments, node=node)
    if status != 200: return status, prepared
    return client.api("rename", bearer, node=node, envelope={"arguments": arguments, "ticket": prepared["ticket"]})


def proof_campaign(service, contract, clients, record):
    alice, bob = clients["alice"], clients["bob"]
    rng = random.Random(SEED)
    last = ""
    for index in range(12):
        last = "Case-" + str(index) + ":" + "".join(rng.choice(["a", "Z", "雪", "😀", "é", "<", "&"]) for _ in range(9))
    expected = [{"id": "alpha-a", "workspace": "alpha", "title": "Old A", "revision": 0}, {"id": "alpha-b", "workspace": "alpha", "title": last, "revision": 14}, {"id": "beta-c", "workspace": "beta", "title": "Private C", "revision": 0}]
    record("proof-campaign-start", rows(service) == expected, rows(service), expected)
    service.sql("DELETE FROM diamond_identity.rates WHERE app=" + literal(APP))

    def body(title, key="alpha-a"):
        return {"contract": contract, "arguments": [nominal("issue", key), {"title": title}]}

    def prepare(title, client=alice):
        status, value = client.request("A", "/_diamond/actions/rename/prepare", body(title))
        if status != 200: raise AssertionError(("proof setup", status, sorted(value)))
        return {**body(title), "ticket": value["ticket"]}

    def execute(request, client=alice, node="A"):
        return client.request(node, "/_diamond/actions/rename", request)

    def tag(outcome):
        return outcome[1].get("value", {}).get("tag") if outcome[0] == 200 else str(outcome[0])

    def changed(title):
        expected[0]["title"] = title
        expected[0]["revision"] += 1

    request = prepare("One attempt")
    outcome = execute(request)
    changed("One attempt")
    record("single-proof-first-attempt", tag(outcome) == "ActionSucceeded" and rows(service) == expected, [outcome, rows(service)], expected)
    outcome = execute(request, node="B")
    record("single-proof-reuse-other-node", tag(outcome) == "ActionUnknown" and rows(service) == expected, [outcome, rows(service)], "one saved revision; no recovered result or second execution")

    request = prepare("Concurrent once")
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda node: execute(request, node=node), ("A", "B")))
    changed("Concurrent once")
    record("concurrent-proof-two-servers", sorted(map(tag, outcomes)) == ["ActionSucceeded", "ActionUnknown"] and rows(service) == expected, [list(map(tag, outcomes)), rows(service)], "exactly one revision increment across two real servers")

    request = prepare("Bound input")
    altered = {**request, "arguments": [nominal("issue", "alpha-b"), {"title": "Changed target"}]}
    outcome = execute(altered)
    record("proof-binds-canonical-input-and-id", tag(outcome) == "ActionUnknown" and rows(service) == expected, [outcome, rows(service)], "changed payload cannot execute")
    outcome = execute(request)
    changed("Bound input")
    record("original-bound-input-still-usable", tag(outcome) == "ActionSucceeded" and rows(service) == expected, [outcome, rows(service)], expected)

    for label, other in [("different-account", bob), ("different-session-same-account", Client(service, contract))]:
        if label == "different-session-same-account": other.login("alice", PASSWORD, node="B")
        request = prepare("Subject bound " + label)
        outcome = execute(request, other, "B")
        record("proof-" + label, tag(outcome) == "ActionUnknown" and rows(service) == expected, [outcome, rows(service)], "proof is not transferable between current identities/sessions")
        outcome = execute(request)
        changed("Subject bound " + label)
        record("subject-owner-" + label, tag(outcome) == "ActionSucceeded" and rows(service) == expected, [outcome, rows(service)], expected)

    request = prepare("Expired submission")
    digest = hashlib.sha256(request["ticket"].encode()).hexdigest()
    service.sql("UPDATE diamond_actions.tickets SET expires=pg_catalog.clock_timestamp()-interval '1 second' WHERE app=" + literal(APP) + " AND digest=" + literal(digest))
    outcome = execute(request)
    record("expired-proof-never-becomes-fresh", tag(outcome) == "ActionUnknown" and rows(service) == expected, [outcome, rows(service)], "expired identity cannot execute")

    request = prepare("Revoked member")
    service.sql(f"DELETE FROM {table('Members')} WHERE id='member-alice'")
    outcome = execute(request, node="B")
    record("revocation-after-preparation", tag(outcome) == "ActionDenied" and rows(service) == expected, [outcome, rows(service)], "fresh resource policy denies after preparation; no write")
    service.sql(f"INSERT INTO {table('Members')}(id,workspace,\"user\",role) VALUES('member-alice','alpha','alice','Editor')")

    status, credential = alice.account("create_token", {"label": "LP2 proof scope", "operations": ["rename", "load"], "scopes": {"workspace": ["alpha"]}, "lifetime_seconds": "3600"})
    if status != 200: raise AssertionError(("scoped proof credential setup", status))
    bearer, identity = credential["value"], credential["id"]
    arguments = body("API proof once")["arguments"]
    status, prepared = alice.api("rename/prepare", bearer, arguments)
    if status != 200: raise AssertionError(("API proof setup", status))
    envelope = {"arguments": arguments, "ticket": prepared["ticket"]}
    outcome = alice.api("rename", bearer, envelope=envelope, node="B")
    changed("API proof once")
    record("api-proof-first-attempt", tag(outcome) == "ActionSucceeded" and outcome[1].get("api_version") == 1 and rows(service) == expected, [outcome, rows(service)], expected)
    outcome = alice.api("rename", bearer, envelope=envelope)
    record("api-proof-duplicate-envelope", tag(outcome) == "ActionUnknown" and outcome[1].get("api_version") == 1 and rows(service) == expected, [outcome, rows(service)], "versioned API envelope; no second revision")

    request = prepare("Cookie realm")
    outcome = alice.api("rename", bearer, envelope={"arguments": request["arguments"], "ticket": request["ticket"]})
    record("cookie-proof-not-api-proof", tag(outcome) == "ActionUnknown" and rows(service) == expected, [outcome, rows(service)], "cookie/API realms and subjects remain distinct")
    outcome = execute(request)
    changed("Cookie realm")
    record("cookie-realm-owner-can-execute", tag(outcome) == "ActionSucceeded" and rows(service) == expected, [outcome, rows(service)], expected)

    arguments = body("Revoked credential")["arguments"]
    status, prepared = alice.api("rename/prepare", bearer, arguments)
    if status != 200: raise AssertionError(("revoked credential proof setup", status))
    revoked = alice.account("revoke_token", {"id": identity})
    if revoked[0] != 200: raise AssertionError(("revoke test credential", revoked))
    outcome = alice.api("rename", bearer, envelope={"arguments": arguments, "ticket": prepared["ticket"]}, node="B")
    record("credential-revoked-after-preparation", outcome[0] in (401, 403) and rows(service) == expected, [outcome, rows(service)], "current credential grant rechecked before consuming/executing")

    # Start a distinct quota subcampaign without spending a minute waiting for
    # the unrelated shared request-rate window. All data belongs to this fixture.
    service.sql("DELETE FROM diamond_identity.rates WHERE app=" + literal(APP))
    service.sql("UPDATE diamond_actions.tickets SET expires=pg_catalog.clock_timestamp()-interval '1 second' WHERE app=" + literal(APP) + " AND NOT consumed")
    pending = []
    for index in range(16): pending.append(prepare("Pending " + str(index)))
    status, value = alice.request("B", "/_diamond/actions/rename/prepare", body("Seventeenth"))
    count = service.sql("SELECT count(*) FROM diamond_actions.tickets WHERE app=" + literal(APP) + " AND username='alice' AND NOT consumed AND expires>pg_catalog.clock_timestamp()")
    record("shared-pending-proof-quota", status == 429 and count == "16" and rows(service) == expected, [status, value, count], "sixteen live unused proofs per user across nodes; next preparation rejected")
    outcome = execute(pending[0], node="B")
    changed("Pending 0")
    record("quota-following-valid-execution", tag(outcome) == "ActionSucceeded" and rows(service) == expected, [outcome, rows(service)], expected)
    status, value = alice.request("A", "/_diamond/actions/rename/prepare", body("Slot recovered"))
    record("consumption-releases-pending-slot", status == 200 and "ticket" in value and rows(service) == expected, [status, rows(service)], "a consumed proof releases its pending-user slot")
    service.sql("UPDATE diamond_actions.tickets SET expires=pg_catalog.clock_timestamp()-interval '1 second' WHERE app=" + literal(APP) + " AND NOT consumed")

    # A real independent PostgreSQL transaction holds the row lock. The runtime's
    # lock deadline must abort rather than fabricate success or leak SQL details.
    blocker = subprocess.Popen(["psql", service.owner, "-X", "-q", "-At", "-v", "ON_ERROR_STOP=1"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        blocker.stdin.write(f"BEGIN; UPDATE {table('Issues')} SET revision=revision WHERE id='alpha-a';\n\\echo LP2_LOCKED\n")
        blocker.stdin.flush()
        if blocker.stdout.readline().strip() != "LP2_LOCKED": raise AssertionError("independent blocker failed to acquire row")
        request = prepare("Must abort on lock")
        outcome = execute(request, node="B")
        record("confirmed-lock-timeout-abort", tag(outcome) == "ActionAborted" and rows(service) == expected, [outcome, rows(service)], "confirmed abort; no saved revision and no private SQL text")
    finally:
        blocker.stdin.write("ROLLBACK;\n\\quit\n")
        blocker.stdin.flush()
        blocker.communicate(timeout=10)
    record("owned-blocker-shutdown", blocker.returncode == 0, blocker.returncode, 0)
    request = prepare("Recovery after abort")
    outcome = execute(request)
    changed("Recovery after abort")
    record("valid-action-after-abort", tag(outcome) == "ActionSucceeded" and rows(service) == expected, [outcome, rows(service)], expected)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, default=ROOT / "target/debug/diamond")
    parser.add_argument("--report", type=Path, default=ROOT / "verification/lp2/results.json")
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--language-version", default="0.20.0")
    parser.add_argument("--format-version", type=int, default=5)
    parser.add_argument("--wire-version", type=int, default=7)
    options = parser.parse_args()
    driver_hash = sha256(Path(__file__))
    root = Path(tempfile.mkdtemp(prefix="diamond-lp2-"))
    binary = root / "diamond"
    shutil.copy2(options.binary, binary)
    project = ROOT / "examples/language-spine/diamond.toml"
    service = Service(root, binary, project)
    service.extra_serve = ("--identity-mailbox", str(service.mailbox))
    public_port = 39171 if options.serve else free_port()
    service.origin = f"http://127.0.0.1:{public_port}"
    service.authority = service.origin.removeprefix("http://")
    observations, gate, error = [], None, None

    def record(name, passed, observed, expected):
        observations.append({"name": name, "pass": bool(passed), "observed": copy.deepcopy(observed), "expected": copy.deepcopy(expected)})
        if not passed:
            raise AssertionError((name, observed, expected))

    try:
        service.postgres()
        metadata, build = service.build()
        contract = metadata["rpc_id"]
        expected_versions = [options.language_version, options.format_version, options.wire_version]
        record("versions", [metadata["language_version"], metadata["format_version"], metadata["wire_version"]] == expected_versions, [metadata["language_version"], metadata["format_version"], metadata["wire_version"]], expected_versions)
        service.cli("project-init", project, "--postgres-env", ENV_NAME)
        for user in ("alice", "bob", "reader"):
            service.cli("project-user-add", project, "--postgres-env", ENV_NAME, "--user", user, "--password-stdin", input=PASSWORD + "\n")
        seed(service)
        restrict(service)
        service.start_node("A")
        service.start_node("B")
        gate = Gate(public_port, service.ports["A"])
        clients = backend(service, contract, record)
        proof_campaign(service, contract, clients, record)
        if options.serve:
            seed(service)
            print(json.dumps({"ready": True, "root": str(root), "origin": service.origin, "binary_sha256": sha256(binary), "contract": contract, "fixture_password": PASSWORD}), flush=True)
            for line in sys.stdin:
                command = line.strip()
                if command == "stop": break
                if command == "observe": print(json.dumps({"rows": rows(service), "held": gate.held, "forwarded_actions": gate.actions_seen, "http_requests": len(service.trace)}, ensure_ascii=False), flush=True)
                if command == "arm": gate.arm(); print('{"armed":true}', flush=True)
                if command == "release": gate.release.set(); print('{"released":true}', flush=True)
                if command == "drop": gate.drop = True; gate.release.set(); print('{"dropped":true}', flush=True)
        print(json.dumps({"status": "PASS", "observations": len(observations)}), flush=True)
    except Exception as failure:
        error = repr(failure)
        print(json.dumps({"status": "FAIL", "error": error}), flush=True)
    finally:
        if gate: gate.close()
        service.close()
        report = {"status": "PASS" if error is None and not service.cleanup_errors else "FAIL", "error": error, "binary_sha256": sha256(binary), "driver_sha256": driver_hash, "seed": SEED, "observations": observations, "shutdowns": service.shutdowns, "cleanup_errors": service.cleanup_errors, "source_files": {str(path.relative_to(ROOT)): sha256(path) for path in sorted(project.parent.iterdir()) if path.is_file()}}
        options.report.parent.mkdir(parents=True, exist_ok=True)
        options.report.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
        shutil.rmtree(root)
    return 0 if error is None and not service.cleanup_errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
