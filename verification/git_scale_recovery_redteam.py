#!/usr/bin/env python3
"""G4 real-interface scale, coordination, search, replica, and GC qualification."""

import base64
import hashlib
import http.client
import json
import os
import pathlib
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
BIN = ROOT / "target/debug/diamond"
PROJECT = "examples/git-browser/diamond.toml"
TMP = pathlib.Path(tempfile.mkdtemp(prefix="diamond-g4-scale-"))
PG, SOCK = TMP / "pg", TMP / "sock"
GITROOT, OBJECTS = TMP / "git", TMP / "objects"
DELIVERY, OPERATIONS = TMP / "delivery", TMP / "operations"
MAIL_ONE, MAIL_TWO = TMP / "mail-one", TMP / "mail-two"
OUTSIDE = TMP / "outside-replica"
for directory in (SOCK, GITROOT, OBJECTS, DELIVERY, OPERATIONS, MAIL_ONE, MAIL_TWO, OUTSIDE):
    directory.mkdir(mode=0o700)


def free_port():
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    listener.close()
    return port


pgport, port_one, port_two = free_port(), free_port(), free_port()
pgcfg = f"host={SOCK} port={pgport} user=diamond_rt dbname=postgres"
runtime = {
    "objects": str(OBJECTS),
    "git": {"repos": {"root": str(GITROOT), "executable": "/usr/bin/git"}},
    "delivery": {
        "builds": {
            "root": str(DELIVERY),
            "sandbox": "/usr/bin/systemd-run",
            "limit": "/usr/bin/prlimit",
        }
    },
    "operations": {"control": {"root": str(OPERATIONS)}},
    "oauth": {
        "transaction_key_env": "DIA_OAUTH_KEY",
        "google": {
            "client_id": "synthetic-google-client",
            "client_secret_env": "DIA_GOOGLE_SECRET",
        },
        "github": {
            "client_id": "synthetic-github-client",
            "client_secret_env": "DIA_GITHUB_SECRET",
        },
    },
    "http": {
        "webhook": {
            "origin": "http://127.0.0.1:9",
            "network": "loopback",
            "paths": ["/hooks/"],
            "methods": ["POST"],
            "timeout_ms": 100,
            "max_response_bytes": 1024,
            "max_redirects": 0,
        }
    },
}
env = os.environ.copy()
env.update(
    DIAMOND_POSTGRES=pgcfg,
    DIAMOND_RUNTIME=json.dumps(runtime, separators=(",", ":")),
    DIA_OAUTH_KEY="K" * 48,
    DIA_GOOGLE_SECRET="synthetic-google-secret",
    DIA_GITHUB_SECRET="synthetic-github-secret",
    GIT_TERMINAL_PROMPT="0",
    GIT_CONFIG_COUNT="1",
    GIT_CONFIG_KEY_0="credential.helper",
    GIT_CONFIG_VALUE_0="",
)
results = []
servers = []
logs = []


def check(name, condition, detail=""):
    results.append({"name": name, "pass": bool(condition)})
    if not condition:
        for log_name in ("server-one.log", "server-two.log", "postgres.log"):
            path = TMP / log_name
            if path.exists():
                print(f"--- {log_name} ---\n{path.read_text(errors='replace')[-16000:]}", file=sys.stderr)
        raise AssertionError(f"{name}: {detail}")


def run(arguments, cwd=ROOT, timeout=90, input=None):
    return subprocess.run(
        arguments,
        cwd=cwd,
        env=env,
        text=True,
        input=input,
        capture_output=True,
        timeout=timeout,
    )


def ok(name, arguments, **kwargs):
    completed = run(arguments, **kwargs)
    check(name, completed.returncode == 0, (completed.stdout + completed.stderr)[-4000:])
    return completed


def request(port, method, path, body=None, headers=None, cookie=None):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    request_headers = dict(headers or {})
    if cookie:
        request_headers["Cookie"] = cookie
    encoded = None
    if body is not None:
        encoded = json.dumps(body).encode()
        request_headers.setdefault("Content-Type", "application/json")
        request_headers.setdefault("Origin", f"http://127.0.0.1:{port}")
    connection.request(method, path, body=encoded, headers=request_headers)
    response = connection.getresponse()
    payload = response.read()
    response_headers = {key.lower(): value for key, value in response.getheaders()}
    connection.close()
    try:
        payload = json.loads(payload)
    except Exception:
        pass
    return response.status, response_headers, payload


def rpc(port, client, contract, name, arguments):
    return request(
        port,
        "POST",
        "/_diamond/rpc/" + name,
        {"contract": contract, "arguments": arguments},
        {"X-Diamond-CSRF": client["csrf"]},
        client["cookie"],
    )


def login(port, username, password):
    status, headers, session = request(
        port, "GET", "/_diamond/session", headers={"X-Diamond-Session": "1"}
    )
    check(f"bootstrap {username} session", status == 200, str(session))
    cookie = headers["set-cookie"].split(";", 1)[0]
    status, headers, reply = request(
        port,
        "POST",
        "/_diamond/login",
        {"username": username, "password": password},
        {"X-Diamond-CSRF": session["csrf"]},
        cookie,
    )
    check(f"login {username}", status == 200, str(reply))
    return {"cookie": headers["set-cookie"].split(";", 1)[0], "csrf": reply["csrf"]}


def value(reply):
    return reply["value"]


def wait_server(port):
    for _ in range(400):
        try:
            status, _, page = request(port, "GET", "/")
            if status == 200:
                return page.decode() if isinstance(page, bytes) else str(page)
        except Exception:
            pass
        time.sleep(0.05)
    raise AssertionError(f"server {port} did not start")


def ops_work(expect_success=True):
    completed = run(
        [
            str(BIN),
            "project-ops-work",
            PROJECT,
            "--postgres-env",
            "DIAMOND_POSTGRES",
            "--runtime-env",
            "DIAMOND_RUNTIME",
        ]
    )
    check(
        "operations worker expected outcome",
        (completed.returncode == 0) == expect_success,
        (completed.stdout + completed.stderr)[-4000:],
    )
    return completed


try:
    ok(
        "initialize PostgreSQL cluster",
        ["initdb", "-D", str(PG), "-A", "trust", "--no-locale", "-E", "UTF8", "-U", "diamond_rt", "--no-sync"],
    )
    ok(
        "start PostgreSQL",
        ["pg_ctl", "-D", str(PG), "-l", str(TMP / "postgres.log"), "-o", f"-k {SOCK} -p {pgport} -c listen_addresses=", "-w", "start"],
    )
    ok("initialize application schema", [str(BIN), "project-init", PROJECT, "--postgres-env", "DIAMOND_POSTGRES"])
    for label, command in (
        ("attachment", "project-files-init"),
        ("Git", "project-git-init"),
        ("delivery", "project-delivery-init"),
        ("operations", "project-ops-init"),
    ):
        ok(
            f"initialize {label} store",
            [str(BIN), command, PROJECT, "--postgres-env", "DIAMOND_POSTGRES", "--runtime-env", "DIAMOND_RUNTIME"],
        )
    check(
        "operations store is private and application-bound",
        (OPERATIONS / ".diamond-operations-store").stat().st_mode & 0o777 == 0o600
        and (OPERATIONS / "replicas").stat().st_mode & 0o777 == 0o700,
    )

    passwords = {"alice": "Synthetic G4 password 91!", "bob": "Synthetic G4 password 92!"}
    ok(
        "provision administrator",
        [str(BIN), "project-admin-add", PROJECT, "--postgres-env", "DIAMOND_POSTGRES", "--user", "alice", "--email", "alice@example.test", "--password-stdin"],
        input=passwords["alice"] + "\n",
    )
    ok(
        "provision ordinary user",
        [str(BIN), "project-user-add", PROJECT, "--postgres-env", "DIAMOND_POSTGRES", "--user", "bob", "--password-stdin"],
        input=passwords["bob"] + "\n",
    )
    for name, port, mailbox in (("one", port_one, MAIL_ONE), ("two", port_two, MAIL_TWO)):
        log = open(TMP / f"server-{name}.log", "wb")
        logs.append(log)
        servers.append(
            subprocess.Popen(
                [str(BIN), "serve", PROJECT, "--listen", f"127.0.0.1:{port}", "--origin", f"http://127.0.0.1:{port}", "--postgres-env", "DIAMOND_POSTGRES", "--runtime-env", "DIAMOND_RUNTIME", "--identity-mailbox", str(mailbox)],
                cwd=ROOT,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
        )
    page_one, page_two = wait_server(port_one), wait_server(port_two)
    contract = re.search(r'data-contract="([0-9a-f]{64})"', page_one).group(1)
    check("both web nodes serve the same checked contract", contract in page_two, page_two[:500])
    status, _, operations_page = request(port_two, "GET", "/operations")
    operations_text = operations_page.decode() if isinstance(operations_page, bytes) else str(operations_page)
    check("Diamond renders the G4 operations console", status == 200 and "Scale, recovery" in operations_text)
    for port in (port_one, port_two):
        live, _, _ = request(port, "GET", "/_diamond/health/live")
        ready, _, ready_body = request(port, "GET", "/_diamond/health/ready")
        check(f"node {port} is live and ready", live == 200 and ready == 200 and ready_body["contract"] == contract)

    alice = login(port_one, "alice", passwords["alice"])
    bob = login(port_two, "bob", passwords["bob"])
    status, _, reply = rpc(port_two, alice, contract, "operations_nodes", [])
    check("query shared node registry through peer", status == 200, str(reply))
    nodes = value(reply)["cards"]
    check("session and CSRF state work across two web nodes", status == 200 and len([n for n in nodes if n["one"] == "web"]) == 2, str(reply))
    status, _, _ = rpc(port_one, bob, contract, "operations_nodes", [])
    check("ordinary users cannot inspect administrative topology", status == 403, str(status))

    status, _, reply = rpc(port_one, alice, contract, "create_organization", ["g4-team"])
    check("create organization", status == 200, str(reply))
    organization = value(reply)
    repositories = {}
    for name, description, public in (
        ("scale-public", "citrine public discovery", True),
        ("scale-secret", "obsidian private recovery", False),
    ):
        status, _, reply = rpc(port_two, alice, contract, "create_repository", [organization, name, description, public])
        check(f"create {name} repository through second node", status == 200, str(reply))
        repositories[name] = value(reply)
        status, _, reply = rpc(port_one, alice, contract, "initialize_repository", [repositories[name]])
        check(f"initialize {name} through first node", status == 200, str(reply))
    repository = repositories["scale-public"]
    status, _, reply = rpc(port_one, alice, contract, "create_issue", [repository, "Background search candidate", "g5needle appears only in this issue body"])
    check("create body-only search candidate", status == 200, str(reply))
    status, _, reply = rpc(port_two, alice, contract, "create_issue", [repository, "g5needle", "Exact-title relevance candidate"])
    check("create exact-title search candidate", status == 200, str(reply))

    token_request = {
        "label": "g4-redteam",
        "operations": ["git:repos:read", "git:repos:write"],
        "scopes": {"repository": [repository]},
        "lifetime_seconds": "3600",
    }
    status, _, reply = request(
        port_two,
        "POST",
        "/_diamond/accounts/create_token",
        token_request,
        {"X-Diamond-CSRF": alice["csrf"]},
        alice["cookie"],
    )
    check("create repository-scoped Git credential on second node", status == 200, str(reply))
    token = reply["value"]
    git_url = f"http://diamond:{token}@127.0.0.1:{port_one}/_diamond/git/repos/{repository}.git"
    clone = TMP / "clone"
    ok("clone through first node", ["git", "clone", git_url, str(clone)])
    ok("configure Git author", ["git", "config", "user.email", "alice@example.test"], cwd=clone)
    ok("configure Git name", ["git", "config", "user.name", "Alice"], cwd=clone)
    (clone / "README.md").write_text("Diamond G4 citrine fixture\n")
    ok("stage G4 fixture", ["git", "add", "README.md"], cwd=clone)
    ok("commit G4 fixture", ["git", "commit", "-m", "G4 scale fixture"], cwd=clone)
    ok("push through first node", ["git", "push", git_url, "HEAD:refs/heads/main"], cwd=clone)
    commit = run(["git", "rev-parse", "HEAD"], cwd=clone).stdout.strip()

    status, _, reply = rpc(port_one, alice, contract, "record_shard", ["secondary", "active", "0"])
    check("register secondary shard", status == 200 and value(reply) == "1", str(reply))
    status, _, _ = rpc(port_two, alice, contract, "record_shard", ["../escape", "active", "0"])
    check("path-shaped shard identity is rejected", status == 400, str(status))
    status, _, reply = rpc(port_two, alice, contract, "operations_placements", [])
    initial = next(card for card in value(reply)["cards"] if card["id"] == repository)
    check("repository creation and push automatically establish the primary placement", status == 200 and initial["one"] == "primary" and initial["two"] == commit and initial["three"] == "1", str(reply))

    status, _, reply = rpc(port_one, alice, contract, "schedule_search_rebuild", ["search-initial"])
    check("schedule durable search rebuild", status == 200 and len(value(reply)) == 64, str(reply))
    status, _, reply = rpc(port_two, alice, contract, "schedule_mirror", [repository, "secondary", commit, "1", "mirror-initial"])
    check("schedule exact-object replica", status == 200 and len(value(reply)) == 64, str(reply))

    held_bytes, orphan_bytes = b"retain-g4\n", b"delete-g4\n"
    held = hashlib.sha256(held_bytes).hexdigest()
    orphan = hashlib.sha256(orphan_bytes).hexdigest()
    for digest, content in ((held, held_bytes), (orphan, orphan_bytes)):
        path = DELIVERY / "objects" / digest
        path.write_bytes(content)
        os.chmod(path, 0o600)
        old = time.time() - 7200
        os.utime(path, (old, old))
    status, _, reply = rpc(port_one, alice, contract, "set_object_hold", [held, "G4 legal hold"])
    check("set explicit object hold", status == 200 and value(reply) == "true", str(reply))
    status, _, reply = rpc(port_two, alice, contract, "schedule_gc", ["gc-initial"])
    check("schedule bounded object GC", status == 200 and len(value(reply)) == 64, str(reply))
    for _ in range(3):
        ops_work()

    replica = next((OPERATIONS / "replicas" / "secondary").glob("*.git"))
    replica_head = run(["git", "--git-dir", str(replica), "rev-parse", "HEAD^{commit}"]).stdout.strip()
    fsck = run(["git", "--git-dir", str(replica), "fsck", "--strict", "--no-dangling"])
    check("mirror is a verified stock-Git replica", replica_head == commit and fsck.returncode == 0, fsck.stderr)
    check("GC deletes only the expired orphan", not (DELIVERY / "objects" / orphan).exists() and (DELIVERY / "objects" / held).read_bytes() == held_bytes)
    status, _, reply = rpc(port_one, alice, contract, "operations_tasks", [])
    check("durable tasks complete with bounded receipts", status == 200 and all(card["three"] == "done" for card in value(reply)["cards"]), str(reply))
    status, _, reply = rpc(port_two, alice, contract, "operations_mirrors", [])
    check("replica catalog records verified object", status == 200 and value(reply)["cards"][0]["three"] == "verified" and value(reply)["cards"][0]["four"] == commit, str(reply))

    status, _, reply = rpc(port_two, bob, contract, "global_search", ["citrine"])
    check("ordinary user can search an authorized public repository", status == 200 and value(reply)["total"] == "1", str(reply))
    status, _, reply = rpc(port_one, bob, contract, "global_search", ["  g5needle  "])
    ranked = value(reply)["cards"] if status == 200 else []
    check("search trims input and ranks an exact title above a body match", status == 200 and len(ranked) == 2 and ranked[0]["title"] == "g5needle" and ranked[1]["title"] == "Background search candidate", str(reply))
    status, _, reply = rpc(port_two, bob, contract, "global_search", ["%"])
    check("search treats SQL wildcard punctuation literally instead of matching the corpus", status == 200 and value(reply)["total"] == "0", str(reply))
    status, _, reply = rpc(port_one, bob, contract, "global_search", ["obsidian"])
    check("search hides unauthorized private candidates", status == 200 and value(reply)["total"] == "0", str(reply))
    status, _, reply = rpc(port_one, alice, contract, "grant_reporter", [organization, "bob"])
    membership = value(reply)
    check("grant reporter for stale-index authorization probe", status == 200, str(reply))
    status, _, reply = rpc(port_two, bob, contract, "global_search", ["obsidian"])
    check("fresh authorization reveals existing indexed candidate", status == 200 and value(reply)["total"] == "1", str(reply))
    status, _, reply = rpc(port_two, alice, contract, "revoke_member", [membership])
    check("revoke reporter", status == 200, str(reply))
    status, _, reply = rpc(port_one, bob, contract, "global_search", ["obsidian"])
    check("revocation immediately hides stale search index data", status == 200 and value(reply)["total"] == "0", str(reply))

    status, _, reply = rpc(port_two, alice, contract, "schedule_promotion", [repository, "secondary", commit, "1", "promote-initial"])
    check("schedule verified repository promotion", status == 200 and len(value(reply)) == 64, str(reply))
    ops_work()
    status, _, reply = rpc(port_two, alice, contract, "operations_placements", [])
    promoted = next(card for card in value(reply)["cards"] if card["id"] == repository)
    check("worker atomically promotes the verified replica", status == 200 and promoted["one"] == "secondary" and promoted["two"] == commit and promoted["three"] == "2", str(reply))
    status, _, _ = rpc(port_one, alice, contract, "record_placement", [repository, "primary", commit, "1"])
    check("stale placement generation cannot overwrite failover", status == 409, str(status))

    status, _, reply = rpc(port_one, alice, contract, "record_shard", ["evil", "active", "0"])
    check("register hostile mirror probe shard", status == 200, str(reply))
    os.symlink(OUTSIDE, OPERATIONS / "replicas" / "evil")
    status, _, reply = rpc(port_two, alice, contract, "schedule_mirror", [repository, "evil", commit, "2", "mirror-symlink"])
    check("queue hostile symlink mirror without granting path authority", status == 200, str(reply))
    failed = ops_work(expect_success=False)
    check("worker fails closed on a symlink replica root", failed.returncode != 0 and not any(OUTSIDE.iterdir()), failed.stderr)
    os.unlink(OPERATIONS / "replicas" / "evil")

    (clone / "README.md").write_text("Diamond G4 citrine fixture\nrouted after promotion\n")
    ok("stage routed fixture", ["git", "add", "README.md"], cwd=clone)
    ok("commit routed fixture", ["git", "commit", "-m", "Route through promoted shard"], cwd=clone)
    routed_commit = run(["git", "rev-parse", "HEAD"], cwd=clone).stdout.strip()
    ok("push through placement-routed data plane", ["git", "push", git_url, "HEAD:refs/heads/main"], cwd=clone)
    primary_repository = next(path for path in GITROOT.glob("*.git") if path.name.endswith(f"-{repository}.git"))
    primary_head = run(["git", "--git-dir", str(primary_repository), "rev-parse", "HEAD^{commit}"]).stdout.strip()
    secondary_head = run(["git", "--git-dir", str(replica), "rev-parse", "HEAD^{commit}"]).stdout.strip()
    check("post-promotion writes reach only the promoted shard", primary_head == commit and secondary_head == routed_commit, f"primary={primary_head} secondary={secondary_head}")
    routed_clone = TMP / "routed-clone"
    routed_url = f"http://diamond:{token}@127.0.0.1:{port_two}/_diamond/git/repos/{repository}.git"
    ok("fresh clone reads from promoted shard", ["git", "clone", routed_url, str(routed_clone)])
    routed_clone_head = run(["git", "rev-parse", "HEAD"], cwd=routed_clone).stdout.strip()
    check("fresh clone observes promoted-shard write", routed_clone_head == routed_commit, routed_clone_head)
    status, _, reply = rpc(port_one, alice, contract, "operations_placements", [])
    routed = next(card for card in value(reply)["cards"] if card["id"] == repository)
    check("receive hook advances placement object without changing owner generation", status == 200 and routed["one"] == "secondary" and routed["two"] == routed_commit and routed["three"] == "2", str(reply))
    reconciled = ok(
        "reconcile push spools across active placement roots",
        [str(BIN), "project-git-reconcile", PROJECT, "--postgres-env", "DIAMOND_POSTGRES", "--runtime-env", "DIAMOND_RUNTIME"],
    )
    reconcile_receipt = json.loads(reconciled.stdout)
    check("reconciliation scans promoted-shard events without reverting refs", reconcile_receipt["committed"] >= 2 and run(["git", "--git-dir", str(replica), "rev-parse", "HEAD^{commit}"]).stdout.strip() == routed_commit)

    for name, args in (
        ("search-concurrent", ["search-concurrent"]),
        ("gc-concurrent", ["gc-concurrent"]),
    ):
        rpc_name = "schedule_search_rebuild" if name.startswith("search") else "schedule_gc"
        status, _, reply = rpc(port_one, alice, contract, rpc_name, args)
        check(f"schedule {name} task", status == 200, str(reply))
    workers = [
        subprocess.Popen(
            [str(BIN), "project-ops-work", PROJECT, "--postgres-env", "DIAMOND_POSTGRES", "--runtime-env", "DIAMOND_RUNTIME"],
            cwd=ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for _ in range(2)
    ]
    worker_results = [worker.communicate(timeout=90) + (worker.returncode,) for worker in workers]
    worker_receipts = [json.loads(stdout) for stdout, _, code in worker_results if code == 0]
    check("two workers claim distinct durable tasks", len(worker_receipts) == 2 and len({receipt["task"] for receipt in worker_receipts}) == 2, str(worker_results))

    backup_digest = hashlib.sha256(b"synthetic-encrypted-backup").hexdigest()
    status, _, reply = rpc(port_one, alice, contract, "record_backup", ["backup_g4_1", "complete", backup_digest, "1"])
    check("catalog completed encrypted backup evidence", status == 200, str(reply))
    status, _, _ = rpc(port_two, alice, contract, "record_backup", ["backup_g4_1", "failed", "", "2"])
    check("stale backup generation cannot report a false update", status == 409, str(status))
    status, _, reply = rpc(port_two, alice, contract, "record_restore", ["restore_g4_1", "backup_g4_1", "verified", "isolated restore hashes verified"])
    check("catalog isolated restore evidence", status == 200, str(reply))
    status, _, _ = rpc(port_one, alice, contract, "record_restore", ["restore_missing", "missing", "verified", "forged"])
    check("restore evidence cannot reference an incomplete backup", status == 409, str(status))
    status, _, reply = rpc(port_one, alice, contract, "set_operations_policy", ["maintenance", "scheduled", "0"])
    check("create versioned operations policy", status == 200 and value(reply) == "1", str(reply))
    status, _, reply = rpc(port_two, alice, contract, "set_operations_policy", ["maintenance", "normal", "1"])
    check("compare-and-set operations policy", status == 200 and value(reply) == "2", str(reply))
    status, _, _ = rpc(port_one, alice, contract, "set_operations_policy", ["maintenance", "stale", "1"])
    check("stale policy generation is rejected", status == 409, str(status))

    statuses = []
    for index in range(51):
        port = port_one if index % 2 == 0 else port_two
        status, _, _ = rpc(port, alice, contract, "global_search", ["citrine"])
        statuses.append(status)
    check("shared search rate spans both application nodes", statuses[:50] == [200] * 50 and statuses[50] == 429, str(statuses[-5:]))

    status, _, reply = rpc(port_two, alice, contract, "operations_nodes", [])
    web_nodes = [card for card in value(reply)["cards"] if card["one"] == "web" and card["three"] == "active"]
    target = web_nodes[0]
    status, _, reply = rpc(port_one, alice, contract, "drain_operations_node", [target["id"], target["five"]])
    check("operator drain uses exact node generation", status == 200, str(reply))
    deadline = time.time() + 15
    node_statuses = [200, 200]
    while time.time() < deadline:
        node_statuses = [request(port, "GET", "/")[0] for port in (port_one, port_two)]
        if sorted(node_statuses) == [200, 503]:
            break
        time.sleep(0.25)
    check("drained node stops ordinary traffic while peer remains ready", sorted(node_statuses) == [200, 503], str(node_statuses))
    ready_statuses = [request(port, "GET", "/_diamond/health/ready")[0] for port in (port_one, port_two)]
    check("readiness reflects coordinated drain", sorted(ready_statuses) == [200, 503], str(ready_statuses))

    status, _, metrics = request(port_one if node_statuses[0] == 200 else port_two, "GET", "/_diamond/health/metrics")
    check("operational metrics remain bounded and identity-free", status == 200 and set(metrics) == {"requests", "active_requests", "active_git_transports", "active_sockets", "draining"}, str(metrics))
    status, _, reply = rpc(port_one if node_statuses[0] == 200 else port_two, alice, contract, "operations_audit", [])
    audit_text = json.dumps(value(reply)) if status == 200 else ""
    check("administrative mutations leave a redacted audit trail", status == 200 and "policy_set" in audit_text and token not in audit_text and passwords["alice"] not in audit_text, audit_text)

    for server in servers:
        if server.poll() is None:
            server.send_signal(signal.SIGINT)
    for server in servers:
        server.wait(timeout=20)

    env["DIA_BACKUP_KEY"] = "Synthetic G4 backup key 2026!"
    rejected_backup = TMP / "rejected-symlink-backup"
    symlink_probe = DELIVERY / "objects" / "symlink-probe"
    os.symlink(OUTSIDE, symlink_probe)
    rejected = run(
        [str(BIN), "project-backup", PROJECT, "--postgres-env", "DIAMOND_POSTGRES", "--runtime-env", "DIAMOND_RUNTIME", "--out", str(rejected_backup), "--backup-key-env", "DIA_BACKUP_KEY"],
        timeout=180,
    )
    check("backup rejects symlinked runtime content without traversing it", rejected.returncode != 0 and not any(OUTSIDE.iterdir()), rejected.stderr)
    os.unlink(symlink_probe)
    if rejected_backup.exists():
        shutil.rmtree(rejected_backup)

    backup = TMP / "encrypted-backup"
    completed = ok(
        "create encrypted database and runtime-root snapshot",
        [str(BIN), "project-backup", PROJECT, "--postgres-env", "DIAMOND_POSTGRES", "--runtime-env", "DIAMOND_RUNTIME", "--out", str(backup), "--backup-key-env", "DIA_BACKUP_KEY"],
        timeout=180,
    )
    backup_receipt = json.loads(completed.stdout)
    check("backup covers shared objects, Git, delivery, and operations roots", backup_receipt["runtime_roots"] == 4 and backup_receipt["runtime_entries"] > 20, str(backup_receipt))
    check("backup leaves only encrypted artifacts", {path.name for path in backup.iterdir()} == {"database.dump.gpg", "runtime.data.gpg", "manifest.json.gpg"} and all(path.stat().st_mode & 0o077 == 0 for path in backup.iterdir()), str(list(backup.iterdir())))

    restored_database = "diamond_restored"
    ok("create isolated restore database", ["createdb", "-h", str(SOCK), "-p", str(pgport), "-U", "diamond_rt", restored_database])
    restore_roots = {name: TMP / f"restored-{name}" for name in ("objects", "git", "delivery", "operations")}
    for path in restore_roots.values():
        path.mkdir(mode=0o700)
    restore_runtime = json.loads(json.dumps(runtime))
    restore_runtime["objects"] = str(restore_roots["objects"])
    restore_runtime["git"]["repos"]["root"] = str(restore_roots["git"])
    restore_runtime["delivery"]["builds"]["root"] = str(restore_roots["delivery"])
    restore_runtime["operations"]["control"]["root"] = str(restore_roots["operations"])
    env["DIAMOND_RESTORE_POSTGRES"] = f"host={SOCK} port={pgport} user=diamond_rt dbname={restored_database}"
    env["DIAMOND_RESTORE_RUNTIME"] = json.dumps(restore_runtime, separators=(",", ":"))
    env["DIA_WRONG_BACKUP_KEY"] = "Definitely wrong G4 backup key!"
    wrong = run(
        [str(BIN), "project-restore", PROJECT, "--postgres-env", "DIAMOND_RESTORE_POSTGRES", "--runtime-env", "DIAMOND_RESTORE_RUNTIME", "--from", str(backup), "--backup-key-env", "DIA_WRONG_BACKUP_KEY"],
        timeout=180,
    )
    check("wrong backup key fails before database or filesystem writes", wrong.returncode != 0 and all(not any(path.iterdir()) for path in restore_roots.values()), wrong.stderr)
    tampered_backup = TMP / "tampered-backup"
    shutil.copytree(backup, tampered_backup)
    tampered_runtime = tampered_backup / "runtime.data.gpg"
    tampered_bytes = bytearray(tampered_runtime.read_bytes())
    tampered_bytes[len(tampered_bytes) // 2] ^= 0x80
    tampered_runtime.write_bytes(tampered_bytes)
    tampered = run(
        [str(BIN), "project-restore", PROJECT, "--postgres-env", "DIAMOND_RESTORE_POSTGRES", "--runtime-env", "DIAMOND_RESTORE_RUNTIME", "--from", str(tampered_backup), "--backup-key-env", "DIA_BACKUP_KEY"],
        timeout=180,
    )
    check("tampered encrypted runtime bundle fails before database or filesystem writes", tampered.returncode != 0 and all(not any(path.iterdir()) for path in restore_roots.values()), tampered.stderr)
    completed = ok(
        "restore encrypted snapshot into isolated database and roots",
        [str(BIN), "project-restore", PROJECT, "--postgres-env", "DIAMOND_RESTORE_POSTGRES", "--runtime-env", "DIAMOND_RESTORE_RUNTIME", "--from", str(backup), "--backup-key-env", "DIA_BACKUP_KEY"],
        timeout=180,
    )
    restore_receipt = json.loads(completed.stdout)
    check("restore independently verifies every runtime root", restore_receipt["runtime_roots_verified"] == 4 and restore_receipt["runtime_entries_verified"] == backup_receipt["runtime_entries"], str(restore_receipt))
    restored_primary = next(path for path in restore_roots["git"].glob("*.git") if path.name.endswith(f"-{repository}.git"))
    restored_secondary = next((restore_roots["operations"] / "replicas" / "secondary").glob("*.git"))
    restored_primary_head = run(["git", "--git-dir", str(restored_primary), "rev-parse", "HEAD^{commit}"]).stdout.strip()
    restored_secondary_head = run(["git", "--git-dir", str(restored_secondary), "rev-parse", "HEAD^{commit}"]).stdout.strip()
    restored_fsck = run(["git", "--git-dir", str(restored_secondary), "fsck", "--strict", "--no-dangling"])
    check("isolated restore preserves primary and promoted-shard histories", restored_primary_head == commit and restored_secondary_head == routed_commit and restored_fsck.returncode == 0, restored_fsck.stderr)
    check("isolated restore preserves held delivery bytes and operations metadata", (restore_roots["delivery"] / "objects" / held).read_bytes() == held_bytes and (restore_roots["operations"] / ".diamond-operations-store").is_file())

    restore_port = free_port()
    restore_mail = TMP / "restore-mail"
    restore_mail.mkdir(mode=0o700)
    restore_log = open(TMP / "restore-server.log", "wb")
    logs.append(restore_log)
    restored_server = subprocess.Popen(
        [str(BIN), "serve", PROJECT, "--listen", f"127.0.0.1:{restore_port}", "--origin", f"http://127.0.0.1:{restore_port}", "--postgres-env", "DIAMOND_RESTORE_POSTGRES", "--runtime-env", "DIAMOND_RESTORE_RUNTIME", "--identity-mailbox", str(restore_mail)],
        cwd=ROOT,
        env=env,
        stdout=restore_log,
        stderr=subprocess.STDOUT,
    )
    servers.append(restored_server)
    restored_page = wait_server(restore_port)
    check("restored server exposes the same checked contract", contract in restored_page)
    old_url = f"http://diamond:{token}@127.0.0.1:{restore_port}/_diamond/git/repos/{repository}.git"
    revoked = run(["git", "push", old_url, f"{routed_commit}:refs/heads/revoked-probe"], cwd=clone, timeout=30)
    check("restore revokes pre-snapshot API credentials", revoked.returncode != 0)
    restored_alice = login(restore_port, "alice", passwords["alice"])
    status, _, reply = request(
        restore_port,
        "POST",
        "/_diamond/accounts/create_token",
        token_request,
        {"X-Diamond-CSRF": restored_alice["csrf"]},
        restored_alice["cookie"],
    )
    check("restored account can mint a fresh bounded credential", status == 200, str(reply))
    restored_token = reply["value"]
    restored_clone = TMP / "restored-clone"
    restored_url = f"http://diamond:{restored_token}@127.0.0.1:{restore_port}/_diamond/git/repos/{repository}.git"
    ok("clone through restored placement-routed server", ["git", "clone", restored_url, str(restored_clone)])
    check("restored data plane routes to promoted shard", run(["git", "rev-parse", "HEAD"], cwd=restored_clone).stdout.strip() == routed_commit)

    receipt = {
        "profile": "diamond-forge-g4-scale-recovery",
        "checks": len(results),
        "passed": sum(1 for result in results if result["pass"]),
        "contract": contract,
        "commit": commit,
        "worker_tasks": [receipt["task"] for receipt in worker_receipts],
        "results": results,
    }
    (ROOT / "verification/git_scale_recovery_redteam_results.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps({key: receipt[key] for key in ("profile", "checks", "passed", "contract", "commit")}, indent=2))
finally:
    for server in servers:
        if server.poll() is None:
            server.send_signal(signal.SIGINT)
    for server in servers:
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait()
    for log in logs:
        log.close()
    if PG.exists():
        subprocess.run(["pg_ctl", "-D", str(PG), "-m", "immediate", "stop"], env=env, capture_output=True)
    if "--keep" not in sys.argv:
        shutil.rmtree(TMP, ignore_errors=True)
