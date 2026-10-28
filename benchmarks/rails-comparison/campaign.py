#!/usr/bin/env python3
"""Checkpointed sustained mixed-workload Diamond/Rails benchmark campaign."""

from __future__ import annotations

import argparse
import concurrent.futures
import contextlib
import datetime as dt
import hashlib
import itertools
import json
import math
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile
import threading
import time
from typing import Any

from run import (
    APP_NAME,
    DIAMOND_PROJECT,
    HERE,
    HttpTarget,
    MemorySampler,
    RAILS,
    RAILS_BEARER,
    ROOT,
    RUBY_IMAGE,
    Service,
    command_version,
    cpu_model,
    diamond_setup,
    diamond_table,
    free_port,
    process_cpu_seconds,
    rails_environment,
    rails_setup,
    rails_start,
    run,
    sha256,
    source_hashes,
    stop_process,
)

MIX = (
    "read", "read", "read", "read", "read", "read", "read",
    "page", "page", "page", "page", "page",
    "update", "update", "update", "update",
    "create", "create",
    "denied", "denied",
)
CAPACITY_STATUSES = {429, 503}


def latency_ceiling_us(value: float) -> int:
    microseconds = max(1, math.ceil(value))
    if microseconds <= 10_000:
        quantum = 10
    elif microseconds <= 100_000:
        quantum = 100
    elif microseconds <= 1_000_000:
        quantum = 1_000
    elif microseconds <= 20_000_000:
        quantum = 10_000
    else:
        quantum = 100_000
    return ((microseconds + quantum - 1) // quantum) * quantum


class Histogram:
    def __init__(self) -> None:
        self.count = 0
        self.total_us = 0.0
        self.maximum_us = 0.0
        self.buckets: dict[int, int] = {}

    def add_ms(self, milliseconds: float) -> None:
        microseconds = milliseconds * 1_000
        bucket = latency_ceiling_us(microseconds)
        self.count += 1
        self.total_us += microseconds
        self.maximum_us = max(self.maximum_us, microseconds)
        self.buckets[bucket] = self.buckets.get(bucket, 0) + 1

    def merge(self, other: "Histogram") -> None:
        self.count += other.count
        self.total_us += other.total_us
        self.maximum_us = max(self.maximum_us, other.maximum_us)
        for bucket, count in other.buckets.items():
            self.buckets[bucket] = self.buckets.get(bucket, 0) + count

    def percentile_ms(self, fraction: float) -> float:
        if not self.count:
            return 0.0
        wanted = max(1, math.ceil(self.count * fraction))
        observed = 0
        for upper, count in sorted(self.buckets.items()):
            observed += count
            if observed >= wanted:
                return upper / 1_000
        return self.maximum_us / 1_000

    def report(self) -> dict[str, float | int | str]:
        return {
            "count": self.count,
            "mean": self.total_us / self.count / 1_000 if self.count else 0,
            "p50": self.percentile_ms(0.50),
            "p95": self.percentile_ms(0.95),
            "p99": self.percentile_ms(0.99),
            "max": self.maximum_us / 1_000,
            "quantization": "10us<=10ms,100us<=100ms,1ms<=1s,10ms<=20s,100ms above",
        }


class OperationStats:
    def __init__(self) -> None:
        self.completed = Histogram()
        self.successful = Histogram()
        self.successes = 0
        self.capacity_rejections = 0
        self.transaction_conflicts = 0
        self.unexpected = 0
        self.transport_errors = 0
        self.successful_creates = 0
        self.examples: list[dict[str, Any]] = []
        self.statuses: dict[str, int] = {}

    def observe(self, target: HttpTarget, operation: str, sequence: int,
                status: int, body: Any, elapsed_ms: float) -> None:
        self.completed.add_ms(elapsed_ms)
        self.statuses[str(status)] = self.statuses.get(str(status), 0) + 1
        if target.valid(operation, status, body, sequence):
            self.successes += 1
            self.successful.add_ms(elapsed_ms)
            self.successful_creates += int(operation == "create")
        elif status in CAPACITY_STATUSES:
            self.capacity_rejections += 1
        elif status == 409 and body == {"error": "aborted"}:
            self.transaction_conflicts += 1
        else:
            self.unexpected += 1
            if len(self.examples) < 5:
                self.examples.append({"sequence": sequence, "status": status, "body": body})

    def transport_error(self, sequence: int, failure: Exception) -> None:
        self.unexpected += 1
        self.transport_errors += 1
        if len(self.examples) < 5:
            self.examples.append({"sequence": sequence, "exception": repr(failure)})

    def merge(self, other: "OperationStats") -> None:
        self.completed.merge(other.completed)
        self.successful.merge(other.successful)
        self.successes += other.successes
        self.capacity_rejections += other.capacity_rejections
        self.transaction_conflicts += other.transaction_conflicts
        self.unexpected += other.unexpected
        self.transport_errors += other.transport_errors
        self.successful_creates += other.successful_creates
        self.examples = (self.examples + other.examples)[:5]
        for status, count in other.statuses.items():
            self.statuses[status] = self.statuses.get(status, 0) + count

    def report(self, elapsed: float) -> dict[str, Any]:
        completed = self.completed.count
        return {
            "completed_requests": completed,
            "http_status_counts": self.statuses,
            "successful_requests": self.successes,
            "capacity_rejections": self.capacity_rejections,
            "transaction_conflicts": self.transaction_conflicts,
            "unexpected_responses": self.unexpected,
            "transport_errors": self.transport_errors,
            "success_rate": self.successes / completed if completed else 0,
            "completed_throughput_requests_per_second": completed / elapsed if elapsed else 0,
            "successful_throughput_requests_per_second": self.successes / elapsed if elapsed else 0,
            "all_response_latency_ms": self.completed.report(),
            "successful_response_latency_ms": self.successful.report(),
            "successful_creates": self.successful_creates,
            "representative_errors": self.examples,
        }


def mixed_phase(target: HttpTarget, concurrency: int, duration: float) -> dict[str, Any]:
    sequence = itertools.count(0)
    barrier = threading.Barrier(concurrency + 1)
    stop_at = [0.0]

    def worker() -> dict[str, OperationStats]:
        local = {operation: OperationStats() for operation in set(MIX)}
        barrier.wait()
        while time.perf_counter() < stop_at[0]:
            current = next(sequence)
            operation = MIX[current % len(MIX)]
            status = 0
            try:
                status, body, elapsed = target.request(operation, current)
                local[operation].observe(target, operation, current, status, body, elapsed)
            except Exception as failure:
                local[operation].transport_error(current, failure)
            if target.backoff and status in CAPACITY_STATUSES:
                raw = getattr(target.local, 'headers', {}).get('retry-after', '1')
                delay = min(5, max(1, int(raw))) if raw.isdecimal() else 1
                remaining = stop_at[0]-time.perf_counter()
                if remaining > 0:
                    time.sleep(min(remaining, delay))
        target.close_connection()
        return local

    idle_memory = MemorySampler(target.pid)
    initial_memory = {name: values[0] for name, values in idle_memory.samples.items()}
    cpu_before = process_cpu_seconds(target.pid)
    started = time.perf_counter()
    cost_samplers = {'client': MemorySampler(os.getpid())}
    if hasattr(target, 'postgres_pid'):
        cost_samplers['postgresql'] = MemorySampler(target.postgres_pid, tree=True)
    with contextlib.ExitStack() as stack, MemorySampler(target.pid, tree=True) as memory, concurrent.futures.ThreadPoolExecutor(
            max_workers=concurrency) as pool:
        for sampler in cost_samplers.values():
            stack.enter_context(sampler)
        futures = [pool.submit(worker) for _ in range(concurrency)]
        stop_at[0] = time.perf_counter() + duration
        barrier.wait()
        outcomes = [future.result(timeout=duration + 60) for future in futures]
    elapsed = time.perf_counter() - started
    cpu_seconds = memory.cpu_seconds
    merged = {operation: OperationStats() for operation in set(MIX)}
    for outcome in outcomes:
        for operation, stats in outcome.items():
            merged[operation].merge(stats)
    aggregate = OperationStats()
    for stats in merged.values():
        aggregate.merge(stats)
    return {
        "elapsed_seconds": elapsed,
        "concurrency": concurrency,
        "aggregate": aggregate.report(elapsed),
        "operations": {name: merged[name].report(elapsed) for name in sorted(merged)},
        "process_cpu_seconds": cpu_seconds,
        "process_cpu_utilization_percent": cpu_seconds / elapsed * 100 if elapsed else 0,
        "process_cpu_microseconds_per_completed_request": (
            cpu_seconds * 1_000_000 / aggregate.completed.count
            if aggregate.completed.count else 0),
        "idle_process_memory_bytes": initial_memory,
        "process_memory_bytes": memory.statistics(),
        "successful_creates": aggregate.successful_creates,
        "service_cost": {name: {'cpu_seconds': sampler.cpu_seconds,
            'cpu_utilization_percent': sampler.cpu_seconds/elapsed*100,
            'memory_bytes': sampler.statistics()} for name, sampler in cost_samplers.items()},
    }


def qualify(target: HttpTarget) -> None:
    socket_address = None
    for sequence, operation in enumerate(("page", "read", "denied", "update", "create"), 1):
        status, body, _ = target.request(operation, sequence)
        if not target.valid(operation, status, body, sequence):
            raise AssertionError({"qualification": operation, "status": status, "body": body})
        if target.connection_reuse:
            observed = target.local.connection.sock.getsockname()
            if socket_address is not None and observed != socket_address:
                raise AssertionError('persistent HTTP connection was replaced')
            socket_address = observed
    target.close_connection()


def copy_rows(service: Service, table: str, columns: list[str], rows: list[tuple[Any, ...]]) -> None:
    def field(value: Any) -> str:
        text = str(value)
        if any(character in text for character in ("\t", "\n", "\r", "\\")):
            raise ValueError("benchmark COPY field requires escaping")
        return text

    data = "\n".join("\t".join(field(value) for value in row) for row in rows) + "\n"
    quoted = ",".join(f'"{column}"' for column in columns)
    subprocess.run(
        ["psql", service.owner, "-X", "-q", "-v", "ON_ERROR_STOP=1", "-c",
         f"COPY {table} ({quoted}) FROM STDIN"],
        input=data, text=True, check=True, capture_output=True, timeout=60,
    )


def reset_state(service: Service, target: HttpTarget, dataset: int) -> dict[str, Any]:
    if target.name == "diamond":
        table = diamond_table("Issues")
        service.sql(f"TRUNCATE {table}")
        app = hashlib.sha256(APP_NAME.encode()).hexdigest()
        service.sql(f"DELETE FROM diamond_identity.rates WHERE app='{app}'")
        rows = [
            (target.issue_ids[index], "alice", target.workspace,
             f"Issue {index:08d}".encode().hex(),
             f"Benchmark issue details {index} — Unicode 雪".encode().hex(), index)
            for index in range(dataset)
        ]
        rows.append((target.denied_issue, "bob", target.denied_workspace,
                     "Denied issue".encode().hex(),
                     "This issue belongs to another principal.".encode().hex(), 0))
        copy_rows(service, table, ["id", "owner", "workspace", "title", "details", "rank"], rows)
        service.sql(f"ANALYZE {table}")
        state = service.sql(
            "SELECT rank::text||E'\\t'||convert_from(decode(title,'hex'),'UTF8')||E'\\t'||"
            f"convert_from(decode(details,'hex'),'UTF8') FROM {table} "
            f"WHERE workspace='{target.workspace}' ORDER BY rank,id")
        count = int(service.sql(f"SELECT count(*) FROM {table} WHERE workspace='{target.workspace}'"))
    else:
        table = "rails_bench.bench_issues"
        service.sql("TRUNCATE rails_bench.bench_issues, rails_bench.bench_rates")
        alice = hashlib.sha256(b"rails-bench-alice").hexdigest()
        bob = hashlib.sha256(b"rails-bench-bob").hexdigest()
        rows = [
            (target.issue_ids[index], alice, target.workspace,
             f"Issue {index:08d}", f"Benchmark issue details {index} — Unicode 雪", index)
            for index in range(dataset)
        ]
        rows.append((target.denied_issue, bob, target.denied_workspace, "Denied issue",
                     "This issue belongs to another principal.", 0))
        copy_rows(service, table, ["id", "owner_id", "workspace_id", "title", "details", "rank"], rows)
        service.sql(f"ANALYZE {table}")
        state = service.sql(
            f"SELECT rank::text||E'\\t'||title||E'\\t'||details FROM {table} "
            f"WHERE workspace_id='{target.workspace}' ORDER BY rank,id")
        count = int(service.sql(
            f"SELECT count(*) FROM {table} WHERE workspace_id='{target.workspace}'"))
    observation = {
        "rows": count,
        "logical_state_sha256": hashlib.sha256(state.encode()).hexdigest(),
    }
    if count != dataset:
        raise AssertionError({"baseline": observation, "expected_rows": dataset})
    return observation


def state_oracle(service: Service, target: HttpTarget, dataset: int,
                 successful_creates: int) -> dict[str, Any]:
    if target.name == "diamond":
        table = diamond_table("Issues")
        title = "convert_from(decode(title,'hex'),'UTF8')"
        workspace = f"workspace='{target.workspace}'"
    else:
        table = "rails_bench.bench_issues"
        title = "title"
        workspace = f"workspace_id='{target.workspace}'"
    row = service.sql_json(
        f"SELECT count(*)::bigint AS count, count(*) FILTER (WHERE {title} LIKE 'Updated %')::bigint AS updated, "
        f"count(*) FILTER (WHERE {title} LIKE 'Created %')::bigint AS created FROM {table} WHERE {workspace}")[0]
    expected = dataset + successful_creates
    return {
        **row,
        "expected_count": expected,
        "count_matches": int(row["count"]) == expected,
        "create_count_matches": int(row["created"]) == successful_creates,
        "update_observed": int(row["updated"]) > 0,
    }


def ruby_yjit_enabled(service: Service, dataset: int, enabled: bool) -> bool:
    environment = rails_environment(service, dataset, free_port(), enabled)
    result = run(
        ["bundle", "exec", "ruby", "-e", "print RubyVM::YJIT.enabled?"],
        cwd=RAILS, env=environment,
    )
    return result.stdout == "true"


def cell_key(duration: float, concurrency: int, repeat: int, variant: str) -> str:
    return f"{duration:g}:{concurrency}:{repeat}:{variant}"


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
    os.replace(temporary, path)


def campaign_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Sustained Diamond and Rails campaign",
        "",
        f"Status: **{report['status']}**",
        "",
        "| Variant | Duration s | C | Repeat | Success req/s | p50 ms | p95 ms | p99 ms | CPU % | Mean PSS MiB | p95 PSS MiB | Capacity rejects | Conflicts | Errors |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for cell in report.get("cells", []):
        if "measurement" not in cell:
            continue
        measurement = cell["measurement"]
        aggregate = measurement["aggregate"]
        latency = aggregate["successful_response_latency_ms"]
        pss = measurement["process_memory_bytes"]["pss"]
        lines.append(
            f"| {cell['variant']} | {cell['duration_seconds']:g} | {cell['concurrency']} | "
            f"{cell['repeat']} | {aggregate['successful_throughput_requests_per_second']:.1f} | "
            f"{latency['p50']:.2f} | {latency['p95']:.2f} | {latency['p99']:.2f} | "
            f"{measurement['process_cpu_utilization_percent']:.1f} | {pss['mean']/1048576:.1f} | "
            f"{pss['p95']/1048576:.1f} | {aggregate['capacity_rejections']} | "
            f"{aggregate['transaction_conflicts']} | "
            f"{aggregate['unexpected_responses']} |")
    lines += [
        "",
        "Latency percentiles are successful responses. The JSON report also contains all-response",
        "latency, per-operation results, offered throughput, CPU/request, RSS, USS, PSS, baseline",
        "state hashes, capacity rejections, and independent database readback.",
        "",
    ]
    return "\n".join(lines)


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, default=ROOT / "target/release/diamond")
    parser.add_argument("--dataset", type=int, default=1000)
    parser.add_argument("--durations", type=float, nargs="+", default=[30, 120, 600])
    parser.add_argument("--concurrency", type=int, nargs="+", default=[1, 8, 32, 128])
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--warmup", type=float, default=10)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument('--keep-alive', action='store_true')
    parser.add_argument('--backoff', action='store_true', help='Honor capacity Retry-After between offered requests')
    parser.add_argument('--rails-workers', type=int, default=0)
    parser.add_argument('--rails-threads', type=int, default=4)
    parser.add_argument('--diamond-runtime', type=Path, help='TOML contents of a [runtime] table, without the header')
    parser.add_argument('--cpu-affinity', type=int, nargs='+', help='Inherit this CPU set for harness, apps and PostgreSQL')
    options = parser.parse_args()
    if options.dataset < 100 or options.repeats < 2 or options.warmup <= 0:
        parser.error("dataset must be >=100, repeats >=2, and warmup positive")
    if any(duration <= 0 for duration in options.durations):
        parser.error("durations must be positive")
    if any(value < 1 or value > 256 for value in options.concurrency):
        parser.error("concurrency must be between 1 and 256")
    if not (0 <= options.rails_workers <= 4 and 1 <= options.rails_threads <= 16
            and max(1, options.rails_workers)*options.rails_threads <= 16):
        parser.error('Rails workers 0..4, threads 1..16, at most 16 pooled connections in this fixture')
    if options.cpu_affinity:
        if not set(options.cpu_affinity).issubset(os.sched_getaffinity(0)):
            parser.error('requested CPUs are outside the current allowed set')
        os.sched_setaffinity(0, options.cpu_affinity)
    if options.report is None:
        timestamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        options.report = HERE / "results" / f"sustained-{timestamp}.json"
    return options


def main() -> int:
    options = parse_arguments()
    binary = options.binary.resolve()
    if not binary.is_file():
        raise SystemExit(f"Diamond release binary not found: {binary}")
    configuration = {
        "dataset": options.dataset,
        "durations_seconds": options.durations,
        "concurrency": options.concurrency,
        "repeats": options.repeats,
        "warmup_seconds": options.warmup,
        "mix_percent": {"read": 35, "page": 25, "update": 20, "create": 10, "denied": 10},
        "variants": ["diamond", "rails-no-yjit", "rails-yjit"],
        'keep_alive': options.keep_alive,
        'backoff': options.backoff,
        'rails_workers': options.rails_workers,
        'rails_threads': options.rails_threads,
        'diamond_runtime': options.diamond_runtime.read_text() if options.diamond_runtime else None,
        'cpu_affinity': sorted(os.sched_getaffinity(0)),
    }
    options.report.parent.mkdir(parents=True, exist_ok=True)
    prior: dict[str, Any] | None = None
    if options.resume:
        if not options.report.is_file():
            raise SystemExit("--resume requires an existing --report")
        prior = json.loads(options.report.read_text())
        if prior.get("configuration") != configuration:
            raise SystemExit("resume configuration differs from the checkpoint")
        if prior.get("provenance", {}).get("diamond_binary_sha256") != sha256(binary):
            raise SystemExit("resume binary differs from the checkpoint")
        if prior.get("provenance", {}).get("benchmark_source_sha256") != source_hashes():
            raise SystemExit("resume sources differ from the checkpoint")

    prior_incomplete = ([cell for cell in prior.get("cells", [])
                         if cell.get("status") != "PASS"] if prior else [])
    cells = ([cell for cell in prior.get("cells", [])
              if cell.get("status") == "PASS"] if prior else [])
    completed = {cell["key"] for cell in cells if cell.get("status") == "PASS"}
    state_root = ROOT / ".diamond"
    state_root.mkdir(exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix="rails-campaign-", dir=state_root))
    copied_binary = temporary / "diamond"
    shutil.copy2(binary, copied_binary)
    project = DIAMOND_PROJECT
    if options.diamond_runtime:
        shutil.copytree(DIAMOND_PROJECT.parent, temporary / 'app')
        project = temporary / 'app/diamond.toml'
        project.write_text(project.read_text() + '\n[runtime]\n' + options.diamond_runtime.read_text())
    service = Service(temporary, copied_binary, project)
    service.rails_workers = options.rails_workers
    service.rails_threads = options.rails_threads
    service.extra_serve = ("--identity-mailbox", str(service.mailbox))
    cleanup: dict[str, Any] = {}
    fatal: str | None = None
    segments = list(prior.get("segments", [])) if prior else []
    segment = {"started_utc": dt.datetime.now(dt.timezone.utc).isoformat(), "temporary_root": str(temporary)}
    segments.append(segment)
    report: dict[str, Any] = {
        "status": "RUNNING",
        "configuration": configuration,
        "scope": ("actual release Diamond and production Rails/Puma over loopback HTTP; one owned "
                  "PostgreSQL cluster; one application deployment active during each cell; "
                  "application process-tree, PostgreSQL process-tree and client CPU/PSS reported separately"),
        "provenance": {
            "timestamp_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
            "git_revision": command_version(["git", "rev-parse", "HEAD"]),
            "git_dirty": bool(run(["git", "status", "--porcelain"], check=False).stdout.strip()),
            "platform": platform.platform(),
            "kernel": platform.release(),
            "cpu_model": cpu_model(),
            "logical_cpu_count": os.cpu_count(),
            "python": platform.python_version(),
            "ruby": command_version(["ruby", "--version"]),
            "rails": command_version(["bundle", "exec", "rails", "--version"], cwd=RAILS),
            "puma": command_version(["bundle", "exec", "puma", "--version"], cwd=RAILS),
            "postgresql": command_version(["postgres", "--version"]),
            "ruby_build_image": RUBY_IMAGE,
            "diamond_binary_sha256": sha256(binary),
            "rails_lockfile_sha256": sha256(RAILS / "Gemfile.lock"),
            "benchmark_source_sha256": source_hashes(),
            "http_connection_reuse": options.keep_alive,
            'cpu_affinity': sorted(os.sched_getaffinity(0)),
            'cpu_governors': {p.parent.parent.name: p.read_text().strip() for p in
                Path('/sys/devices/system/cpu').glob('cpu[0-9]*/cpufreq/scaling_governor')},
            "latency_histogram": "bounded microsecond histogram; quantized percentiles, exact mean/max",
        },
        "segments": segments,
        "cells": cells,
        "prior_incomplete_cells": prior_incomplete,
        "cleanup": cleanup,
    }

    def checkpoint() -> None:
        atomic_json(options.report, report)
        options.report.with_suffix(".md").write_text(campaign_markdown(report))

    try:
        service.postgres()
        diamond, diamond_setup_startup, diamond_seed = diamond_setup(service, options.dataset)
        service.stop_node("A")
        rails_port = free_port()
        rails_process, rails_setup_startup, rails_seed = rails_setup(
            service, options.dataset, rails_port, False, temporary / "rails-setup.log")
        cleanup["rails_setup"] = stop_process(rails_process)
        report["setup_startup_seconds"] = {
            "diamond": diamond_setup_startup,
            "rails_no_yjit": rails_setup_startup,
        }
        report["provenance"]["rails_yjit_probe"] = {
            "without_yjit": ruby_yjit_enabled(service, options.dataset, False),
            "with_yjit": ruby_yjit_enabled(service, options.dataset, True),
        }
        if report["provenance"]["rails_yjit_probe"] != {
                "without_yjit": False, "with_yjit": True}:
            raise AssertionError(report["provenance"]["rails_yjit_probe"])

        rails_issue_ids = [
            hashlib.sha256(f"rails-bench-issue-{index}".encode()).hexdigest()
            for index in range(options.dataset)
        ]
        rails_template = {
            "workspace": rails_seed["workspace"],
            "issue": rails_seed["issue"],
            "denied_issue": rails_seed["denied_issue"],
            "denied_workspace": hashlib.sha256(b"rails-bench-denied-workspace").hexdigest(),
            "issue_ids": rails_issue_ids,
        }
        expected_baseline: str | None = None
        ordinal = 0
        for repeat in range(1, options.repeats + 1):
            for duration in options.durations:
                for concurrency in options.concurrency:
                    variants = list(configuration["variants"])
                    variants = variants[ordinal % len(variants):] + variants[:ordinal % len(variants)]
                    ordinal += 1
                    for variant in variants:
                        key = cell_key(duration, concurrency, repeat, variant)
                        if key in completed:
                            continue
                        print(json.dumps({"cell": key, "status": "STARTING"}), flush=True)
                        cell: dict[str, Any] = {
                            "key": key,
                            "variant": variant,
                            "duration_seconds": duration,
                            "concurrency": concurrency,
                            "repeat": repeat,
                            "status": "RUNNING",
                            "started_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
                        }
                        cells.append(cell)
                        checkpoint()
                        process: subprocess.Popen[str] | None = None
                        try:
                            if variant == "diamond":
                                started = time.perf_counter()
                                service.start_node("A")
                                cell["startup_seconds"] = time.perf_counter() - started
                                process = service.nodes["A"]
                                target = HttpTarget(
                                    "diamond", service.ports["A"], diamond.bearer,
                                    diamond.workspace, diamond.issue, diamond.denied_issue,
                                    process.pid, diamond.issue_ids,
                                )
                                target.denied_workspace = diamond_seed["denied_workspace"]
                            else:
                                yjit = variant == "rails-yjit"
                                port = free_port()
                                process, cell["startup_seconds"] = rails_start(
                                    service, options.dataset, port, yjit,
                                    temporary / f"{variant}-{repeat}-{duration:g}-{concurrency}.log",
                                )
                                target = HttpTarget(
                                    variant, port, RAILS_BEARER,
                                    rails_template["workspace"], rails_template["issue"],
                                    rails_template["denied_issue"], process.pid,
                                    rails_template["issue_ids"],
                                )
                                target.denied_workspace = rails_template["denied_workspace"]

                            target.connection_reuse = options.keep_alive
                            target.backoff = options.backoff
                            target.postgres_pid = int((service.data / 'postmaster.pid').read_text().splitlines()[0])

                            reset_state(service, target, options.dataset)
                            qualify(target)
                            warmup = mixed_phase(target, concurrency, options.warmup)
                            cell["warmup"] = {
                                "elapsed_seconds": warmup["elapsed_seconds"],
                                "completed_requests": warmup["aggregate"]["completed_requests"],
                                "successful_requests": warmup["aggregate"]["successful_requests"],
                                "capacity_rejections": warmup["aggregate"]["capacity_rejections"],
                                "transaction_conflicts": warmup["aggregate"]["transaction_conflicts"],
                                "unexpected_responses": warmup["aggregate"]["unexpected_responses"],
                            }
                            baseline = reset_state(service, target, options.dataset)
                            cell["baseline"] = baseline
                            if expected_baseline is None:
                                expected_baseline = baseline["logical_state_sha256"]
                            if baseline["logical_state_sha256"] != expected_baseline:
                                raise AssertionError({"baseline_mismatch": baseline,
                                                      "expected": expected_baseline})
                            cell["host_load_average_before"] = os.getloadavg()
                            measurement = mixed_phase(target, concurrency, duration)
                            cell["host_load_average_after"] = os.getloadavg()
                            cell["measurement"] = measurement
                            cell["oracle"] = state_oracle(
                                service, target, options.dataset,
                                measurement["successful_creates"],
                            )
                            aggregate = measurement["aggregate"]
                            oracle = cell["oracle"]
                            cell["status"] = "PASS" if (
                                aggregate["unexpected_responses"] == 0
                                and oracle["count_matches"]
                                and oracle["create_count_matches"]
                                and oracle["update_observed"]
                            ) else "FAIL"
                        except Exception as failure:
                            cell["status"] = "FAIL"
                            cell["error"] = repr(failure)
                        finally:
                            if variant == "diamond":
                                service.stop_node("A")
                            else:
                                cell["shutdown"] = stop_process(process)
                            cell["finished_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
                            checkpoint()
                            print(json.dumps({"cell": key, "status": cell["status"]}), flush=True)
    except KeyboardInterrupt:
        fatal = "interrupted"
        report["status"] = "INTERRUPTED"
    except Exception as failure:
        fatal = repr(failure)
        report["status"] = "FAIL"
    finally:
        try:
            service.close()
        except Exception as failure:
            cleanup["service_exception"] = repr(failure)
        cleanup["diamond_shutdowns"] = service.shutdowns
        cleanup["errors"] = service.cleanup_errors
        segment["finished_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
        segment["fatal"] = fatal

    expected_cells = (len(options.durations) * len(options.concurrency)
                      * options.repeats * len(configuration["variants"]))
    if report["status"] == "RUNNING":
        report["status"] = "PASS" if (
            len(cells) == expected_cells
            and all(cell.get("status") == "PASS" for cell in cells)
            and not service.cleanup_errors
        ) else "FAIL"
    report["expected_cells"] = expected_cells
    report["completed_cells"] = sum(cell.get("status") == "PASS" for cell in cells)
    report["fatal"] = fatal
    report["temporary_root"] = str(temporary) if report["status"] != "PASS" else None
    checkpoint()
    print(json.dumps({"status": report["status"], "report": str(options.report),
                      "completed_cells": report["completed_cells"],
                      "expected_cells": expected_cells, "fatal": fatal}), flush=True)
    if report["status"] == "PASS":
        shutil.rmtree(temporary)
    return int(report["status"] != "PASS")


if __name__ == "__main__":
    raise SystemExit(main())
