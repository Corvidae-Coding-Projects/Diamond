"""Bounded real PostgreSQL + actual Diamond service lifecycle, no app substitutes."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import http.client
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import time

PASSWORD = "Synthetic W4 test password 42!"
ENV_NAME = "DIAMOND_W4_INDEPENDENT_POSTGRES"


def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class Service:
    """Disposable Unix-socket cluster and bounded direct-node HTTP clients.

    The caller supplies an actual Diamond project. This helper implements no
    application handlers, authentication, policy decisions, or expected values.
    It owns its PostgreSQL cluster and processes and always waits when stopping.
    """

    def __init__(self, root, binary, project):
        self.root, self.binary, self.project = Path(root), Path(binary), Path(project)
        self.sockets = self.root / "socket"
        self.sockets.mkdir(mode=0o700)
        self.data = self.root / "pgdata"
        self.pg_port = 55449
        self.owner = f"host={self.sockets} port={self.pg_port} user=diamond_w4_owner dbname=postgres"
        self.configuration = self.owner
        self.nodes, self.logs, self.trace = {}, [], []
        self.node_logs, self.shutdowns, self.cleanup_errors = {}, [], []
        self.pg_started = False
        self.ports = {"A": free_port(), "B": free_port()}
        # Both servers accept the same explicit origin. Tests connect directly
        # while preserving Host; this is actual no-affinity shared-state use.
        self.origin = f"http://127.0.0.1:{self.ports['A']}"
        self.authority = self.origin.removeprefix("http://")

    def postgres(self):
        subprocess.run(["initdb", "-D", str(self.data), "-A", "trust", "--no-locale",
                        "-E", "UTF8", "-U", "diamond_w4_owner"], check=True, capture_output=True)
        subprocess.run(["pg_ctl", "-D", str(self.data), "-l", str(self.root / "postgres.log"),
                        "-o", f'-k {self.sockets} -h "" -p {self.pg_port} -c max_connections=32',
                        "-w", "start"], check=True, capture_output=True, timeout=20)
        self.pg_started = True

    def cli(self, *arguments, success=True, configuration=None, input=None, timeout=30):
        run = subprocess.run([str(self.binary), *map(str, arguments)],
                             env=os.environ | {ENV_NAME: configuration or self.configuration},
                             input=input, capture_output=True, text=True, timeout=timeout)
        if success is not None and (run.returncode == 0) != success:
            raise AssertionError({"command": run.args, "exit": run.returncode,
                                  "stdout": run.stdout, "stderr": run.stderr})
        return run

    def sql(self, sql, *, configuration=None, input=None):
        return subprocess.check_output(["psql", configuration or self.owner, "-X", "-q",
                                        "-v", "ON_ERROR_STOP=1", "-Atc", sql],
                                       input=input, text=True, timeout=45).strip()

    def sql_json(self, sql, *, configuration=None):
        return json.loads(self.sql(f"SELECT coalesce(json_agg(observation), '[]'::json)::text FROM ({sql}) AS observation",
                                   configuration=configuration))

    def build(self, project=None):
        build = self.root / ("build-" + str(time.monotonic_ns()))
        self.cli("check", project or self.project)
        self.cli("build", project or self.project, "--out", build)
        contract = json.loads((build / "contract.json").read_text())
        return contract["rpc_id"], build

    def start_node(self, node, project=None, configuration=None):
        log = (self.root / f"{node}-{time.monotonic_ns()}.log").open("w")
        self.logs.append(log)
        self.node_logs[node] = Path(log.name)
        self.nodes[node] = subprocess.Popen([
            str(self.binary), "serve", str(project or self.project), "--listen",
            f"127.0.0.1:{self.ports[node]}", "--origin", self.origin,
            "--postgres-env", ENV_NAME], stdout=log, stderr=log,
            env=os.environ | {ENV_NAME: configuration or self.configuration})
        for _ in range(160):
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

    def stop_node(self, node, kill=False):
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
        log = self.node_logs.pop(node, None)
        text = log.read_text() if log is not None else ""
        receipt = {"node": node, "exit": process.returncode, "expected_exit": -signal.SIGKILL if kill else 0,
                   "forced_kill": forced, "panic": "panicked at" in text}
        self.shutdowns.append(receipt)
        if receipt["exit"] != receipt["expected_exit"] or forced or receipt["panic"]:
            self.cleanup_errors.append({**receipt, "log": str(log), "log_content": text})

    def request(self, node, path, body=None, headers=None):
        headers = {"Host": self.authority, **(headers or {})}
        method = "GET" if body is None else "POST"
        if body is not None:
            if not isinstance(body, (bytes, str)):
                body = json.dumps(body, ensure_ascii=True).encode("utf-8")
            headers.setdefault("Content-Type", "application/json")
            headers.setdefault("Origin", self.origin)
        connection = http.client.HTTPConnection("127.0.0.1", self.ports[node], timeout=20)
        try:
            connection.request(method, path, body, headers)
            response = connection.getresponse()
            status, response_headers, data = response.status, dict(response.getheaders()), response.read()
            self.trace.append({"node": node, "method": method, "path": path, "status": status})
            return status, response_headers, data
        finally:
            connection.close()

    def close(self):
        for node in list(self.nodes):
            self.stop_node(node)
        if self.pg_started:
            subprocess.run(["pg_ctl", "-D", str(self.data), "-m", "fast", "-w", "stop"],
                           check=True, capture_output=True, timeout=20)
            self.pg_started = False
        for log in self.logs:
            log.close()


class Client:
    def __init__(self, service, contract):
        self.service, self.contract = service, contract
        self.cookie, self.csrf = None, ""

    def request(self, node, path, body=None, headers=None):
        headers = dict(headers or {})
        if path == "/_diamond/session":
            headers.setdefault("X-Diamond-Session", "1")
        if self.cookie:
            headers.setdefault("Cookie", self.cookie)
        if body is not None:
            headers.setdefault("X-Diamond-CSRF", self.csrf)
        status, response_headers, raw = self.service.request(node, path, body, headers)
        if "set-cookie" in response_headers:
            self.cookie = response_headers["set-cookie"].split(";", 1)[0]
        value = json.loads(raw) if raw else None
        if isinstance(value, dict) and "csrf" in value:
            self.csrf = value["csrf"]
        return status, value

    def login(self, username, node="A"):
        status, session = self.request(node, "/_diamond/session")
        if status != 200:
            raise AssertionError(("session", status, session))
        status, value = self.request(node, "/_diamond/login", {"username": username, "password": PASSWORD})
        if status != 200:
            raise AssertionError(("login", status, value))

    def rpc(self, operation, *arguments, node="A", extra=None):
        body = {"contract": self.contract, "arguments": arguments}
        body.update(extra or {})
        return self.request(node, "/_diamond/rpc/" + operation, body)
