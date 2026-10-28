#!/usr/bin/env python3
"""External, state-validating Diamond versus Rails benchmark."""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import hashlib
import http.client
import itertools
import json
import math
import os
from pathlib import Path
import platform
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
RAILS = HERE / "rails"
DIAMOND_PROJECT = HERE / "diamond" / "diamond.toml"
sys.path.insert(0, str(ROOT / "verification" / "w5_independent"))

from service import Client, ENV_NAME, Service, free_port, sha256  # noqa: E402

PASSWORD = "Benchmark-only password 73!"
RAILS_BEARER = "dia_benchmark_only_rails_bearer_4ea8cb79"
APP_NAME = "DiamondRailsBench"
RUBY_IMAGE = "docker.io/library/ruby@sha256:b95324944d8326fc2c0908461a39ea055f5b493e85c364a7e6b4fb7b04be890f"


def run(command: list[str], *, cwd: Path = ROOT, env: dict[str, str] | None = None,
        timeout: float = 180, check: bool = True) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(command, cwd=cwd, env=env, text=True,
                               capture_output=True, timeout=timeout)
    if check and completed.returncode != 0:
        raise RuntimeError({"command": command, "exit": completed.returncode,
                            "stdout": completed.stdout, "stderr": completed.stderr})
    return completed


def command_version(command: list[str], *, cwd: Path = ROOT,
                    env: dict[str, str] | None = None) -> str:
    try:
        completed = run(command, cwd=cwd, env=env, timeout=30, check=False)
        return (completed.stdout or completed.stderr).strip().splitlines()[0]
    except (OSError, IndexError, subprocess.TimeoutExpired):
        return "unavailable"


def percentile(values: list[float], percentage: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    position = (len(ordered) - 1) * percentage
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def process_cpu_seconds(pid: int) -> float:
    text = Path(f"/proc/{pid}/stat").read_text()
    fields = text[text.rfind(")") + 2:].split()
    ticks = int(fields[11]) + int(fields[12])
    return ticks / os.sysconf(os.sysconf_names["SC_CLK_TCK"])


def process_rss_bytes(pid: int) -> int:
    for line in Path(f"/proc/{pid}/status").read_text().splitlines():
        if line.startswith("VmRSS:"):
            return int(line.split()[1]) * 1024
    return 0


def process_memory_bytes(pid: int) -> dict[str, int]:
    values = {"rss": process_rss_bytes(pid), "pss": 0, "uss": 0}
    private = 0
    for line in Path(f"/proc/{pid}/smaps_rollup").read_text().splitlines():
        name, separator, raw = line.partition(":")
        if not separator:
            continue
        fields = raw.split()
        if not fields:
            continue
        amount = int(fields[0]) * 1024
        if name == "Pss":
            values["pss"] = amount
        elif name in {"Private_Clean", "Private_Dirty", "Private_Hugetlb"}:
            private += amount
    values["uss"] = private
    return values


def memory_statistics(samples: list[int]) -> dict[str, float | int]:
    return {
        "samples": len(samples),
        "mean": sum(samples) / len(samples) if samples else 0,
        "p50": percentile(samples, 0.50),
        "p95": percentile(samples, 0.95),
        "p99": percentile(samples, 0.99),
        "peak": max(samples, default=0),
    }


def process_tree(pid: int) -> set[int]:
    found, pending = set(), [pid]
    while pending:
        current = pending.pop()
        if current in found:
            continue
        found.add(current)
        try:
            pending.extend(map(int, Path(f'/proc/{current}/task/{current}/children').read_text().split()))
        except (FileNotFoundError, ProcessLookupError):
            pass
    return found


class MemorySampler:
    def __init__(self, pid: int, tree: bool = False):
        self.pid = pid
        self.tree = tree
        self.previous_cpu: dict[int, float] = {}
        self.cpu_seconds = 0.0
        initial = self.snapshot(initial=True)
        self.samples = {name: [value] for name, value in initial.items()}
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._sample, daemon=True)

    def snapshot(self, initial: bool = False) -> dict[str, int]:
        total = {'rss': 0, 'pss': 0, 'uss': 0}
        for pid in process_tree(self.pid) if self.tree else {self.pid}:
            try:
                memory = process_memory_bytes(pid)
                cpu = process_cpu_seconds(pid)
            except (FileNotFoundError, ProcessLookupError):
                continue
            for name, value in memory.items():
                total[name] += value
            if not initial:
                self.cpu_seconds += max(0, cpu-self.previous_cpu.get(pid, 0))
            self.previous_cpu[pid] = cpu
        return total

    def _sample(self) -> None:
        while not self._stop.wait(0.1):
            try:
                values = self.snapshot()
                for name, value in values.items():
                    self.samples[name].append(value)
            except (FileNotFoundError, ProcessLookupError):
                return

    def __enter__(self) -> "MemorySampler":
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._stop.set()
        self._thread.join(timeout=1)
        for name, value in self.snapshot().items():
            self.samples[name].append(value)

    def statistics(self) -> dict[str, dict[str, float | int]]:
        return {name: memory_statistics(values) for name, values in self.samples.items()}


class HttpTarget:
    def __init__(self, name: str, port: int, bearer: str, workspace: str,
                 issue: str, denied_issue: str, pid: int, issue_ids: list[str]):
        self.name = name
        self.port = port
        self.bearer = bearer
        self.workspace = workspace
        self.issue = issue
        self.denied_issue = denied_issue
        self.pid = pid
        self.issue_ids = issue_ids
        self.connection_reuse = False
        self.backoff = False
        self.local = threading.local()

    def close_connection(self) -> None:
        connection = getattr(self.local, 'connection', None)
        if connection:
            connection.close()
            self.local.connection = None

    def request(self, operation: str, sequence: int) -> tuple[int, Any, float]:
        if operation == "page":
            arguments: list[Any] = [self.workspace, ""]
        elif operation == "read":
            arguments = [self.issue]
        elif operation == "denied":
            arguments = [self.denied_issue]
        elif operation == "update":
            arguments = [self.issue_ids[sequence % len(self.issue_ids)],
                         f"Updated {sequence:012d}",
                         f"Updated benchmark details {sequence} — Unicode 雪"]
        elif operation == "create":
            arguments = [self.workspace, f"Created {sequence:012d}",
                         f"Created benchmark details {sequence} — Unicode 雪",
                         str(1_000_000_000 + sequence)]
        else:
            raise ValueError(operation)

        body = json.dumps({"arguments": arguments}, ensure_ascii=True,
                          separators=(",", ":")).encode()
        headers = {
            "Authorization": "Bearer " + self.bearer,
            "Content-Type": "application/json",
            "X-Diamond-API-Version": "1",
        }
        started = time.perf_counter_ns()
        connection = getattr(self.local, 'connection', None)
        if connection is None:
            connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
            if self.connection_reuse:
                self.local.connection = connection
        try:
            endpoint = 'read' if operation == 'denied' else operation
            connection.request("POST", f"/api/v1/{endpoint}", body, headers)
            response = connection.getresponse()
            self.local.headers = dict((k.lower(), v) for k, v in response.getheaders())
            raw = response.read()
        finally:
            if not self.connection_reuse:
                connection.close()
        elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000
        try:
            decoded = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            decoded = None
        return response.status, decoded, elapsed_ms

    @staticmethod
    def valid(operation: str, status: int, body: Any, sequence: int) -> bool:
        if operation == "denied":
            return status == 403 and not (isinstance(body, dict) and "value" in body)
        if (status != 200 or not isinstance(body, dict) or body.get("api_version") != 1
                or "value" not in body):
            return False
        value = body["value"]
        if operation == "page":
            try:
                total = int(value.get("total", 0))
            except (AttributeError, TypeError, ValueError):
                return False
            cards = value.get("cards") if isinstance(value, dict) else None
            valid_cards = isinstance(cards, list) and all(
                isinstance(card, dict)
                and card.keys() == {"id", "title", "details", "rank"}
                and isinstance(card["id"], dict)
                and card["id"].get("tag") == "issue"
                and isinstance(card["id"].get("value"), str)
                and isinstance(card["title"], str)
                and isinstance(card["details"], str)
                and isinstance(card["rank"], str)
                for card in cards or []
            )
            return (valid_cards and len(cards) == 20 and total >= 20
                    and isinstance(value.get("total"), str)
                    and isinstance(value.get("after"), str))
        if operation == "read":
            if value == "Issue 00000000\nBenchmark issue details 0 — Unicode 雪":
                return True
            match = re.fullmatch(
                r"Updated (\d{12})\nUpdated benchmark details (\d+) — Unicode 雪",
                str(value),
            )
            return bool(match and int(match.group(1)) == int(match.group(2)))
        if operation == "update":
            return value == (f"Updated {sequence:012d}\n"
                             f"Updated benchmark details {sequence} — Unicode 雪")
        if operation == "create":
            return isinstance(value, str) and bool(value)
        return False


def load_phase(target: HttpTarget, operation: str, concurrency: int, duration: float,
               sequence: itertools.count[int]) -> dict[str, Any]:
    warm_sequence = next(sequence)
    warm = target.request(operation, warm_sequence)
    if not target.valid(operation, warm[0], warm[1], warm_sequence):
        raise AssertionError({"phase": "warmup", "application": target.name,
                              "operation": operation, "response": warm[:2]})

    barrier = threading.Barrier(concurrency + 1)
    stop_at = [0.0]

    def worker() -> tuple[list[float], int, list[dict[str, Any]], int]:
        latencies: list[float] = []
        errors: list[dict[str, Any]] = []
        successes = 0
        creates = 0
        barrier.wait()
        while time.perf_counter() < stop_at[0]:
            current = next(sequence)
            try:
                status, body, elapsed = target.request(operation, current)
                latencies.append(elapsed)
                if target.valid(operation, status, body, current):
                    successes += 1
                    creates += int(operation == "create")
                elif len(errors) < 5:
                    errors.append({"sequence": current, "status": status, "body": body})
            except Exception as failure:  # preserve representative failures in the report
                if len(errors) < 5:
                    errors.append({"sequence": current, "exception": repr(failure)})
        return latencies, successes, errors, creates

    idle_rss = process_rss_bytes(target.pid)
    cpu_before = process_cpu_seconds(target.pid)
    started = time.perf_counter()
    with MemorySampler(target.pid) as memory, concurrent.futures.ThreadPoolExecutor(
            max_workers=concurrency) as pool:
        futures = [pool.submit(worker) for _ in range(concurrency)]
        stop_at[0] = time.perf_counter() + duration
        barrier.wait()
        outcomes = [future.result(timeout=duration + 30) for future in futures]
    elapsed = time.perf_counter() - started
    cpu_seconds = max(0.0, process_cpu_seconds(target.pid) - cpu_before)
    latencies = [value for outcome in outcomes for value in outcome[0]]
    successes = sum(outcome[1] for outcome in outcomes)
    errors = [error for outcome in outcomes for error in outcome[2]]
    creates = sum(outcome[3] for outcome in outcomes)
    requests = len(latencies)
    return {
        "application": target.name,
        "operation": operation,
        "concurrency": concurrency,
        "elapsed_seconds": elapsed,
        "requests": requests,
        "successful_requests": successes,
        "unexpected_responses": requests - successes,
        "representative_errors": errors[:5],
        "throughput_requests_per_second": requests / elapsed if elapsed else 0,
        "latency_ms": {
            "p50": percentile(latencies, 0.50),
            "p95": percentile(latencies, 0.95),
            "p99": percentile(latencies, 0.99),
            "max": max(latencies, default=0),
        },
        "process_cpu_seconds": cpu_seconds,
        "process_cpu_utilization_percent": cpu_seconds / elapsed * 100 if elapsed else 0,
        "process_cpu_microseconds_per_request": (
            cpu_seconds * 1_000_000 / requests if requests else 0),
        "idle_rss_bytes": idle_rss,
        "peak_rss_bytes": memory.statistics()["rss"]["peak"],
        "process_memory_bytes": memory.statistics(),
        "successful_creates": creates,
        "warmup_was_valid": True,
    }


def wait_for_http(port: int, path: str, process: subprocess.Popen[str], timeout: float = 20) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError({"startup_exit": process.returncode})
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
        try:
            connection.request("GET", path)
            response = connection.getresponse()
            response.read()
            if response.status == 200:
                return
        except OSError:
            pass
        finally:
            connection.close()
        time.sleep(0.025)
    raise TimeoutError((port, path))


def rails_environment(service: Service, dataset: int, port: int, yjit: bool) -> dict[str, str]:
    environment = os.environ | {
        "RAILS_ENV": "production",
        "RAILS_LOG_LEVEL": "warn",
        "SECRET_KEY_BASE": "benchmark-only-not-a-deployment-secret-" + "7" * 48,
        "BENCH_PGHOST": str(service.sockets),
        "BENCH_PGPORT": str(service.pg_port),
        "BENCH_PGUSER": "diamond_w5_owner",
        "BENCH_PGDATABASE": "postgres",
        "BENCH_DATASET": str(dataset),
        "BENCH_BEARER": RAILS_BEARER,
        "PORT": str(port),
        "RAILS_MAX_THREADS": str(getattr(service, 'rails_threads', 4)),
        "WEB_CONCURRENCY": str(getattr(service, 'rails_workers', 0)),
    }
    if yjit:
        environment["RUBYOPT"] = (environment.get("RUBYOPT", "") + " --yjit").strip()
    return environment


def rails_setup(service: Service, dataset: int, port: int, yjit: bool,
                log_path: Path) -> tuple[subprocess.Popen[str], float, dict[str, str]]:
    environment = rails_environment(service, dataset, port, yjit)
    service.sql("DROP SCHEMA IF EXISTS rails_bench CASCADE; CREATE SCHEMA rails_bench")
    run(["bundle", "exec", "rails", "db:migrate"], cwd=RAILS, env=environment)
    seeded = run(["bundle", "exec", "rails", "db:seed"], cwd=RAILS, env=environment)
    seed = json.loads(seeded.stdout.strip().splitlines()[-1])
    process, startup = rails_start(service, dataset, port, yjit, log_path)
    return process, startup, seed


def rails_start(service: Service, dataset: int, port: int, yjit: bool,
                log_path: Path) -> tuple[subprocess.Popen[str], float]:
    environment = rails_environment(service, dataset, port, yjit)
    log = log_path.open("w")
    started = time.perf_counter()
    process = subprocess.Popen(["bundle", "exec", "puma", "-C", "config/puma.rb"],
                               cwd=RAILS, env=environment, text=True,
                               stdout=log, stderr=subprocess.STDOUT)
    process._benchmark_log = log  # type: ignore[attr-defined]
    wait_for_http(port, "/up", process)
    return process, time.perf_counter() - started


def stop_process(process: subprocess.Popen[str] | None) -> dict[str, Any] | None:
    if process is None:
        return None
    forced = False
    if process.poll() is None:
        process.send_signal(signal.SIGINT)
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            forced = True
            process.kill()
            process.wait(timeout=5)
    log = getattr(process, "_benchmark_log", None)
    if log:
        log.close()
    return {"exit": process.returncode, "forced": forced}


def diamond_setup(service: Service, dataset: int) -> tuple[HttpTarget, float, dict[str, str]]:
    metadata, _ = service.build()
    contract = metadata["rpc_id"]
    service.cli("project-init", service.project, "--postgres-env", ENV_NAME)
    for username in ("alice", "bob"):
        service.cli("project-user-add", service.project, "--postgres-env", ENV_NAME,
                    "--user", username, "--password-stdin", input=PASSWORD + "\n")
    started = time.perf_counter()
    service.start_node("A")
    startup = time.perf_counter() - started
    alice = Client(service, contract)
    bob = Client(service, contract)
    alice.login("alice", PASSWORD)
    bob.login("bob", PASSWORD)
    status, reply = alice.rpc("create_workspace", "Main benchmark workspace")
    if status != 200:
        raise AssertionError((status, reply))
    workspace = reply["value"]
    status, reply = bob.rpc("create_workspace", "Denied benchmark workspace")
    if status != 200:
        raise AssertionError((status, reply))
    denied_workspace = reply["value"]

    issue_ids: list[str] = []
    for index in range(dataset):
        status, reply = alice.rpc(
            "create_issue", workspace, f"Issue {index:08d}",
            f"Benchmark issue details {index} — Unicode 雪", str(index))
        if status != 200:
            raise AssertionError(("diamond seed", index, status, reply))
        issue_ids.append(reply["value"])
    issue = issue_ids[0]
    status, reply = bob.rpc("create_issue", denied_workspace, "Denied issue",
                            "This issue belongs to another principal.", "0")
    if status != 200:
        raise AssertionError((status, reply))
    denied_issue = reply["value"]
    status, credential = alice.account("create_token", {
        "label": "Diamond Rails benchmark",
        "operations": ["page", "read", "create", "update"],
        "scopes": {"workspace": [workspace]},
        "lifetime_seconds": "2592000",
    })
    if status != 200:
        raise AssertionError((status, credential))
    process = service.nodes["A"]
    return HttpTarget("diamond", service.ports["A"], credential["value"],
                      workspace, issue, denied_issue, process.pid, issue_ids), startup, {
        "workspace": workspace,
        "issue": issue,
        "denied_workspace": denied_workspace,
        "denied_issue": denied_issue,
    }


def diamond_table(resource: str) -> str:
    app = hashlib.sha256(APP_NAME.encode()).hexdigest()[:16]
    item = hashlib.sha256(resource.encode()).hexdigest()[:16]
    return f'diamond_w4."r_{app}_{item}"'


def independent_oracle(service: Service, targets: dict[str, HttpTarget],
                       initial: int, successful_creates: dict[str, int]) -> dict[str, Any]:
    diamond = service.sql_json(
        f"SELECT count(*)::bigint AS count, "
        "max(convert_from(decode(title,'hex'),'UTF8')) FILTER (WHERE convert_from(decode(title,'hex'),'UTF8') LIKE 'Updated %') AS updated_title "
        f"FROM {diamond_table('Issues')} WHERE workspace='{targets['diamond'].workspace}'")
    rails = service.sql_json(
        "SELECT count(*)::bigint AS count, "
        "max(title) FILTER (WHERE title LIKE 'Updated %') AS updated_title "
        "FROM rails_bench.bench_issues "
        f"WHERE workspace_id='{targets['rails'].workspace}'")
    observations = {"diamond": diamond[0], "rails": rails[0]}
    for name in ("diamond", "rails"):
        expected = initial + successful_creates[name]
        actual = int(observations[name]["count"])
        observations[name]["expected_count"] = expected
        observations[name]["count_matches"] = actual == expected
        observations[name]["update_observed"] = str(observations[name]["updated_title"]).startswith("Updated ")
    return observations


def cpu_model() -> str:
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or "unknown"


def source_hashes() -> dict[str, str]:
    excluded = {".bundle", "__pycache__", "log", "results", "tmp", "vendor"}
    files = [
        path for path in HERE.rglob("*")
        if path.is_file() and not excluded.intersection(path.relative_to(HERE).parts)
    ]
    return {str(path.relative_to(ROOT)): sha256(path) for path in sorted(files)}


def provenance(options: argparse.Namespace, binary: Path, rails_env: dict[str, str]) -> dict[str, Any]:
    git_revision = command_version(["git", "rev-parse", "HEAD"])
    dirty = bool(run(["git", "status", "--porcelain"], check=False).stdout.strip())
    memory_kib = 0
    try:
        match = re.search(r"^MemTotal:\s+(\d+)\s+kB$", Path("/proc/meminfo").read_text(), re.M)
        memory_kib = int(match.group(1)) if match else 0
    except OSError:
        pass
    return {
        "timestamp_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "git_revision": git_revision,
        "git_dirty": dirty,
        "platform": platform.platform(),
        "kernel": platform.release(),
        "cpu_model": cpu_model(),
        "logical_cpu_count": os.cpu_count(),
        "memory_bytes": memory_kib * 1024,
        "python": platform.python_version(),
        "diamond_binary": str(binary),
        "diamond_binary_sha256": sha256(binary),
        "ruby": command_version(["ruby", "--version"]),
        "ruby_build_image": RUBY_IMAGE,
        "rails": command_version(["bundle", "exec", "rails", "--version"], cwd=RAILS, env=rails_env),
        "puma": command_version(["bundle", "exec", "puma", "--version"], cwd=RAILS, env=rails_env),
        "postgresql": command_version(["postgres", "--version"]),
        "rails_lockfile_sha256": sha256(RAILS / "Gemfile.lock"),
        "rails_yjit": options.rails_yjit,
        "rails_processes": 1,
        "rails_threads": 4,
        "http_connection_reuse": False,
        "dataset": options.dataset,
        "concurrency": options.concurrency,
        "duration_seconds": options.duration,
        "repeats": options.repeats,
        "benchmark_source_sha256": source_hashes(),
    }


def markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Diamond and Rails benchmark result",
        "",
        f"Status: **{report['status']}**",
        "",
        "This is a local workload result, not a universal framework ranking.",
        "",
        "| App | Operation | C | Repeat | req/s | p50 ms | p95 ms | p99 ms | Peak MiB | Errors |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for phase in report.get("phases", []):
        lines.append(
            f"| {phase['application']} | {phase['operation']} | {phase['concurrency']} | "
            f"{phase['repeat']} | {phase['throughput_requests_per_second']:.1f} | "
            f"{phase['latency_ms']['p50']:.2f} | {phase['latency_ms']['p95']:.2f} | "
            f"{phase['latency_ms']['p99']:.2f} | {phase['peak_rss_bytes'] / 1048576:.1f} | "
            f"{phase['unexpected_responses']} |")
    lines += [
        "",
        "The JSON file beside this summary contains provenance, CPU observations,",
        "independent database readback, startup timing, and representative errors.",
        "See `SCENARIOS.md` for the predeclared oracle and measurement limits.",
        "",
    ]
    return "\n".join(lines)


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, default=ROOT / "target/release/diamond")
    parser.add_argument("--dataset", type=int, default=1000)
    parser.add_argument("--duration", type=float, default=5.0)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--concurrency", type=int, nargs="+", default=[1, 8, 16])
    parser.add_argument("--rails-yjit", action="store_true")
    parser.add_argument("--quick", action="store_true")
    parser.add_argument("--report", type=Path)
    options = parser.parse_args()
    if options.quick:
        options.dataset = 100
        options.duration = 0.75
        options.repeats = 1
        options.concurrency = [1, 8]
    if options.dataset < 20 or options.duration <= 0 or options.repeats < 1:
        parser.error("dataset must be >=20; duration and repeats must be positive")
    if any(value < 1 or value > 24 for value in options.concurrency):
        parser.error("concurrency values must be between 1 and 24")
    timestamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    options.report = options.report or HERE / "results" / f"local-{timestamp}.json"
    return options


def main() -> int:
    options = parse_arguments()
    binary = options.binary.resolve()
    if not binary.is_file():
        raise SystemExit(f"Diamond release binary not found: {binary}; run cargo build --release --locked")
    if not (RAILS / "vendor" / "bundle").exists():
        raise SystemExit("Rails bundle is absent; run benchmarks/rails-comparison/prepare.sh")

    state_root = ROOT / ".diamond"
    state_root.mkdir(exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix="rails-comparison-", dir=state_root))
    copied_binary = temporary / "diamond"
    shutil.copy2(binary, copied_binary)
    service = Service(temporary, copied_binary, DIAMOND_PROJECT)
    service.extra_serve = ("--identity-mailbox", str(service.mailbox))
    rails_process: subprocess.Popen[str] | None = None
    phases: list[dict[str, Any]] = []
    startup: dict[str, float] = {}
    targets: dict[str, HttpTarget] = {}
    successful_creates = {"diamond": 0, "rails": 0}
    error: str | None = None
    oracle: dict[str, Any] = {}
    cleanup: dict[str, Any] = {}
    rails_env: dict[str, str] = os.environ.copy()
    try:
        service.postgres()
        diamond, startup["diamond_seconds"], _ = diamond_setup(service, options.dataset)
        rails_port = free_port()
        rails_process, startup["rails_seconds"], rails_seed = rails_setup(
            service, options.dataset, rails_port, options.rails_yjit,
            temporary / "rails.log")
        rails_env = rails_environment(service, options.dataset, rails_port, options.rails_yjit)
        rails = HttpTarget("rails", rails_port, RAILS_BEARER,
                           rails_seed["workspace"], rails_seed["issue"],
                           rails_seed["denied_issue"], rails_process.pid,
                           [hashlib.sha256(f"rails-bench-issue-{index}".encode()).hexdigest()
                            for index in range(options.dataset)])
        targets = {"diamond": diamond, "rails": rails}
        sequence = itertools.count(1)
        operations = ("page", "read", "denied", "update", "create")
        ordinal = 0
        for repeat in range(1, options.repeats + 1):
            for concurrency in options.concurrency:
                for operation in operations:
                    order = ["diamond", "rails"] if ordinal % 2 == 0 else ["rails", "diamond"]
                    ordinal += 1
                    for name in order:
                        print(json.dumps({"running": name, "operation": operation,
                                          "concurrency": concurrency, "repeat": repeat}), flush=True)
                        phase = load_phase(targets[name], operation, concurrency,
                                           options.duration, sequence)
                        phase["repeat"] = repeat
                        phases.append(phase)
                        # The warm-up is also a persisted create and is included in the oracle.
                        if operation == "create":
                            successful_creates[name] += phase["successful_creates"] + 1

        oracle = independent_oracle(service, targets, options.dataset, successful_creates)
        if any(phase["unexpected_responses"] for phase in phases):
            raise AssertionError("one or more measured phases returned unexpected responses")
        if any(not item["count_matches"] or not item["update_observed"]
               for item in oracle.values()):
            raise AssertionError({"independent_oracle": oracle})
    except Exception as failure:
        error = repr(failure)
    finally:
        cleanup["rails"] = stop_process(rails_process)
        try:
            service.close()
        except Exception as failure:
            cleanup["service_exception"] = repr(failure)
        cleanup["diamond"] = service.shutdowns
        cleanup["errors"] = service.cleanup_errors

    status = "PASS" if error is None and not service.cleanup_errors else "FAIL"
    report = {
        "status": status,
        "error": error,
        "scope": ("real loopback HTTP, real release/production processes, one owned PostgreSQL "
                  "cluster, authenticated CRUD, authorization denial, and direct persisted-state "
                  "readback; no TLS, proxy, browser, WebSocket, job, or cross-host claim"),
        "provenance": provenance(options, copied_binary, rails_env),
        "startup": startup,
        "phases": phases,
        "independent_oracle": oracle,
        "cleanup": cleanup,
        "temporary_root": str(temporary) if status == "FAIL" else None,
    }
    options.report.parent.mkdir(parents=True, exist_ok=True)
    options.report.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    options.report.with_suffix(".md").write_text(markdown(report))
    print(json.dumps({"status": status, "report": str(options.report),
                      "phases": len(phases), "error": error}, ensure_ascii=False))
    if status == "PASS":
        shutil.rmtree(temporary)
    return int(status != "PASS")


if __name__ == "__main__":
    raise SystemExit(main())
