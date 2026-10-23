#!/usr/bin/env python3
"""Real-interface red-team replay for the Diamond Forge application slice."""

import http.client
import hashlib
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
TMP = pathlib.Path(tempfile.mkdtemp(prefix="diamond-git-application-"))
PG = TMP / "pg"
SOCK = TMP / "sock"
GITROOT = TMP / "git"
OBJECTS = TMP / "objects"
MAIL = TMP / "mail"
for directory in (SOCK, GITROOT, OBJECTS, MAIL):
    directory.mkdir(mode=0o700)


def free_port():
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    listener.close()
    return port


pgport = free_port()
webport = (
    int(sys.argv[sys.argv.index("--web-port") + 1])
    if "--web-port" in sys.argv
    else free_port()
)
pgcfg = f"host={SOCK} port={pgport} user=diamond_rt dbname=postgres"
runtime = {
    "objects": str(OBJECTS),
    "git": {"repos": {"root": str(GITROOT), "executable": "/usr/bin/git"}},
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
    "http": {},
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
server = None
server_log = None


def check(name, condition, detail=""):
    results.append({"name": name, "pass": bool(condition)})
    if not condition:
        for log_name in ("server.log", "postgres.log"):
            path = TMP / log_name
            if path.exists():
                print(f"--- {log_name} ---\n{path.read_text(errors='replace')[-12000:]}", file=sys.stderr)
        raise AssertionError(f"{name}: {detail}")


def run(arguments, cwd=ROOT, timeout=60, input=None):
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
    check(name, completed.returncode == 0, (completed.stdout + completed.stderr)[-2000:])
    return completed


def request(method, path, body=None, headers=None, cookie=None):
    connection = http.client.HTTPConnection("127.0.0.1", webport, timeout=20)
    request_headers = dict(headers or {})
    if cookie:
        request_headers["Cookie"] = cookie
    encoded = None
    if body is not None:
        encoded = json.dumps(body).encode()
        request_headers.setdefault("Content-Type", "application/json")
        request_headers.setdefault("Origin", f"http://127.0.0.1:{webport}")
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


def rpc(client, contract, name, arguments):
    return request(
        "POST",
        "/_diamond/rpc/" + name,
        {"contract": contract, "arguments": arguments},
        {"X-Diamond-CSRF": client["csrf"]},
        client["cookie"],
    )


def login(username, password):
    status, headers, session = request("GET", "/_diamond/session", headers={"X-Diamond-Session": "1"})
    check(f"bootstrap {username} session", status == 200, str(session))
    cookie = headers["set-cookie"].split(";", 1)[0]
    status, headers, reply = request(
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


def git_head(bare, reference="refs/heads/main"):
    completed = run(["git", "--git-dir", str(bare), "rev-parse", reference])
    return completed.stdout.strip() if completed.returncode == 0 else ""


try:
    ok(
        "initialize PostgreSQL",
        ["initdb", "-D", str(PG), "-A", "trust", "--no-locale", "-E", "UTF8", "-U", "diamond_rt", "--no-sync"],
    )
    ok(
        "start PostgreSQL",
        ["pg_ctl", "-D", str(PG), "-l", str(TMP / "postgres.log"), "-o", f"-k {SOCK} -p {pgport} -c listen_addresses=", "-w", "start"],
    )
    ok("initialize application schema", [str(BIN), "project-init", PROJECT, "--postgres-env", "DIAMOND_POSTGRES"])
    ok(
        "initialize shared object store",
        [str(BIN), "project-files-init", PROJECT, "--postgres-env", "DIAMOND_POSTGRES", "--runtime-env", "DIAMOND_RUNTIME"],
    )
    ok(
        "initialize Git store",
        [str(BIN), "project-git-init", PROJECT, "--postgres-env", "DIAMOND_POSTGRES", "--runtime-env", "DIAMOND_RUNTIME"],
    )
    passwords = {
        "alice": "Synthetic forge password 81!",
        "bob": "Synthetic forge password 82!",
        "charlie": "Synthetic forge password 83!",
    }
    ok(
        "provision administrator",
        [str(BIN), "project-admin-add", PROJECT, "--postgres-env", "DIAMOND_POSTGRES", "--user", "alice", "--email", "alice@example.test", "--password-stdin"],
        input=passwords["alice"] + "\n",
    )
    for user in ("bob", "charlie"):
        ok(
            f"provision {user}",
            [str(BIN), "project-user-add", PROJECT, "--postgres-env", "DIAMOND_POSTGRES", "--user", user, "--password-stdin"],
            input=passwords[user] + "\n",
        )

    server_log = open(TMP / "server.log", "wb")
    server = subprocess.Popen(
        [str(BIN), "serve", PROJECT, "--listen", f"127.0.0.1:{webport}", "--origin", f"http://127.0.0.1:{webport}", "--postgres-env", "DIAMOND_POSTGRES", "--runtime-env", "DIAMOND_RUNTIME", "--identity-mailbox", str(MAIL)],
        cwd=ROOT,
        env=env,
        stdout=server_log,
        stderr=subprocess.STDOUT,
    )
    for _ in range(300):
        try:
            status, _, page = request("GET", "/")
            if status == 200:
                break
        except Exception:
            pass
        time.sleep(0.05)
    else:
        raise AssertionError("server did not start")
    page_text = page.decode() if isinstance(page, bytes) else str(page)
    contract = re.search(r'data-contract="([0-9a-f]{64})"', page_text).group(1)
    check("server renders application navigation", all(label in page_text for label in ("Repositories", "Issues", "Merge requests", "Settings")))

    alice = login("alice", passwords["alice"])
    bob = login("bob", passwords["bob"])
    charlie = login("charlie", passwords["charlie"])

    status, _, reply = rpc(alice, contract, "create_organization", ["corvidae"])
    check("create organization", status == 200, str(reply))
    organization = value(reply)
    status, _, reply = rpc(alice, contract, "create_repository", [organization, "diamond", "Private forge target", False])
    check("create private repository", status == 200, str(reply))
    repository = value(reply)
    status, _, reply = rpc(alice, contract, "initialize_repository", [repository])
    check("initialize private Git repository", status == 200, str(reply))
    bare = next(GITROOT.glob("*.git"))

    status, _, reply = rpc(bob, contract, "repository_page", [organization, ""])
    check("nonmember cannot discover private repository", status == 200 and value(reply)["total"] == "0", str(reply))
    status, _, _ = rpc(bob, contract, "create_issue", [repository, "forbidden", "forbidden"])
    check("nonmember cannot create issue", status == 403, str(status))

    status, _, reply = rpc(alice, contract, "grant_developer", [organization, "bob"])
    check("grant developer", status == 200, str(reply))
    bob_membership = value(reply)
    status, _, reply = rpc(alice, contract, "grant_developer", [organization, "charlie"])
    check("grant second developer", status == 200, str(reply))
    charlie_membership = value(reply)
    status, _, reply = rpc(bob, contract, "repository_page", [organization, ""])
    check("member sees private repository", status == 200 and value(reply)["total"] == "1", str(reply))

    hostile = '<img src=x onerror=alert(1)> & "quoted" 雪'
    issue_ids = []
    for index in range(22):
        title = hostile if index == 0 else f"Generated issue {index:02d}"
        body = "literal <script>alert(2)</script> javascript:alert(3)" if index == 0 else f"Body {index}"
        status, _, reply = rpc(bob, contract, "create_issue", [repository, title, body])
        check(f"create bounded issue {index:02d}", status == 200, str(reply))
        issue_ids.append(value(reply))
    status, _, reply = rpc(bob, contract, "issue_page", [organization, repository, ""])
    first_page = value(reply)
    check("issue first page bounded", status == 200 and len(first_page["cards"]) == 20 and first_page["after"], str(reply))
    status, _, reply = rpc(bob, contract, "issue_page", [organization, repository, first_page["after"]])
    check("issue cursor reaches remaining rows", status == 200 and len(value(reply)["cards"]) == 2, str(reply))
    status, _, _ = rpc(bob, contract, "create_issue", [organization, repository, True, "visibility injection", "must reject"])
    check("caller cannot inject child visibility", status == 400, str(status))
    status, _, reply = rpc(bob, contract, "read_issue", [issue_ids[0]])
    check("hostile issue text remains literal", status == 200 and value(reply)["title"] == hostile and "<script>" in value(reply)["body"], str(reply))
    status, _, reply = rpc(bob, contract, "add_issue_comment", [issue_ids[0], "Comment <svg/onload=alert(4)> & café"])
    check("add issue comment", status == 200, str(reply))
    status, _, reply = rpc(alice, contract, "issue_comments", [organization, repository, issue_ids[0]])
    check("authorized collaborator reads literal comment", status == 200 and "<svg/onload" in value(reply)[0]["body"], str(reply))

    status, _, reply = rpc(alice, contract, "revoke_member", [bob_membership])
    check("revoke developer", status == 200, str(reply))
    status, _, _ = rpc(bob, contract, "read_issue", [issue_ids[0]])
    check("revocation immediately hides private issue", status == 403, str(status))
    status, _, reply = rpc(alice, contract, "grant_developer", [organization, "bob"])
    check("restore developer for merge flow", status == 200, str(reply))

    token_request = {
        "label": "forge-redteam",
        "operations": ["git:repos:read", "git:repos:write"],
        "scopes": {"repository": [repository]},
        "lifetime_seconds": "3600",
    }
    status, _, reply = request("POST", "/_diamond/accounts/create_token", token_request, {"X-Diamond-CSRF": alice["csrf"]}, alice["cookie"])
    check("create scoped Git credential", status == 200, str(reply))
    token = reply["value"]
    git_url = f"http://diamond:{token}@127.0.0.1:{webport}/_diamond/git/repos/{repository}.git"
    clone = TMP / "clone"
    ok("clone empty repository", ["git", "clone", git_url, str(clone)])
    ok("configure Git author email", ["git", "config", "user.email", "alice@example.test"], cwd=clone)
    ok("configure Git author name", ["git", "config", "user.name", "Alice"], cwd=clone)
    (clone / "README.md").write_text("main\n")
    ok("stage main", ["git", "add", "README.md"], cwd=clone)
    ok("commit main", ["git", "commit", "-m", "main"], cwd=clone)
    ok("push main", ["git", "push", git_url, "HEAD:refs/heads/main"], cwd=clone)
    ok("create feature branch", ["git", "switch", "-c", "feature"], cwd=clone)
    (clone / "feature.txt").write_text("reviewed feature\n")
    ok("stage feature", ["git", "add", "feature.txt"], cwd=clone)
    ok("commit feature", ["git", "commit", "-m", "feature"], cwd=clone)
    ok("push feature", ["git", "push", git_url, "HEAD:refs/heads/feature"], cwd=clone)
    original_main = git_head(bare)
    feature_head = git_head(bare, "refs/heads/feature")

    ok("create unprotected recovery target", ["git", "push", git_url, f"{original_main}:refs/heads/recovery"], cwd=clone)
    status, _, reply = rpc(bob, contract, "create_merge_request", [repository, "Unapproved recovery probe", "must remain open", "refs/heads/feature", "refs/heads/recovery"])
    check("open recovery-finalization probe", status == 200, str(reply))
    recovery_probe = value(reply)
    ok("move recovery target outside merge flow", ["git", "push", git_url, f"{feature_head}:refs/heads/recovery"], cwd=clone)
    status, _, _ = rpc(alice, contract, "finalize_merge_request", [recovery_probe])
    check("ref movement cannot finalize unapproved merge request", status >= 400, str(status))

    status, _, reply = rpc(alice, contract, "protect_reference", [repository, "refs/heads/main", "authenticated", False, False, "1", False, True, False, False])
    check("protect main with approval and discussion gate", status == 200, str(reply))
    status, _, reply = rpc(bob, contract, "create_merge_request", [repository, "Merge feature", hostile, "refs/heads/feature", "refs/heads/main"])
    check("open merge request", status == 200, str(reply))
    merge_request = value(reply)
    status, _, _ = rpc(bob, contract, "approve_merge_request", [merge_request])
    check("author cannot self-approve", status == 403, str(status))
    status, _, reply = rpc(alice, contract, "approve_merge_request", [merge_request])
    check("non-author approval records exact review gate", status == 200 and value(reply)["approvals"] == "1", str(reply))
    status, _, _ = rpc(charlie, contract, "approve_merge_request", [merge_request])
    check("second approval remains bounded and unique by reviewer", status == 200, str(status))
    status, _, _ = rpc(alice, contract, "approve_merge_request", [merge_request])
    check("duplicate reviewer approval rejected", status in (403, 409), str(status))
    status, _, reply = rpc(alice, contract, "prepare_merge_request", [merge_request])
    check("prepare exact merge plan", status == 200 and value(reply)["source_object"] == feature_head, str(reply))
    plan = value(reply)
    status, _, _ = rpc(alice, contract, "execute_merge", [plan["repository"], plan["source_reference"], plan["target_reference"], plan["source_object"], plan["target_object"]])
    check("unresolved discussion blocks native merge", status == 403 and git_head(bare) == original_main, str(status))
    status, _, reply = rpc(alice, contract, "resolve_merge_discussions", [merge_request])
    check("resolve discussions updates exact native gate", status == 200, str(reply))
    status, _, _ = rpc(alice, contract, "execute_merge", [repository, "refs/heads/feature", "refs/heads/main", "0" * 40, original_main])
    check("forged candidate object rejected", status >= 400 and git_head(bare) == original_main, str(status))
    status, _, reply = rpc(alice, contract, "execute_merge", [plan["repository"], plan["source_reference"], plan["target_reference"], plan["source_object"], plan["target_object"]])
    check("approved exact-CAS merge succeeds", status == 200 and value(reply) == feature_head and git_head(bare) == feature_head, str(reply))
    status, _, reply = rpc(alice, contract, "finalize_merge_request", [merge_request])
    check("authoritative target finalizes merge request", status == 200 and value(reply)["state"] == "merged", str(reply))
    status, _, _ = rpc(alice, contract, "close_merge_request", [merge_request])
    check("merged request cannot transition to closed", status >= 400, str(status))
    status, _, _ = rpc(charlie, contract, "approve_merge_request", [merge_request])
    check("merged request cannot receive another approval", status >= 400, str(status))
    status, _, _ = rpc(alice, contract, "execute_merge", [plan["repository"], plan["source_reference"], plan["target_reference"], plan["source_object"], plan["target_object"]])
    check("merge replay rejected without moving target", status == 409 and git_head(bare) == feature_head, str(status))

    ok("create stale branch", ["git", "switch", "-c", "stale", "main"], cwd=clone)
    (clone / "stale.txt").write_text("first\n")
    ok("stage stale branch", ["git", "add", "stale.txt"], cwd=clone)
    ok("commit stale branch", ["git", "commit", "-m", "stale-one"], cwd=clone)
    ok("push stale branch", ["git", "push", git_url, "HEAD:refs/heads/stale"], cwd=clone)
    status, _, reply = rpc(bob, contract, "create_merge_request", [repository, "Stale head", "must abort", "refs/heads/stale", "refs/heads/main"])
    check("open stale-head probe", status == 200, str(reply))
    stale_mr = value(reply)
    status, _, _ = rpc(alice, contract, "approve_merge_request", [stale_mr])
    check("approve stale-head probe", status == 200, str(status))
    status, _, _ = rpc(alice, contract, "resolve_merge_discussions", [stale_mr])
    check("resolve stale-head probe", status == 200, str(status))
    (clone / "stale.txt").write_text("second\n")
    ok("commit source movement", ["git", "commit", "-am", "stale-two"], cwd=clone)
    ok("push source movement", ["git", "push", git_url, "HEAD:refs/heads/stale"], cwd=clone)
    before_stale = git_head(bare)
    status, _, _ = rpc(alice, contract, "prepare_merge_request", [stale_mr])
    check("changed source invalidates reviewed merge", status >= 400 and git_head(bare) == before_stale, str(status))

    status, _, events = rpc(alice, contract, "pending_push_events", ["100"])
    check("web merge emits durable user push event", status == 200 and feature_head in value(events) and '"actor_kind":"user"' in value(events), str(events))
    spool = GITROOT / ".diamond-events"
    committed_spools = list(spool.glob("*.committed"))
    check("web merge leaves recovery spool until reconciliation", bool(committed_spools))
    merge_spool = None
    merge_event = None
    for candidate in committed_spools:
        event = json.loads(candidate.read_text())
        if any(update["reference"] == "refs/heads/main" and update["new"] == feature_head for update in event["updates"]):
            merge_spool = candidate
            merge_event = event
            break
    check("locate exact web-merge recovery event", merge_spool is not None and merge_event is not None)
    merge_nonce = merge_event["claim"]["nonce"]
    subprocess.run(
        ["psql", pgcfg, "-v", "ON_ERROR_STOP=1", "-c", f"DELETE FROM diamond_git.push_events WHERE nonce='{merge_nonce}'"],
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
        check=True,
    )
    merge_spool.rename(spool / f"{merge_nonce}.prepared")
    abandoned_event = json.loads(json.dumps(merge_event))
    abandoned_nonce = hashlib.sha256(b"diamond-abandoned-merge-spool").hexdigest()
    abandoned_event["claim"]["nonce"] = abandoned_nonce
    for update in abandoned_event["updates"]:
        update["old"], update["new"] = update["new"], update["old"]
    abandoned_path = spool / f"{abandoned_nonce}.prepared"
    descriptor = os.open(abandoned_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as output:
        json.dump(abandoned_event, output, separators=(",", ":"))
    check("stage committed and abandoned prepared-spool faults", len(list(spool.glob("*.prepared"))) == 2)
    if server is not None:
        server.send_signal(signal.SIGTERM)
        server.wait(timeout=20)
        server = None
    if server_log is not None:
        server_log.close()
        server_log = None
    reconciled = ok(
        "reconcile web merge event spool",
        [str(BIN), "project-git-reconcile", PROJECT, "--postgres-env", "DIAMOND_POSTGRES", "--runtime-env", "DIAMOND_RUNTIME"],
    )
    reconciliation = json.loads(reconciled.stdout)
    check("reconciliation distinguishes committed and abandoned prepared events", reconciliation["prepared"] == 2 and reconciliation["committed"] >= 1, reconciled.stdout)
    check("reconciliation clears committed spool", not any((GITROOT / ".diamond-events").glob("*.committed")))
    check("reconciliation clears prepared spool", not any((GITROOT / ".diamond-events").glob("*.prepared")))
    ok("repository passes strict fsck", ["git", "--git-dir", str(bare), "fsck", "--strict", "--no-dangling"])

    if "--serve-fixture" in sys.argv:
        server_log = open(TMP / "server.log", "ab")
        server = subprocess.Popen(
            [str(BIN), "serve", PROJECT, "--listen", f"127.0.0.1:{webport}", "--origin", f"http://127.0.0.1:{webport}", "--postgres-env", "DIAMOND_POSTGRES", "--runtime-env", "DIAMOND_RUNTIME", "--identity-mailbox", str(MAIL)],
            cwd=ROOT,
            env=env,
            stdout=server_log,
            stderr=subprocess.STDOUT,
        )
        for _ in range(300):
            try:
                status, _, _ = request("GET", "/")
                if status == 200:
                    break
            except Exception:
                pass
            time.sleep(0.05)
        else:
            raise AssertionError("browser fixture did not restart")
        print(json.dumps({"browser_fixture": f"http://127.0.0.1:{webport}/", "username": "alice", "password": passwords["alice"]}), flush=True)
        while True:
            time.sleep(1)

finally:
    if server is not None:
        server.send_signal(signal.SIGTERM)
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
    if server_log is not None:
        server_log.close()
    if PG.exists():
        subprocess.run(["pg_ctl", "-D", str(PG), "-m", "immediate", "stop"], env=env, capture_output=True)
    receipt = {
        "profile": "diamond-forge-g1-application",
        "passed": sum(1 for result in results if result["pass"]),
        "total": len(results),
        "results": results,
    }
    (ROOT / "verification/git_application_redteam_results.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt, indent=2))
    shutil.rmtree(TMP, ignore_errors=True)
