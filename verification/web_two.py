#!/usr/bin/env python3
"""Operate two real Diamond servers, PostgreSQL and a disposable loopback proxy.

The proxy supplies routing only: it preserves Host/Origin and forwards raw bodies.
No authentication, CRUD, expected answers, pages or JavaScript are implemented
by this driver. Direct-node cases bypass it. Browser controls are operated
separately against --serve-fixture and recorded with their actual observations.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import random
import shutil
import signal
import socket
import subprocess
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
BINARY = ROOT / "target/debug/diamond"
SEED = 0xD1A08202
PASSWORD = "Synthetic W2 test password 42!"


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Fixture:
    def __init__(self, root: Path, explore=False, public_host="localhost", public_port=None):
        self.root = root
        self.nodes = {}
        self.logs = []
        self.trace = []
        self.pg_started = False
        self.proxy = None
        self.project = root / "project/diamond.toml"
        self.origin = f"http://{public_host}:{public_port or free_port()}"
        self.authority = self.origin.removeprefix("http://")
        self.ports = {"A": free_port(), "B": free_port()}
        shutil.copytree(ROOT / "examples/workspace", self.project.parent)
        if explore:
            self.add_exploration()
        self.env_name = "DIAMOND_W2_TEST_POSTGRES"
        sockets = root / "socket"
        sockets.mkdir(mode=0o700)
        self.data = root / "pgdata"
        self.configuration = f"host={sockets} port=55448 user=diamond_w2 dbname=postgres"

    def cli(self, *args, input=None, granted=True, success=True):
        env = os.environ.copy()
        if granted:
            env[self.env_name] = self.configuration
        p = subprocess.run([str(BINARY), *map(str, args)], input=input, capture_output=True,
                           text=True, env=env, timeout=20)
        assert (p.returncode == 0) == success, (p.args, p.stdout, p.stderr)
        return p

    def start(self):
        subprocess.run(["initdb", "-D", str(self.data), "-A", "trust", "--no-locale", "-E", "UTF8", "-U", "diamond_w2"], check=True, capture_output=True)
        subprocess.run(["pg_ctl", "-D", str(self.data), "-l", str(self.root / "pg.log"), "-o",
                        f'-k {self.root / "socket"} -h "" -p 55448 -c max_connections=24', "-w", "start"],
                       check=True, capture_output=True, timeout=20)
        self.pg_started = True
        self.cli("check", self.project)
        self.cli("project-init", self.project, "--postgres-env", self.env_name)
        self.cli("project-init", self.project, "--postgres-env", self.env_name)
        for user in ["alice", "bob", "rate-user"]:
            self.cli("project-user-add", self.project, "--postgres-env", self.env_name,
                     "--user", user, "--password-stdin", input=PASSWORD + "\n")
        build = self.root / "build"
        self.cli("build", self.project, "--out", build)
        self.contract = json.loads((build / "contract.json").read_text())["rpc_id"]
        self.start_node("A")
        self.start_node("B")
        fixture = self

        class Proxy(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def forward(self):
                forced = self.headers.get("X-Diamond-Test-Node")
                node = forced or ("B" if self.path in ["/_diamond/rpc/create_issue", "/_diamond/logout"] else "A")
                assert node in fixture.ports
                body = self.rfile.read(min(int(self.headers.get("Content-Length", "0")), 20000))
                headers = {k: v for k, v in self.headers.items() if k.lower() not in ["x-diamond-test-node", "connection"]}
                connection = http.client.HTTPConnection("127.0.0.1", fixture.ports[node], timeout=15)
                try:
                    connection.request(self.command, self.path, body=body, headers=headers)
                    result = connection.getresponse()
                    response = result.read()
                    self.send_response(result.status)
                    for key, value in result.getheaders():
                        if key.lower() not in ["connection", "transfer-encoding"]:
                            self.send_header(key, value)
                    self.end_headers()
                    self.wfile.write(response)
                    fixture.trace.append({"node": node, "method": self.command, "path": self.path, "status": result.status})
                finally:
                    connection.close()

            do_GET = do_POST = do_HEAD = do_OPTIONS = forward

        self.proxy = ThreadingHTTPServer(("127.0.0.1", int(self.authority.rsplit(":", 1)[1])), Proxy)
        self.thread = threading.Thread(target=self.proxy.serve_forever, daemon=True)
        self.thread.start()
        (self.root / "state.json").write_text(json.dumps({"root": str(self.root), "origin": self.origin,
            "ports": self.ports, "project": str(self.project), "contract": self.contract,
            "configuration": self.configuration, "node_pids": {k: v.pid for k, v in self.nodes.items()},
            "binary_sha256": hashlib.sha256(BINARY.read_bytes()).hexdigest()}, indent=2) + "\n")

    def start_node(self, node):
        log = (self.root / f"{node}-{len(self.logs)}.log").open("w")
        self.logs.append(log)
        self.nodes[node] = subprocess.Popen([str(BINARY), "serve", str(self.project), "--listen",
            f"127.0.0.1:{self.ports[node]}", "--origin", self.origin, "--postgres-env", self.env_name],
            env=os.environ | {self.env_name: self.configuration}, stdout=log, stderr=log)
        for _ in range(150):
            try:
                status, _, _ = self.request("/", node=node, direct=True)
                if status == 200:
                    return
            except OSError:
                pass
            assert self.nodes[node].poll() is None, Path(log.name).read_text()
            time.sleep(.03)
        raise AssertionError(f"node {node} failed readiness")

    def stop_node(self, node):
        process = self.nodes.pop(node, None)
        if process and process.poll() is None:
            process.send_signal(signal.SIGINT)
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()

    def request(self, path, body=None, headers=None, node=None, direct=False, method=None):
        headers = dict(headers or {})
        headers.setdefault("Host", self.authority)
        if body is not None:
            if not isinstance(body, (str, bytes)):
                body = json.dumps(body, ensure_ascii=False).encode()
            headers.setdefault("Content-Type", "application/json")
            headers.setdefault("Origin", self.origin)
        if node and not direct:
            headers["X-Diamond-Test-Node"] = node
        connection = http.client.HTTPConnection("127.0.0.1", self.ports[node] if direct else self.proxy.server_port, timeout=15)
        try:
            connection.request(method or ("POST" if body is not None else "GET"), path, body, headers)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def sql(self, statement):
        return subprocess.check_output(["psql", self.configuration, "-X", "-v", "ON_ERROR_STOP=1", "-Atc", statement], text=True).strip()

    def close(self):
        if self.proxy:
            self.proxy.shutdown()
            self.proxy.server_close()
        for node in list(self.nodes):
            self.stop_node(node)
        if self.pg_started:
            subprocess.run(["pg_ctl", "-D", str(self.data), "-m", "fast", "-w", "stop"], check=True, capture_output=True, timeout=20)
        for log in self.logs:
            log.close()

    def add_exploration(self):
        # Legal Diamond handlers exercise codec, policy and atomicity boundaries.
        server = self.project.parent / "server.dia"
        with server.open("a") as stream:
            stream.write('''
export fn echo_integer(value: Int) -> Int:
  value

export fn echo_boolean(value: Bool) -> Bool:
  value

export fn caught_failure(title: Text<length: 1..120>) -> Result<Text, DataError>:
  let created = issues.create(NewIssue(workspace: "atomic", title: title, details: "pending"))?
  match issues.get(IssueId("missing-known-id")):
    Err(error) => Ok("caught")
    Ok(issue) => Ok(issue.title)

export fn transaction_failure(title: Text<length: 1..120>) -> Result<Text, DataError>:
  transaction db:
    let created = issues.create(NewIssue(workspace: "atomic", title: title, details: "pending"))?
    issues.get(IssueId("missing-known-id"))?
    Ok("unreachable")

export fn atomic_update(id: Text, title: Text<length: 1..120>) -> Result<Text, DataError>:
  transaction db:
    let issue = issues.update(IssueId(id), EditIssue(title: title, details: "atomic"))?
    Ok(issue.title)
''')
        with self.project.open("a") as stream:
            for name in ["echo_integer", "echo_boolean", "caught_failure", "transaction_failure", "atomic_update"]:
                stream.write(f'{name} = "server.{name}"\n')


class Client:
    def __init__(self, fixture):
        self.fixture = fixture
        self.cookie = None
        self.csrf = ""
        self.flags = []

    def request(self, path, body=None, headers=None, **kwargs):
        headers = dict(headers or {})
        if path == "/_diamond/session":
            headers.setdefault("X-Diamond-Session", "1")
        if self.cookie:
            headers.setdefault("Cookie", self.cookie)
        if body is not None:
            headers.setdefault("X-Diamond-CSRF", self.csrf)
        status, response_headers, raw = self.fixture.request(path, body, headers, **kwargs)
        if "set-cookie" in response_headers:
            parts = response_headers["set-cookie"].split(";")
            self.cookie = None if "Max-Age=0" in parts else parts[0]
            self.flags = [part.strip() for part in parts[1:]]
        value = json.loads(raw) if raw else None
        if isinstance(value, dict) and "csrf" in value:
            self.csrf = value["csrf"]
        return status, value

    def login(self, user, node="A"):
        status, value = self.request("/_diamond/session", node=node)
        assert status == 200 and value["user"] is None
        old_cookie = self.cookie
        status, value = self.request("/_diamond/login", {"username": user, "password": PASSWORD}, node=node)
        assert status == 200 and value["value"] == user and self.cookie != old_cookie, (status, value)

    def rpc(self, name, *args, **kwargs):
        return self.request("/_diamond/rpc/" + name, {"contract": self.fixture.contract, "arguments": args}, **kwargs)


def campaign(fixture):
    status, headers, _ = fixture.request("/", node="A", direct=True)
    assert status == 200 and "connect-src 'self'" in headers["content-security-policy"]
    alice, bob, anonymous = (Client(fixture) for _ in range(3))
    alice.login("alice", "A")
    bob.login("bob", "B")
    assert all(flag in alice.flags for flag in ["Secure", "HttpOnly", "SameSite=Strict", "Path=/"])
    status, value = alice.rpc("create_issue", "team-one", "First issue", "Created through B", node="B")
    assert status == 200, (status, value)
    issue_id = value["value"]
    assert alice.rpc("read_issue", issue_id, node="A") == (200, {"value": "team-one | First issue\nCreated through B"})
    assert alice.rpc("update_issue", issue_id, "Edited issue", "Saved through A", node="A") == (200, {"value": "team-one | Edited issue\nSaved through A"})
    fixture.stop_node("B")
    fixture.start_node("B")
    assert alice.rpc("read_issue", issue_id, node="B") == (200, {"value": "team-one | Edited issue\nSaved through A"})
    before = fixture.sql("SELECT json_agg(json_build_object('resource',resource,'id',id,'payload',payload::text) ORDER BY resource,id)::text FROM diamond_w2.rows")
    denied = []
    for user in [bob, anonymous]:
        for operation, args in [("read_issue", [issue_id]), ("update_issue", [issue_id, "stolen", "forged"]), ("delete_issue", [issue_id])]:
            status, value = user.rpc(operation, *args, node="B")
            assert status == 403, (status, value)
            denied.append(operation)
    assert before == fixture.sql("SELECT json_agg(json_build_object('resource',resource,'id',id,'payload',payload::text) ORDER BY resource,id)::text FROM diamond_w2.rows")
    assert fixture.sql("SELECT count(*) FROM diamond_w2.rows WHERE payload->'owner'->>'value'='alice'") == "1"
    assert alice.rpc("list_issues", "team-one")[1]["value"] == issue_id + " | Edited issue"
    assert bob.rpc("list_issues", "team-one") == (200, {"value": ""})
    return {"journey": "login A, create B, read/edit A, restart B, read B", "cross_identity_denials": len(denied),
            "cookie_flags": alice.flags, "independent_owner_row_count": 1,
            "exploration": explore(fixture, alice, bob, issue_id)}


def explore(fixture, alice, bob, issue_id):
    rng = random.Random(SEED)
    rows = []

    def observe(label, result, statuses, expected=None):
        status, value = result
        assert status in statuses and (expected is None or value == expected), (label, result, statuses, expected)
        rows.append({"case": label, "status": status, "value": value})

    baseline = fixture.sql("SELECT json_agg(json_build_object('resource',resource,'id',id,'payload',payload::text) ORDER BY resource,id)::text FROM diamond_w2.rows")
    operation = "/_diamond/rpc/update_issue"
    valid = {"contract": fixture.contract, "arguments": [issue_id, "stolen", "bad"]}
    for label, body, headers, statuses in [
        ("wrong-contract", {**valid, "contract": "0" * 64}, {}, {409}),
        ("unknown-envelope-field", {**valid, "principal": {"id": "alice"}}, {}, {400}),
        ("wrong-arity", {**valid, "arguments": [issue_id, "forged"]}, {}, {400}),
        ("coerced-text", {**valid, "arguments": [issue_id, 4, "bad"]}, {}, {400}),
        ("invalid-origin", valid, {"Origin": "https://other.invalid"}, {400, 403}),
        ("missing-origin", valid, {"Origin": ""}, {400, 403}),
        ("forwarded-origin", valid, {"X-Forwarded-Host": fixture.authority}, {400}),
        ("wrong-host", valid, {"Host": "untrusted.invalid"}, {400}),
        ("wrong-csrf", valid, {"X-Diamond-CSRF": "0" * 64}, {403}),
        ("another-session-csrf", valid, {"X-Diamond-CSRF": bob.csrf}, {403}),
        ("duplicate-session-cookie", valid, {"Cookie": alice.cookie + "; " + alice.cookie}, {403}),
        ("unknown-cookie-token", valid, {"Cookie": alice.cookie.split("=", 1)[0] + "=" + "0" * 64}, {403}),
        ("empty-json", "{}", {}, {400}),
        ("malformed-json", "[not-json", {}, {400}),
        ("duplicate-contract", '{"contract":"x","contract":"' + fixture.contract + '","arguments":[]}', {}, {400}),
    ]:
        observe(label, alice.request(operation, body, headers, node="B", direct=True), statuses)
    assert fixture.sql("SELECT json_agg(json_build_object('resource',resource,'id',id,'payload',payload::text) ORDER BY resource,id)::text FROM diamond_w2.rows") == baseline
    for path in ["/server.json", "/contract.json", "/_diamond/rpc/private_probe", "/_diamond/rpc/main", "/_diamond/rpc/__diamond_fn_6_server_private_probe"]:
        status, _, _ = fixture.request(path, body={} if "/rpc/" in path else None, node="A", direct=True)
        assert status == 404, (path, status)
        rows.append({"case": "private-dispatch:" + path, "status": status})
    observe("get-cannot-mutate", alice.request(operation, method="GET", node="B", direct=True), {404})
    status, _, _ = fixture.request("/_diamond/session", node="A", direct=True)
    assert status == 403
    rows.append({"case": "bootstrap-requires-script-header", "status": status})
    for n in [-2**63, 2**63 - 1, -1, 0, 9007199254740993, *[rng.randrange(-2**63, 2**63) for _ in range(24)]]:
        observe("integer:" + str(n), alice.rpc("echo_integer", str(n), node="B"), {200}, {"value": str(n)})
    for value in [0, 1, True, None, "-0", "00", "+1", "1.0", "9223372036854775808", "-9223372036854775809", {}, []]:
        observe("invalid-integer:" + repr(value), alice.rpc("echo_integer", value), {400})
    for value in [True, False]:
        observe("boolean:" + str(value), alice.rpc("echo_boolean", value), {200}, {"value": value})
    for value in ["true", "false", 0, 1, None]:
        observe("invalid-boolean:" + repr(value), alice.rpc("echo_boolean", value), {400})
    for workspace, title, details in [("team-one", "", ""), ("team-one", "x" * 121, ""),
                                      ("", "Title", ""), ("x" * 65, "Title", ""), ("team-one", "Title", "x" * 4001)]:
        observe("bounds-rejection", alice.rpc("create_issue", workspace, title, details), {400})
    count_before = fixture.sql("SELECT count(*) FROM diamond_w2.rows")
    observe("caught-failure-poisons-transaction", alice.rpc("caught_failure", "must roll back", node="B"), {409})
    observe("explicit-transaction-rolls-back", alice.rpc("transaction_failure", "must roll back", node="A"), {403})
    assert fixture.sql("SELECT count(*) FROM diamond_w2.rows") == count_before
    generated = []
    payloads = ["", "café 雪😀", "nul\x00text", "<script>literal & text</script>", "x'); DROP TABLE diamond_w2.rows; --", 'quote " slash \\ and\nnew line']
    for i in range(18):
        workspace = "team-" + str(i % 3)
        title = ("😀" * 120) if i == 0 else f"Issue-{i}-{rng.randrange(1000000)}"
        details = payloads[i % len(payloads)]
        client = alice if i % 2 == 0 else bob
        status, value = client.rpc("create_issue", workspace, title, details, node="B" if i % 2 else "A")
        assert status == 200, (status, value)
        identifier = value["value"]
        observe("generated-create-read", client.rpc("read_issue", identifier, node="B"), {200}, {"value": workspace + " | " + title + "\n" + details})
        other = bob if client is alice else alice
        observe("generated-other-owner-denied", other.rpc("read_issue", identifier), {403})
        generated.append((client, identifier, workspace, title, details))
    # Compare full stored rows using an independent SQL read and explicit model.
    stored = json.loads(fixture.sql("SELECT json_agg(json_build_object('id',id,'payload',payload::text)) FROM diamond_w2.rows"))
    for row in stored:
        payload = json.loads(row.pop("payload"))
        row.update({"owner": payload["owner"]["value"], **{key: payload[key] for key in ["workspace", "title", "details"]}})
    expected = {identifier: ("alice" if client is alice else "bob", workspace, title, details) for client, identifier, workspace, title, details in generated}
    for row in stored:
        if row["id"] in expected:
            assert tuple(row[k] for k in ["owner", "workspace", "title", "details"]) == expected[row["id"]]
    assert len(stored) == len(generated) + 1
    titles = [f"Concurrent-{i}" for i in range(8)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(lambda item: alice.rpc("atomic_update", issue_id, item[1], node="A" if item[0] % 2 else "B"), enumerate(titles)))
    assert sorted(value["value"] for status, value in outcomes if status == 200) == sorted(titles), outcomes
    status, final = alice.rpc("read_issue", issue_id, node="B")
    assert status == 200 and final["value"] in ["team-one | " + title + "\natomic" for title in titles]
    rows.append({"case": "concurrent-atomic-updates", "successful_updates": len(outcomes), "fresh_read": final})
    # Real deletion, then read through the other node and independent SQL absence.
    _, identifier, _, _, _ = generated[0]
    observe("delete-through-B", alice.rpc("delete_issue", identifier, node="B"), {200}, {"value": "Issue deleted."})
    observe("deleted-read-through-A", alice.rpc("read_issue", identifier, node="A"), {403})
    assert fixture.sql("SELECT count(*) FROM diamond_w2.rows WHERE id='" + identifier + "'") == "0"
    digest = hashlib.sha256(bob.cookie.split("=", 1)[1].encode()).hexdigest()
    assert fixture.sql("SELECT count(*) FROM diamond_w2.sessions WHERE digest='" + digest + "'") == "1"
    fixture.sql("UPDATE diamond_w2.sessions SET expires=clock_timestamp()-interval '1 second' WHERE digest='" + digest + "'")
    for node in ["A", "B"]:
        observe("shared-session-expiry:" + node, bob.rpc("list_issues", "team-one", node=node), {403})
    bob.login("bob", "B")
    old_cookie, old_csrf = bob.cookie, bob.csrf
    observe("logout-through-B", bob.request("/_diamond/logout", {}, node="B"), {200}, {"value": ""})
    bob.cookie, bob.csrf = old_cookie, old_csrf
    for node in ["A", "B"]:
        observe("shared-logout-revocation:" + node, bob.rpc("list_issues", "team-one", node=node), {403})
    for user in ["rate-user", "unknown-user"]:
        client = Client(fixture)
        assert client.request("/_diamond/session")[0] == 200
        for i in range(12):
            observe("shared-login-limit:" + user, client.request("/_diamond/login", {"username": user, "password": "wrong password"}, node="A" if i % 2 else "B"), {403} if i < 10 else {429})
    assert int(fixture.sql("SELECT attempts FROM diamond_w2.rates WHERE bucket='global'")) >= 24
    return {"seed": SEED, "observations": rows, "generated_records": len(generated),
            "independent_stored_record_comparisons": len(expected), "concurrent_updates": len(outcomes),
            "delivery_and_commit_faults": fault_campaign(fixture, alice)}


def fault_campaign(fixture, alice):
    """Native fixture-only triggers gate real execution; no runtime replacement."""
    count_before = int(fixture.sql("SELECT count(*) FROM diamond_w2.rows"))
    fixture.sql("CREATE FUNCTION diamond_w2.delivery_gate() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN PERFORM pg_advisory_xact_lock(828202); RETURN NEW; END $$; CREATE TRIGGER delivery_gate AFTER INSERT ON diamond_w2.rows FOR EACH ROW EXECUTE FUNCTION diamond_w2.delivery_gate()")
    gate = subprocess.Popen(["psql", fixture.configuration, "-XAt", "-v", "ON_ERROR_STOP=1"],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
    connection = None
    try:
        gate.stdin.write("SELECT pg_advisory_lock(828202);\n\\echo GATE_READY\n")
        gate.stdin.flush()
        assert gate.stdout.readline().strip() == ""
        assert gate.stdout.readline().strip() == "GATE_READY"
        body = json.dumps({"contract": fixture.contract, "arguments": ["faults", "Disconnected delivery", "one committed row"]}).encode()
        request = (f"POST /_diamond/rpc/create_issue HTTP/1.1\r\nHost: {fixture.authority}\r\nOrigin: {fixture.origin}\r\nCookie: {alice.cookie}\r\nX-Diamond-CSRF: {alice.csrf}\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n").encode() + body
        connection = socket.create_connection(("127.0.0.1", fixture.ports["A"]), timeout=5)
        connection.sendall(request)
        for _ in range(40):
            waiting = fixture.sql("SELECT count(*) FROM pg_stat_activity WHERE wait_event='advisory' AND query LIKE 'INSERT INTO diamond_w2.rows%'")
            if waiting == "1":
                break
            time.sleep(.02)
        else:
            raise AssertionError("create never reached the real PostgreSQL delivery gate")
        connection.close()
        connection = None
        # The caller has disconnected while the actual database operation waits.
        gate.stdin.write("SELECT pg_advisory_unlock(828202);\n\\q\n")
        gate.stdin.flush()
        gate.communicate(timeout=5)
        for _ in range(100):
            if int(fixture.sql("SELECT count(*) FROM diamond_w2.rows")) == count_before + 1:
                break
            time.sleep(.02)
        else:
            raise AssertionError("disconnected mutation did not finish its owned work")
    finally:
        if connection:
            connection.close()
        if gate.poll() is None:
            gate.kill()
            gate.communicate()
        fixture.sql("DROP TRIGGER delivery_gate ON diamond_w2.rows; DROP FUNCTION diamond_w2.delivery_gate()")
    assert alice.rpc("list_issues", "faults", node="B")[1]["value"].endswith(" | Disconnected delivery")
    # A real deferred failure happens specifically at COMMIT, after INSERT has
    # returned. The caller receives uncertainty and must not blindly replay.
    fixture.sql("CREATE FUNCTION diamond_w2.commit_fault() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'synthetic deferred commit fault'; END $$; CREATE CONSTRAINT TRIGGER commit_fault AFTER INSERT ON diamond_w2.rows DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION diamond_w2.commit_fault()")
    try:
        status, value = alice.rpc("create_issue", "faults", "Commit must fail", "no retry", node="B")
        assert status == 503 and value == {"error": "outcome_unknown"}, (status, value)
        assert int(fixture.sql("SELECT count(*) FROM diamond_w2.rows")) == count_before + 1
    finally:
        fixture.sql("DROP TRIGGER commit_fault ON diamond_w2.rows; DROP FUNCTION diamond_w2.commit_fault()")
    assert alice.rpc("list_issues", "faults", node="A")[0] == 200
    return {"disconnected_while_database_blocked": True, "new_rows_after_delivery_loss": 1,
            "commit_fault_status": status, "commit_fault_error": value["error"],
            "new_rows_after_commit_fault": 0, "recovery_on_other_node": "PASS"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=ROOT / "verification/web_two_results.json")
    parser.add_argument("--serve-fixture", type=Path)
    parser.add_argument("--public-host", choices=["localhost", "127.0.0.1"], default="localhost")
    parser.add_argument("--public-port", type=int)
    options = parser.parse_args()
    root = options.serve_fixture or Path(tempfile.mkdtemp(prefix="diamond-w2-campaign-"))
    if options.serve_fixture:
        root.mkdir(mode=0o700)
    fixture = Fixture(root, explore=not bool(options.serve_fixture), public_host=options.public_host, public_port=options.public_port)
    report = {"binary_sha256": hashlib.sha256(BINARY.read_bytes()).hexdigest(), "seed": SEED, "status": "RUNNING"}
    try:
        fixture.start()
        if options.serve_fixture:
            print(json.dumps({"status": "READY", "origin": fixture.origin, "state": str(root / "state.json")}), flush=True)
            while True:
                signal.pause()
        report["observations"] = campaign(fixture)
        report["routing"] = fixture.trace
        report["status"] = "PASS"
    except KeyboardInterrupt:
        pass
    except Exception as error:
        report.update(status="FAIL", error=repr(error), retained_directory=str(root))
        raise
    finally:
        fixture.close()
        if not options.serve_fixture:
            options.report.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
        if report["status"] == "PASS":
            shutil.rmtree(root)
    print(json.dumps({"status": report["status"], "report": str(options.report)}))


if __name__ == "__main__":
    main()
