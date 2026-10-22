"""Owned W5 PostgreSQL, mailbox and actual Diamond two-node HTTP lifecycle.

This infrastructure provides no authentication/policy implementation and no fake
application responses. All identity calls go to the real compiled program.
"""
from __future__ import annotations
import hashlib
import http.client
import json
import os
from pathlib import Path
import signal
import socket
import stat
import subprocess
import time

ENV_NAME = "DIAMOND_W5_INDEPENDENT_POSTGRES"


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def fingerprint(value):
    return hashlib.sha256(value.encode() if isinstance(value, str) else value).hexdigest()


def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


class Service:
    def __init__(self, root, binary, project, *, extra_serve=()):
        self.root, self.binary, self.project = Path(root), Path(binary), Path(project)
        self.sockets, self.data = self.root / "socket", self.root / "pgdata"
        self.sockets.mkdir(mode=0o700)
        self.mailbox = self.root / "mailbox"
        self.mailbox.mkdir(mode=0o700)
        self.pg_port = 55459
        self.owner = f"host={self.sockets} port={self.pg_port} user=diamond_w5_owner dbname=postgres"
        self.configuration = self.owner
        self.ports = {"A": free_port(), "B": free_port()}
        self.origin = f"http://127.0.0.1:{self.ports['A']}"
        self.authority = self.origin.removeprefix("http://")
        self.extra_serve = tuple(map(str, extra_serve))
        self.nodes, self.node_logs, self.logs = {}, {}, []
        self.trace, self.shutdowns, self.cleanup_errors = [], [], []
        self.pg_started = False

    def postgres(self):
        subprocess.run(["initdb", "-D", str(self.data), "-A", "trust", "--no-locale", "-E", "UTF8",
                        "-U", "diamond_w5_owner"], check=True, capture_output=True, timeout=20)
        self.start_postgres()

    def start_postgres(self):
        subprocess.run(["pg_ctl", "-D", str(self.data), "-l", str(self.root / "postgres.log"), "-o",
                        f'-k {self.sockets} -h "" -p {self.pg_port} -c max_connections=32', "-w", "start"],
                       check=True, capture_output=True, timeout=20)
        self.pg_started = True

    def stop_postgres(self):
        if self.pg_started:
            subprocess.run(["pg_ctl", "-D", str(self.data), "-m", "fast", "-w", "stop"],
                           check=True, capture_output=True, timeout=20)
            self.pg_started = False

    def cli(self, *arguments, success=True, configuration=None, input=None, timeout=30):
        run = subprocess.run([str(self.binary), *map(str, arguments)],
                             env=os.environ | {ENV_NAME: configuration or self.configuration},
                             input=input, capture_output=True, text=True, timeout=timeout)
        if success is not None and (run.returncode == 0) != success:
            raise AssertionError({"command": run.args, "exit": run.returncode,
                                  "stdout": run.stdout, "stderr": run.stderr})
        return run

    def sql(self, sql, *, configuration=None):
        return subprocess.check_output(["psql", configuration or self.owner, "-X", "-q", "-v",
                                        "ON_ERROR_STOP=1", "-Atc", sql], text=True, timeout=40).strip()

    def sql_json(self, sql, *, configuration=None):
        return json.loads(self.sql("SELECT coalesce(json_agg(observation), '[]'::json)::text FROM (" + sql
                                   + ") AS observation", configuration=configuration))

    def build(self, project=None):
        build = self.root / ("build-" + str(time.monotonic_ns()))
        self.cli("check", project or self.project)
        self.cli("build", project or self.project, "--out", build)
        return json.loads((build / "contract.json").read_text()), build

    def start_node(self, node, *, project=None, configuration=None, extra_serve=None):
        if node in self.nodes:
            raise AssertionError(("owned node already started", node))
        log = (self.root / f"{node}-{time.monotonic_ns()}.log").open("w")
        self.logs.append(log)
        self.node_logs[node] = Path(log.name)
        args = self.extra_serve if extra_serve is None else tuple(map(str, extra_serve))
        self.nodes[node] = subprocess.Popen([str(self.binary), "serve", str(project or self.project),
                                            "--listen", f"127.0.0.1:{self.ports[node]}", "--origin", self.origin,
                                            "--postgres-env", ENV_NAME, *args], stdout=log, stderr=log,
                                           env=os.environ | {ENV_NAME: configuration or self.configuration})
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            try:
                status, _, _ = self.request(node, "/")
                if status == 200:
                    return
            except OSError:
                pass
            if self.nodes[node].poll() is not None:
                raise AssertionError(("startup exited", node, Path(log.name).read_text()))
            time.sleep(.025)
        raise AssertionError(("startup deadline", node, Path(log.name).read_text()))

    def request(self, node, path, body=None, headers=None, *, method=None, source=None):
        headers = {"Host": self.authority, **(headers or {})}
        method = method or ("GET" if body is None else "POST")
        if body is not None:
            if not isinstance(body, (bytes, str)):
                body = json.dumps(body, ensure_ascii=True).encode()
            headers.setdefault("Content-Type", "application/json")
            headers.setdefault("Origin", self.origin)
        connection = http.client.HTTPConnection("127.0.0.1", self.ports[node], timeout=20, source_address=(source, 0) if source else None)
        try:
            connection.request(method, path, body, headers)
            response = connection.getresponse()
            data = response.read()
            self.trace.append({"node": node, "method": method, "path": path, "status": response.status})
            return response.status, {name.lower(): value for name, value in response.getheaders()}, data
        finally:
            connection.close()

    def mailbox_snapshot(self):
        return {path.name: (path.stat().st_mtime_ns, path.stat().st_size) for path in self.mailbox.iterdir()}

    def mailbox_delta(self, before):
        """Return new actual delivery files without recording plaintext secrets."""
        paths = [path for path in self.mailbox.iterdir() if path.name not in before]
        if stat.S_IMODE(self.mailbox.stat().st_mode) != 0o700:
            raise AssertionError("mailbox must remain owner-only mode0700")
        for path in paths:
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.getuid():
                raise AssertionError(("unsafe actual delivery file", path.name, oct(stat.S_IMODE(info.st_mode))))
        return paths

    def stop_node(self, node, *, kill=False):
        process = self.nodes.pop(node, None)
        if process is None:
            return
        forced = False
        if process.poll() is None:
            process.send_signal(signal.SIGKILL if kill else signal.SIGINT)
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                forced = True
                process.kill()
                process.wait(timeout=10)
        log = self.node_logs.pop(node)
        text = log.read_text()
        receipt = {"node": node, "exit": process.returncode, "expected_exit": -signal.SIGKILL if kill else 0,
                   "forced_kill": forced, "panic": "panicked at" in text}
        self.shutdowns.append(receipt)
        if receipt["exit"] != receipt["expected_exit"] or forced or receipt["panic"]:
            self.cleanup_errors.append({**receipt, "log": str(log), "log_content": text})

    def close(self):
        for node in list(self.nodes):
            self.stop_node(node)
        self.stop_postgres()
        for log in self.logs:
            log.close()


class Client:
    def __init__(self, service, contract):
        self.service, self.contract = service, contract
        self.cookie, self.csrf = None, ""

    def request(self, node, path, body=None, headers=None, *, method=None):
        headers = dict(headers or {})
        if path == "/_diamond/session":
            headers.setdefault("X-Diamond-Session", "1")
        if self.cookie:
            headers.setdefault("Cookie", self.cookie)
        if body is not None:
            headers.setdefault("X-Diamond-CSRF", self.csrf)
        status, response_headers, raw = self.service.request(node, path, body, headers, method=method)
        if "set-cookie" in response_headers:
            set_cookie = response_headers["set-cookie"]
            cookie = set_cookie.split(";", 1)[0]
            self.cookie = None if not cookie.partition("=")[2] or "max-age=0" in set_cookie.lower() else cookie
        value = json.loads(raw) if raw else None
        if isinstance(value, dict) and "csrf" in value:
            self.csrf = value["csrf"]
        return status, value

    def bootstrap(self, node="A"):
        return self.request(node, "/_diamond/session")

    def login(self, username, password, node="A", *, success=True):
        status, value = self.bootstrap(node)
        if status != 200:
            raise AssertionError(("session bootstrap", status, value))
        outcome = self.request(node, "/_diamond/login", {"username": username, "password": password})
        if success is not None and (outcome[0] == 200) != success:
            raise AssertionError(("login", username, outcome))
        return outcome

    def rpc(self, operation, *arguments, node="A", extra=None):
        body = {"contract": self.contract, "arguments": arguments}
        body.update(extra or {})
        return self.request(node, "/_diamond/rpc/" + operation, body)

    def api(self, operation, bearer, arguments=(), *, node="A", envelope=None, headers=None, method="POST"):
        body = envelope if envelope is not None else {"arguments": list(arguments)}
        headers = {"X-Diamond-API-Version": "1", **(headers or {})}
        if bearer:
            headers = {"Authorization": "Bearer " + bearer, **headers}
        # Explicit API profile intentionally does not attach this client's cookie
        # or CSRF token unless the caller is testing forbidden cookie fallback.
        status, response_headers, raw = self.service.request(node, "/api/v1/" + operation, body, headers, method=method)
        return status, json.loads(raw) if raw else None

    def account(self, operation, body, *, node="A"):
        outcome = self.request(node, "/_diamond/accounts/" + operation, body)
        if outcome[0] == 200 and operation in {"verify", "reset", "change_password", "logout_all"}:
            status, value = self.bootstrap(node)
            if status != 200:
                raise AssertionError(("refresh CSRF after identity action", operation, status, value))
        return outcome
