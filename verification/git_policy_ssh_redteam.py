#!/usr/bin/env python3
"""Real-interface adversarial replay for protected refs and the OpenSSH adapter."""

import base64
import http.client
import json
import os
import pathlib
import pwd
import re
import signal
import shlex
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
BIN = ROOT / "target/debug/diamond"
TMP = pathlib.Path(tempfile.mkdtemp(prefix="diamond-git-policy-ssh-"))
PG = TMP / "pg"
SOCK = TMP / "sock"
GITROOT = TMP / "git"
OBJECTS = TMP / "objects"
DELIVERY = TMP / "delivery"
OPERATIONS = TMP / "operations"
MAIL = TMP / "mail"
for directory in (SOCK, GITROOT, OBJECTS, DELIVERY, OPERATIONS, MAIL):
    directory.mkdir(mode=0o700)


def free_port():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


pgport, webport, sshport = free_port(), free_port(), free_port()
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
    DIA_GOOGLE_SECRET="google-test-secret",
    DIA_GITHUB_SECRET="github-test-secret",
    GIT_TERMINAL_PROMPT="0",
    GIT_CONFIG_COUNT="1",
    GIT_CONFIG_KEY_0="credential.helper",
    GIT_CONFIG_VALUE_0="",
)
results = []
server = None
sshd = None


def check(name, condition, detail=""):
    results.append({"name": name, "pass": bool(condition)})
    if not condition:
        for log_name in ("server.log", "postgres.log", "sshd.log"):
            log = TMP / log_name
            if log.exists():
                print(
                    f"--- {log_name} ---\n{log.read_text(errors='replace')[-16000:]}",
                    file=sys.stderr,
                )
        raise AssertionError(f"{name}: {detail}")


def run(arguments, cwd=ROOT, timeout=60, input=None, extra_env=None):
    call_env = env.copy()
    if extra_env:
        call_env.update(extra_env)
    return subprocess.run(
        arguments,
        cwd=cwd,
        env=call_env,
        text=True,
        input=input,
        capture_output=True,
        timeout=timeout,
    )


def ok(name, arguments, **kwargs):
    result = run(arguments, **kwargs)
    check(name, result.returncode == 0, (result.stdout + result.stderr)[-2000:])
    return result


def psql(statement):
    return subprocess.run(
        ["psql", pgcfg, "-Atqc", statement],
        env=env,
        text=True,
        capture_output=True,
        timeout=20,
        check=True,
    ).stdout.strip()


def request(method, path, body=None, headers=None, cookie=None):
    connection = http.client.HTTPConnection("127.0.0.1", webport, timeout=20)
    request_headers = dict(headers or {})
    if cookie:
        request_headers["Cookie"] = cookie
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        request_headers.setdefault("Content-Type", "application/json")
        request_headers.setdefault("Origin", f"http://127.0.0.1:{webport}")
    connection.request(method, path, body=data, headers=request_headers)
    response = connection.getresponse()
    payload = response.read()
    response_headers = dict(response.getheaders())
    connection.close()
    try:
        payload = json.loads(payload)
    except Exception:
        pass
    return response.status, response_headers, payload


def rpc(cookie, csrf, contract, name, arguments):
    return request(
        "POST",
        "/_diamond/rpc/" + name,
        {"contract": contract, "arguments": arguments},
        {"X-Diamond-CSRF": csrf},
        cookie,
    )


def remote_head(url, ssh_environment=None):
    result = run(["git", "ls-remote", url, "refs/heads/main"], extra_env=ssh_environment)
    check("read remote main", result.returncode == 0, result.stderr[-1000:])
    return result.stdout.split()[0] if result.stdout.strip() else ""


try:
    ok(
        "initialize PostgreSQL",
        [
            "initdb",
            "-D",
            str(PG),
            "-A",
            "trust",
            "--no-locale",
            "-E",
            "UTF8",
            "-U",
            "diamond_rt",
            "--no-sync",
        ],
    )
    ok(
        "start PostgreSQL",
        [
            "pg_ctl",
            "-D",
            str(PG),
            "-l",
            str(TMP / "postgres.log"),
            "-o",
            f"-k {SOCK} -p {pgport} -c listen_addresses=",
            "-w",
            "start",
        ],
    )
    ok(
        "initialize project schema",
        [str(BIN), "project-init", "examples/git-browser/diamond.toml", "--postgres-env", "DIAMOND_POSTGRES"],
    )
    ok(
        "initialize shared object store",
        [
            str(BIN),
            "project-files-init",
            "examples/git-browser/diamond.toml",
            "--postgres-env",
            "DIAMOND_POSTGRES",
            "--runtime-env",
            "DIAMOND_RUNTIME",
        ],
    )
    ok(
        "install runtime Git hook",
        [
            str(BIN),
            "project-git-init",
            "examples/git-browser/diamond.toml",
            "--postgres-env",
            "DIAMOND_POSTGRES",
            "--runtime-env",
            "DIAMOND_RUNTIME",
        ],
    )
    ok(
        "initialize delivery store",
        [
            str(BIN),
            "project-delivery-init",
            "examples/git-browser/diamond.toml",
            "--postgres-env",
            "DIAMOND_POSTGRES",
            "--runtime-env",
            "DIAMOND_RUNTIME",
        ],
    )
    ok(
        "initialize operations store",
        [
            str(BIN),
            "project-ops-init",
            "examples/git-browser/diamond.toml",
            "--postgres-env",
            "DIAMOND_POSTGRES",
            "--runtime-env",
            "DIAMOND_RUNTIME",
        ],
    )
    hook = GITROOT / ".diamond-hooks" / "pre-receive"
    check("hook is small and mode 0500", hook.stat().st_size < 4096 and hook.stat().st_mode & 0o777 == 0o500)
    password = "Synthetic protected Git password 71!"
    result = run(
        [
            str(BIN),
            "project-admin-add",
            "examples/git-browser/diamond.toml",
            "--postgres-env",
            "DIAMOND_POSTGRES",
            "--user",
            "alice",
            "--email",
            "alice@example.test",
            "--password-stdin",
        ],
        input=password + "\n",
    )
    check("provision administrator", result.returncode == 0, result.stderr)

    server_log = open(TMP / "server.log", "wb")
    server = subprocess.Popen(
        [
            str(BIN),
            "serve",
            "examples/git-browser/diamond.toml",
            "--listen",
            f"127.0.0.1:{webport}",
            "--origin",
            f"http://127.0.0.1:{webport}",
            "--postgres-env",
            "DIAMOND_POSTGRES",
            "--runtime-env",
            "DIAMOND_RUNTIME",
            "--identity-mailbox",
            str(MAIL),
        ],
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
        raise AssertionError("Diamond server failed: " + (TMP / "server.log").read_text(errors="replace"))
    page = page.decode() if isinstance(page, bytes) else str(page)
    contract = re.search(r'data-contract="([0-9a-f]{64})"', page).group(1)
    status, headers, session = request("GET", "/_diamond/session", headers={"X-Diamond-Session": "1"})
    check("bootstrap account session", status == 200)
    cookie = headers["set-cookie"].split(";", 1)[0]
    status, headers, login = request(
        "POST",
        "/_diamond/login",
        {"username": "alice", "password": password},
        {"X-Diamond-CSRF": session["csrf"]},
        cookie,
    )
    check("login", status == 200, str(login))
    cookie = headers["set-cookie"].split(";", 1)[0]
    csrf = login["csrf"]
    status, _, value = rpc(cookie, csrf, contract, "register_repository", ["protected", True])
    check("register repository", status == 200, str(value))
    repository = value["value"]
    status, _, value = rpc(cookie, csrf, contract, "initialize_repository", [repository])
    check("initialize repository", status == 200, str(value))
    bare = next(GITROOT.glob("*.git"))
    hooks_path = run(["git", "--git-dir", str(bare), "config", "--get", "core.hooksPath"]).stdout.strip()
    check("repository uses runtime hook only", hooks_path == str(GITROOT / ".diamond-hooks"), hooks_path)

    token_request = {
        "label": "policy-rw",
        "operations": ["git:repos:read", "git:repos:write"],
        "scopes": {"repository": [repository]},
        "lifetime_seconds": "3600",
    }
    status, _, token_reply = request(
        "POST",
        "/_diamond/accounts/create_token",
        token_request,
        {"X-Diamond-CSRF": csrf},
        cookie,
    )
    check("create HTTPS Git token", status == 200)
    token = token_reply["value"]
    https_url = f"http://diamond:{token}@127.0.0.1:{webport}/_diamond/git/repos/{repository}.git"
    clone = TMP / "https-clone"
    ok("clone over HTTPS", ["git", "clone", https_url, str(clone)])
    ok("set Git email", ["git", "config", "user.email", "alice@example.test"], cwd=clone)
    ok("set Git name", ["git", "config", "user.name", "Alice"], cwd=clone)
    (clone / "README.md").write_text("one\n")
    ok("stage initial commit", ["git", "add", "README.md"], cwd=clone)
    ok("commit initial", ["git", "commit", "-m", "one"], cwd=clone)
    ok("push initial", ["git", "push", https_url, "HEAD:main"], cwd=clone)

    status, _, initial_rule = rpc(
        cookie,
        csrf,
        contract,
        "protect_reference",
        [repository, "refs/heads/main", "authenticated", False, False, "0", False, False, False, False],
    )
    check("first valid protected-ref rule", status == 200, str(initial_rule))

    status, _, _ = rpc(
        cookie,
        csrf,
        contract,
        "protect_reference",
        [repository, "main", "authenticated", False, False, "0", False, False, False, False],
    )
    check("short protected ref rejected", status >= 400)
    status, _, _ = rpc(
        cookie,
        csrf,
        contract,
        "protect_reference",
        [repository, "refs/heads/main", "owner;drop", False, False, "0", False, False, False, False],
    )
    check("unknown protected role rejected", status >= 400)

    status, _, value = rpc(
        cookie,
        csrf,
        contract,
        "protect_reference",
        [repository, "refs/heads/main", "authenticated", False, False, "0", False, False, False, False],
    )
    check("create protected-ref rule through Diamond", status == 200 and value["value"] == "protected", str(value))
    (clone / "README.md").write_text("two\n")
    ok("commit fast forward", ["git", "commit", "-am", "two"], cwd=clone)
    ok("protected fast forward allowed", ["git", "push", https_url, "HEAD:main"], cwd=clone)
    protected_head = remote_head(https_url)
    ok("rewind local branch", ["git", "reset", "--hard", "HEAD~1"], cwd=clone)
    (clone / "README.md").write_text("divergent\n")
    ok("commit divergent update", ["git", "commit", "-am", "divergent"], cwd=clone)
    result = run(["git", "push", "--force", https_url, "HEAD:main"], cwd=clone)
    check("protected force push rejected", result.returncode != 0)
    check("rejected force push leaves ref unchanged", remote_head(https_url) == protected_head)
    atomic = run(
        [
            "git",
            "push",
            "--atomic",
            "--force",
            https_url,
            "HEAD:refs/heads/main",
            "HEAD:refs/heads/atomic-side-probe",
        ],
        cwd=clone,
    )
    check("one protected update rejects atomic push", atomic.returncode != 0)
    check(
        "atomic rejection creates no side ref",
        run(["git", "--git-dir", str(bare), "show-ref", "--verify", "refs/heads/atomic-side-probe"]).returncode
        != 0,
    )
    result = run(["git", "push", https_url, ":refs/heads/main"], cwd=clone)
    check("protected deletion rejected", result.returncode != 0)
    check("rejected deletion leaves ref unchanged", remote_head(https_url) == protected_head)

    status, _, value = rpc(
        cookie,
        csrf,
        contract,
        "protect_reference",
        [repository, "refs/heads/main", "authenticated", True, False, "0", False, False, False, False],
    )
    check("update protection generation", status == 200 and value["value"] == "protected")
    ok("explicitly allowed force push", ["git", "push", "--force", https_url, "HEAD:main"], cwd=clone)
    forced_head = remote_head(https_url)
    bounded_refs = [
        f"HEAD:refs/heads/bound-{index:03d}" for index in range(257)
    ]
    bounded = run(
        ["git", "push", "--atomic", https_url, *bounded_refs],
        cwd=clone,
        timeout=120,
    )
    check("more than 256 ref updates rejected", bounded.returncode != 0)
    check(
        "oversized ref transaction creates no refs",
        "refs/heads/bound-" not in run(["git", "--git-dir", str(bare), "show-ref"]).stdout,
    )

    status, _, value = rpc(
        cookie,
        csrf,
        contract,
        "protect_reference",
        [repository, "refs/heads/main", "account_admin", False, False, "0", False, False, False, False],
    )
    check("require live administrator role", status == 200)
    psql("UPDATE diamond_identity.users SET administrator=false WHERE username='alice'")
    status, _, _ = rpc(
        cookie,
        csrf,
        contract,
        "protect_reference",
        [repository, "refs/heads/main", "authenticated", False, False, "0", False, False, False, False],
    )
    check("non-administrator cannot mutate protection", status == 403)
    (clone / "README.md").write_text("demoted\n")
    ok("commit after demotion", ["git", "commit", "-am", "demoted"], cwd=clone)
    result = run(["git", "push", https_url, "HEAD:main"], cwd=clone)
    check("role removal blocks in-flight credential", result.returncode != 0)
    check("role rejection leaves ref unchanged", remote_head(https_url) == forced_head)
    psql("UPDATE diamond_identity.users SET administrator=true WHERE username='alice'")
    ok("restored role permits push", ["git", "push", https_url, "HEAD:main"], cwd=clone)

    before_tamper = remote_head(https_url)
    original_hook = hook.read_bytes()
    hook.chmod(0o700)
    hook.write_text("#!/bin/sh\nexit 0\n")
    hook.chmod(0o500)
    (clone / "README.md").write_text("tamper\n")
    ok("commit hook tamper probe", ["git", "commit", "-am", "tamper"], cwd=clone)
    result = run(["git", "push", https_url, "HEAD:main"], cwd=clone)
    check("runtime hook replacement rejected", result.returncode != 0)
    check(
        "hook replacement cannot move ref",
        run(["git", "--git-dir", str(bare), "rev-parse", "refs/heads/main"]).stdout.strip()
        == before_tamper,
    )
    hook.chmod(0o700)
    hook.write_bytes(original_hook)
    hook.chmod(0o500)
    ok("push after exact hook restore", ["git", "push", https_url, "HEAD:main"], cwd=clone)

    user_key = TMP / "user-key"
    host_key = TMP / "host-key"
    ok("generate user Ed25519 key", ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "synthetic key", "-f", str(user_key)])
    ok("generate SSH host key", ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(host_key)])
    public_key = user_key.with_suffix(".pub").read_text().strip()
    fingerprint = run(["ssh-keygen", "-lf", str(user_key.with_suffix(".pub")), "-E", "sha256"]).stdout.split()[1]
    status, _, key_reply = request(
        "POST",
        "/_diamond/accounts/add_ssh_key",
        {"label": "synthetic workstation", "public_key": public_key},
        {"X-Diamond-CSRF": csrf},
        cookie,
    )
    check("enroll Ed25519 key through account API", status == 200 and re.fullmatch(r"[0-9a-f]{64}", key_reply["value"]), str(key_reply))
    key_id = key_reply["value"]
    status, _, listed = request(
        "POST",
        "/_diamond/accounts/list_ssh_keys",
        {},
        {"X-Diamond-CSRF": csrf},
        cookie,
    )
    check("SSH key listing omits public material", status == 200 and fingerprint in listed["value"] and public_key not in listed["value"])
    malformed = "ssh-ed25519 " + base64.b64encode(b"not-an-ssh-key").decode()
    status, _, _ = request(
        "POST",
        "/_diamond/accounts/add_ssh_key",
        {"label": "bad", "public_key": malformed},
        {"X-Diamond-CSRF": csrf},
        cookie,
    )
    check("malformed SSH wire key rejected", status >= 400)
    status, _, _ = request(
        "POST",
        "/_diamond/accounts/add_ssh_key",
        {"label": "rsa", "public_key": "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQC7"},
        {"X-Diamond-CSRF": csrf},
        cookie,
    )
    check("non-Ed25519 SSH key rejected", status >= 400)
    status, _, _ = request(
        "POST",
        "/_diamond/accounts/add_ssh_key",
        {"label": "duplicate", "public_key": public_key},
        {"X-Diamond-CSRF": csrf},
        cookie,
    )
    check("duplicate SSH key rejected", status >= 400)

    status, _, value = rpc(
        cookie,
        csrf,
        contract,
        "protect_reference",
        [repository, "refs/heads/signed/*", "authenticated", False, False, "0", False, False, True, False],
    )
    check("install signed-commit wildcard policy", status == 200, str(value))
    ok("configure SSH commit signing format", ["git", "config", "gpg.format", "ssh"], cwd=clone)
    ok("configure SSH commit signing key", ["git", "config", "user.signingkey", str(user_key)], cwd=clone)
    ok("enable SSH commit signing", ["git", "config", "commit.gpgsign", "true"], cwd=clone)
    ok("start signed orphan branch", ["git", "switch", "--orphan", "signed-good"], cwd=clone)
    (clone / "signed.txt").write_text("signed\n")
    ok("stage signed commit", ["git", "add", "signed.txt"], cwd=clone)
    ok("create signed commit", ["git", "commit", "-S", "-m", "signed"], cwd=clone)
    ok("signed commit accepted on wildcard protected ref", ["git", "push", https_url, "HEAD:refs/heads/signed/good"], cwd=clone)

    ok("disable signing for bypass probe", ["git", "config", "commit.gpgsign", "false"], cwd=clone)
    ok("start unsigned orphan branch", ["git", "switch", "--orphan", "unsigned-reuse"], cwd=clone)
    (clone / "unsigned.txt").write_text("unsigned\n")
    ok("stage unsigned commit", ["git", "add", "unsigned.txt"], cwd=clone)
    ok("create unsigned commit", ["git", "commit", "-m", "unsigned"], cwd=clone)
    ok("stage unsigned commit on unprotected ref", ["git", "push", https_url, "HEAD:refs/heads/unsigned-staging"], cwd=clone)
    unsigned_oid = run(["git", "rev-parse", "HEAD"], cwd=clone).stdout.strip()
    reused_unsigned = run(["git", "push", https_url, "HEAD:refs/heads/signed/reused"], cwd=clone)
    check("unsigned commit reuse cannot bypass wildcard signature policy", reused_unsigned.returncode != 0)
    check(
        "rejected unsigned reuse creates no protected ref",
        run(["git", "--git-dir", str(bare), "show-ref", "--verify", "refs/heads/signed/reused"]).returncode != 0,
    )

    status, _, value = rpc(
        cookie,
        csrf,
        contract,
        "protect_reference",
        [repository, "refs/heads/release/*", "authenticated", False, False, "0", False, False, False, True],
    )
    check("install secret-scan wildcard policy", status == 200, str(value))
    ok("start secret orphan branch", ["git", "switch", "--orphan", "secret-reuse"], cwd=clone)
    (clone / "credentials.txt").write_text("AKIA1234567890ABCDEF\n")
    ok("stage secret probe", ["git", "add", "credentials.txt"], cwd=clone)
    ok("commit secret probe", ["git", "commit", "-m", "secret probe"], cwd=clone)
    ok("stage secret commit on unprotected ref", ["git", "push", https_url, "HEAD:refs/heads/secret-staging"], cwd=clone)
    reused_secret = run(["git", "push", https_url, "HEAD:refs/heads/release/reused-secret"], cwd=clone)
    check("reachable secret cannot bypass wildcard secret scan", reused_secret.returncode != 0)
    check(
        "rejected secret reuse creates no protected ref",
        run(["git", "--git-dir", str(bare), "show-ref", "--verify", "refs/heads/release/reused-secret"]).returncode != 0,
    )
    ok("start clean release orphan branch", ["git", "switch", "--orphan", "release-clean"], cwd=clone)
    (clone / "release.txt").write_text("clean\n")
    ok("stage clean release", ["git", "add", "release.txt"], cwd=clone)
    ok("commit clean release", ["git", "commit", "-m", "clean release"], cwd=clone)
    ok("clean commit accepted by wildcard secret policy", ["git", "push", https_url, "HEAD:refs/heads/release/clean"], cwd=clone)
    check(
        "wildcard policy does not match sibling prefix",
        run(
            ["git", "push", https_url, f"{unsigned_oid}:refs/heads/releases/not-protected"],
            cwd=clone,
        ).returncode
        == 0,
    )

    status, _, value = rpc(
        cookie,
        csrf,
        contract,
        "protect_reference",
        [repository, "refs/heads/review/*", "authenticated", False, False, "2", True, True, False, False],
    )
    check("install approval pipeline discussion gate policy", status == 200, str(value))
    ok("start gated orphan branch", ["git", "switch", "--orphan", "gated"], cwd=clone)
    (clone / "review.txt").write_text("reviewed\n")
    ok("stage gated commit", ["git", "add", "review.txt"], cwd=clone)
    ok("commit gated candidate", ["git", "commit", "-m", "gated"], cwd=clone)
    gated_oid = run(["git", "rev-parse", "HEAD"], cwd=clone).stdout.strip()
    gated_push = run(["git", "push", https_url, "HEAD:refs/heads/review/candidate"], cwd=clone)
    check("protected gate rejects missing evidence", gated_push.returncode != 0)
    status, _, _ = rpc(cookie, csrf, contract, "record_gate", [repository, "refs/heads/review/candidate", "0" * 40, "2", True, True])
    check("record wrong-object gate", status == 200)
    check("wrong-object gate cannot authorize candidate", run(["git", "push", https_url, "HEAD:refs/heads/review/candidate"], cwd=clone).returncode != 0)
    status, _, _ = rpc(cookie, csrf, contract, "record_gate", [repository, "refs/heads/review/candidate", gated_oid, "1", True, True])
    check("record insufficient-approval gate", status == 200)
    check("insufficient approvals reject candidate", run(["git", "push", https_url, "HEAD:refs/heads/review/candidate"], cwd=clone).returncode != 0)
    status, _, _ = rpc(cookie, csrf, contract, "record_gate", [repository, "refs/heads/review/candidate", gated_oid, "2", False, True])
    check("record failed-pipeline gate", status == 200)
    check("failed pipeline rejects candidate", run(["git", "push", https_url, "HEAD:refs/heads/review/candidate"], cwd=clone).returncode != 0)
    status, _, _ = rpc(cookie, csrf, contract, "record_gate", [repository, "refs/heads/review/candidate", gated_oid, "2", True, True])
    check("record complete gate", status == 200)
    ok("exact candidate passes complete gate", ["git", "push", https_url, "HEAD:refs/heads/review/candidate"], cwd=clone)

    status, _, event_reply = rpc(cookie, csrf, contract, "pending_push_events", ["100"])
    events = json.loads(event_reply["value"]) if status == 200 else []
    check("durable committed push events exposed through bounded RPC", status == 200 and events, str(event_reply))
    check("push events identify user actor and store generation", all(event["actor_kind"] == "user" and event["store_generation"] == 1 for event in events))
    first_event = events[0]["id"]
    status, _, ack_reply = rpc(cookie, csrf, contract, "acknowledge_push_event", [first_event])
    check(
        "acknowledge durable push event",
        status == 200 and ack_reply["value"] == "event-acknowledged",
        str(ack_reply),
    )
    status, _, event_reply = rpc(cookie, csrf, contract, "pending_push_events", ["100"])
    remaining_events = json.loads(event_reply["value"]) if status == 200 else []
    check("acknowledged event leaves pending delivery set", all(event["id"] != first_event for event in remaining_events))

    launcher = TMP / "diamond-git-ssh-launcher"
    launcher.write_text(
        "#!/bin/sh\n"
        + "export DIAMOND_POSTGRES="
        + shlex.quote(pgcfg)
        + "\nexport DIAMOND_RUNTIME="
        + shlex.quote(env["DIAMOND_RUNTIME"])
        + "\nexport DIA_OAUTH_KEY="
        + shlex.quote(env["DIA_OAUTH_KEY"])
        + "\nexport DIA_GOOGLE_SECRET="
        + shlex.quote(env["DIA_GOOGLE_SECRET"])
        + "\nexport DIA_GITHUB_SECRET="
        + shlex.quote(env["DIA_GITHUB_SECRET"])
        + "\nexec "
        + shlex.quote(str(BIN))
        + " project-git-ssh "
        + shlex.quote(str((ROOT / "examples/git-browser/diamond.toml").resolve()))
        + " --postgres-env DIAMOND_POSTGRES --runtime-env DIAMOND_RUNTIME --key-id \"$1\"\n"
    )
    launcher.chmod(0o700)
    authorized = run(
        [
            str(BIN),
            "project-git-authorized-key",
            "examples/git-browser/diamond.toml",
            "--postgres-env",
            "DIAMOND_POSTGRES",
            "--runtime-env",
            "DIAMOND_RUNTIME",
            "--fingerprint",
            fingerprint,
            "--ssh-launcher",
            str(launcher),
        ]
    )
    check("authorized-key lookup succeeds", authorized.returncode == 0, authorized.stderr)
    check("authorized-key output is forced and restricted", authorized.stdout.startswith('restrict,command="') and public_key.split()[1] in authorized.stdout)
    check("authorized-key output contains no runtime secrets", pgcfg not in authorized.stdout and env["DIAMOND_RUNTIME"] not in authorized.stdout)
    invalid_fingerprint = run(
        [
            str(BIN),
            "project-git-authorized-key",
            "examples/git-browser/diamond.toml",
            "--postgres-env",
            "DIAMOND_POSTGRES",
            "--fingerprint",
            "SHA256:not-base64!!!",
            "--ssh-launcher",
            str(launcher),
        ]
    )
    check("malformed SSH fingerprint rejected", invalid_fingerprint.returncode != 0)
    launcher.chmod(0o722)
    unsafe_launcher = run(
        [
            str(BIN),
            "project-git-authorized-key",
            "examples/git-browser/diamond.toml",
            "--postgres-env",
            "DIAMOND_POSTGRES",
            "--fingerprint",
            fingerprint,
            "--ssh-launcher",
            str(launcher),
        ]
    )
    check("group-writable SSH launcher rejected", unsafe_launcher.returncode != 0)
    launcher.chmod(0o700)
    launcher_link = TMP / "launcher-link"
    launcher_link.symlink_to(launcher)
    symlink_launcher = run(
        [
            str(BIN),
            "project-git-authorized-key",
            "examples/git-browser/diamond.toml",
            "--postgres-env",
            "DIAMOND_POSTGRES",
            "--fingerprint",
            fingerprint,
            "--ssh-launcher",
            str(launcher_link),
        ]
    )
    check("symlink SSH launcher rejected", symlink_launcher.returncode != 0)

    deploy_read_key = TMP / "deploy-read-key"
    deploy_write_key = TMP / "deploy-write-key"
    ok("generate read-only deploy key", ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "read deploy", "-f", str(deploy_read_key)])
    ok("generate write deploy key", ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "write deploy", "-f", str(deploy_write_key)])
    deploy_read_public = deploy_read_key.with_suffix(".pub").read_text().strip()
    deploy_write_public = deploy_write_key.with_suffix(".pub").read_text().strip()
    status, _, deploy_read_reply = rpc(cookie, csrf, contract, "add_deploy_key", [repository, "read mirror", deploy_read_public, False])
    check("add repository-scoped read deploy key", status == 200 and re.fullmatch(r"[0-9a-f]{64}", deploy_read_reply["value"]), str(deploy_read_reply))
    deploy_read_id = deploy_read_reply["value"]
    status, _, deploy_write_reply = rpc(cookie, csrf, contract, "add_deploy_key", [repository, "write automation", deploy_write_public, True])
    check("add repository-scoped write deploy key", status == 200 and re.fullmatch(r"[0-9a-f]{64}", deploy_write_reply["value"]), str(deploy_write_reply))
    deploy_write_id = deploy_write_reply["value"]
    deploy_read_fingerprint = run(["ssh-keygen", "-lf", str(deploy_read_key.with_suffix(".pub")), "-E", "sha256"]).stdout.split()[1]
    deploy_write_fingerprint = run(["ssh-keygen", "-lf", str(deploy_write_key.with_suffix(".pub")), "-E", "sha256"]).stdout.split()[1]
    deploy_read_authorized = run([
        str(BIN), "project-git-authorized-key", "examples/git-browser/diamond.toml",
        "--postgres-env", "DIAMOND_POSTGRES", "--runtime-env", "DIAMOND_RUNTIME",
        "--fingerprint", deploy_read_fingerprint, "--ssh-launcher", str(launcher),
    ])
    check("read deploy key authorization lookup", deploy_read_authorized.returncode == 0 and deploy_read_id in deploy_read_authorized.stdout, deploy_read_authorized.stderr)
    deploy_write_authorized = run([
        str(BIN), "project-git-authorized-key", "examples/git-browser/diamond.toml",
        "--postgres-env", "DIAMOND_POSTGRES", "--runtime-env", "DIAMOND_RUNTIME",
        "--fingerprint", deploy_write_fingerprint, "--ssh-launcher", str(launcher),
    ])
    check("write deploy key authorization lookup", deploy_write_authorized.returncode == 0 and deploy_write_id in deploy_write_authorized.stdout, deploy_write_authorized.stderr)

    ca_key = TMP / "certificate-ca"
    certificate_key = TMP / "certificate-key"
    certificate_principal = "diamond-build-certificate"
    ok("generate SSH certificate CA", ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "synthetic ca", "-f", str(ca_key)])
    ok("generate SSH certificate subject key", ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "synthetic certificate", "-f", str(certificate_key)])
    status, _, certificate_reply = rpc(cookie, csrf, contract, "add_certificate", [repository, certificate_principal, False])
    check("add repository-scoped certificate principal", status == 200 and re.fullmatch(r"[0-9a-f]{64}", certificate_reply["value"]), str(certificate_reply))
    certificate_id = certificate_reply["value"]
    ok(
        "sign short-lived SSH user certificate",
        ["ssh-keygen", "-q", "-s", str(ca_key), "-I", "diamond-runtime-v2", "-n", certificate_principal, "-V", "-1m:+10m", str(certificate_key.with_suffix(".pub"))],
    )
    principals = run([
        str(BIN), "project-git-authorized-principals", "examples/git-browser/diamond.toml",
        "--postgres-env", "DIAMOND_POSTGRES", "--runtime-env", "DIAMOND_RUNTIME",
        "--ssh-launcher", str(launcher),
    ])
    check("authorized principals lookup is forced and restricted", principals.returncode == 0 and certificate_id in principals.stdout and certificate_principal in principals.stdout, principals.stderr)

    authorized_file = TMP / "authorized_keys"
    authorized_file.write_text(authorized.stdout + deploy_read_authorized.stdout + deploy_write_authorized.stdout)
    authorized_file.chmod(0o600)
    principals_file = TMP / "authorized_principals"
    principals_file.write_text(principals.stdout)
    principals_file.chmod(0o600)

    current_user = pwd.getpwuid(os.getuid()).pw_name
    sshd_config = TMP / "sshd_config"
    sshd_config.write_text(
        "\n".join(
            [
                f"Port {sshport}",
                "ListenAddress 127.0.0.1",
                f"HostKey {host_key}",
                f"PidFile {TMP / 'sshd.pid'}",
                f"AuthorizedKeysFile {authorized_file}",
                f"TrustedUserCAKeys {ca_key.with_suffix('.pub')}",
                f"AuthorizedPrincipalsFile {principals_file}",
                "StrictModes no",
                "PubkeyAuthentication yes",
                "PasswordAuthentication no",
                "KbdInteractiveAuthentication no",
                "UsePAM no",
                "PermitRootLogin no",
                "AllowTcpForwarding no",
                "X11Forwarding no",
                "PermitTTY no",
                f"AllowUsers {current_user}",
                "LogLevel VERBOSE",
                "",
            ]
        )
    )
    checked_sshd = run(["/usr/bin/sshd", "-t", "-f", str(sshd_config)])
    check("OpenSSH test configuration valid", checked_sshd.returncode == 0, checked_sshd.stderr)
    sshd_log = open(TMP / "sshd.log", "wb")
    sshd = subprocess.Popen(
        ["/usr/bin/sshd", "-D", "-e", "-f", str(sshd_config)],
        env=env,
        stdout=sshd_log,
        stderr=subprocess.STDOUT,
    )
    for _ in range(200):
        if sshd.poll() is not None:
            break
        try:
            probe = socket.create_connection(("127.0.0.1", sshport), timeout=0.2)
            probe.close()
            break
        except OSError:
            time.sleep(0.05)
    check("OpenSSH daemon starts", sshd.poll() is None, (TMP / "sshd.log").read_text(errors="replace"))
    ssh_command = " ".join(
        shlex.quote(value)
        for value in [
            "ssh",
            "-i",
            str(user_key),
            "-p",
            str(sshport),
            "-o",
            "IdentitiesOnly=yes",
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            f"UserKnownHostsFile={TMP / 'known_hosts'}",
        ]
    )
    ssh_environment = {"GIT_SSH_COMMAND": ssh_command}
    ssh_url = f"ssh://{current_user}@127.0.0.1:{sshport}/repos/{repository}.git"
    sentinel = TMP / "ssh-command-injection"
    invalid_commands = [
        f"git-upload-pack 'repos/{repository}.git';touch {sentinel}",
        "git-upload-pack 'repos/../../escape.git'",
        f"git-upload-pack 'repos/{repository}.git' extra",
        f"git-upload-pack 'repos/{repository}.git'\ntouch {sentinel}",
    ]
    for index, original in enumerate(invalid_commands):
        rejected = run(
            [
                str(BIN),
                "project-git-ssh",
                "examples/git-browser/diamond.toml",
                "--postgres-env",
                "DIAMOND_POSTGRES",
                "--runtime-env",
                "DIAMOND_RUNTIME",
                "--key-id",
                key_id,
            ],
            extra_env={"SSH_ORIGINAL_COMMAND": original},
        )
        check(f"reject hostile SSH command {index + 1}", rejected.returncode != 0)
    check("hostile SSH commands never execute", not sentinel.exists())
    wrong_key = run(
        [
            str(BIN),
            "project-git-ssh",
            "examples/git-browser/diamond.toml",
            "--postgres-env",
            "DIAMOND_POSTGRES",
            "--runtime-env",
            "DIAMOND_RUNTIME",
            "--key-id",
            "0" * 64,
        ],
        extra_env={"SSH_ORIGINAL_COMMAND": f"git-upload-pack 'repos/{repository}.git'"},
    )
    check("unknown SSH key id rejected", wrong_key.returncode != 0)
    ssh_clone = TMP / "ssh-clone"
    ok("stock Git clone over OpenSSH", ["git", "clone", ssh_url, str(ssh_clone)], extra_env=ssh_environment)
    check("SSH clone content exact", (ssh_clone / "README.md").read_text() == "tamper\n")
    archive = TMP / "ssh-archive.tar"
    ok(
        "stock Git archive over OpenSSH",
        ["git", "archive", f"--remote={ssh_url}", f"--output={archive}", "HEAD", "README.md"],
        extra_env=ssh_environment,
    )
    with tarfile.open(archive) as uploaded:
        archived = uploaded.extractfile("README.md")
        check("SSH archive content exact", archived is not None and archived.read() == b"tamper\n")
    ok("configure SSH clone email", ["git", "config", "user.email", "alice@example.test"], cwd=ssh_clone)
    ok("configure SSH clone name", ["git", "config", "user.name", "Alice"], cwd=ssh_clone)
    (ssh_clone / "README.md").write_text("ssh\n")
    ok("commit SSH update", ["git", "commit", "-am", "ssh"], cwd=ssh_clone)
    ok("stock Git push over OpenSSH", ["git", "push", "origin", "HEAD:main"], cwd=ssh_clone, extra_env=ssh_environment)
    random_bytes = os.urandom(8 * 1024 * 1024)
    (ssh_clone / "random.bin").write_bytes(random_bytes)
    ok("stage streamed SSH object", ["git", "add", "random.bin"], cwd=ssh_clone)
    ok("commit streamed SSH object", ["git", "commit", "-m", "ssh-stream"], cwd=ssh_clone)
    ok(
        "push 8 MiB incompressible object over SSH",
        ["git", "push", "origin", "HEAD:main"],
        cwd=ssh_clone,
        extra_env=ssh_environment,
        timeout=120,
    )
    streamed_clone = TMP / "ssh-stream-clone"
    ok(
        "clone 8 MiB incompressible object over SSH",
        ["git", "clone", ssh_url, str(streamed_clone)],
        extra_env=ssh_environment,
        timeout=120,
    )
    check("streamed SSH object exact", (streamed_clone / "random.bin").read_bytes() == random_bytes)
    archive_environment = env.copy()
    archive_environment.update(ssh_environment)
    concurrent_archives = []
    for index in range(4):
        target = TMP / f"concurrent-archive-{index}.tar"
        process = subprocess.Popen(
            ["git", "archive", f"--remote={ssh_url}", f"--output={target}", "HEAD", "random.bin"],
            cwd=ROOT,
            env=archive_environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        concurrent_archives.append((target, process))
    archive_failures = []
    for target, process in concurrent_archives:
        stdout, stderr = process.communicate(timeout=120)
        if process.returncode != 0:
            archive_failures.append((process.returncode, (stdout + stderr)[-2000:].decode(errors="replace")))
    check("four concurrent large Git archives complete over OpenSSH", not archive_failures, str(archive_failures))
    archive_mismatches = []
    for target, _ in concurrent_archives:
        with tarfile.open(target) as uploaded:
            archived = uploaded.extractfile("random.bin")
            if archived is None or archived.read() != random_bytes:
                archive_mismatches.append(str(target))
    check("concurrent OpenSSH archive bytes are exact", not archive_mismatches, str(archive_mismatches))

    deploy_read_command = " ".join(
        shlex.quote(value)
        for value in [
            "ssh", "-i", str(deploy_read_key), "-p", str(sshport),
            "-o", "IdentitiesOnly=yes", "-o", "StrictHostKeyChecking=no",
            "-o", f"UserKnownHostsFile={TMP / 'known_hosts'}",
        ]
    )
    deploy_read_environment = {"GIT_SSH_COMMAND": deploy_read_command}
    deploy_read_clone = TMP / "deploy-read-clone"
    ok("read deploy key clones through stock OpenSSH", ["git", "clone", ssh_url, str(deploy_read_clone)], extra_env=deploy_read_environment)
    ok("configure read deploy clone email", ["git", "config", "user.email", "deploy@example.test"], cwd=deploy_read_clone)
    ok("configure read deploy clone name", ["git", "config", "user.name", "Deploy"], cwd=deploy_read_clone)
    (deploy_read_clone / "deploy-read.txt").write_text("write denied\n")
    ok("stage read deploy write probe", ["git", "add", "deploy-read.txt"], cwd=deploy_read_clone)
    ok("commit read deploy write probe", ["git", "commit", "-m", "read deploy write probe"], cwd=deploy_read_clone)
    denied_deploy_push = run(
        ["git", "push", "origin", "HEAD:refs/heads/deploy-read-denied"],
        cwd=deploy_read_clone,
        extra_env=deploy_read_environment,
    )
    check("read deploy key cannot push", denied_deploy_push.returncode != 0)

    deploy_write_command = " ".join(
        shlex.quote(value)
        for value in [
            "ssh", "-i", str(deploy_write_key), "-p", str(sshport),
            "-o", "IdentitiesOnly=yes", "-o", "StrictHostKeyChecking=no",
            "-o", f"UserKnownHostsFile={TMP / 'known_hosts'}",
        ]
    )
    deploy_write_environment = {"GIT_SSH_COMMAND": deploy_write_command}
    deploy_write_clone = TMP / "deploy-write-clone"
    ok("write deploy key clones through stock OpenSSH", ["git", "clone", ssh_url, str(deploy_write_clone)], extra_env=deploy_write_environment)
    ok("configure write deploy clone email", ["git", "config", "user.email", "deploy@example.test"], cwd=deploy_write_clone)
    ok("configure write deploy clone name", ["git", "config", "user.name", "Deploy"], cwd=deploy_write_clone)
    (deploy_write_clone / "deploy-write.txt").write_text("write allowed\n")
    ok("stage write deploy update", ["git", "add", "deploy-write.txt"], cwd=deploy_write_clone)
    ok("commit write deploy update", ["git", "commit", "-m", "write deploy"], cwd=deploy_write_clone)
    ok(
        "write deploy key pushes repository-scoped ref",
        ["git", "push", "origin", "HEAD:refs/heads/deploy-write"],
        cwd=deploy_write_clone,
        extra_env=deploy_write_environment,
    )

    certificate_command = " ".join(
        shlex.quote(value)
        for value in [
            "ssh", "-i", str(certificate_key),
            "-o", f"CertificateFile={certificate_key.with_name(certificate_key.name + '-cert.pub')}",
            "-p", str(sshport), "-o", "IdentitiesOnly=yes",
            "-o", "StrictHostKeyChecking=no", "-o", f"UserKnownHostsFile={TMP / 'known_hosts'}",
        ]
    )
    certificate_environment = {"GIT_SSH_COMMAND": certificate_command}
    certificate_clone = TMP / "certificate-clone"
    ok("short-lived SSH certificate clones through stock OpenSSH", ["git", "clone", ssh_url, str(certificate_clone)], extra_env=certificate_environment)
    ok("configure certificate clone email", ["git", "config", "user.email", "certificate@example.test"], cwd=certificate_clone)
    ok("configure certificate clone name", ["git", "config", "user.name", "Certificate"], cwd=certificate_clone)
    (certificate_clone / "certificate.txt").write_text("write denied\n")
    ok("stage certificate write probe", ["git", "add", "certificate.txt"], cwd=certificate_clone)
    ok("commit certificate write probe", ["git", "commit", "-m", "certificate write probe"], cwd=certificate_clone)
    denied_certificate_push = run(
        ["git", "push", "origin", "HEAD:refs/heads/certificate-write-denied"],
        cwd=certificate_clone,
        extra_env=certificate_environment,
    )
    check("read certificate principal cannot push", denied_certificate_push.returncode != 0)

    status, _, _ = rpc(cookie, csrf, contract, "revoke_deploy_key", [repository, deploy_read_id])
    check("revoke deploy key through Diamond RPC", status == 200)
    revoked_deploy_clone = run(["git", "clone", ssh_url, str(TMP / "revoked-deploy-clone")], extra_env=deploy_read_environment)
    check("revoked deploy key denied by live forced adapter", revoked_deploy_clone.returncode != 0)
    status, _, _ = rpc(cookie, csrf, contract, "revoke_certificate", [repository, certificate_id])
    check("revoke certificate principal through Diamond RPC", status == 200)
    revoked_certificate_clone = run(["git", "clone", ssh_url, str(TMP / "revoked-certificate-clone")], extra_env=certificate_environment)
    check("revoked certificate denied by live forced adapter", revoked_certificate_clone.returncode != 0)

    status, _, deploy_event_reply = rpc(cookie, csrf, contract, "pending_push_events", ["100"])
    deploy_events = json.loads(deploy_event_reply["value"]) if status == 200 else []
    check("deploy push emits durable deploy actor event", any(event["actor_kind"] == "deploy" and event["actor"] == deploy_write_id for event in deploy_events))

    raw_ssh = run(
        shlex.split(ssh_command)
        + [f"{current_user}@127.0.0.1", "printf", "shell-was-opened"],
        extra_env=ssh_environment,
    )
    check("SSH shell command rejected", raw_ssh.returncode != 0 and "shell-was-opened" not in raw_ssh.stdout)

    psql("UPDATE diamond_identity.users SET disabled=true WHERE username='alice'")
    disabled_clone = run(["git", "clone", ssh_url, str(TMP / "disabled-clone")], extra_env=ssh_environment)
    check("disabled account rejected inside SSH adapter", disabled_clone.returncode != 0)
    psql("UPDATE diamond_identity.users SET disabled=false WHERE username='alice'")

    status, _, _ = request(
        "POST",
        "/_diamond/accounts/revoke_ssh_key",
        {"id": key_id},
        {"X-Diamond-CSRF": csrf},
        cookie,
    )
    check("revoke SSH key through account API", status == 200)
    revoked_clone = run(["git", "clone", ssh_url, str(TMP / "revoked-clone")], extra_env=ssh_environment)
    check("revoked key denied inside forced adapter", revoked_clone.returncode != 0)
    revoked_lookup = run(
        [
            str(BIN),
            "project-git-authorized-key",
            "examples/git-browser/diamond.toml",
            "--postgres-env",
            "DIAMOND_POSTGRES",
            "--runtime-env",
            "DIAMOND_RUNTIME",
            "--fingerprint",
            fingerprint,
            "--ssh-launcher",
            str(launcher),
        ]
    )
    check("revoked key omitted from future authorization", revoked_lookup.returncode != 0)
    check("repository remains consistent", run(["git", "--git-dir", str(bare), "fsck", "--strict"]).returncode == 0)

    reconciliation = run(
        [
            str(BIN),
            "project-git-reconcile",
            "examples/git-browser/diamond.toml",
            "--postgres-env",
            "DIAMOND_POSTGRES",
            "--runtime-env",
            "DIAMOND_RUNTIME",
        ]
    )
    reconciliation_value = json.loads(reconciliation.stdout) if reconciliation.returncode == 0 else {}
    check(
        "reconcile committed event spool into PostgreSQL",
        reconciliation.returncode == 0 and reconciliation_value.get("committed", 0) > 0,
        reconciliation.stdout + reconciliation.stderr,
    )
    check(
        "reconciliation removes committed spool files after database commit",
        not list((GITROOT / ".diamond-events").glob("*.committed")),
    )

    next_gitroot = TMP / "git-next"
    shutil.copytree(GITROOT, next_gitroot)
    next_gitroot.chmod(0o700)
    (next_gitroot / ".diamond-git-store").unlink()
    next_runtime = json.loads(env["DIAMOND_RUNTIME"])
    next_runtime["git"]["repos"]["root"] = str(next_gitroot)
    next_env = env.copy()
    next_env["DIAMOND_RUNTIME"] = json.dumps(next_runtime, separators=(",", ":"))

    ok("start promotion-fence branch", ["git", "switch", "--orphan", "promotion-fence"], cwd=clone)
    (clone / "promotion-fence.bin").write_bytes(os.urandom(64 * 1024 * 1024))
    ok("stage promotion-fence object", ["git", "add", "promotion-fence.bin"], cwd=clone)
    ok("commit promotion-fence object", ["git", "commit", "-m", "promotion fence"], cwd=clone)
    push_process = subprocess.Popen(
        ["git", "push", https_url, "HEAD:refs/heads/promotion-fence"],
        cwd=clone,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    receive_pid = None
    deadline = time.time() + 10
    while time.time() < deadline and push_process.poll() is None:
        for proc in pathlib.Path("/proc").iterdir():
            if not proc.name.isdigit():
                continue
            try:
                command = (proc / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
            except (FileNotFoundError, PermissionError, ProcessLookupError):
                continue
            if "git receive-pack" in command or "git-receive-pack" in command:
                receive_pid = int(proc.name)
                break
        if receive_pid is not None:
            os.kill(receive_pid, signal.SIGSTOP)
            break
        time.sleep(0.005)
    check("observe live receive process for shard fence", receive_pid is not None)
    promotion_process = subprocess.Popen(
        [
            str(BIN),
            "project-git-promote",
            "examples/git-browser/diamond.toml",
            "--postgres-env",
            "DIAMOND_POSTGRES",
            "--runtime-env",
            "DIAMOND_RUNTIME",
            "--binding",
            "repos",
            "--expected-generation",
            "1",
        ],
        cwd=ROOT,
        env=next_env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    time.sleep(0.5)
    promotion_waited = promotion_process.poll() is None
    os.kill(receive_pid, signal.SIGCONT)
    push_stdout, push_stderr = push_process.communicate(timeout=120)
    promote_stdout, promote_stderr = promotion_process.communicate(timeout=30)
    check("active receive holds shard promotion fence", promotion_waited)
    check("fenced push completes after release", push_process.returncode == 0, push_stdout + push_stderr)
    check(
        "promotion rejects target that missed fenced push",
        promotion_process.returncode != 0,
        promote_stdout + promote_stderr,
    )

    late_reconciliation = run(
        [
            str(BIN),
            "project-git-reconcile",
            "examples/git-browser/diamond.toml",
            "--postgres-env",
            "DIAMOND_POSTGRES",
            "--runtime-env",
            "DIAMOND_RUNTIME",
        ]
    )
    check(
        "reconcile source shard before cutover retry",
        late_reconciliation.returncode == 0,
        late_reconciliation.stdout + late_reconciliation.stderr,
    )

    next_bare = next_gitroot / bare.name
    shutil.rmtree(next_bare)
    shutil.copytree(bare, next_bare)
    promoted = run(
        [
            str(BIN),
            "project-git-promote",
            "examples/git-browser/diamond.toml",
            "--postgres-env",
            "DIAMOND_POSTGRES",
            "--runtime-env",
            "DIAMOND_RUNTIME",
            "--binding",
            "repos",
            "--expected-generation",
            "1",
        ],
        extra_env={"DIAMOND_RUNTIME": next_env["DIAMOND_RUNTIME"]},
    )
    promoted_value = json.loads(promoted.stdout) if promoted.returncode == 0 else {}
    expected_inventory = psql(
        "SELECT repository||' '||reference||' '||object FROM diamond_git.ref_states ORDER BY repository,reference"
    )
    actual_inventory = run(
        [
            "git",
            "--git-dir",
            str(next_bare),
            "for-each-ref",
            "--format=%(refname) %(objectname)",
            "refs/heads",
            "refs/tags",
        ]
    ).stdout
    check(
        "promote exact repository and ref inventory",
        promoted.returncode == 0 and promoted_value.get("generation") == 2,
        promoted.stdout
        + promoted.stderr
        + "\nEXPECTED:\n"
        + expected_inventory
        + "\nACTUAL:\n"
        + actual_inventory,
    )
    stale_generation = run(
        [
            str(BIN),
            "project-git-promote",
            "examples/git-browser/diamond.toml",
            "--postgres-env",
            "DIAMOND_POSTGRES",
            "--runtime-env",
            "DIAMOND_RUNTIME",
            "--binding",
            "repos",
            "--expected-generation",
            "1",
        ],
        extra_env={"DIAMOND_RUNTIME": next_env["DIAMOND_RUNTIME"]},
    )
    check("stale generation cannot repeat promotion", stale_generation.returncode != 0)
    stale_server_clone = run(["git", "clone", https_url, str(TMP / "stale-server-clone")])
    check("old shard identity is fenced after promotion", stale_server_clone.returncode != 0)

    server.terminate()
    server.wait(timeout=8)
    server_log.close()
    next_server_log = open(TMP / "server-next.log", "wb")
    server = subprocess.Popen(
        [
            str(BIN),
            "serve",
            "examples/git-browser/diamond.toml",
            "--listen",
            f"127.0.0.1:{webport}",
            "--origin",
            f"http://127.0.0.1:{webport}",
            "--postgres-env",
            "DIAMOND_POSTGRES",
            "--runtime-env",
            "DIAMOND_RUNTIME",
            "--identity-mailbox",
            str(MAIL),
        ],
        cwd=ROOT,
        env=next_env,
        stdout=next_server_log,
        stderr=subprocess.STDOUT,
    )
    for _ in range(300):
        try:
            if request("GET", "/")[0] == 200:
                break
        except Exception:
            pass
        time.sleep(0.05)
    else:
        raise AssertionError("promoted Diamond server failed")
    promoted_clone = TMP / "promoted-clone"
    ok("clone through promoted Git shard", ["git", "clone", https_url, str(promoted_clone)])
    check("promoted repository passes strict fsck", run(["git", "--git-dir", str(next_bare), "fsck", "--strict"]).returncode == 0)
    ok("configure promoted clone email", ["git", "config", "user.email", "alice@example.test"], cwd=promoted_clone)
    ok("configure promoted clone name", ["git", "config", "user.name", "Alice"], cwd=promoted_clone)
    (promoted_clone / "generation-two.txt").write_text("generation two\n")
    ok("stage generation-two update", ["git", "add", "generation-two.txt"], cwd=promoted_clone)
    ok("commit generation-two update", ["git", "commit", "-m", "generation two"], cwd=promoted_clone)
    ok("push through promoted Git shard", ["git", "push", "origin", "HEAD:refs/heads/generation-two"], cwd=promoted_clone)
    status, _, generation_event_reply = rpc(cookie, csrf, contract, "pending_push_events", ["100"])
    generation_events = json.loads(generation_event_reply["value"]) if status == 200 else []
    check("promoted shard emits generation-two event", any(event["store_generation"] == 2 for event in generation_events))

    output = {
        "profile": "Diamond protected receive and SSH adapter red-team campaign",
        "versions": {
            "diamond": run([str(BIN), "--version"]).stdout.strip(),
            "git": run(["git", "--version"]).stdout.strip(),
            "openssh": run(["ssh", "-V"]).stderr.strip(),
            "postgres": run(["postgres", "--version"]).stdout.strip(),
            "python": sys.version.split()[0],
        },
        "passed": sum(result["pass"] for result in results),
        "total": len(results),
        "results": results,
    }
    (ROOT / "verification/git_policy_ssh_redteam_results.json").write_text(
        json.dumps(output, indent=2) + "\n"
    )
    print(json.dumps(output, indent=2))
finally:
    if sshd is not None:
        sshd.terminate()
        try:
            sshd.wait(timeout=5)
        except subprocess.TimeoutExpired:
            sshd.kill()
            sshd.wait()
    if server is not None:
        server.terminate()
        try:
            server.wait(timeout=8)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait()
    try:
        subprocess.run(["pg_ctl", "-D", str(PG), "-m", "fast", "-w", "stop"], capture_output=True, timeout=20)
    except Exception:
        pass
    shutil.rmtree(TMP, ignore_errors=True)
