#!/usr/bin/env python3
"""Run process-local realtime jumps against the real public SessionStore API.

The duration contract expects a live 80ms session to survive a forward realtime
jump, and an expired session to remain expired after a backward realtime jump.
The host clock is never changed. A test-only LD_PRELOAD clock_gettime wrapper
adjusts only the consumer process's CLOCK_REALTIME, after its exact marker exists.
SystemTime/Instant observations must prove the injection was active; otherwise
the result is SKIP, never PASS. --observe retains the raw baseline mismatch in its
JSON output while returning success for observation collection.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import resource
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def fingerprint(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def limits() -> None:
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_CPU, (3, 3))
    resource.setrlimit(resource.RLIMIT_AS, (512 * 1024**2, 512 * 1024**2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library", type=Path, default=ROOT / "target/debug/libdiamond.rlib")
    parser.add_argument("--observe", action="store_true")
    arguments = parser.parse_args()
    if sys.platform != "linux" or shutil.which("cc") is None:
        print(json.dumps({"status": "SKIP", "reason": "Linux and an installed C compiler are required"}))
        return
    library = arguments.library.resolve()
    c_source = ROOT / "verification/session_clock.c"
    rust_source = ROOT / "verification/session_clock.rs"
    directory = Path(tempfile.mkdtemp(prefix="diamond-session-clock-"))
    try:
        shim, consumer = directory / "clock.so", directory / "session_clock"
        subprocess.run(["cc", "-shared", "-fPIC", "-Wall", "-Wextra", "-Werror", str(c_source), "-o", str(shim)],
                       capture_output=True, text=True, check=True, timeout=20)
        subprocess.run(["rustc", "--edition=2024", "-Dwarnings", str(rust_source),
                        "-L", f"dependency={library.parent / 'deps'}", "--extern", f"diamond={library}", "-o", str(consumer)],
                       capture_output=True, text=True, check=True, timeout=30)
        base_env = {key: value for key, value in os.environ.items()
                    if key != "LD_PRELOAD" and not key.startswith("DIAMOND_CLOCK_")}
        observations = {}
        for mode, offset in [("forward", 3600), ("backward", -3600)]:
            marker = directory / f"{mode}.armed"
            environment = {**base_env, "LD_PRELOAD": str(shim), "DIAMOND_CLOCK_MARKER": str(marker),
                           "DIAMOND_CLOCK_OFFSET": str(offset)}
            result = subprocess.run([str(consumer), mode, str(marker)], cwd=ROOT, env=environment,
                                    capture_output=True, text=True, timeout=5, preexec_fn=limits)
            assert result.returncode == 0, (result.returncode, result.stdout, result.stderr)
            observed = json.loads(result.stdout)
            expected_valid = mode == "forward"
            active = abs(observed["wall_elapsed_seconds"] - offset - observed["monotonic_elapsed_seconds"]) < 0.2
            timing = (observed["monotonic_elapsed_seconds"] < 0.06 if expected_valid
                      else 0.12 <= observed["monotonic_elapsed_seconds"] < 1.0)
            observed.update(fault_active=active, valid_timing=timing,
                            expected_valid=expected_valid,
                            passed=active and timing and observed["issued_valid"]
                            and observed["before_fault_valid"] == expected_valid
                            and observed["after_fault_valid"] == expected_valid
                            and observed["after_fault_csrf"] == expected_valid)
            observations[mode] = observed
        if not all(observed["fault_active"] and observed["valid_timing"] for observed in observations.values()):
            status = "SKIP"
        else:
            status = "PASS" if all(observed["passed"] for observed in observations.values()) else "FAIL"
        receipt = {"status": status, "observations": observations,
                   "library_sha256": fingerprint(library), "rust_source_sha256": fingerprint(rust_source),
                   "c_source_sha256": fingerprint(c_source), "consumer_sha256": fingerprint(consumer),
                   "shim_sha256": fingerprint(shim), "driver_sha256": fingerprint(Path(__file__)),
                   "child_limits": {"address_space_bytes": 512 * 1024**2, "cpu_seconds": 3, "wall_seconds": 5, "core_bytes": 0}}
        print(json.dumps(receipt, indent=2))
    except Exception:
        print(f"Clock probe inputs retained at {directory}", file=sys.stderr)
        raise
    else:
        shutil.rmtree(directory)
        if status == "FAIL" and not arguments.observe:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
