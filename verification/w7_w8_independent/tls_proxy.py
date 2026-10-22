#!/usr/bin/env python3
"""Real Caddy TLS edge, two actual Diamond nodes and verified native clients."""
from __future__ import annotations

import argparse
import hashlib
import http.client
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import signal
import ssl
import subprocess
import tempfile
import time

from fixture import PASSWORD, identifier, write
from receiver import Receiver
from service import Client, ENV_NAME, Service, free_port, sha256
from wire import WebSocket

BASE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("_w8_tls_previous_common", BASE.parent / "w5_independent/common.py")
previous = importlib.util.module_from_spec(spec)
spec.loader.exec_module(previous)
Receipt, seed, wait_for = previous.Receipt, previous.seed, previous.wait_for
CADDY_HASH = "678ade3bfc088749c81a681adc603333ee0bb023b6a6cfe3c0f58bef8ff854e9"


class TLSService(Service):
    def request(self, node, path, body=None, headers=None, *, method=None, source=None):
        if node != "TLS":
            return super().request(node, path, body, headers, method=method, source=source)
        headers = {"Host": self.authority, **(headers or {})}
        method = method or ("GET" if body is None else "POST")
        if body is not None:
            if not isinstance(body, (bytes, str)):
                body = json.dumps(body, ensure_ascii=True).encode()
            headers.setdefault("Content-Type", "application/json")
            headers.setdefault("Origin", self.origin)
        connection = http.client.HTTPSConnection("localhost", self.tls_port, context=self.tls_context, timeout=15)
        try:
            connection.request(method, path, body, headers)
            response = connection.getresponse()
            data = response.read()
            self.trace.append({"node": "TLS", "method": method, "path": path, "status": response.status,
                               "tls": connection.sock.version() if connection.sock else None})
            return response.status, {name.lower(): value for name, value in response.getheaders()}, data
        finally:
            connection.close()


def certificates(root):
    root.mkdir(mode=0o700)
    ca, ca_key, leaf, leaf_key, csr, ext = [root / name for name in
        ["ca.pem", "ca.key", "localhost.pem", "localhost.key", "localhost.csr", "localhost.ext"]]
    def run(*arguments):
        subprocess.run(["openssl", *map(str, arguments)], capture_output=True, check=True, timeout=20)
    run("req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", ca_key, "-out", ca,
        "-days", "1", "-subj", "/CN=Diamond W8 owned localhost test CA",
        "-addext", "basicConstraints=critical,CA:TRUE", "-addext", "keyUsage=critical,keyCertSign,cRLSign")
    run("req", "-newkey", "rsa:2048", "-nodes", "-keyout", leaf_key, "-out", csr, "-subj", "/CN=localhost")
    ext.write_text("basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\nsubjectAltName=DNS:localhost\nextendedKeyUsage=serverAuth\n")
    run("x509", "-req", "-in", csr, "-CA", ca, "-CAkey", ca_key, "-CAcreateserial", "-out", leaf,
        "-days", "1", "-extfile", ext)
    ca_key.chmod(0o600)
    leaf_key.chmod(0o600)
    return ca, leaf, leaf_key


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--caddy", type=Path, required=True)
    parser.add_argument("--report", type=Path, default=BASE / "tls_proxy_results.json")
    options = parser.parse_args()
    root = Path(tempfile.mkdtemp(prefix="diamond-w8-tls-proxy-"))
    binary, caddy = root / "diamond", root / "caddy"
    shutil.copy2(options.binary, binary)
    shutil.copy2(options.caddy, caddy)
    if sha256(caddy) != CADDY_HASH:
        raise AssertionError("Caddy does not match parent's verified official fixture binary")
    project = write(root / "project")
    with project.open("a") as manifest:
        manifest.write('''
[http_api]
version = 1
[http_api.operations.read]
function = "server.read"
auth = "token"
''')
    service, receipt = TLSService(root, binary, project), Receipt()
    receipt.secret(PASSWORD)
    receiver = proxy = log = ws = None
    fatal = None
    start = time.monotonic()
    caddyfile = None
    try:
        ca, certificate, private_key = certificates(root / "certificates")
        service.tls_port = free_port()
        service.origin = f"https://localhost:{service.tls_port}"
        service.authority = service.origin.removeprefix("https://")
        service.tls_context = ssl.create_default_context(cafile=str(ca))
        if service.tls_context.verify_mode != ssl.CERT_REQUIRED or not service.tls_context.check_hostname:
            raise AssertionError("TLS fixture must verify certificate chain and hostname")
        receiver = Receiver()
        service.runtime["http"] = {"inventory": {"origin": receiver.origin, "network": "loopback", "paths": ["/v1/"], "methods": ["POST"]}}
        service.postgres()
        contract, _ = service.build()
        service.cli("project-init", project, "--postgres-env", ENV_NAME)
        service.files_cli(initialize=True)
        service.cli("project-admin-add", project, "--postgres-env", ENV_NAME, "--user", "alice", "--email", "alice@qualification.invalid",
                    "--password-stdin", input=PASSWORD + "\n")
        seed(service)
        service.start_node("A")
        service.start_node("B")
        caddyfile = f'''{{
    admin off
    persist_config off
    auto_https off
    storage file_system {{
        root {root / "caddy-data"}
    }}
    servers {{
        protocols h1 h2
    }}
}}
https://localhost:{service.tls_port} {{
    bind 127.0.0.1
    tls {certificate} {private_key}
    reverse_proxy 127.0.0.1:{service.ports["A"]} 127.0.0.1:{service.ports["B"]} {{
        lb_policy round_robin
        header_up Host {{http.request.hostport}}
        header_up -Forwarded
        header_up -X-Forwarded-*
        health_uri /_diamond/health/ready
        health_headers {{
            Host localhost:{service.tls_port}
        }}
        health_interval 1s
    }}
}}
'''
        configuration = root / "Caddyfile"
        configuration.write_text(caddyfile)
        validated = subprocess.run([str(caddy), "validate", "--config", str(configuration), "--adapter", "caddyfile"],
                                   capture_output=True, text=True, timeout=20)
        if validated.returncode:
            raise AssertionError(("real Caddy configuration rejected", validated.stderr))
        log = (root / "caddy.log").open("w")
        proxy = subprocess.Popen([str(caddy), "run", "--config", str(configuration), "--adapter", "caddyfile"],
                                 stdout=log, stderr=log, env=os.environ | {"XDG_DATA_HOME": str(root / "xdg-data"), "XDG_CONFIG_HOME": str(root / "xdg-config")})
        def ready():
            try:
                return service.request("TLS", "/_diamond/health/ready")[0] == 200
            except (OSError, ssl.SSLError):
                return False
        wait_for(ready, "real CA-verified Caddy HTTPS listener", timeout=10)
        alice = Client(service, contract["rpc_id"])
        alice.login("alice", PASSWORD, node="TLS")
        session = alice.request("TLS", "/_diamond/session")
        receipt.record("verified-tls-cookie-login", session[0] == 200 and bool(alice.cookie) and
            any(row.get("tls") in {"TLSv1.2", "TLSv1.3"} for row in service.trace),
            {"session_status": session[0], "tls_versions": sorted({r["tls"] for r in service.trace if r.get("tls")})},
            "ordinary session/login traverses actual Caddy TLS using explicit trusted fixture CA and hostname verification")
        def rpc_count(node):
            return sum('"class":"rpc"' in line and '"status":200' in line for line in service.node_logs[node].read_text().splitlines())
        before = {node: rpc_count(node) for node in ["A", "B"]}
        replies = [alice.rpc("read", {"tag": "issue", "value": identifier("issue-red-one")}, node="TLS") for _ in range(20)]
        distributed = {node: rpc_count(node) - before[node] for node in before}
        receipt.record("actual-round-robin-rpc-without-affinity", all(r[0] == 200 for r in replies) and
            distributed == {"A": 10, "B": 10}, distributed,
            "twenty authenticated TLS requests reach each real upstream ten times using shared sessions")
        page = service.request("TLS", "/")
        assets = sorted(set(re.findall(r'(?:src|href)="(/assets/[^" ]+)"', page[2].decode())))
        asset_status = {path: service.request("TLS", path)[0] for path in assets}
        receipt.record("browser-page-and-assets-through-tls", page[0] == 200 and bool(assets) and all(v == 200 for v in asset_status.values()),
            asset_status, "actual compiled SSR and content-addressed assets load through TLS proxy")
        data = b"W8 verified TLS streamed attachment\n" + bytes(range(256)) * 512
        begin = alice.request("TLS", "/_diamond/files/issue_files/begin", {"parent": identifier("issue-red-one"),
            "filename": "tls-synthetic.bin", "length": str(len(data)), "media_type": "application/octet-stream"})
        if begin[0] != 200:
            raise AssertionError(("TLS upload reservation denied", begin))
        file_id, ticket = begin[1]["id"], begin[1]["upload"]
        receipt.secret(ticket)
        put = service.transfer("TLS", alice, "/_diamond/files/issue_files/" + file_id, data, upload=ticket)
        finish = alice.request("TLS", "/_diamond/files/issue_files/" + file_id + "/finish", {}, {"X-Diamond-Upload": ticket})
        download = service.request("TLS", "/_diamond/files/issue_files/" + file_id, headers={"Cookie": alice.cookie})
        receipt.record("binary-stream-round-trip-through-tls", put[0] == 200 and finish[0] == 200 and download[0] == 200 and download[2] == data,
            {"put": put[0], "finish": finish[0], "download": download[0], "bytes": len(download[2]), "sha256": hashlib.sha256(download[2]).hexdigest()},
            "native HTTPS client uploads/finalizes/downloads exact streamed synthetic attachment")
        ws = WebSocket("localhost", service.tls_port, "/_diamond/events/issue_changes/" + identifier("issue-red-one") + "/0",
                       authority=service.authority, origin=service.origin, cookie=alice.cookie, tls_context=service.tls_context)
        event = "actual verified WSS typed event 雪"
        emitted = alice.rpc("emit", identifier("issue-red-one"), event, node="TLS")
        message = ws.receive()
        receipt.record("websocket-upgrade-and-event-through-verified-tls", ws.status == 101 and emitted[0] == 200 and message.get("value") == event,
            {"upgrade": ws.status, "message": message}, "real Caddy forwards WebSocket upgrade and authorized typed event over CA-verified TLS")
        ws.close()
        headers = {"Forwarded": "for=198.51.100.9;proto=http;host=forged.invalid", "X-Forwarded-For": "198.51.100.9", "X-Forwarded-Host": "forged.invalid"}
        proxy_stripped = service.request("TLS", "/", headers=headers)[0]
        direct_denied = service.request("A", "/", headers=headers)[0]
        receipt.record("edge-strips-untrusted-forwarding-headers", proxy_stripped == 200 and direct_denied == 400,
            {"through_edge": proxy_stripped, "direct": direct_denied}, "configured trusted edge removes forged forwarding headers and native direct profile rejects them")
        service.start_worker("one")
        service.start_worker("two")
        queued = alice.rpc("submit", identifier("issue-red-one"), "TLS queued durable work", "tls-job", node="TLS")
        if queued[0] != 200:
            raise AssertionError(("TLS durable submission denied", queued))
        wait_for(lambda: queued[1]["value"] in receiver.snapshot()["effect_keys"], "TLS caller reaches actual durable worker receiver", timeout=10)
        receipt.record("tls-submission-real-workers", True, receiver.snapshot(), "two owned worker processes service work submitted through TLS edge")
        service.stop_node("A")
        time.sleep(1.5)
        survivor = [alice.rpc("read", {"tag": "issue", "value": identifier("issue-red-one")}, node="TLS")[0] for _ in range(10)]
        receipt.record("active-health-routes-to-surviving-node", survivor == [200] * 10,
            {"statuses": survivor}, "after real health detection, canonical TLS endpoint continues on remaining upstream")
    except Exception as error:
        fatal = repr(error)
    finally:
        if ws:
            ws.close()
        if proxy and proxy.poll() is None:
            proxy.send_signal(signal.SIGINT)
            try:
                proxy.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proxy.kill()
                proxy.wait(timeout=5)
        if log:
            log.close()
        service.close()
        if receiver:
            receiver.close()
    report = receipt.report(service, sha256(binary), error=fatal, elapsed_seconds=time.monotonic() - start,
        temporary_root=str(root), caddy_sha256=sha256(caddy), caddy_exit=proxy.returncode if proxy else None,
        caddyfile=caddyfile, references=["https://caddyserver.com/docs/caddyfile/directives/reverse_proxy",
            "https://caddyserver.com/docs/caddyfile/options"],
        coverage_gaps=["Native HTTPS/WSS clients only here; actual browser UI journeys recorded separately.",
            "Fixture CA supplied only to test clients; no operating-system trust installation or public deployment."])
    if proxy and proxy.returncode != 0:
        report["status"] = "FAIL"
    options.report.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"status": report["status"], "observations": len(receipt.results), "error": fatal,
                      "root": str(root), "elapsed_seconds": report["elapsed_seconds"]}), flush=True)
    if report["status"] == "PASS":
        shutil.rmtree(root)
    return report["status"] != "PASS"


if __name__ == "__main__":
    raise SystemExit(main())
