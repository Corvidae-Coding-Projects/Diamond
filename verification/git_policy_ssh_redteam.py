#!/usr/bin/env python3
"""Real-interface adversarial replay for protected refs and the OpenSSH adapter."""

import base64
import http.client
import json
import os
import pathlib
import pwd
import re
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
BIN = ROOT / "target/debug/diamond"
TMP = pathlib.Path(tempfile.mkdtemp(prefix="diamond-git-policy-ssh-"))
PG = TMP / "pg"
SOCK = TMP / "sock"
GITROOT = TMP / "git"
MAIL = TMP / "mail"
for directory in (SOCK, GITROOT, MAIL):
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

    status, _, _ = rpc(
        cookie,
        csrf,
        contract,
        "protect_reference",
        [repository, "main", "authenticated", False, False],
    )
    check("short protected ref rejected", status >= 400)
    status, _, _ = rpc(
        cookie,
        csrf,
        contract,
        "protect_reference",
        [repository, "refs/heads/main", "owner;drop", False, False],
    )
    check("unknown protected role rejected", status >= 400)

    status, _, value = rpc(
        cookie,
        csrf,
        contract,
        "protect_reference",
        [repository, "refs/heads/main", "authenticated", False, False],
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
        [repository, "refs/heads/main", "authenticated", True, False],
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
        [repository, "refs/heads/main", "account_admin", False, False],
    )
    check("require live administrator role", status == 200)
    psql("UPDATE diamond_identity.users SET administrator=false WHERE username='alice'")
    status, _, _ = rpc(
        cookie,
        csrf,
        contract,
        "protect_reference",
        [repository, "refs/heads/main", "authenticated", False, False],
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
    authorized_file = TMP / "authorized_keys"
    authorized_file.write_text(authorized.stdout)
    authorized_file.chmod(0o600)

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
        f"git-upload-archive 'repos/{repository}.git'",
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
