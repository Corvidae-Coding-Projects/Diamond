#!/usr/bin/env python3
"""Real-interface red-team replay for the Diamond Forge application slice."""

import http.client
import http.server
import base64
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
import threading
import time
import urllib.parse

ROOT = pathlib.Path(__file__).resolve().parents[1]
BIN = ROOT / "target/debug/diamond"
PROJECT = "examples/git-browser/diamond.toml"
TMP = pathlib.Path(tempfile.mkdtemp(prefix="diamond-git-application-"))
PG = TMP / "pg"
SOCK = TMP / "sock"
GITROOT = TMP / "git"
OBJECTS = TMP / "objects"
DELIVERY = TMP / "delivery"
MAIL = TMP / "mail"
for directory in (SOCK, GITROOT, OBJECTS, DELIVERY, MAIL):
    directory.mkdir(mode=0o700)


def free_port():
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    listener.close()
    return port


pgport = free_port()
hookport = free_port()
webport = (
    int(sys.argv[sys.argv.index("--web-port") + 1])
    if "--web-port" in sys.argv
    else free_port()
)
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
            "origin": f"http://127.0.0.1:{hookport}",
            "network": "loopback",
            "paths": ["/hooks/"],
            "methods": ["POST"],
            "credential_env": "DIA_WEBHOOK_SECRET",
            "timeout_ms": 3000,
            "max_response_bytes": 65536,
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
    DIA_WEBHOOK_SECRET="synthetic-webhook-secret",
    GIT_TERMINAL_PROMPT="0",
    GIT_CONFIG_COUNT="1",
    GIT_CONFIG_KEY_0="credential.helper",
    GIT_CONFIG_VALUE_0="",
)
results = []
server = None
server_log = None
webhook_deliveries = []


class WebhookHandler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        webhook_deliveries.append({
            "path": self.path,
            "authorization": self.headers.get("Authorization", ""),
            "body": body.decode(errors="replace"),
        })
        encoded = json.dumps("accepted").encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, *_args):
        pass


webhook_server = http.server.ThreadingHTTPServer(("127.0.0.1", hookport), WebhookHandler)
webhook_thread = threading.Thread(target=webhook_server.serve_forever, daemon=True)
webhook_thread.start()


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


def raw_request(method, path, body=None, headers=None):
    connection = http.client.HTTPConnection("127.0.0.1", webport, timeout=30)
    connection.request(method, path, body=body, headers=dict(headers or {}))
    response = connection.getresponse()
    payload = response.read()
    response_headers = {key.lower(): value for key, value in response.getheaders()}
    connection.close()
    return response.status, response_headers, payload


def variant(name):
    return {"tag": name, "fields": []}


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
    ok(
        "initialize delivery store",
        [str(BIN), "project-delivery-init", PROJECT, "--postgres-env", "DIAMOND_POSTGRES", "--runtime-env", "DIAMOND_RUNTIME"],
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
    check("legacy receipt-free finalization endpoint is absent", status == 404, str(status))

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
    check("unresolved discussion blocks merge preparation", status >= 400, str(reply))
    status, _, _ = rpc(alice, contract, "merge_approved_request", [merge_request, ""])
    check("unresolved discussion blocks native merge", status >= 400 and git_head(bare) == original_main, str(status))
    status, _, reply = rpc(alice, contract, "resolve_merge_discussions", [merge_request])
    check("resolve discussions updates exact native gate", status == 200, str(reply))
    status, _, reply = rpc(alice, contract, "prepare_merge_request", [merge_request])
    check("prepare exact merge plan", status == 200 and value(reply)["source_object"] == feature_head, str(reply))
    status, _, _ = rpc(alice, contract, "execute_merge", [repository, "refs/heads/feature", "refs/heads/main", feature_head, original_main])
    check("raw client-supplied merge primitive is absent", status == 404 and git_head(bare) == original_main, str(status))
    status, _, reply = rpc(alice, contract, "merge_approved_request", [merge_request, ""])
    check("approved exact-CAS merge succeeds", status == 200 and value(reply) == feature_head and git_head(bare) == feature_head, str(reply))
    status, _, reply = rpc(alice, contract, "finalize_approved_request", [merge_request, feature_head])
    check("native receipt finalizes exact merge request", status == 200 and value(reply)["state"] == "merged", str(reply))
    status, _, _ = rpc(alice, contract, "close_merge_request", [merge_request])
    check("merged request cannot transition to closed", status >= 400, str(status))
    status, _, _ = rpc(charlie, contract, "approve_merge_request", [merge_request])
    check("merged request cannot receive another approval", status >= 400, str(status))
    status, _, _ = rpc(alice, contract, "merge_approved_request", [merge_request, ""])
    check("merge replay rejected without moving target", status >= 400 and git_head(bare) == feature_head, str(status))

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

    # G2 repository-scoped teams and direct grants must grant exactly their role and revoke immediately.
    status, _, reply = rpc(alice, contract, "revoke_member", [charlie_membership])
    check("remove organization-wide access before scoped permission tests", status == 200, str(reply))
    status, _, reply = rpc(alice, contract, "create_team", [organization, "platform"])
    check("create organization team", status == 200, str(reply))
    team = value(reply)
    status, _, reply = rpc(alice, contract, "add_team_member", [team, repository, "charlie", variant("Developer")])
    check("grant repository access through team", status == 200, str(reply))
    team_member = value(reply)
    status, _, reply = rpc(charlie, contract, "repository_page", [organization, ""])
    check("team developer discovers private repository", status == 200 and value(reply)["total"] == "1", str(reply))
    status, _, reply = rpc(charlie, contract, "create_issue", [repository, "Team access", "repository scoped"])
    check("team developer contributes issue", status == 200, str(reply))
    status, _, reply = rpc(alice, contract, "revoke_team_member", [team_member])
    check("revoke team repository access", status == 200, str(reply))
    status, _, reply = rpc(charlie, contract, "repository_page", [organization, ""])
    check("team revocation immediately hides private repository", status == 200 and value(reply)["total"] == "0", str(reply))
    status, _, reply = rpc(alice, contract, "grant_repository_permission", [repository, "charlie", variant("Reporter")])
    check("grant direct reporter permission", status == 200, str(reply))
    direct_grant = value(reply)
    status, _, reply = rpc(charlie, contract, "repository_page", [organization, ""])
    check("direct reporter reads private repository", status == 200 and value(reply)["total"] == "1", str(reply))
    status, _, _ = rpc(charlie, contract, "create_issue", [repository, "Reporter write", "must fail"])
    check("reporter cannot create issue", status == 403, str(status))
    status, _, _ = rpc(alice, contract, "revoke_repository_permission", [direct_grant])
    check("revoke direct repository permission", status == 200, str(status))
    status, _, reply = rpc(alice, contract, "grant_developer", [organization, "charlie"])
    check("restore second developer after scoped permission tests", status == 200, str(reply))

    # Fork metadata and the native bare-repository copy must agree.
    status, _, reply = rpc(alice, contract, "create_fork_metadata", [repository, organization, "diamond-fork", "Forked in Diamond", False])
    check("create fork metadata", status == 200, str(reply))
    fork = value(reply)
    status, _, reply = rpc(alice, contract, "initialize_fork", [fork])
    check("copy authorized bare repository for fork", status == 200, str(reply))
    status, _, reply = rpc(alice, contract, "collaboration_dashboard", [organization, repository])
    dashboard = value(reply)
    fork_repository = next(card["repository"] for card in dashboard["forks"] if card["id"] == fork)
    fork_bare = next(path for path in GITROOT.glob("*.git") if fork_repository in path.name)
    check("fork preserves main object without hard links", git_head(fork_bare) == git_head(bare) and fork_bare.stat().st_ino != bare.stat().st_ino)

    # Issue metadata, boards, notifications, wiki, and snippets remain repository-bound.
    status, _, reply = rpc(alice, contract, "create_milestone", [repository, "G2", "2026-12-31"])
    check("create milestone", status == 200, str(reply))
    status, _, reply = rpc(alice, contract, "create_label", [repository, "security", "b91c1c"])
    check("create label", status == 200, str(reply))
    label = value(reply)
    status, _, _ = rpc(bob, contract, "label_issue", [issue_ids[0], label])
    check("attach repository label to issue", status == 200, str(status))
    status, _, _ = rpc(bob, contract, "assign_issue", [issue_ids[0], "charlie"])
    check("assign issue and emit notification", status == 200, str(status))
    status, _, reply = rpc(alice, contract, "create_board_column", [repository, "Backlog"])
    check("create first board column", status == 200, str(reply))
    backlog = value(reply)
    status, _, reply = rpc(alice, contract, "create_board_column", [repository, "Done"])
    check("create second board column", status == 200, str(reply))
    done = value(reply)
    status, _, reply = rpc(bob, contract, "add_board_card", [issue_ids[0], backlog])
    check("add issue to board", status == 200, str(reply))
    board_card = value(reply)
    status, _, _ = rpc(bob, contract, "move_board_card", [board_card, done])
    check("move board card", status == 200, str(status))
    status, _, reply = rpc(alice, contract, "create_wiki_page", [repository, "security", "Security", hostile])
    check("create wiki page with hostile literal text", status == 200, str(reply))
    wiki = value(reply)
    status, _, _ = rpc(alice, contract, "update_wiki_page", [wiki, "Security revised", hostile + " revision"])
    check("create next wiki revision", status == 200, str(status))
    status, _, _ = rpc(bob, contract, "create_snippet", [repository, "Reproduction", "probe.sh", hostile])
    check("create repository snippet", status == 200, str(status))
    status, _, reply = rpc(charlie, contract, "collaboration_dashboard", [organization, repository])
    charlie_dashboard = value(reply)
    check("notifications are recipient-visible", status == 200 and any(card["kind"] == "issue.assigned" for card in charlie_dashboard["notifications"]), str(reply))
    check("hostile wiki and snippet content remains data", any("<img" in card["body"] for card in charlie_dashboard["wiki"]) and any("<img" in card["body"] for card in charlie_dashboard["snippets"]))

    # A label from the parent repository cannot cross the fork's composite foreign-key boundary.
    status, _, reply = rpc(bob, contract, "create_issue", [fork_repository, "Fork issue", "cross-scope probe"])
    check("create issue in fork", status == 200, str(reply))
    fork_issue = value(reply)
    status, _, _ = rpc(bob, contract, "label_issue", [fork_issue, label])
    check("cross-repository label attachment rejected", status >= 400, str(status))

    # Squash integration: a new thread revokes the native gate until the exact thread resolves.
    ok("refresh integrated main", ["git", "fetch", git_url, "refs/heads/main"], cwd=clone)
    ok("create squash branch", ["git", "switch", "-C", "squash", "FETCH_HEAD"], cwd=clone)
    (clone / "squash.txt").write_text("squashed\n")
    ok("stage squash branch", ["git", "add", "squash.txt"], cwd=clone)
    ok("commit squash branch", ["git", "commit", "-m", "squash source"], cwd=clone)
    ok("push squash branch", ["git", "push", git_url, "HEAD:refs/heads/squash"], cwd=clone)
    squash_source = git_head(bare, "refs/heads/squash")
    squash_target = git_head(bare)
    status, _, reply = rpc(bob, contract, "create_merge_request_strategy", [repository, "Squash feature", "receipt-bound", "refs/heads/squash", "refs/heads/main", variant("Squash")])
    check("open squash merge request", status == 200, str(reply))
    squash_mr = value(reply)
    status, _, _ = rpc(alice, contract, "approve_merge_request", [squash_mr])
    check("approve squash merge request", status == 200, str(status))
    status, _, reply = rpc(alice, contract, "add_review_thread", [squash_mr, "Resolve this exact thread"])
    check("start review thread and revoke resolved gate", status == 200, str(reply))
    thread = value(reply)
    status, _, _ = rpc(alice, contract, "resolve_merge_discussions", [squash_mr])
    check("generic discussion resolution cannot bypass open thread", status >= 400, str(status))
    denied_push = run(["git", "push", git_url, "refs/heads/squash:refs/heads/main"], cwd=clone)
    check("new review thread revokes native protected-ref authorization", denied_push.returncode != 0 and git_head(bare) == squash_target, denied_push.stderr)
    status, _, _ = rpc(alice, contract, "resolve_review_thread", [thread])
    check("resolve exact review thread", status == 200, str(status))
    status, _, reply = rpc(alice, contract, "merge_approved_request", [squash_mr, "Squash feature through Diamond"])
    check("execute receipt-bound squash integration", status == 200, str(reply))
    squash_result = value(reply)
    status, _, reply = rpc(alice, contract, "finalize_approved_request", [squash_mr, squash_result])
    check("finalize squash only with matching native receipt", status == 200 and value(reply)["state"] == "merged", str(reply))
    squash_parents = run(["git", "--git-dir", str(bare), "show", "-s", "--format=%P", squash_result]).stdout.strip().split()
    squash_tree = run(["git", "--git-dir", str(bare), "show", "-s", "--format=%T", squash_result]).stdout.strip()
    source_tree = run(["git", "--git-dir", str(bare), "show", "-s", "--format=%T", squash_source]).stdout.strip()
    check("squash result has target parent and source tree", squash_result != squash_source and squash_parents == [squash_target] and squash_tree == source_tree)
    status, _, _ = rpc(alice, contract, "finalize_approved_request", [squash_mr, "0" * 40])
    check("forged integration receipt cannot finalize", status >= 400, str(status))

    # Approval dismissal must revoke the native gate, and merge-commit must preserve two parents.
    ok("refresh squashed main", ["git", "fetch", git_url, "refs/heads/main"], cwd=clone)
    ok("create merge-commit branch", ["git", "switch", "-C", "merge-commit", "FETCH_HEAD"], cwd=clone)
    (clone / "merge-commit.txt").write_text("two parents\n")
    ok("stage merge-commit branch", ["git", "add", "merge-commit.txt"], cwd=clone)
    ok("commit merge-commit branch", ["git", "commit", "-m", "merge commit source"], cwd=clone)
    ok("push merge-commit branch", ["git", "push", git_url, "HEAD:refs/heads/merge-commit"], cwd=clone)
    merge_source = git_head(bare, "refs/heads/merge-commit")
    merge_target = git_head(bare)
    status, _, reply = rpc(bob, contract, "create_merge_request_strategy", [repository, "Merge commit feature", "revocable approval", "refs/heads/merge-commit", "refs/heads/main", variant("MergeCommit")])
    check("open merge-commit request", status == 200, str(reply))
    merge_commit_mr = value(reply)
    status, _, _ = rpc(alice, contract, "approve_merge_request", [merge_commit_mr])
    check("approve merge-commit request", status == 200, str(status))
    status, _, _ = rpc(alice, contract, "resolve_merge_discussions", [merge_commit_mr])
    check("resolve thread-free merge request", status == 200, str(status))
    status, _, reply = rpc(alice, contract, "collaboration_dashboard", [organization, repository])
    review_id = next(card["id"] for card in value(reply)["reviews"] if card["merge_request"] == merge_commit_mr and card["reviewer"] == "alice")
    status, _, _ = rpc(alice, contract, "dismiss_approval", [review_id])
    check("dismiss recorded approval", status == 200, str(status))
    denied_push = run(["git", "push", git_url, "refs/heads/merge-commit:refs/heads/main"], cwd=clone)
    check("approval dismissal revokes native protected-ref gate", denied_push.returncode != 0 and git_head(bare) == merge_target, denied_push.stderr)
    status, _, _ = rpc(charlie, contract, "approve_merge_request", [merge_commit_mr])
    check("independent reviewer restores exact approval gate", status == 200, str(status))
    status, _, reply = rpc(alice, contract, "merge_approved_request", [merge_commit_mr, "Merge feature through Diamond"])
    check("execute receipt-bound merge commit", status == 200, str(reply))
    merge_result = value(reply)
    status, _, reply = rpc(alice, contract, "finalize_approved_request", [merge_commit_mr, merge_result])
    check("finalize merge commit", status == 200 and value(reply)["state"] == "merged", str(reply))
    merge_parents = run(["git", "--git-dir", str(bare), "show", "-s", "--format=%P", merge_result]).stdout.strip().split()
    check("merge-commit result records target and source parents", merge_parents == [merge_target, merge_source], str(merge_parents))

    # Releases, webhooks, REST credentials, and audit feed.
    status, _, reply = rpc(alice, contract, "create_release", [repository, "refs/heads/main", "G2 preview", hostile, False])
    check("create release from resolvable ref", status == 200, str(reply))
    status, _, reply = rpc(alice, contract, "create_webhook", [repository, "/hooks/diamond"])
    check("create allowlisted webhook", status == 200, str(reply))
    webhook = value(reply)
    status, _, reply = rpc(alice, contract, "deliver_webhook", [webhook, "event-1", hostile])
    check("deliver bounded outbound webhook", status == 200 and value(reply) == "accepted", str(reply))
    check("webhook uses configured bearer secret and literal payload", len(webhook_deliveries) == 1 and webhook_deliveries[0]["authorization"] == "Bearer synthetic-webhook-secret" and "<img src=x onerror=alert(1)>" in webhook_deliveries[0]["body"])
    status, _, reply = rpc(alice, contract, "create_webhook", [repository, "http://attacker.invalid/"])
    check("store hostile webhook probe without contacting it", status == 200 and len(webhook_deliveries) == 1, str(status))
    status, _, _ = rpc(alice, contract, "deliver_webhook", [value(reply), "escape", "{}"])
    check("absolute webhook URL cannot escape runtime destination", status >= 400 and len(webhook_deliveries) == 1, str(status))
    api_request = {
        "label": "g2-api",
        "operations": ["collaboration_dashboard", "create_issue"],
        "scopes": {"organization": [organization], "repository": [repository]},
        "lifetime_seconds": "3600",
    }
    status, _, reply = request("POST", "/_diamond/accounts/create_token", api_request, {"X-Diamond-CSRF": alice["csrf"]}, alice["cookie"])
    check("create scope-limited REST credential", status == 200, str(reply))
    api_token = reply["value"]
    status, _, reply = request("POST", "/api/v1/collaboration_dashboard", {"arguments": [organization, repository]}, {"Authorization": f"Bearer {api_token}", "X-Diamond-API-Version": "1"})
    check("versioned REST dashboard succeeds", status == 200 and reply["api_version"] == 1, str(reply))
    status, _, _ = request("POST", "/api/v1/create_release", {"arguments": [repository, "refs/heads/main", "forbidden", "", False]}, {"Authorization": f"Bearer {api_token}", "X-Diamond-API-Version": "1"})
    check("REST credential cannot exceed operation allowlist", status == 403, str(status))
    status, _, reply = rpc(alice, contract, "collaboration_dashboard", [organization, repository])
    check("maintainer audit feed records administrative actions", status == 200 and {"team.permission.granted", "repository.permission.granted", "release.created", "webhook.created"}.issubset({card["action"] for card in value(reply)["audit"]}), str(reply))

    # Git LFS basic transfer, integrity rejection, private authorization, and owner-bounded locks.
    lfs_base = f"/_diamond/git/repos/{repository}.git/info/lfs"
    lfs_headers = {
        "Authorization": "Basic " + base64.b64encode(f"diamond:{token}".encode()).decode(),
        "Content-Type": "application/vnd.git-lfs+json",
        "Accept": "application/vnd.git-lfs+json",
    }
    lfs_data = (b"Diamond G2 LFS\n" * 9000) + b"end"
    lfs_oid = hashlib.sha256(lfs_data).hexdigest()
    batch_body = json.dumps({"operation": "upload", "transfers": ["basic"], "hash_algo": "sha256", "objects": [{"oid": lfs_oid, "size": len(lfs_data)}]}).encode()
    status, _, payload = raw_request("POST", lfs_base + "/objects/batch", batch_body, lfs_headers)
    batch_reply = json.loads(payload)
    check("LFS upload batch returns bounded basic action", status == 200 and batch_reply["transfer"] == "basic" and "expires_in" not in batch_reply["objects"][0]["actions"]["upload"], payload[-1000:])
    upload_path = urllib.parse.urlsplit(batch_reply["objects"][0]["actions"]["upload"]["href"]).path
    status, _, _ = raw_request("PUT", upload_path, lfs_data, {"Authorization": lfs_headers["Authorization"], "Content-Type": "application/octet-stream", "Content-Length": str(len(lfs_data))})
    check("stream LFS object with digest verification", status == 200, str(status))
    download_body = json.dumps({"operation": "download", "objects": [{"oid": lfs_oid, "size": len(lfs_data)}]}).encode()
    status, _, payload = raw_request("POST", lfs_base + "/objects/batch", download_body, lfs_headers)
    download_path = urllib.parse.urlsplit(json.loads(payload)["objects"][0]["actions"]["download"]["href"]).path
    status, _, downloaded = raw_request("GET", download_path, None, {"Authorization": lfs_headers["Authorization"]})
    check("download exact LFS object", status == 200 and downloaded == lfs_data, str(status))
    wrong = b"wrong-object"
    wrong_oid = hashlib.sha256(b"right-object").hexdigest()
    status, _, _ = raw_request("PUT", lfs_base + "/objects/" + wrong_oid, wrong, {"Authorization": lfs_headers["Authorization"], "Content-Type": "application/octet-stream", "Content-Length": str(len(wrong))})
    check("LFS digest mismatch rejected", status == 400, str(status))
    status, _, _ = raw_request("POST", lfs_base + "/objects/batch", batch_body, {"Content-Type": "application/vnd.git-lfs+json"})
    check("private LFS upload requires credential", status == 401, str(status))
    lock_body = json.dumps({"path": "assets/model.bin", "ref": {"name": "refs/heads/main"}}).encode()
    status, _, payload = raw_request("POST", lfs_base + "/locks", lock_body, lfs_headers)
    lock = json.loads(payload)["lock"]
    check("create owner-bound LFS lock", status == 200 and lock["owner"]["name"] == "alice", payload)
    bob_token_request = {"label": "g2-bob-git", "operations": ["git:repos:read", "git:repos:write"], "scopes": {"repository": [repository]}, "lifetime_seconds": "3600"}
    status, _, reply = request("POST", "/_diamond/accounts/create_token", bob_token_request, {"X-Diamond-CSRF": bob["csrf"]}, bob["cookie"])
    check("create second writer credential for lock attack", status == 200, str(reply))
    bob_lfs_headers = dict(lfs_headers)
    bob_lfs_headers["Authorization"] = "Basic " + base64.b64encode(f"diamond:{reply['value']}".encode()).decode()
    status, _, _ = raw_request("POST", lfs_base + f"/locks/{lock['id']}/unlock", json.dumps({"force": True}).encode(), bob_lfs_headers)
    check("repository writer cannot force-unlock another owner's LFS lock", status == 403, str(status))
    status, _, _ = raw_request("POST", lfs_base + f"/locks/{lock['id']}/unlock", json.dumps({"force": False}).encode(), lfs_headers)
    check("lock owner can unlock", status == 200, str(status))

    status, _, events = rpc(alice, contract, "pending_push_events", ["100"])
    check("web merge emits durable user push event", status == 200 and merge_result in value(events) and '"actor_kind":"user"' in value(events), str(events))
    spool = GITROOT / ".diamond-events"
    committed_spools = list(spool.glob("*.committed"))
    check("web merge leaves recovery spool until reconciliation", bool(committed_spools))
    merge_spool = None
    merge_event = None
    for candidate in committed_spools:
        event = json.loads(candidate.read_text())
        if any(update["reference"] == "refs/heads/main" and update["new"] == merge_result for update in event["updates"]):
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
    webhook_server.shutdown()
    webhook_server.server_close()
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
        "profile": "diamond-forge-g2-collaboration",
        "passed": sum(1 for result in results if result["pass"]),
        "total": len(results),
        "results": results,
    }
    encoded_receipt = json.dumps(receipt, indent=2) + "\n"
    (ROOT / "verification/git_application_redteam_results.json").write_text(encoded_receipt)
    (ROOT / "verification/git_collaboration_redteam_results.json").write_text(encoded_receipt)
    print(json.dumps(receipt, indent=2))
    shutil.rmtree(TMP, ignore_errors=True)
