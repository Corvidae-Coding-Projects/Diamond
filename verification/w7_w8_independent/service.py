"""Owned real PG/server/worker lifecycle, reusing historical plumbing only."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import signal
import subprocess
import time

source = Path(__file__).resolve().parents[1] / "w6_independent/service.py"
spec = importlib.util.spec_from_file_location("_w7_w8_previous_service", source)
previous = importlib.util.module_from_spec(spec)
spec.loader.exec_module(previous)
ENV_NAME = previous.ENV_NAME
RUNTIME_NAME = previous.RUNTIME_NAME
Client = previous.Client
sha256 = previous.sha256
free_port = previous.free_port


class Service(previous.Service):
    def __init__(self, root, binary, project):
        super().__init__(root, binary, project)
        self.workers = {}
        self.worker_logs = {}

    def start_worker(self, name):
        if name in self.workers:
            raise AssertionError("owned worker already running")
        log = (self.root / f"worker-{name}-{time.monotonic_ns()}.log").open("w")
        self.logs.append(log)
        self.worker_logs[name] = Path(log.name)
        self.workers[name] = subprocess.Popen([str(self.binary), "project-work", str(self.project),
            "--postgres-env", ENV_NAME, "--runtime-env", RUNTIME_NAME],
            stdout=log, stderr=log, env=self.process_env())

    def stop_worker(self, name, *, kill=False):
        process = self.workers.pop(name, None)
        if process is None:
            return
        forced = False
        if process.poll() is None:
            process.send_signal(signal.SIGKILL if kill else signal.SIGINT)
            try:
                process.wait(timeout=40)
            except subprocess.TimeoutExpired:
                forced = True
                process.kill()
                process.wait(timeout=5)
        text = self.worker_logs.pop(name).read_text()
        receipt = {"worker": name, "exit": process.returncode,
                   "expected_exit": -signal.SIGKILL if kill else 0,
                   "forced_kill": forced, "panic": "panicked at" in text}
        self.shutdowns.append(receipt)
        if receipt["exit"] != receipt["expected_exit"] or forced or receipt["panic"]:
            self.cleanup_errors.append(receipt)

    def close(self):
        for name in list(self.workers):
            self.stop_worker(name)
        super().close()
