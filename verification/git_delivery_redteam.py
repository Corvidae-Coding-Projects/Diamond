#!/usr/bin/env python3
"""G3 real-interface delivery qualification and hostile-boundary replay."""

import base64
import hashlib
import http.client as http_client
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
TMP = pathlib.Path(tempfile.mkdtemp(prefix="diamond-g3-delivery-"))
PG, SOCK, GITROOT = TMP / "pg", TMP / "sock", TMP / "git"
OBJECTS, DELIVERY, OPERATIONS, MAIL = TMP / "objects", TMP / "delivery", TMP / "operations", TMP / "mail"
for directory in (SOCK, GITROOT, OBJECTS, DELIVERY, OPERATIONS, MAIL):
    directory.mkdir(mode=0o700)


def free_port():
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    listener.close()
    return port


pgport, webport, pageport = free_port(), free_port(), free_port()
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
server = pages = None
server_log = pages_log = None


def check(name, condition, detail=""):
    results.append({"name": name, "pass": bool(condition)})
    if not condition:
        for filename in ("server.log", "pages.log", "postgres.log"):
            path = TMP / filename
            if path.exists():
                print(f"--- {filename} ---\n{path.read_text(errors='replace')[-12000:]}")
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


def http(port, method, path, body=None, headers=None, cookie=None):
    connection = http_client.HTTPConnection("127.0.0.1", port, timeout=20)
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


def rpc(client, contract, name, arguments):
    return http(
        webport,
        "POST",
        "/_diamond/rpc/" + name,
        {"contract": contract, "arguments": arguments},
        {"X-Diamond-CSRF": client["csrf"]},
        client["cookie"],
    )


def login(username, password):
    status, headers, session = http(
        webport, "GET", "/_diamond/session", headers={"X-Diamond-Session": "1"}
    )
    check("bootstrap authenticated session", status == 200, str(session))
    cookie = headers["set-cookie"].split(";", 1)[0]
    status, headers, reply = http(
        webport,
        "POST",
        "/_diamond/login",
        {"username": username, "password": password},
        {"X-Diamond-CSRF": session["csrf"]},
        cookie,
    )
    check("login administrator", status == 200, str(reply))
    return {"cookie": headers["set-cookie"].split(";", 1)[0], "csrf": reply["csrf"]}


def value(reply):
    return reply["value"]


def wait_http(port, path="/"):
    for _ in range(300):
        try:
            status, _, body = http(port, "GET", path)
            if status < 500:
                return status, body
        except Exception:
            pass
        time.sleep(0.05)
    details = []
    for filename in ("server.log", "pages.log", "postgres.log"):
        candidate = TMP / filename
        if candidate.exists():
            details.append(f"--- {filename} ---\n{candidate.read_text(errors='replace')[-12000:]}")
    raise AssertionError(f"listener {port} did not start\n" + "\n".join(details))


try:
    ok(
        "initialize PostgreSQL cluster",
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
    ok("initialize application schema", [str(BIN), "project-init", PROJECT, "--postgres-env", "DIAMOND_POSTGRES"])
    ok(
        "initialize attachment store",
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
    ok(
        "initialize operations store",
        [str(BIN), "project-ops-init", PROJECT, "--postgres-env", "DIAMOND_POSTGRES", "--runtime-env", "DIAMOND_RUNTIME"],
    )
    password = "Synthetic G3 password 91!"
    ok(
        "provision administrator",
        [str(BIN), "project-admin-add", PROJECT, "--postgres-env", "DIAMOND_POSTGRES", "--user", "alice", "--email", "alice@example.test", "--password-stdin"],
        input=password + "\n",
    )
    server_log = open(TMP / "server.log", "wb")
    server = subprocess.Popen(
        [str(BIN), "serve", PROJECT, "--listen", f"127.0.0.1:{webport}", "--origin", f"http://127.0.0.1:{webport}", "--postgres-env", "DIAMOND_POSTGRES", "--runtime-env", "DIAMOND_RUNTIME", "--identity-mailbox", str(MAIL)],
        cwd=ROOT,
        env=env,
        stdout=server_log,
        stderr=subprocess.STDOUT,
    )
    _, page = wait_http(webport)
    text = page.decode() if isinstance(page, bytes) else str(page)
    check("delivery UI is rendered", "delivery" in text.lower() and "/delivery" in text, text[:500])
    contract = re.search(r'data-contract="([0-9a-f]{64})"', text).group(1)
    alice = login("alice", password)
    status, _, reply = rpc(alice, contract, "create_organization", ["g3-team"])
    check("create organization", status == 200, str(reply))
    organization = value(reply)
    status, _, reply = rpc(alice, contract, "create_repository", [organization, "delivery", "G3 delivery target", False])
    check("create private repository", status == 200, str(reply))
    repository = value(reply)
    status, _, reply = rpc(alice, contract, "initialize_repository", [repository])
    check("initialize repository", status == 200, str(reply))
    token_request = {
        "label": "g3-redteam",
        "operations": ["git:repos:read", "git:repos:write"],
        "scopes": {"repository": [repository]},
        "lifetime_seconds": "3600",
    }
    status, _, reply = http(
        webport,
        "POST",
        "/_diamond/accounts/create_token",
        token_request,
        {"X-Diamond-CSRF": alice["csrf"]},
        alice["cookie"],
    )
    check("create repository-scoped credential", status == 200, str(reply))
    token = reply["value"]
    authorization = "Basic " + base64.b64encode(f"diamond:{token}".encode()).decode()
    git_url = f"http://diamond:{token}@127.0.0.1:{webport}/_diamond/git/repos/{repository}.git"
    clone = TMP / "clone"
    ok("clone empty repository", ["git", "clone", git_url, str(clone)])
    ok("configure author email", ["git", "config", "user.email", "alice@example.test"], cwd=clone)
    ok("configure author name", ["git", "config", "user.name", "Alice"], cwd=clone)
    pipeline = {
        "version": 1,
        "jobs": [
            {
                "name": "publish",
                "script": "echo sandbox-start\ntest ! -e /etc/passwd || { echo etc-visible; exit 71; }\necho filesystem-isolated\nif /usr/bin/curl --max-time 1 http://127.0.0.1:9 >/dev/null 2>&1; then echo network-visible; exit 70; fi\necho network-isolated\nmkdir -p out site\nprintf 'artifact-g3\\n' > out/result.txt\nprintf '<!doctype html><title>Diamond G3</title><h1>Published from Diamond</h1>\\n' > site/index.html\nprintf 'oci-layer-g3\\n' > image.tar\nprintf 'cache-g3\\n' > cache.bin\necho outputs-ready",
                "timeout_seconds": 15,
                "artifact": {"name": "release-bundle", "path": "out/result.txt"},
                "cache": {"key": "main-cache", "path": "cache.bin"},
                "pages": {"site": "docs", "path": "site"},
                "oci": {"name": "diamond", "tag": "g3", "path": "image.tar"},
                "environment": "preview",
            }
        ],
    }
    (clone / ".diamond-ci.json").write_text(json.dumps(pipeline, separators=(",", ":")))
    (clone / "README.md").write_text("G3 delivery fixture\n")
    ok("stage delivery fixture", ["git", "add", ".diamond-ci.json", "README.md"], cwd=clone)
    ok("commit delivery fixture", ["git", "commit", "-m", "G3 delivery fixture"], cwd=clone)
    ok("push delivery fixture", ["git", "push", git_url, "HEAD:refs/heads/main"], cwd=clone)
    commit = run(["git", "rev-parse", "HEAD"], cwd=clone).stdout.strip()
    status, _, reply = rpc(alice, contract, "schedule_pipeline", [repository, "refs/heads/main", "push-main-1"])
    check("schedule exact pushed commit", status == 200, str(reply))
    pipeline_id = value(reply)
    status, _, duplicate = rpc(alice, contract, "schedule_pipeline", [repository, "refs/heads/main", "push-main-1"])
    check("duplicate push event is idempotent", status == 200 and value(duplicate) == pipeline_id, str(duplicate))
    completed = ok(
        "run one isolated delivery job",
        [str(BIN), "project-delivery-work", PROJECT, "--postgres-env", "DIAMOND_POSTGRES", "--runtime-env", "DIAMOND_RUNTIME"],
    )
    probe_status, _, probe_reply = rpc(alice, contract, "delivery_jobs", [repository, pipeline_id])
    check(
        "worker reports completion",
        json.loads(completed.stdout)["completed"] == 1,
        completed.stdout + " " + str((probe_status, probe_reply)),
    )
    status, _, reply = rpc(alice, contract, "delivery_pipelines", [repository])
    cards = value(reply)["cards"]
    check("pipeline succeeds at immutable commit", status == 200 and cards[0]["state"] == "succeeded" and cards[0]["commit"] == commit, str(reply))
    status, _, reply = rpc(alice, contract, "delivery_jobs", [repository, pipeline_id])
    check("sandbox job succeeds with bounded log", status == 200 and value(reply)["cards"][0]["state"] == "succeeded", str(reply))
    status, _, reply = rpc(alice, contract, "delivery_artifacts", [repository, pipeline_id])
    artifact = value(reply)["cards"][0]
    status, headers, body = http(
        webport,
        "GET",
        f"/_diamond/delivery/builds/{repository}/artifacts/{artifact['id']}",
        headers={"Authorization": authorization},
    )
    check("artifact downloads through scoped credential", status == 200 and body == b"artifact-g3\n" and headers.get("docker-content-digest") == "sha256:" + hashlib.sha256(body).hexdigest(), str((status, headers, body)))
    status, _, _ = http(webport, "GET", f"/_diamond/delivery/builds/{repository}/artifacts/{artifact['id']}")
    check("private artifact denies anonymous access", status == 401, str(status))
    status, _, reply = rpc(alice, contract, "create_repository", [organization, "other", "Cross-scope probe", False])
    other_repository = value(reply)
    check("create cross-scope repository", status == 200, str(reply))
    status, _, reply = rpc(alice, contract, "initialize_repository", [other_repository])
    check("initialize cross-scope repository", status == 200, str(reply))
    status, _, _ = http(
        webport,
        "GET",
        f"/_diamond/delivery/builds/{other_repository}/artifacts/{artifact['id']}",
        headers={"Authorization": authorization},
    )
    check("repository token cannot cross artifact scope", status == 403, str(status))
    manifest_path = f"/v2/builds/{repository}/diamond/manifests/g3"
    status, headers, manifest = http(webport, "GET", manifest_path, headers={"Authorization": authorization})
    check("OCI manifest is distribution-readable", status == 200 and manifest["schemaVersion"] == 2 and headers.get("docker-content-digest", "").startswith("sha256:"), str((status, manifest)))
    manifest_digest = headers["docker-content-digest"]
    status, digest_headers, digest_body = http(
        webport,
        "HEAD",
        f"/v2/builds/{repository}/diamond/manifests/{manifest_digest}",
        headers={"Authorization": authorization},
    )
    check("OCI manifest resolves by digest with bodyless HEAD", status == 200 and digest_body == b"" and digest_headers.get("docker-content-digest") == manifest_digest, str((status, digest_headers, digest_body)))
    layer = manifest["layers"][0]
    status, headers, body = http(webport, "GET", f"/v2/builds/{repository}/diamond/blobs/{layer['digest']}", headers={"Authorization": authorization})
    check("OCI layer digest and bytes agree", status == 200 and hashlib.sha256(body).hexdigest() == layer["digest"].split(":", 1)[1] and body == b"oci-layer-g3\n", str((status, body)))
    status, _, tags = http(webport, "GET", f"/v2/builds/{repository}/diamond/tags/list", headers={"Authorization": authorization})
    check("OCI tags list is repository scoped", status == 200 and tags == {"name": "diamond", "tags": ["g3"]}, str(tags))
    pages_log = open(TMP / "pages.log", "wb")
    pages = subprocess.Popen(
        [str(BIN), "project-pages-serve", PROJECT, "--listen", f"127.0.0.1:{pageport}", "--binding", "builds", "--postgres-env", "DIAMOND_POSTGRES", "--runtime-env", "DIAMOND_RUNTIME"],
        cwd=ROOT,
        env=env,
        stdout=pages_log,
        stderr=subprocess.STDOUT,
    )
    status, page_body = wait_http(pageport, "/docs/")
    check("Pages deployment is publicly served", status == 200 and b"Published from Diamond" in page_body, str((status, page_body)))
    status, headers, _ = http(pageport, "GET", "/docs/")
    check("Pages listener sets isolation headers and no cookie", status == 200 and "content-security-policy" in headers and "set-cookie" not in headers, str(headers))
    status, _, _ = http(pageport, "GET", "/docs/", headers={"Cookie": "session=attacker"})
    check("Pages listener rejects ambient cookies", status == 400, str(status))
    status, _, _ = http(pageport, "GET", "/docs/%2e%2e/%2e%2e/etc/passwd")
    check("Pages traversal is unavailable", status in (400, 404), str(status))
    status, _, reply = rpc(alice, contract, "create_release", [repository, "refs/heads/main", "G3 release", "Pipeline, OCI, and Pages qualified", False])
    check("successful pipeline commit can become a release", status == 200 and len(value(reply)) == 64, str(reply))

    pipeline["jobs"][0]["script"] = "test \"$(cat cache.bin)\" = cache-g3\necho cache-restored\nmkdir -p out site\nprintf 'artifact-g3-cache\\n' > out/result.txt\nprintf '<!doctype html><title>Diamond G3 cache</title><h1>Published from the restored Diamond cache</h1>\\n' > site/index.html\nprintf 'oci-layer-cache\\n' > image.tar"
    (clone / ".diamond-ci.json").write_text(json.dumps(pipeline, separators=(",", ":")))
    ok("stage cache consumer", ["git", "add", ".diamond-ci.json"], cwd=clone)
    ok("commit cache consumer", ["git", "commit", "-m", "consume trusted cache"], cwd=clone)
    ok("push cache consumer", ["git", "push", git_url, "HEAD:refs/heads/main"], cwd=clone)
    status, _, reply = rpc(alice, contract, "schedule_pipeline", [repository, "refs/heads/main", "cache-restore-1"])
    cache_pipeline = value(reply)
    check("schedule cache consumer", status == 200, str(reply))
    cache_run = ok(
        "run cache consumer",
        [str(BIN), "project-delivery-work", PROJECT, "--postgres-env", "DIAMOND_POSTGRES", "--runtime-env", "DIAMOND_RUNTIME"],
    )
    status, _, reply = rpc(alice, contract, "delivery_jobs", [repository, cache_pipeline])
    cache_jobs = value(reply)["cards"]
    check("trusted reference cache restores before execution", json.loads(cache_run.stdout)["completed"] == 1 and cache_jobs[0]["state"] == "succeeded" and "cache-restored" in cache_jobs[0]["log"], str(reply))

    ok("create symlink-output branch", ["git", "switch", "-c", "symlink-output"], cwd=clone)
    symlink_pipeline = {
        "version": 1,
        "jobs": [{"name": "leak", "script": "ln -s /usr/bin/env leaked", "artifact": {"name": "leaked-host-file", "path": "leaked"}}],
    }
    (clone / ".diamond-ci.json").write_text(json.dumps(symlink_pipeline))
    ok("stage symlink-output configuration", ["git", "add", ".diamond-ci.json"], cwd=clone)
    ok("commit symlink-output configuration", ["git", "commit", "-m", "attempt output symlink"], cwd=clone)
    ok("push symlink-output configuration", ["git", "push", git_url, "HEAD:refs/heads/symlink-output"], cwd=clone)
    status, _, reply = rpc(alice, contract, "schedule_pipeline", [repository, "refs/heads/symlink-output", "symlink-output-1"])
    symlink_pipeline_id = value(reply)
    check("schedule output-symlink attack", status == 200, str(reply))
    symlink_run = ok(
        "run output-symlink attack",
        [str(BIN), "project-delivery-work", PROJECT, "--postgres-env", "DIAMOND_POSTGRES", "--runtime-env", "DIAMOND_RUNTIME"],
    )
    status, _, reply = rpc(alice, contract, "delivery_jobs", [repository, symlink_pipeline_id])
    check("artifact symlink cannot publish a host file", json.loads(symlink_run.stdout)["completed"] == 0 and value(reply)["cards"][0]["state"] == "failed", str(reply))
    status, _, reply = rpc(alice, contract, "delivery_artifacts", [repository, symlink_pipeline_id])
    check("failed symlink attack publishes no artifact", status == 200 and value(reply)["cards"] == [], str(reply))

    ok("return to main for dependency attack", ["git", "switch", "main"], cwd=clone)
    ok("create dependency-failure branch", ["git", "switch", "-c", "dependency-failure"], cwd=clone)
    dependency_pipeline = {
        "version": 1,
        "jobs": [
            {"name": "first", "script": "echo expected-failure; exit 9"},
            {"name": "dependent", "needs": ["first"], "script": "echo must-not-run"},
        ],
    }
    (clone / ".diamond-ci.json").write_text(json.dumps(dependency_pipeline))
    ok("stage dependency-failure configuration", ["git", "add", ".diamond-ci.json"], cwd=clone)
    ok("commit dependency-failure configuration", ["git", "commit", "-m", "dependency failure"], cwd=clone)
    ok("push dependency-failure configuration", ["git", "push", git_url, "HEAD:refs/heads/dependency-failure"], cwd=clone)
    status, _, reply = rpc(alice, contract, "schedule_pipeline", [repository, "refs/heads/dependency-failure", "dependency-failure-1"])
    dependency_id = value(reply)
    check("schedule dependency-failure pipeline", status == 200, str(reply))
    ok(
        "run failing dependency root",
        [str(BIN), "project-delivery-work", PROJECT, "--postgres-env", "DIAMOND_POSTGRES", "--runtime-env", "DIAMOND_RUNTIME"],
    )
    status, _, reply = rpc(alice, contract, "delivery_jobs", [repository, dependency_id])
    states = {card["name"]: card["state"] for card in value(reply)["cards"]}
    check("failed job fences and cancels dependent work", states == {"first": "failed", "dependent": "cancelled"}, str(reply))

    ok("return to main for invalid configuration", ["git", "switch", "main"], cwd=clone)
    ok("create hostile configuration branch", ["git", "switch", "-c", "invalid-config"], cwd=clone)
    hostile = {"version": 1, "jobs": [{"name": "escape", "script": "true", "artifact": {"name": "escape", "path": "../etc/passwd"}}]}
    (clone / ".diamond-ci.json").write_text(json.dumps(hostile))
    ok("commit hostile configuration", ["git", "add", ".diamond-ci.json"], cwd=clone)
    ok("record hostile configuration", ["git", "commit", "-m", "hostile path"], cwd=clone)
    ok("push hostile configuration", ["git", "push", git_url, "HEAD:refs/heads/invalid-config"], cwd=clone)
    status, _, _ = rpc(alice, contract, "schedule_pipeline", [repository, "refs/heads/invalid-config", "hostile-path-1"])
    check("pipeline parser rejects artifact traversal before execution", status == 400, str(status))

    ok("return to valid configuration", ["git", "switch", "main"], cwd=clone)
    (clone / "cancel.txt").write_text("cancel\n")
    ok("stage cancellation commit", ["git", "add", "cancel.txt"], cwd=clone)
    ok("commit cancellation target", ["git", "commit", "-m", "cancel target"], cwd=clone)
    ok("push cancellation target", ["git", "push", git_url, "HEAD:refs/heads/main"], cwd=clone)
    status, _, reply = rpc(alice, contract, "schedule_pipeline", [repository, "refs/heads/main", "cancel-1"])
    cancel_id = value(reply)
    check("schedule cancellation target", status == 200, str(reply))
    status, _, reply = rpc(alice, contract, "cancel_pipeline", [repository, cancel_id])
    check("cancel fences pending pipeline", status == 200 and value(reply) == "cancelled", str(reply))
    idle = ok(
        "worker skips cancelled jobs",
        [str(BIN), "project-delivery-work", PROJECT, "--postgres-env", "DIAMOND_POSTGRES", "--runtime-env", "DIAMOND_RUNTIME"],
    )
    check("cancelled queue remains idle", json.loads(idle.stdout)["completed"] == 0, idle.stdout)
    status, _, conflict = rpc(alice, contract, "schedule_pipeline", [repository, "refs/heads/main", "push-main-1"])
    check("event identity cannot be rebound to another commit", status == 409, str(conflict))

    marker = json.loads((DELIVERY / ".diamond-delivery-store").read_text())
    check("delivery marker is private and app-bound", (DELIVERY / ".diamond-delivery-store").stat().st_mode & 0o777 == 0o600 and marker["binding"] == "builds", str(marker))
    check("runner workspaces are removed", not any((DELIVERY / "jobs").iterdir()))
    check("content-addressed objects are private files", all(path.stat().st_mode & 0o777 == 0o600 for path in (DELIVERY / "objects").iterdir()))
    if "--serve-fixture" in sys.argv:
        print(
            json.dumps(
                {
                    "browser_fixture": f"http://127.0.0.1:{webport}/delivery",
                    "pages_fixture": f"http://127.0.0.1:{pageport}/docs/",
                    "username": "alice",
                    "password": password,
                    "repository": repository,
                    "pipeline": pipeline_id,
                }
            ),
            flush=True,
        )
        while True:
            time.sleep(1)

finally:
    for process in (pages, server):
        if process is not None:
            process.send_signal(signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
    for stream in (pages_log, server_log):
        if stream is not None:
            stream.close()
    if PG.exists():
        subprocess.run(["pg_ctl", "-D", str(PG), "-m", "immediate", "stop"], env=env, capture_output=True)
    receipt = {
        "profile": "diamond-forge-g3-delivery",
        "passed": sum(1 for result in results if result["pass"]),
        "total": len(results),
        "results": results,
    }
    (ROOT / "verification/git_delivery_redteam_results.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt, indent=2))
    shutil.rmtree(TMP, ignore_errors=True)
