#!/usr/bin/env python3
"""G3 delivery and G5 product real-interface qualification replay."""

import base64
import argparse
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
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

ROOT = pathlib.Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--binary', type=pathlib.Path, default=ROOT / 'target/debug/diamond')
parser.add_argument('--report', type=pathlib.Path, default=ROOT / 'verification/git_delivery_redteam_results.json')
parser.add_argument('--serve-fixture', action='store_true')
options = parser.parse_args()
BIN = options.binary.resolve()
PROJECT = "examples/git-browser/diamond.toml"
TMP_PARENT = ROOT / "target" / "verification-tmp"
TMP_PARENT.mkdir(parents=True, exist_ok=True)
TMP = pathlib.Path(tempfile.mkdtemp(prefix="diamond-g3-delivery-", dir=TMP_PARENT))
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
            "credential_env": "DIA_WEBHOOK_SECRET",
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
    DIA_WEBHOOK_SECRET="synthetic-webhook-secret",
    GIT_TERMINAL_PROMPT="0",
    GIT_CONFIG_COUNT="1",
    GIT_CONFIG_KEY_0="credential.helper",
    GIT_CONFIG_VALUE_0="",
)
results = []
metrics = {}
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


def raw_http(port, method, path, body=None, headers=None):
    connection = http_client.HTTPConnection("127.0.0.1", port, timeout=60)
    connection.request(method, path, body=body, headers=dict(headers or {}))
    response = connection.getresponse()
    payload = response.read()
    response_headers = {key.lower(): value for key, value in response.getheaders()}
    connection.close()
    return response.status, response_headers, payload


def rss_kib(pid):
    try:
        for line in pathlib.Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1])
    except (FileNotFoundError, ProcessLookupError):
        pass
    return 0


def stream_get(target, barrier):
    port, path, headers, expected_size, expected_digest = target
    connection = http_client.HTTPConnection("127.0.0.1", port, timeout=60)
    barrier.wait(timeout=10)
    connection.request("GET", path, headers=headers)
    response = connection.getresponse()
    digest = hashlib.sha256()
    size = 0
    while chunk := response.read(65536):
        digest.update(chunk)
        size += len(chunk)
        time.sleep(0.0005)
    status = response.status
    connection.close()
    return status, size, digest.hexdigest(), expected_size, expected_digest


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
    bob_password = "Synthetic G5 password 92!"
    ok(
        "provision ordinary user",
        [str(BIN), "project-user-add", PROJECT, "--postgres-env", "DIAMOND_POSTGRES", "--user", "bob", "--password-stdin"],
        input=bob_password + "\n",
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
    # The current UX shows the sign-in shell before private delivery navigation.
    check("Forge sign-in shell is rendered", "DiamondForge" in text and 'data-contract=' in text and 'data-diamond-component=' in text, text[:500])
    contract = re.search(r'data-contract="([0-9a-f]{64})"', text).group(1)
    alice = login("alice", password)
    bob = login("bob", bob_password)
    status, _, reply = rpc(alice, contract, "create_organization", ["g3-team"])
    check("create organization", status == 200, str(reply))
    organization = value(reply)
    status, _, reply = rpc(alice, contract, "create_repository", [organization, "delivery", "G3 delivery target", False])
    check("create private repository", status == 200, str(reply))
    repository = value(reply)
    status, _, reply = rpc(alice, contract, "grant_developer", [organization, "bob"])
    bob_membership = value(reply)
    check("grant ordinary user repository access", status == 200, str(reply))
    status, _, reply = rpc(alice, contract, "initialize_repository", [repository])
    check("initialize repository", status == 200, str(reply))
    status, _, reply = rpc(bob, contract, "notification_preferences", [repository])
    check(
        "notification preferences default to enabled and all activity",
        status == 200 and value(reply)["enabled"] is True and value(reply)["unread_only"] is False,
        str(reply),
    )
    status, _, reply = rpc(bob, contract, "save_notification_preferences", [repository, False, False])
    check("ordinary user can disable own repository notifications", status == 200 and value(reply)["enabled"] is False, str(reply))
    status, _, reply = rpc(alice, contract, "create_issue", [repository, "G5 preference probe", "Synthetic notification preference fixture"])
    issue = value(reply)
    check("create notification preference issue", status == 200, str(reply))
    status, _, reply = rpc(alice, contract, "assign_issue", [issue, "bob"])
    check("assign issue while recipient notifications are disabled", status == 200, str(reply))
    status, _, reply = rpc(bob, contract, "collaboration_dashboard", [organization, repository])
    check("disabled preference hides the recipient notification", status == 200 and value(reply)["notifications"] == [], str(reply))
    status, _, reply = rpc(bob, contract, "save_notification_preferences", [repository, True, True])
    check("recipient can select unread-only notifications", status == 200 and value(reply)["unread_only"] is True, str(reply))
    status, _, reply = rpc(bob, contract, "collaboration_dashboard", [organization, repository])
    notices = value(reply)["notifications"]
    check("re-enabled unread preference reveals the unread assignment", status == 200 and len(notices) == 1, str(reply))
    status, _, reply = rpc(bob, contract, "mark_notification_read", [notices[0]["id"]])
    check("recipient can mark preferred notification read", status == 200, str(reply))
    status, _, reply = rpc(bob, contract, "collaboration_dashboard", [organization, repository])
    check("unread-only preference hides read notifications", status == 200 and value(reply)["notifications"] == [], str(reply))
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
                "executor": "standard",
                "script": "echo sandbox-start\ntest ! -e /etc/passwd || { echo etc-visible; exit 71; }\necho filesystem-isolated\nif /usr/bin/curl --max-time 1 http://127.0.0.1:9 >/dev/null 2>&1; then echo network-visible; exit 70; fi\necho network-isolated\nmkdir -p out site\nprintf 'artifact-g3\\n' > out/result.txt\n/usr/bin/head -c 16777204 /dev/zero >> out/result.txt\nprintf '<!doctype html><title>Diamond G3</title><h1>Published from Diamond</h1>\\n' > site/index.html\n/usr/bin/head -c 16777216 /dev/zero > site/large.bin\nprintf 'oci-layer-g3\\n' > image.tar\n/usr/bin/head -c 16777203 /dev/zero >> image.tar\nprintf 'cache-g3\\n' > cache.bin\necho outputs-ready",
                "timeout_seconds": 30,
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
    (clone / "résumé.txt").write_text("Unicode repository path: λ\n")
    ok("stage delivery fixture", ["git", "add", ".diamond-ci.json", "README.md", "résumé.txt"], cwd=clone)
    ok("commit delivery fixture", ["git", "commit", "-m", "G5 delivery fixture – Unicode"], cwd=clone)
    ok("create annotated export tag", ["git", "tag", "-a", "v1.0-g5", "-m", "G5 export tag"], cwd=clone)
    ok("create Git notes export ref", ["git", "notes", "add", "-m", "G5 migration note"], cwd=clone)
    ok(
        "push delivery fixture with tag and notes",
        ["git", "push", git_url, "HEAD:refs/heads/main", "refs/tags/v1.0-g5", "refs/notes/commits"],
        cwd=clone,
    )
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
    large_bytes = 16 * 1024 * 1024
    status, headers, body = http(
        webport,
        "GET",
        f"/_diamond/delivery/builds/{repository}/artifacts/{artifact['id']}",
        headers={"Authorization": authorization},
    )
    artifact_digest = hashlib.sha256(body).hexdigest()
    check(
        "large artifact downloads through scoped credential",
        status == 200
        and len(body) == large_bytes
        and body[:12] == b"artifact-g3\n"
        and body[12:] == bytes(large_bytes - 12)
        and headers.get("docker-content-digest") == "sha256:" + artifact_digest,
        str((status, headers, len(body))),
    )
    etag = headers.get("etag")
    status, range_headers, range_body = http(
        webport,
        "GET",
        f"/_diamond/delivery/builds/{repository}/artifacts/{artifact['id']}",
        headers={"Authorization": authorization, "Range": "bytes=1-5"},
    )
    check(
        "artifact supports a bounded single byte range",
        status == 206 and range_body == b"rtifa" and range_headers.get("content-range") == f"bytes 1-5/{large_bytes}",
        str((status, range_headers, range_body)),
    )
    status, suffix_headers, suffix_body = http(
        webport,
        "GET",
        f"/_diamond/delivery/builds/{repository}/artifacts/{artifact['id']}",
        headers={"Authorization": authorization, "Range": "bytes=-4"},
    )
    check(
        "artifact supports a suffix byte range",
        status == 206 and suffix_body == bytes(4) and suffix_headers.get("content-range") == f"bytes {large_bytes - 4}-{large_bytes - 1}/{large_bytes}",
        str((status, suffix_headers, suffix_body)),
    )
    status, open_headers, open_body = http(
        webport,
        "HEAD",
        f"/_diamond/delivery/builds/{repository}/artifacts/{artifact['id']}",
        headers={"Authorization": authorization, "Range": f"bytes={large_bytes - 3}-"},
    )
    check(
        "artifact HEAD applies an open range without a body",
        status == 206 and open_body == b"" and open_headers.get("content-range") == f"bytes {large_bytes - 3}-{large_bytes - 1}/{large_bytes}" and open_headers.get("content-length") == "3",
        str((status, open_headers, open_body)),
    )
    status, _, multiple_body = http(
        webport,
        "GET",
        f"/_diamond/delivery/builds/{repository}/artifacts/{artifact['id']}",
        headers={"Authorization": authorization, "Range": "bytes=0-1,4-5"},
    )
    check("artifact rejects unsupported multiple ranges", status == 416 and multiple_body == b"", str(status))
    status, invalid_headers, invalid_body = http(
        webport,
        "GET",
        f"/_diamond/delivery/builds/{repository}/artifacts/{artifact['id']}",
        headers={"Authorization": authorization, "Range": f"bytes={large_bytes + 1}-{large_bytes + 2}"},
    )
    check(
        "artifact rejects an unsatisfiable range without streaming bytes",
        status == 416 and invalid_body == b"" and invalid_headers.get("content-range") == f"bytes */{large_bytes}",
        str((status, invalid_headers, invalid_body)),
    )
    status, conditional_headers, conditional_body = http(
        webport,
        "GET",
        f"/_diamond/delivery/builds/{repository}/artifacts/{artifact['id']}",
        headers={"Authorization": authorization, "If-None-Match": etag},
    )
    check(
        "artifact ETag supports a bodyless conditional read",
        status == 304 and conditional_body == b"" and conditional_headers.get("etag") == etag,
        str((status, conditional_headers, conditional_body)),
    )
    status, _, conditional_body = http(
        webport,
        "GET",
        f"/_diamond/delivery/builds/{repository}/artifacts/{artifact['id']}",
        headers={"Authorization": authorization, "If-None-Match": f'"other", W/{etag}'},
    )
    check("artifact conditional read accepts an ETag list and weak comparison", status == 304 and conditional_body == b"", str(status))
    status, _, if_range_body = http(
        webport,
        "GET",
        f"/_diamond/delivery/builds/{repository}/artifacts/{artifact['id']}",
        headers={"Authorization": authorization, "Range": "bytes=1-5", "If-Range": etag},
    )
    check("matching strong If-Range serves the selected bytes", status == 206 and if_range_body == b"rtifa", str((status, if_range_body)))
    status, _, if_range_body = http(
        webport,
        "GET",
        f"/_diamond/delivery/builds/{repository}/artifacts/{artifact['id']}",
        headers={"Authorization": authorization, "Range": "bytes=1-5", "If-Range": '"stale"'},
    )
    check("stale If-Range falls back to the complete representation", status == 200 and if_range_body == body, str((status, if_range_body)))
    status, _, _ = http(webport, "GET", f"/_diamond/delivery/builds/{repository}/artifacts/{artifact['id']}")
    check("private artifact denies anonymous access", status == 401, str(status))
    status, _, reply = rpc(alice, contract, "create_repository", [organization, "other", "Cross-scope probe", False])
    other_repository = value(reply)
    check("create cross-scope repository", status == 200, str(reply))
    status, _, reply = rpc(alice, contract, "initialize_repository", [other_repository])
    check("initialize cross-scope repository", status == 200, str(reply))
    mirror = TMP / "export.git"
    ok("export repository with stock Git mirror clone", ["git", "clone", "--mirror", git_url, str(mirror)])
    exported_refs = run(["git", "--git-dir", str(mirror), "show-ref"]).stdout
    check(
        "mirror export preserves branch, annotated tag, and notes refs",
        all(reference in exported_refs for reference in ("refs/heads/main", "refs/tags/v1.0-g5", "refs/notes/commits")),
        exported_refs,
    )
    import_token_request = {
        "label": "g5-import",
        "operations": ["git:repos:read", "git:repos:write"],
        "scopes": {"repository": [other_repository]},
        "lifetime_seconds": "3600",
    }
    status, _, import_reply = http(
        webport,
        "POST",
        "/_diamond/accounts/create_token",
        import_token_request,
        {"X-Diamond-CSRF": alice["csrf"]},
        alice["cookie"],
    )
    check("create destination-scoped import credential", status == 200, str(import_reply))
    import_url = f"http://diamond:{import_reply['value']}@127.0.0.1:{webport}/_diamond/git/repos/{other_repository}.git"
    ok("import all refs with stock Git mirror push", ["git", "push", "--mirror", import_url], cwd=mirror)
    imported_refs = run(["git", "ls-remote", import_url]).stdout
    check(
        "mirror import preserves branch, annotated tag, and notes refs",
        all(reference in imported_refs for reference in ("refs/heads/main", "refs/tags/v1.0-g5", "refs/notes/commits")),
        imported_refs,
    )
    imported = TMP / "imported"
    ok("clone imported repository through smart HTTP", ["git", "clone", import_url, str(imported)])
    check("Unicode path and bytes survive export/import", (imported / "résumé.txt").read_text() == "Unicode repository path: λ\n")
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
    oci_digest = hashlib.sha256(body).hexdigest()
    check(
        "large OCI layer digest and bytes agree",
        status == 200
        and len(body) == large_bytes
        and body[:13] == b"oci-layer-g3\n"
        and body[13:] == bytes(large_bytes - 13)
        and oci_digest == layer["digest"].split(":", 1)[1],
        str((status, len(body))),
    )
    status, _, tags = http(webport, "GET", f"/v2/builds/{repository}/diamond/tags/list", headers={"Authorization": authorization})
    check("OCI tags list is repository scoped", status == 200 and tags == {"name": "diamond", "tags": ["g3"]}, str(tags))

    lfs_base = f"/_diamond/git/repos/{repository}.git/info/lfs"
    lfs_data = b"L" * large_bytes
    lfs_digest = hashlib.sha256(lfs_data).hexdigest()
    lfs_headers = {
        "Authorization": authorization,
        "Content-Type": "application/vnd.git-lfs+json",
        "Accept": "application/vnd.git-lfs+json",
    }
    lfs_batch = json.dumps(
        {
            "operation": "upload",
            "transfers": ["basic"],
            "hash_algo": "sha256",
            "objects": [{"oid": lfs_digest, "size": large_bytes}],
        }
    ).encode()
    status, _, payload = raw_http(webport, "POST", lfs_base + "/objects/batch", lfs_batch, lfs_headers)
    check("large LFS upload receives a bounded action", status == 200, str((status, payload[-1000:])))
    lfs_upload_path = urllib.parse.urlsplit(json.loads(payload)["objects"][0]["actions"]["upload"]["href"]).path
    status, _, _ = raw_http(
        webport,
        "PUT",
        lfs_upload_path,
        lfs_data,
        {"Authorization": authorization, "Content-Type": "application/octet-stream", "Content-Length": str(large_bytes)},
    )
    check("large LFS upload is digest verified", status == 200, str(status))
    lfs_download_batch = json.dumps(
        {"operation": "download", "objects": [{"oid": lfs_digest, "size": large_bytes}]}
    ).encode()
    status, _, payload = raw_http(webport, "POST", lfs_base + "/objects/batch", lfs_download_batch, lfs_headers)
    check("large LFS download receives a bounded action", status == 200, str((status, payload[-1000:])))
    lfs_download_path = urllib.parse.urlsplit(json.loads(payload)["objects"][0]["actions"]["download"]["href"]).path

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
    slow_streams=[]
    try:
        for _ in range(2):
            stream=socket.create_connection(("127.0.0.1",webport),timeout=5)
            stream.setsockopt(socket.SOL_SOCKET,socket.SO_RCVBUF,4096)
            stream.sendall((f"GET /_diamond/delivery/builds/{repository}/artifacts/{artifact['id']} HTTP/1.1\r\n"
                f"Host: 127.0.0.1:{webport}\r\nAuthorization: {authorization}\r\nConnection: close\r\n\r\n").encode())
            headers=b""
            while b"\r\n\r\n" not in headers:headers+=stream.recv(4096)
            check("held transfer receives authorized headers",headers.startswith(b"HTTP/1.1 200"))
            slow_streams.append(stream)
        status,_,observation=http(webport,"GET","/_diamond/health/metrics")
        check("held streams consume transfer-class capacity",status==200 and observation['workloads']['transfer']['occupied_requests']>=2)
        started=time.monotonic()
        status,_,reply=rpc(alice,contract,"notification_preferences",[repository])
        check("interactive same-account RPC proceeds during slow transfers",status==200 and time.monotonic()-started<2,str((status,reply)))
    finally:
        for stream in slow_streams:stream.close()
    until=time.monotonic()+3
    while time.monotonic()<until:
        status,_,observation=http(webport,"GET","/_diamond/health/metrics")
        if status==200 and observation['workloads']['transfer']['occupied_requests']==0:break
        time.sleep(.02)
    check("disconnected transfers release workload capacity",observation['workloads']['transfer']['occupied_requests']==0)
    stream_targets = []
    zero_digest = hashlib.sha256(bytes(large_bytes)).hexdigest()
    for _ in range(4):
        stream_targets.extend(
            [
                (
                    webport,
                    f"/_diamond/delivery/builds/{repository}/artifacts/{artifact['id']}",
                    {"Authorization": authorization},
                    large_bytes,
                    artifact_digest,
                ),
                (
                    webport,
                    f"/v2/builds/{repository}/diamond/blobs/{layer['digest']}",
                    {"Authorization": authorization},
                    large_bytes,
                    oci_digest,
                ),
                (
                    webport,
                    lfs_download_path,
                    {"Authorization": authorization},
                    large_bytes,
                    lfs_digest,
                ),
                (
                    pageport,
                    "/docs/large.bin",
                    {},
                    large_bytes,
                    zero_digest,
                ),
            ]
        )
    barrier = threading.Barrier(len(stream_targets))
    web_baseline = rss_kib(server.pid)
    pages_baseline = rss_kib(pages.pid)
    web_peak = web_baseline
    pages_peak = pages_baseline
    with ThreadPoolExecutor(max_workers=len(stream_targets)) as executor:
        futures = [executor.submit(stream_get, target, barrier) for target in stream_targets]
        while not all(future.done() for future in futures):
            web_peak = max(web_peak, rss_kib(server.pid))
            pages_peak = max(pages_peak, rss_kib(pages.pid))
            time.sleep(0.002)
        streamed = [future.result() for future in futures]
    check(
        "sixteen mixed artifact, OCI, LFS, and Pages streams are byte exact",
        all(status == 200 and size == expected_size and digest == expected_digest for status, size, digest, expected_size, expected_digest in streamed),
        str(streamed),
    )
    check(
        "mixed streaming keeps the web process below its aggregate memory budget",
        web_peak - web_baseline < 128 * 1024,
        f"baseline={web_baseline} peak={web_peak}",
    )
    check(
        "mixed streaming keeps the Pages process below its aggregate memory budget",
        pages_peak - pages_baseline < 64 * 1024,
        f"baseline={pages_baseline} peak={pages_peak}",
    )
    check("both listeners survive mixed streaming", server.poll() is None and pages.poll() is None)
    metrics.update(
        mixed_streams=len(stream_targets),
        mixed_stream_bytes=sum(item[1] for item in streamed),
        mixed_web_rss_baseline_kib=web_baseline,
        mixed_web_rss_peak_kib=web_peak,
        mixed_web_rss_delta_kib=web_peak - web_baseline,
        mixed_pages_rss_baseline_kib=pages_baseline,
        mixed_pages_rss_peak_kib=pages_peak,
        mixed_pages_rss_delta_kib=pages_peak - pages_baseline,
    )
    ok("create log-overflow branch", ["git", "switch", "-c", "log-overflow"], cwd=clone)
    log_overflow_pipeline = {
        "version": 1,
        "jobs": [
            {
                "name": "log_overflow",
                "executor": "standard",
                "script": "/usr/bin/head -c 1048577 /dev/zero",
                "timeout_seconds": 15,
            }
        ],
    }
    (clone / ".diamond-ci.json").write_text(json.dumps(log_overflow_pipeline, separators=(",", ":")))
    ok("stage log-overflow configuration", ["git", "add", ".diamond-ci.json"], cwd=clone)
    ok("commit log-overflow configuration", ["git", "commit", "-m", "probe bounded job logs"], cwd=clone)
    ok("push log-overflow configuration", ["git", "push", git_url, "HEAD:refs/heads/log-overflow"], cwd=clone)
    status, _, reply = rpc(alice, contract, "schedule_pipeline", [repository, "refs/heads/log-overflow", "log-overflow-1"])
    check("schedule log-overflow attack", status == 200, str(reply))
    log_overflow_id = value(reply)
    log_overflow_run = ok(
        "run log-overflow attack",
        [str(BIN), "project-delivery-work", PROJECT, "--postgres-env", "DIAMOND_POSTGRES", "--runtime-env", "DIAMOND_RUNTIME"],
    )
    status, _, reply = rpc(alice, contract, "delivery_jobs", [repository, log_overflow_id])
    check(
        "job output above one MiB fails closed",
        json.loads(log_overflow_run.stdout)["completed"] == 0
        and status == 200
        and value(reply)["cards"][0]["state"] == "failed",
        str(reply),
    )
    ok("return to main after log-overflow attack", ["git", "switch", "main"], cwd=clone)
    status, _, reply = rpc(alice, contract, "delivery_pages", [repository])
    first_deployment = value(reply)["cards"][0]["deployment"]
    check("record first immutable Pages deployment", status == 200 and len(first_deployment) == 64, str(reply))
    status, _, _ = rpc(bob, contract, "set_pages_domain", [repository, "docs.example.test", "docs"])
    check("non-administrator cannot claim a Pages custom domain", status == 403, str(status))
    status, _, reply = rpc(alice, contract, "set_pages_domain", [repository, "docs.example.test", "docs"])
    check("administrator maps a canonical custom domain to an owned site", status == 200 and value(reply) == "docs.example.test", str(reply))
    status, _, reply = rpc(alice, contract, "delivery_domains", [repository])
    check("custom-domain mapping is queryable through repository policy", status == 200 and value(reply)["cards"] == [{"domain": "docs.example.test", "site": "docs"}], str(reply))
    status, _, custom_body = http(pageport, "GET", "/", headers={"Host": "docs.example.test"})
    check("Pages routes a verified custom Host without a path prefix", status == 200 and b"Published from Diamond" in custom_body, str((status, custom_body)))
    status, _, _ = rpc(alice, contract, "set_pages_domain", [repository, "Bad_Domain.example", "docs"])
    check("custom-domain operation rejects noncanonical host syntax", status == 403, str(status))
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
    status, _, reply = rpc(alice, contract, "delivery_pages", [repository])
    second_deployment = value(reply)["cards"][0]["deployment"]
    check("second pipeline atomically activates a distinct Pages deployment", status == 200 and second_deployment != first_deployment, str(reply))
    status, _, custom_body = http(pageport, "GET", "/", headers={"Host": "docs.example.test"})
    check("custom domain follows the newly active deployment", status == 200 and b"restored Diamond cache" in custom_body, str((status, custom_body)))
    status, _, reply = rpc(alice, contract, "activate_pages_deployment", [repository, "docs", first_deployment])
    check("repository writer can roll Pages back to a retained deployment", status == 200 and value(reply) == first_deployment, str(reply))
    status, _, custom_body = http(pageport, "GET", "/", headers={"Host": "docs.example.test"})
    check("Pages rollback changes custom-domain content atomically", status == 200 and b"Published from Diamond" in custom_body and b"restored Diamond cache" not in custom_body, str((status, custom_body)))
    status, _, reply = rpc(alice, contract, "stop_delivery_environment", [repository, "preview"])
    check("repository writer stops an active environment", status == 200 and value(reply) == "preview", str(reply))
    status, _, reply = rpc(alice, contract, "delivery_environments", [repository])
    check("stopped environment persists in delivery state", status == 200 and value(reply)["cards"][0]["state"] == "stopped", str(reply))

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
    invalid_executor = {"version": 1, "jobs": [{"name": "escape", "executor": "unbounded", "script": "true"}]}
    (clone / ".diamond-ci.json").write_text(json.dumps(invalid_executor))
    ok("stage undeclared runner profile", ["git", "add", ".diamond-ci.json"], cwd=clone)
    ok("commit undeclared runner profile", ["git", "commit", "-m", "undeclared runner profile"], cwd=clone)
    ok("push undeclared runner profile", ["git", "push", git_url, "HEAD:refs/heads/invalid-executor"], cwd=clone)
    status, _, _ = rpc(alice, contract, "schedule_pipeline", [repository, "refs/heads/invalid-executor", "invalid-executor-1"])
    check("pipeline parser rejects an undeclared executor profile", status == 400, str(status))

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

    status, _, reply = rpc(alice, contract, "delete_delivery_artifact", [repository, artifact["id"]])
    check("repository writer deletes immutable artifact metadata", status == 200 and value(reply) == artifact["id"], str(reply))
    status, _, reply = rpc(alice, contract, "delivery_artifacts", [repository, pipeline_id])
    check("deleted artifact disappears from the pipeline workflow", status == 200 and value(reply)["cards"] == [], str(reply))
    status, _, _ = http(
        webport,
        "GET",
        f"/_diamond/delivery/builds/{repository}/artifacts/{artifact['id']}",
        headers={"Authorization": authorization},
    )
    check("deleted artifact metadata immediately fences downloads", status == 403, str(status))
    status, _, reply = rpc(alice, contract, "remove_pages_domain", [repository, "docs.example.test"])
    check("administrator removes a custom-domain mapping", status == 200 and value(reply) == "docs.example.test", str(reply))
    status, _, reply = rpc(alice, contract, "delivery_domains", [repository])
    check("removed custom domain disappears from repository state", status == 200 and value(reply)["cards"] == [], str(reply))
    status, _, _ = http(pageport, "GET", "/", headers={"Host": "docs.example.test"})
    check("removed custom Host no longer resolves a Pages site", status == 404, str(status))
    status, _, reply = rpc(alice, contract, "revoke_member", [bob_membership])
    check("administrator revokes notification recipient repository access", status == 200, str(reply))
    status, _, _ = rpc(bob, contract, "notification_preferences", [repository])
    check("revoked member cannot read repository notification preferences", status == 403, str(status))
    status, _, _ = rpc(bob, contract, "save_notification_preferences", [repository, True, False])
    check("revoked member cannot change repository notification preferences", status == 403, str(status))

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
        "profile": "diamond-forge-g3-g5-g6-delivery",
        "binary_sha256": hashlib.sha256(BIN.read_bytes()).hexdigest(),
        "driver_sha256": hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest(),
        "passed": sum(1 for result in results if result["pass"]),
        "total": len(results),
        "metrics": metrics,
        "results": results,
    }
    options.report.parent.mkdir(parents=True, exist_ok=True)
    options.report.write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt, indent=2))
    shutil.rmtree(TMP, ignore_errors=True)
