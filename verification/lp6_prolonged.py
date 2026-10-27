#!/usr/bin/env python3
"""Bounded LP6 prolonged request/restart/resource-ownership campaign."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
W78 = ROOT / "verification/w7_w8_independent"
sys.path.insert(0, str(W78))
from fixture import PASSWORD, identifier, write  # noqa: E402
from receiver import Receiver  # noqa: E402
from service import Client, ENV_NAME, Service, sha256  # noqa: E402

spec = importlib.util.spec_from_file_location(
    "_lp6_common", ROOT / "verification/w5_independent/common.py"
)
common = importlib.util.module_from_spec(spec)
spec.loader.exec_module(common)
nominal, seed = common.nominal, common.seed


def process_sample(pid: int) -> dict[str, int | None]:
    result: dict[str, int | None] = {"pid": pid, "rss_kib": None, "fds": None, "tasks": None}
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                result["rss_kib"] = int(line.split()[1])
        result["fds"] = len(list(Path(f"/proc/{pid}/fd").iterdir()))
        result["tasks"] = len(list(Path(f"/proc/{pid}/task").iterdir()))
    except (FileNotFoundError, ProcessLookupError, PermissionError, ValueError):
        pass
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, default=ROOT / "target/debug/diamond")
    parser.add_argument("--report", type=Path, default=ROOT / "verification/lp6/prolonged.json")
    options = parser.parse_args()
    root = Path(tempfile.mkdtemp(prefix="diamond-lp6-prolonged-"))
    binary = root / "diamond"
    shutil.copy2(options.binary, binary)
    project = write(root / "project")
    service = Service(root, binary, project)
    receiver = None
    observations: list[dict[str, object]] = []
    samples: list[dict[str, object]] = []
    error = None
    stop_sampler = None
    monitor = None

    def record(name: str, passed: bool, **detail: object) -> None:
        observations.append({"name": name, "pass": bool(passed), **detail})

    def sample(label: str) -> dict[str, object]:
        nodes = {name: process_sample(process.pid) for name, process in service.nodes.items()}
        database_connections = int(
            service.sql(
                "SELECT count(*) FROM pg_catalog.pg_stat_activity "
                "WHERE pid<>pg_catalog.pg_backend_pid() AND datname=current_database()"
            )
        )
        value: dict[str, object] = {
            "label": label,
            "elapsed": time.monotonic(),
            "nodes": nodes,
            "database_connections": database_connections,
        }
        samples.append(value)
        return value

    try:
        receiver = Receiver()
        service.runtime["http"] = {
            "inventory": {
                "origin": receiver.origin,
                "network": "loopback",
                "paths": ["/v1/"],
                "methods": ["POST"],
                "timeout_ms": 5000,
            }
        }
        service.postgres()
        contract, _build = service.build()
        service.cli("project-init", project, "--postgres-env", ENV_NAME)
        service.files_cli(initialize=True)
        service.cli(
            "project-admin-add",
            project,
            "--postgres-env",
            ENV_NAME,
            "--user",
            "alice",
            "--email",
            "alice@lp6.invalid",
            "--password-stdin",
            input=PASSWORD + "\n",
        )
        seed(service)
        service.start_node("A")
        service.start_node("B")
        clients = []
        for _ in range(4):
            client = Client(service, contract["rpc_id"])
            client.login("alice", PASSWORD)
            clients.append(client)
        parent = identifier("issue-red-one")
        # Warm the lazy per-node connection pools before defining the settled
        # baseline. Pool-owned idle connections are bounded retained resources,
        # not leaks from the measured workload.
        with ThreadPoolExecutor(max_workers=8) as pool:
            warm = list(
                pool.map(
                    lambda index: clients[index % len(clients)].rpc(
                        "read", nominal("issue", parent), node="A" if index % 2 == 0 else "B"
                    )[0],
                    range(16),
                )
            )
        if any(status != 200 for status in warm):
            raise AssertionError(("warmup", warm))
        time.sleep(0.2)
        baseline = sample("settled-before")

        stop_sampler = threading.Event()

        def sampler() -> None:
            index = 0
            while not stop_sampler.wait(0.05):
                try:
                    sample(f"active-{index}")
                except Exception as failure:  # retained; never treated as product success
                    samples.append({"label": f"sample-error-{index}", "error": repr(failure)})
                index += 1

        monitor = threading.Thread(target=sampler, name="lp6-resource-sampler")
        monitor.start()

        def phase(offset: int) -> list[int]:
            def worker(index: int) -> list[int]:
                statuses = []
                client = clients[index]
                for sequence in range(125):
                    node = "A" if (offset + index + sequence) % 2 == 0 else "B"
                    if sequence % 5 == 0:
                        statuses.append(client.rpc("read", nominal("issue", parent), node=node)[0])
                    else:
                        statuses.append(
                            service.request(
                                node, "/", headers={"Cookie": client.cookie or ""}
                            )[0]
                        )
                return statuses

            with ThreadPoolExecutor(max_workers=4) as pool:
                return [status for values in pool.map(worker, range(4)) for status in values]

        first = phase(0)
        old_b = service.nodes["B"].pid
        service.stop_node("B")
        released = not Path(f"/proc/{old_b}").exists()
        service.start_node("B")
        restarted = clients[0].rpc("read", nominal("issue", parent), node="B")[0]
        second = phase(500)
        stop_sampler.set()
        monitor.join(timeout=5)
        time.sleep(0.4)
        settled = sample("settled-after")
        final_a = clients[0].rpc("read", nominal("issue", parent), node="A")[0]
        final_b = clients[0].rpc("read", nominal("issue", parent), node="B")[0]

        statuses = first + second
        record(
            "one-thousand-real-http-and-authorized-operations",
            len(statuses) == 1000 and all(status == 200 for status in statuses),
            count=len(statuses),
            status_counts={str(status): statuses.count(status) for status in sorted(set(statuses))},
        )
        record(
            "graceful-node-restart-releases-old-process",
            released and restarted == 200,
            old_pid=old_b,
            old_pid_released=released,
            restart_status=restarted,
        )
        record(
            "valid-follow-up-after-prolonged-run",
            final_a == final_b == 200,
            statuses=[final_a, final_b],
        )

        active = [sample for sample in samples if str(sample.get("label", "")).startswith("active-")]
        resource_details = {}
        resource_ok = bool(active)
        for node in ["A", "B"]:
            before = baseline["nodes"][node]
            after = settled["nodes"][node]
            peaks = [
                point["nodes"][node]
                for point in active
                if node in point.get("nodes", {}) and point["nodes"][node].get("rss_kib") is not None
            ]
            peak_rss = max((point["rss_kib"] for point in peaks), default=None)
            resource_details[node] = {"before": before, "after": after, "peak_rss_kib": peak_rss}
            if node == "A":
                resource_ok &= (
                    before["rss_kib"] is not None
                    and after["rss_kib"] is not None
                    and after["rss_kib"] <= max(before["rss_kib"] + 32_768, peak_rss or 0)
                    and after["fds"] <= before["fds"] + 4
                    and after["tasks"] <= before["tasks"] + 2
                )
            else:
                # Node B is a fresh process after the deliberate restart.
                resource_ok &= after["fds"] is not None and after["fds"] <= 44 and after["tasks"] <= 16
        resource_ok &= settled["database_connections"] <= baseline["database_connections"] + 4
        record(
            "resources-settle-within-declared-envelope",
            resource_ok,
            nodes=resource_details,
            database_before=baseline["database_connections"],
            database_after=settled["database_connections"],
            sample_count=len(active),
            envelope={"rss_growth_kib": 32768, "fd_growth": 4, "task_growth": 2},
        )
    except Exception as failure:
        error = repr(failure)
    finally:
        if stop_sampler is not None:
            stop_sampler.set()
        if monitor is not None:
            monitor.join(timeout=5)
        service.close()
        if receiver is not None:
            receiver.close()
        shutil.rmtree(root)

    status = (
        "PASS"
        if error is None and not service.cleanup_errors and all(item["pass"] for item in observations)
        else "FAIL"
    )
    report = {
        "status": status,
        "binary_sha256": sha256(options.binary),
        "driver_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "error": error,
        "observations": observations,
        "samples": samples,
        "shutdowns": service.shutdowns,
        "cleanup_errors": service.cleanup_errors,
        "measurement_boundary": "Diamond web PIDs only; PostgreSQL is sampled separately as a dependency",
    }
    options.report.parent.mkdir(parents=True, exist_ok=True)
    options.report.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"status": status, "observations": len(observations), "error": error}))
    return int(status != "PASS")


if __name__ == "__main__":
    raise SystemExit(main())
