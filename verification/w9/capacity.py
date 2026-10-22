#!/usr/bin/env python3
"""Sustain the documented W9 capacity and interruption envelope."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import random
import shutil
import socket
import statistics
import sys
import tempfile
import threading
import time

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[1]
W78 = BASE.parent / "w7_w8_independent"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fixture = load("_w9_capacity_fixture", W78 / "fixture.py")
sys.modules["fixture"] = fixture
service_module = load("_w9_capacity_service", W78 / "service.py")
sys.modules["service"] = service_module
common = load("_w9_capacity_common", BASE.parent / "w5_independent" / "common.py")
receiver_module = load("_w9_capacity_receiver", W78 / "receiver.py")
wire = load("_w9_capacity_wire", W78 / "wire.py")

CLIENTS = 100
RECORDS = 100_000
QUALIFICATION_SECONDS = 1_800
PERIOD_SECONDS = 2.0
SUCCESS_FLOOR = 0.90
SUCCESS_P99_CEILING_SECONDS = 3.0


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = min(len(ordered) - 1, max(0, int((len(ordered) - 1) * fraction)))
    return round(ordered[position], 6)


def rss_kib(pid: int) -> int | None:
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1])
    except (FileNotFoundError, ProcessLookupError, PermissionError, ValueError):
        return None
    return None


def hardware() -> dict:
    cpu = "unknown"
    try:
        cpu = next(
            line.partition(":")[2].strip()
            for line in Path("/proc/cpuinfo").read_text().splitlines()
            if line.startswith("model name")
        )
    except (FileNotFoundError, StopIteration):
        pass
    memory_kib = None
    try:
        memory_kib = int(Path("/proc/meminfo").read_text().splitlines()[0].split()[1])
    except (FileNotFoundError, IndexError, ValueError):
        pass
    return {
        "hostname": platform.node(),
        "kernel": platform.release(),
        "machine": platform.machine(),
        "cpu": cpu,
        "logical_cpus": os.cpu_count(),
        "memory_kib": memory_kib,
        "python": platform.python_version(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", required=True, type=Path)
    parser.add_argument("--report", type=Path, default=BASE / "capacity_results.json")
    parser.add_argument("--duration", type=float, default=QUALIFICATION_SECONDS + 5)
    options = parser.parse_args()
    root = Path(tempfile.mkdtemp(prefix="diamond-w9-capacity-"))
    binary = root / "diamond"
    shutil.copy2(options.binary, binary)
    project = fixture.write(root / "project")
    with project.open("a") as manifest:
        manifest.write(
            """
[http_api]
version = 1
[http_api.operations.read]
function = "server.read"
auth = "token"
[http_api.operations.page]
function = "server.page"
auth = "token"
[http_api.operations.count]
function = "server.count"
auth = "token"
[http_api.operations.joined]
function = "server.joined"
auth = "token"
"""
        )
    service = service_module.Service(root, binary, project)
    receiver = receiver_module.Receiver()
    service.runtime["http"] = {
        "inventory": {
            "origin": receiver.origin,
            "network": "loopback",
            "paths": ["/v1/"],
            "methods": ["POST"],
            "timeout_ms": 5000,
        }
    }
    failures: list[dict] = []
    control: list[dict] = []
    samples: list[dict] = []
    latencies: dict[str, list[float]] = defaultdict(list)
    statuses: dict[str, Counter] = defaultdict(Counter)
    request_errors: Counter = Counter()
    max_rss: dict[str, int] = {}
    stats_lock = threading.Lock()
    auth_lock = threading.Lock()
    stop = threading.Event()
    barrier = threading.Barrier(CLIENTS + 1)
    threads: list[threading.Thread] = []
    socket_connection = None
    socket_events: list[dict] = []
    socket_end: dict = {}
    slow_transfer: dict = {}
    client = None
    bearer = None
    started = None
    report = {
        "status": "FAIL",
        "qualification_complete": False,
        "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
        "error": "interrupted before qualification receipt was finalized",
        "failures": failures,
        "hardware": hardware(),
    }

    def fail(name: str, observed, expected: str) -> None:
        failures.append({"name": name, "observed": observed, "expected": expected})

    def current_auth() -> tuple[str, str]:
        with auth_lock:
            return client.cookie or "", client.csrf

    def request(kind: str, index: int) -> int:
        cookie, csrf = current_auth()
        red = fixture.identifier("red")
        issue = f"capacity-red-{index % (RECORDS // 2):05d}"
        node = "A" if index % 2 == 0 else "B"
        if kind == "root":
            return service.request(node, "/", headers={"Cookie": cookie})[0]
        if kind == "ready":
            return service.request(node, "/_diamond/health/ready")[0]
        arguments = {
            "read": [{"tag": "issue", "value": issue}],
            "page": [{"tag": "workspace", "value": red}, ""],
            "count": [],
            "joined": [{"tag": "workspace", "value": red}],
        }[kind.removeprefix("api-")]
        if kind.startswith("api-"):
            headers = {
                "Authorization": "Bearer " + bearer,
                "X-Diamond-API-Version": "1",
            }
            return service.request(
                node,
                "/api/v1/" + kind.removeprefix("api-"),
                {"arguments": arguments},
                headers,
            )[0]
        headers = {"Cookie": cookie, "X-Diamond-CSRF": csrf}
        return service.request(
            node,
            "/_diamond/rpc/" + kind,
            {"contract": contract["rpc_id"], "arguments": arguments},
            headers,
        )[0]

    choices = (
        ["root"] * 30
        + ["read"] * 24
        + ["page"] * 14
        + ["count"] * 8
        + ["joined"] * 4
        + ["api-read"] * 5
        + ["api-page"] * 3
        + ["api-count"] * 2
        + ["ready"] * 10
    )

    def load_client(index: int) -> None:
        randomizer = random.Random(0xD1A09000 + index)
        barrier.wait()
        sequence = 0
        deadline = time.monotonic()
        while not stop.is_set():
            kind = choices[(index * 17 + sequence * 29) % len(choices)]
            before = time.monotonic()
            try:
                status = request(kind, index + sequence)
                elapsed = time.monotonic() - before
                with stats_lock:
                    statuses[kind][str(status)] += 1
                    if 200 <= status < 300:
                        latencies[kind].append(elapsed)
            except Exception as error:  # exact classes are retained, not discarded
                with stats_lock:
                    request_errors[type(error).__name__] += 1
            sequence += 1
            deadline += PERIOD_SECONDS
            stop.wait(max(0.0, deadline - time.monotonic() + randomizer.uniform(-0.08, 0.08)))

    def read_socket() -> None:
        while not stop.is_set():
            try:
                value = socket_connection.receive()
                socket_events.append({"elapsed": round(time.monotonic() - started, 3), "value": value})
                if value.get("kind") == "close":
                    socket_end.update(value=value, elapsed=round(time.monotonic() - started, 3))
                    return
            except socket.timeout:
                continue
            except (EOFError, OSError, ValueError) as error:
                socket_end.update(error=type(error).__name__, elapsed=round(time.monotonic() - started, 3))
                return

    def slow_upload() -> None:
        length = 8 * 1024 * 1024
        outcome = client.request(
            "A",
            "/_diamond/files/issue_files/begin",
            {
                "parent": "capacity-red-00000",
                "filename": "w9-slow-capacity.bin",
                "length": str(length),
                "media_type": "application/octet-stream",
            },
        )
        slow_transfer["begin_status"] = outcome[0]
        if outcome[0] != 200:
            return
        identifier = outcome[1]["id"]
        upload = outcome[1]["upload"]
        cookie, csrf = current_auth()
        request_head = (
            f"PUT /_diamond/files/issue_files/{identifier} HTTP/1.1\r\n"
            f"Host: {service.authority}\r\nOrigin: {service.origin}\r\nCookie: {cookie}\r\n"
            f"X-Diamond-CSRF: {csrf}\r\nX-Diamond-Upload: {upload}\r\n"
            f"Content-Type: application/octet-stream\r\nContent-Length: {length}\r\nConnection: close\r\n\r\n"
        ).encode()
        before = time.monotonic()
        received = bytearray()
        try:
            with socket.create_connection(("127.0.0.1", service.ports["A"]), timeout=5) as stream:
                stream.settimeout(35)
                stream.sendall(request_head)
                chunk = b"W9-capacity-slow-stream\n" * 2731
                remaining = length
                while remaining and not stop.is_set():
                    piece = chunk[: min(len(chunk), remaining)]
                    stream.sendall(piece)
                    remaining -= len(piece)
                    time.sleep(0.1)
                while len(received) < 65_536:
                    data = stream.recv(4096)
                    if not data:
                        break
                    received.extend(data)
            line = bytes(received).split(b"\r\n", 1)[0].decode("ascii", "replace")
            slow_transfer.update(
                put_status=int(line.split()[1]),
                seconds=round(time.monotonic() - before, 3),
                bytes_sent=length - remaining,
            )
            if slow_transfer["put_status"] == 200:
                finish = client.request(
                    "B",
                    f"/_diamond/files/issue_files/{identifier}/finish",
                    {},
                    {"X-Diamond-Upload": upload},
                )
                slow_transfer["finish_status"] = finish[0]
                if finish[0] == 200:
                    deleted = client.request(
                        "A",
                        f"/_diamond/files/issue_files/{identifier}/delete",
                        {},
                        {"X-Diamond-Upload": upload},
                    )
                    slow_transfer["delete_status"] = deleted[0]
        except Exception as error:
            slow_transfer["error"] = repr(error)

    try:
        service.postgres()
        contract, build = service.build()
        service.cli("project-init", project, "--postgres-env", service_module.ENV_NAME)
        service.files_cli(initialize=True)
        service.cli(
            "project-admin-add",
            project,
            "--postgres-env",
            service_module.ENV_NAME,
            "--user",
            "alice",
            "--email",
            "alice@capacity.invalid",
            "--password-stdin",
            input=fixture.PASSWORD + "\n",
        )
        common.seed(service)
        issues = fixture.table("Issues")
        red = fixture.identifier("red")
        blue = fixture.identifier("blue")
        project_red = fixture.identifier("project-red-visible")
        project_blue = fixture.identifier("project-blue-visible")
        service.sql(
            f"INSERT INTO {issues}(id,workspace,project,rank,title) "
            "SELECT 'capacity-red-'||pg_catalog.lpad(g::text,5,'0'),"
            + common.quote(red)
            + ","
            + common.quote(project_red)
            + ",(g%900),pg_catalog.encode(pg_catalog.convert_to('capacity red '||g::text,'UTF8'),'hex') "
            "FROM pg_catalog.generate_series(0,49999) AS g"
        )
        service.sql(
            f"INSERT INTO {issues}(id,workspace,project,rank,title) "
            "SELECT 'capacity-blue-'||pg_catalog.lpad(g::text,5,'0'),"
            + common.quote(blue)
            + ","
            + common.quote(project_blue)
            + ",(g%900),pg_catalog.encode(pg_catalog.convert_to('capacity blue '||g::text,'UTF8'),'hex') "
            "FROM pg_catalog.generate_series(0,49999) AS g"
        )
        service.sql(f"ANALYZE {issues}")
        records = int(service.sql(f"SELECT count(*) FROM {issues}"))
        plan = json.loads(
            service.sql(
                "EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) SELECT id,workspace,project,rank,title FROM "
                + issues
                + " WHERE workspace="
                + common.quote(red)
                + " AND rank<1000 ORDER BY rank,id LIMIT 2"
            )
        )
        service.start_node("A")
        service.start_node("B")
        service.start_worker("one")
        service.start_worker("two")
        client = service_module.Client(service, contract["rpc_id"])
        client.login("alice", fixture.PASSWORD)
        token = client.account(
            "create_token",
            {
                "label": "W9 sustained capacity",
                "operations": ["read", "page", "count", "joined"],
                "scopes": {"workspace": [red, blue]},
                "lifetime_seconds": "3600",
            },
        )
        if token[0] != 200:
            raise AssertionError(("capacity token", token[0], token[1]))
        bearer = token[1]["value"]
        socket_connection = wire.WebSocket(
            "127.0.0.1",
            service.ports["A"],
            "/_diamond/events/issue_changes/capacity-red-00000/0",
            authority=service.authority,
            origin=service.origin,
            cookie=client.cookie,
            timeout=2,
        )
        if socket_connection.status != 101:
            raise AssertionError(("capacity websocket", socket_connection.status))

        started = time.monotonic()
        for index in range(CLIENTS):
            thread = threading.Thread(target=load_client, args=(index,), name=f"w9-client-{index}")
            thread.start()
            threads.append(thread)
        socket_thread = threading.Thread(target=read_socket, name="w9-websocket")
        socket_thread.start()
        threads.append(socket_thread)
        barrier.wait()
        next_sample = 0.0
        next_control = 0.0
        restarted_node = False
        restarted_worker = False
        upload_started = False
        upload_thread = None
        while True:
            elapsed = time.monotonic() - started
            if elapsed >= options.duration:
                break
            if elapsed >= next_sample:
                process_map = {
                    "node_A": service.nodes.get("A"),
                    "node_B": service.nodes.get("B"),
                    "worker_one": service.workers.get("one"),
                    "worker_two": service.workers.get("two"),
                }
                rss = {}
                for name, process in process_map.items():
                    value = rss_kib(process.pid) if process is not None else None
                    rss[name] = value
                    if value is not None:
                        max_rss[name] = max(max_rss.get(name, 0), value)
                metrics = {}
                for node in ["A", "B"]:
                    if node in service.nodes:
                        try:
                            status, _, raw = service.request(node, "/_diamond/health/metrics")
                            metrics[node] = {"status": status, "value": json.loads(raw) if raw else None}
                        except OSError as error:
                            metrics[node] = {"error": type(error).__name__}
                database = service.sql_json(
                    "SELECT (SELECT count(*) FROM pg_stat_activity WHERE datname=current_database()) AS connections,"
                    "(SELECT count(*) FROM diamond_w7.jobs WHERE app="
                    + common.quote(fixture.app_id())
                    + ") AS jobs,(SELECT count(*) FROM diamond_w7.jobs WHERE app="
                    + common.quote(fixture.app_id())
                    + " AND state='pending') AS jobs_pending,(SELECT count(*) FROM diamond_w7.jobs WHERE app="
                    + common.quote(fixture.app_id())
                    + " AND state='running') AS jobs_running,(SELECT count(*) FROM diamond_w7.jobs WHERE app="
                    + common.quote(fixture.app_id())
                    + " AND state='dead') AS jobs_dead,(SELECT count(*) FROM diamond_w7.events WHERE app="
                    + common.quote(fixture.app_id())
                    + ") AS events,(SELECT count(*) FROM diamond_w6.objects WHERE app="
                    + common.quote(fixture.app_id())
                    + " AND state NOT IN ('deleted','failed')) AS objects"
                )[0]
                samples.append(
                    {
                        "elapsed": round(elapsed, 3),
                        "rss_kib": rss,
                        "metrics": metrics,
                        "database": database,
                    }
                )
                with stats_lock:
                    progress_requests = sum(sum(value.values()) for value in statuses.values())
                    progress_errors = sum(request_errors.values())
                print(json.dumps({"w9_capacity_elapsed": round(elapsed, 1), "requests": progress_requests, "errors": progress_errors}), flush=True)
                next_sample += 15.0
            if elapsed >= next_control and next_control < QUALIFICATION_SECONDS:
                key = f"capacity-job-{int(next_control)}"
                job = client.rpc("submit", "capacity-red-00000", "capacity durable payload", key, node="A")
                event = client.rpc("emit", "capacity-red-00000", f"capacity event {int(next_control)}", node="B")
                edit = client.rpc(
                    "edit",
                    {"tag": "issue", "value": "capacity-red-00000"},
                    {"rank": str(1 + (int(next_control / 120) % 2))},
                    node="A",
                )
                control.append(
                    {
                        "elapsed": round(elapsed, 3),
                        "job_status": job[0],
                        "event_status": event[0],
                        "edit_status": edit[0],
                    }
                )
                next_control += 120.0
            if not upload_started and elapsed >= min(300.0, options.duration * 0.20):
                upload_thread = threading.Thread(target=slow_upload, name="w9-slow-upload")
                upload_thread.start()
                upload_started = True
            if not restarted_node and elapsed >= min(600.0, options.duration * 0.36):
                before = time.monotonic()
                service.stop_node("B", kill=True)
                time.sleep(3)
                service.start_node("B")
                control.append({"action": "kill-restart-node-B", "elapsed": round(elapsed, 3), "recovery_seconds": round(time.monotonic() - before, 3)})
                restarted_node = True
            if not restarted_worker and elapsed >= min(900.0, options.duration * 0.55):
                before = time.monotonic()
                service.stop_worker("two", kill=True)
                time.sleep(3)
                service.start_worker("two")
                control.append({"action": "kill-restart-worker-two", "elapsed": round(elapsed, 3), "recovery_seconds": round(time.monotonic() - before, 3)})
                restarted_worker = True
            time.sleep(0.1)

        measured = time.monotonic() - started
        stop.set()
        for thread in threads[:CLIENTS]:
            thread.join(timeout=10)
        if upload_thread is not None:
            upload_thread.join(timeout=40)
        if socket_connection is not None and not socket_connection.closed:
            socket_connection.close()
        socket_thread.join(timeout=5)
        final_ready = {}
        for node in ["A", "B"]:
            try:
                final_ready[node] = service.request(node, "/_diamond/health/ready")[0]
            except OSError as error:
                final_ready[node] = type(error).__name__
        final_counts = service.sql_json(
            "SELECT (SELECT count(*) FROM "
            + issues
            + ") AS records,(SELECT count(*) FROM diamond_w7.jobs WHERE app="
            + common.quote(fixture.app_id())
            + ") AS jobs,(SELECT count(*) FROM diamond_w7.jobs WHERE app="
            + common.quote(fixture.app_id())
            + " AND state='done') AS jobs_done,(SELECT count(*) FROM diamond_w7.jobs WHERE app="
            + common.quote(fixture.app_id())
            + " AND state IN ('pending','running')) AS jobs_incomplete,(SELECT count(*) FROM diamond_w7.events WHERE app="
            + common.quote(fixture.app_id())
            + ") AS events"
        )[0]
        summaries = {}
        successful = 0
        total = 0
        all_success_latencies = []
        for kind in sorted(statuses):
            values = latencies[kind]
            successful += len(values)
            total += sum(statuses[kind].values())
            all_success_latencies.extend(values)
            summaries[kind] = {
                "statuses": dict(statuses[kind]),
                "successes": len(values),
                "p50_seconds": percentile(values, 0.50),
                "p95_seconds": percentile(values, 0.95),
                "p99_seconds": percentile(values, 0.99),
                "max_seconds": round(max(values), 6) if values else None,
            }
        success_ratio = successful / total if total else 0.0
        overall_p99 = percentile(all_success_latencies, 0.99)
        qualified = measured >= QUALIFICATION_SECONDS
        if records != RECORDS + 4:
            fail("record-corpus", records, f"exactly {RECORDS + 4} seeded records")
        if total < CLIENTS:
            fail("ordinary-client-traffic", total, "all 100 clients complete real requests")
        if success_ratio < SUCCESS_FLOOR:
            fail("success-floor", success_ratio, f">={SUCCESS_FLOOR} despite planned interruption")
        if overall_p99 is None or overall_p99 >= SUCCESS_P99_CEILING_SECONDS:
            fail("latency-ceiling", overall_p99, f"successful p99 < {SUCCESS_P99_CEILING_SECONDS}s")
        if final_ready != {"A": 200, "B": 200}:
            fail("post-interruption-readiness", final_ready, "both nodes ready after planned crash/restart")
        if final_counts["records"] != RECORDS + 4:
            fail("record-integrity", final_counts, "load and interruption preserve exact record count")
        if final_counts["events"] > 10_000:
            fail("event-retention", final_counts["events"], "retained events remain at or below 10,000")
        expected_jobs = sum(row.get("job_status") == 200 for row in control)
        expected_events = sum(row.get("event_status") == 200 for row in control)
        receiver_snapshot = receiver.snapshot()
        if final_counts["jobs_done"] != expected_jobs or final_counts["jobs_incomplete"] != 0 or receiver_snapshot["effects"] != expected_jobs:
            fail(
                "durable-queue-drained",
                {"counts": final_counts, "receiver": receiver_snapshot},
                "every accepted capacity job reaches done and one receiver-side deduplicated effect after worker restart",
            )
        delivered_events = [row for row in socket_events if isinstance(row.get("value"), dict) and "cursor" in row["value"]]
        socket_elapsed = socket_end.get("elapsed")
        if len(delivered_events) != expected_events:
            fail("socket-event-delivery", len(delivered_events), f"all {expected_events} control events arrive on the original socket")
        if not isinstance(socket_elapsed, (int, float)) or not (QUALIFICATION_SECONDS - 50 <= socket_elapsed <= measured + 2):
            fail(
                "socket-lifetime",
                socket_end,
                "the original authenticated socket closes only near its documented 30-minute lifetime and releases its permit",
            )
        if slow_transfer.get("put_status") != 200 or slow_transfer.get("finish_status") != 200 or slow_transfer.get("delete_status") != 200:
            fail("slow-stream", slow_transfer, "8 MiB deliberately slow upload publishes and deletes while ordinary traffic continues")
        if any(row.get("job_status") != 200 or row.get("event_status") != 200 or row.get("edit_status") != 200 for row in control if "job_status" in row):
            fail("control-mutations", control, "jobs, events and protected edits remain available throughout load")
        report = {
            "status": "PASS" if not failures and (qualified or options.duration < QUALIFICATION_SECONDS) else "FAIL",
            "qualification_complete": qualified,
            "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
            "driver_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "hardware": hardware(),
            "thresholds_fixed_before_run": {
                "clients": CLIENTS,
                "records": RECORDS,
                "duration_seconds": QUALIFICATION_SECONDS,
                "success_floor": SUCCESS_FLOOR,
                "successful_p99_ceiling_seconds": SUCCESS_P99_CEILING_SECONDS,
            },
            "observed": {
                "duration_seconds": round(measured, 3),
                "requests": total,
                "successful_requests": successful,
                "success_ratio": round(success_ratio, 6),
                "requests_per_second": round(total / measured, 3),
                "successful_p50_seconds": percentile(all_success_latencies, 0.50),
                "successful_p95_seconds": percentile(all_success_latencies, 0.95),
                "successful_p99_seconds": overall_p99,
                "request_errors": dict(request_errors),
                "by_operation": summaries,
                "maximum_rss_kib": max_rss,
                "database_records": records,
                "final_counts": final_counts,
                "final_readiness": final_ready,
                "slow_transfer": slow_transfer,
                "websocket": {"events": socket_events, "end": socket_end},
                "query_plan": plan,
                "control": control,
                "samples": samples,
                "receiver": receiver_snapshot,
            },
            "failures": failures,
            "scope": "two real app nodes, two real workers, isolated PostgreSQL and shared filesystem; 100 client threads use actual loopback HTTP; direct SQL only seeds and independently observes the fixed synthetic corpus",
        }
    except Exception as error:
        stop.set()
        report = {
            "status": "FAIL",
            "qualification_complete": False,
            "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
            "error": repr(error),
            "failures": failures,
            "hardware": hardware(),
        }
    finally:
        stop.set()
        for thread in threads:
            if thread is not threading.current_thread():
                thread.join(timeout=2)
        try:
            if socket_connection is not None and not socket_connection.closed:
                socket_connection.close()
        except OSError:
            pass
        service.close()
        receiver.close()
        report["shutdowns"] = service.shutdowns
        report["cleanup_errors"] = service.cleanup_errors
        if service.cleanup_errors:
            report["status"] = "FAIL"
        shutil.rmtree(root)
        report["temporary_root_removed"] = not root.exists()
        options.report.parent.mkdir(parents=True, exist_ok=True)
        options.report.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(
        json.dumps(
            {
                "status": report["status"],
                "qualification_complete": report.get("qualification_complete"),
                "requests": report.get("observed", {}).get("requests"),
                "duration_seconds": report.get("observed", {}).get("duration_seconds"),
                "error": report.get("error"),
            }
        )
    )
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
