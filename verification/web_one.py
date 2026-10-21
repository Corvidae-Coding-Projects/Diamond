#!/usr/bin/env python3
"""Exercise W1 through real CLI, HTTP, PostgreSQL, builds, and retained replays.

Browser interaction is performed separately through the real browser UI. The
--browser-fixture option generates a reproducible semantics/atomicity page and
its independent expected values for that interaction.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import http.client
import json
import os
from pathlib import Path
import random
import re
import shutil
import signal
import socket
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
BINARY = ROOT / "target/debug/diamond"
EXAMPLE = ROOT / "examples/foundations"
SEED = 0xD1A07101


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(args: list[str], env: dict[str, str] | None = None, timeout: int = 15) -> dict:
    result = subprocess.run([str(BINARY), *args], cwd=ROOT, env=os.environ | (env or {}),
                            capture_output=True, text=True, timeout=timeout)
    return {"args": args, "exit": result.returncode, "stdout": result.stdout, "stderr": result.stderr}


def project(root: Path) -> Path:
    shutil.copytree(EXAMPLE, root)
    return root / "diamond.toml"


def browser_fixture(destination: Path) -> dict:
    project(destination)
    rng = random.Random(SEED)
    expected = {}
    states = []
    for index in range(24):
        a, b, c = (rng.randint(-50000, 50000) for _ in range(3))
        expression = f"({a} * {b}) + {c}"
        expected[f"case{index}"] = str(a * b + c)
        states.append(f"  state case{index}: Int = {expression}")
    special = [
        ("large", "Int", "9007199254740993 + 11", "9007199254741004"),
        ("negative_division", "Int", "-19 / 4", "-4"),
        ("unicode_length", "Int", 'len("café 雪😀")', "7"),
        ("unicode_order", "Bool", '"😀" > "\ue000"', "true"),
        ("lazy_and", "Bool", "false and (1 / 0 == 0)", "false"),
        ("lazy_or", "Bool", "true or (1 / 0 == 0)", "true"),
        ("class", "Int", "bump(7)", "10"),
        ("__proto__", "Text", '"<script>literal & text</script>"', "<script>literal & text</script>"),
    ]
    for name, ty, expression, answer in special:
        expected[name] = answer
        states.append(f"  state {name}: {ty} = {expression}")
    states += ["  state count: Int = 2", "  state ceiling: Int = 9223372036854775807"]
    html = "\n".join(f'      <p id="{name}">{{{name}}}</p>' for name in expected)
    source = "export component Counter:\n" + "\n".join(states) + "\n\n" + '''  on increment:
    count = bump(count)
  on overflow:
    count = count + 99
    ceiling = ceiling + 1
  html:
    <main>
      <h1>W1 scalar semantics</h1>
      <p>Count: <strong id="count">{count}</strong></p>
      <button id="increment" type="button" on-click={increment}>Add three</button>
      <button id="overflow" type="button" on-click={overflow}>Try overflow</button>
''' + html + "\n    </main>\n"
    (destination / "counter.dia").write_text(source)
    # Parameter names legal in Diamond must not become JavaScript reserved words.
    (destination / "shared.dia").write_text('export fn bump(class: Int) -> Int:\n  class + 3\n\nexport fn welcome() -> Text:\n  "A component written in Diamond"\n')
    expected["count"] = "2"
    result = {"seed": SEED, "expected_initial": expected,
              "event_sequence": [{"click": "Try overflow", "count": "2", "error_visible": True},
                                 {"click": "Add three", "count": "5", "error_visible": False}]}
    (destination / "expected.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    return result


def http_campaign(root: Path) -> dict:
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    log_path = root / "http.log"
    with log_path.open("w") as log:
        process = subprocess.Popen([str(BINARY), "serve", str(EXAMPLE / "diamond.toml"),
                                    "--listen", f"127.0.0.1:{port}"], stdout=log, stderr=log,
                                   cwd=ROOT, start_new_session=True)
        requests = []
        def request(path="/", method="GET", headers=None):
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            try:
                connection.request(method, path, headers=headers or {})
                response = connection.getresponse()
                body = response.read()
                row = {"path": path, "method": method, "status": response.status,
                       "bytes": len(body), "headers": dict(response.getheaders())}
                return row, body
            finally:
                connection.close()
        try:
            for _ in range(100):
                try:
                    row, page = request()
                    assert row["status"] == 200
                    break
                except OSError:
                    assert process.poll() is None, log_path.read_text()
                    time.sleep(.03)
            else:
                raise AssertionError("project server did not become ready")
            requests.append(row)
            asset = re.search(rb'src="([^"]+)"', page).group(1).decode()
            row, js = request(asset)
            assert row["status"] == 200 and b"SERVER_ONLY_CANARY" not in js
            assert b"createTextNode" in js and b"innerHTML" not in js
            requests.append(row)
            for path, method, headers, expected in [
                ("/server.json", "GET", {}, 404), ("/contract.json", "GET", {}, 404),
                ("/", "POST", {}, 405), ("/?query=1", "GET", {}, 400),
                ("/", "GET", {"Host": "evil.invalid"}, 400),
                ("/", "GET", {"X-Forwarded-Host": "evil.invalid"}, 400),
                ("/", "GET", {"Origin": "http://evil.invalid"}, 403),
                ("/", "HEAD", {}, 200),
            ]:
                row, body = request(path, method, headers)
                assert row["status"] == expected, row
                if method == "HEAD": assert not body
                requests.append(row)
            slow = socket.create_connection(("127.0.0.1", port), timeout=5)
            try:
                slow.sendall(b"GET / HTTP/1.1\r\nHost:")
                started = time.monotonic()
                with ThreadPoolExecutor(max_workers=12) as clients:
                    rows = list(clients.map(lambda _: request()[0], range(48)))
                elapsed = time.monotonic() - started
                assert all(row["status"] == 200 for row in rows), rows
                assert elapsed < 5, elapsed
                requests += rows
            finally:
                slow.close()
            return {"requests": len(requests), "concurrent_requests": 48,
                    "seconds_with_slow_connection": round(elapsed, 4), "observations": requests}
        finally:
            process.send_signal(signal.SIGINT)
            try: process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait()


def postgres_campaign(root: Path) -> dict:
    base = root / "postgres"
    base.mkdir(); sockets = base / "socket"; sockets.mkdir(mode=0o700)
    data = base / "data"
    subprocess.run(["initdb", "-D", str(data), "-A", "trust", "--no-locale", "-E", "UTF8", "-U", "diamond_w1"], check=True, capture_output=True)
    port = "55439"
    options = f'-k {sockets} -h "" -p {port} -c max_connections=12'
    subprocess.run(["pg_ctl", "-D", str(data), "-l", str(base / "postgres.log"), "-o", options, "-w", "start"], check=True, capture_output=True, timeout=20)
    configuration = f"host={sockets} port={port} user=diamond_w1 dbname=postgres"
    env = {"DIAMOND_W1_TEST_POSTGRES": configuration}
    observations = []
    try:
        version = subprocess.check_output(["psql", configuration, "-Atc", "SELECT current_setting('server_version')"], text=True).strip()
        result = run(["run", str(EXAMPLE / "diamond.toml"), "--postgres-env", "DIAMOND_W1_TEST_POSTGRES"], env)
        assert result["exit"] == 0 and result["stdout"].strip() == "A component written in Diamond / PostgreSQL " + version, result
        observations.append(result)
        fixture = root / "echo"
        manifest = project(fixture)
        for value in ["", "ordinary", "café 雪😀", "a'b\"c\\d", "x'); DROP TABLE accounts; --", "<script>&literal</script>"]:
            (fixture / "server.dia").write_text("export fn main() -> Text:\n  host.postgres_echo(" + json.dumps(value, ensure_ascii=False) + ")\n")
            result = run(["run", str(manifest), "--postgres-env", "DIAMOND_W1_TEST_POSTGRES"], env)
            assert result["exit"] == 0 and result["stdout"] == value + "\n", result
            observations.append(result)
        denied = run(["run", str(manifest)])
        assert denied["exit"] != 0 and "access denied" in denied["stderr"]
        observations.append(denied)
        tcp_env = {"DIAMOND_W1_TEST_POSTGRES": "host=127.0.0.1 port=1 user=synthetic password=DO_NOT_DISCLOSE_W1"}
        denied = run(["run", str(manifest), "--postgres-env", "DIAMOND_W1_TEST_POSTGRES"], tcp_env)
        assert denied["exit"] != 0 and "Unix socket" in denied["stderr"] and "DO_NOT_DISCLOSE_W1" not in denied["stderr"]
        observations.append(denied)
        counts = subprocess.check_output(["psql", configuration, "-Atc", "SELECT count(*) FROM pg_tables WHERE schemaname='public'"], text=True).strip()
        assert counts == "0", counts
        postgres_pid = int((data / "postmaster.pid").read_text().splitlines()[0])
        os.kill(postgres_pid, signal.SIGSTOP)
        started = time.monotonic()
        try:
            deadline = run(["run", str(manifest), "--postgres-env", "DIAMOND_W1_TEST_POSTGRES"], env, timeout=8)
        finally:
            os.kill(postgres_pid, signal.SIGCONT)
        deadline_seconds = time.monotonic() - started
        assert deadline["exit"] != 0 and "deadline" in deadline["stderr"] and 4 <= deadline_seconds < 7, deadline
        observations.append(deadline)
        recovery = run(["run", str(EXAMPLE / "diamond.toml"), "--postgres-env", "DIAMOND_W1_TEST_POSTGRES"], env)
        assert recovery["exit"] == 0, recovery
        observations.append(recovery)
        native = subprocess.run(["cargo", "run", "--locked", "--example", "project_embedding"], cwd=ROOT,
                                env=os.environ | {"DIAMOND_EXAMPLE_POSTGRES": configuration}, capture_output=True, text=True, timeout=120)
        assert native.returncode == 0, native.stderr
        return {"version": version, "cli_invocations": len(observations), "observations": observations,
                "independent_public_table_count": 0, "stalled_connection_seconds": round(deadline_seconds,3), "native_consumer": native.stdout.strip()}
    finally:
        subprocess.run(["pg_ctl", "-D", str(data), "-m", "fast", "-w", "stop"], check=True, capture_output=True, timeout=20)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=ROOT / "verification/web_one_results.json")
    parser.add_argument("--browser-fixture", type=Path)
    args = parser.parse_args()
    if args.browser_fixture:
        result = browser_fixture(args.browser_fixture.resolve())
        print(json.dumps({"fixture": str(args.browser_fixture), "cases": len(result["expected_initial"]), "seed": SEED}))
        return
    report = {"schema": 1, "seed": SEED, "binary_sha256": digest(BINARY), "status": "FAIL"}
    root = Path(tempfile.mkdtemp(prefix="diamond-w1-campaign-"))
    try:
        first, second = root / "build-a", root / "build-b"
        for output in [first, second]:
            observed = run(["build", str(EXAMPLE / "diamond.toml"), "--out", str(output)])
            assert observed["exit"] == 0, observed
        files = lambda directory: {str(p.relative_to(directory)): digest(p) for p in directory.rglob("*") if p.is_file()}
        assert files(first) == files(second)
        assert run(["check", str(first)])["exit"] == 0
        assert run(["run", str(first)])["exit"] != 0
        report["deterministic_artifacts"] = files(first)
        report["http"] = http_campaign(root)
        report["postgres"] = postgres_campaign(root)
        report["status"] = "PASS"
    except Exception as error:
        report["error"] = str(error)
        report["failure_directory"] = str(root)
        raise
    finally:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
        if report["status"] == "PASS": shutil.rmtree(root)
    print(json.dumps({"status": report["status"], "report": str(args.report), "binary_sha256": report["binary_sha256"]}))


if __name__ == "__main__":
    main()
