#!/usr/bin/env python3
"""Measure fresh-process compiler/runtime latency and peak resident memory."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import statistics
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BINARY = ROOT / "target/debug/diamond"
ITERATIONS = 40


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)]


def repeated(name: str, arguments: list[str], expected: str | None = None) -> dict[str, object]:
    durations = []
    output_hashes = set()
    for _ in range(ITERATIONS):
        started = time.monotonic()
        completed = subprocess.run(
            [str(BINARY), *arguments],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=5,
        )
        durations.append(time.monotonic() - started)
        if completed.returncode != 0:
            raise RuntimeError(f"{name} failed: {completed.stderr}")
        if expected is not None and completed.stdout != expected:
            raise RuntimeError(f"{name} output mismatch: {completed.stdout!r}")
        output_hashes.add(hashlib.sha256((completed.stdout + completed.stderr).encode()).hexdigest())
    return {
        "iterations": ITERATIONS,
        "minimum_seconds": round(min(durations), 6),
        "median_seconds": round(statistics.median(durations), 6),
        "p95_seconds": round(percentile(durations, 0.95), 6),
        "maximum_seconds": round(max(durations), 6),
        "output_hashes": sorted(output_hashes),
    }


def measured_process(arguments: list[str], time_file: Path) -> dict[str, object]:
    started = time.monotonic()
    completed = subprocess.run(
        ["/usr/bin/time", "-v", "-o", str(time_file), str(BINARY), *arguments],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=10,
    )
    metrics: dict[str, str] = {}
    for line in time_file.read_text().splitlines():
        if ": " in line:
            key, value = line.strip().split(": ", 1)
            metrics[key] = value
    return {
        "arguments": arguments,
        "exit": completed.returncode,
        "wall_seconds": round(time.monotonic() - started, 6),
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "maximum_resident_kib": int(metrics["Maximum resident set size (kbytes)"]),
        "user_seconds": metrics.get("User time (seconds)"),
        "system_seconds": metrics.get("System time (seconds)"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--report",
        type=Path,
        default=ROOT / "verification/performance_results.json",
    )
    options = parser.parse_args()
    failures: list[str] = []
    observations: dict[str, object] = {}
    started_utc = datetime.now(timezone.utc).isoformat()

    try:
        observations["check_hello"] = repeated("check hello", ["check", "examples/hello.dia"])
        observations["check_message_board"] = repeated(
            "check message board", ["check", "examples/message_board.dia"]
        )
        observations["run_hello"] = repeated(
            "run hello", ["run", "examples/hello.dia"], "Hello, Diamond!\n"
        )
        for name in ["check_hello", "check_message_board", "run_hello"]:
            p95 = float(observations[name]["p95_seconds"])  # type: ignore[index]
            if p95 >= 1.0:
                failures.append(f"{name} p95 {p95:.6f}s reached the 1.0s budget")

        with tempfile.TemporaryDirectory(prefix="diamond-cp6-performance-") as temporary:
            directory = Path(temporary)
            large_source = directory / "large.dia"
            aliases = "".join(f"type Alias{index} = Int\n" for index in range(3_999))
            large_source.write_text(aliases + "fn main() -> Int:\n  42\n")
            message_time = directory / "message.time"
            large_time = directory / "large.time"
            observations["message_board_process"] = measured_process(
                ["check", "examples/message_board.dia"], message_time
            )
            observations["large_process"] = measured_process(
                ["check", str(large_source)], large_time
            )
            observations["large_source"] = {
                "bytes": large_source.stat().st_size,
                "declarations": 4_000,
                "sha256": digest(large_source),
            }

        for name in ["message_board_process", "large_process"]:
            process = observations[name]
            if process["exit"] != 0:  # type: ignore[index]
                failures.append(f"{name} failed: {process['stderr']}")  # type: ignore[index]
            resident = int(process["maximum_resident_kib"])  # type: ignore[index]
            if resident >= 128 * 1024:
                failures.append(f"{name} resident memory {resident} KiB reached 128 MiB")
        if float(observations["large_process"]["wall_seconds"]) >= 5.0:  # type: ignore[index]
            failures.append("large source check reached the five-second budget")
    except (KeyError, OSError, RuntimeError, subprocess.SubprocessError) as error:
        failures.append(str(error))

    report = {
        "schema": 1,
        "started_utc": started_utc,
        "finished_utc": datetime.now(timezone.utc).isoformat(),
        "host": platform.platform(),
        "python": platform.python_version(),
        "binary_sha256": digest(BINARY),
        "thresholds": {
            "fresh_process_p95_seconds": 1.0,
            "large_check_seconds": 5.0,
            "peak_resident_mib": 128,
        },
        "observations": observations,
        "failures": failures,
        "result": "PASS" if not failures else "FAIL",
    }
    options.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"status": report["result"], "observations": observations, "failures": failures}))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
