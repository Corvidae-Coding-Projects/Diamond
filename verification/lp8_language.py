#!/usr/bin/env python3
"""Fresh LP8 deterministic corpus, artifact, hostile, and native-consumer qualification."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import random
import shutil
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
SEED = 20_261_027


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tree_hashes(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): sha256(path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, default=ROOT / "target/debug/diamond")
    parser.add_argument("--report", type=Path, default=ROOT / "verification/lp8/language.json")
    parser.add_argument("--valid-cases", type=int, default=96)
    parser.add_argument("--hostile-cases", type=int, default=64)
    args = parser.parse_args()
    binary = args.binary.resolve()
    rng = random.Random(SEED)
    observations: list[dict[str, object]] = []

    def observe(name: str, passed: bool, **detail: object) -> None:
        observations.append({"name": name, "pass": passed, **detail})

    def invoke(*arguments: object, timeout: int = 30) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(binary), *map(str, arguments)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )

    temp_parent = ROOT / ".diamond"
    temp_parent.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="lp8-language-", dir=temp_parent) as raw_directory:
        directory = Path(raw_directory)
        first = directory / "build-a"
        second = directory / "build-b"
        project = ROOT / "verification/lp1/browser/diamond.toml"
        result_a = invoke("build", project, "--out", first)
        result_b = invoke("build", project, "--out", second)
        hashes_a = tree_hashes(first) if first.exists() else {}
        hashes_b = tree_hashes(second) if second.exists() else {}
        required = {"contract.json", "index.html", "server.json"}
        observe(
            "A1-deterministic-full-project-build",
            result_a.returncode == result_b.returncode == 0
            and hashes_a == hashes_b
            and required.issubset(hashes_a)
            and any(path.endswith(".js") for path in hashes_a)
            and any(path.endswith(".map") for path in hashes_a),
            files=len(hashes_a),
            artifact_hashes=hashes_a,
            stderr=(result_a.stderr + result_b.stderr).strip(),
        )

        valid_failures: list[dict[str, object]] = []
        for index in range(args.valid_cases):
            kind = index % 4
            if kind == 0:
                left, right, multiplier = (rng.randint(-20_000, 20_000) for _ in range(3))
                expression = f"({left} + {right}) * {multiplier}"
                ty, expected = "Int", str((left + right) * multiplier)
            elif kind == 1:
                text = rng.choice(["雪😀é", "é", "plain", "λ and ☃"])
                expression = json.dumps(text, ensure_ascii=False)
                ty, expected = "Text", text
            elif kind == 2:
                left, right = rng.randint(-9999, 9999), rng.randint(-9999, 9999)
                expression = f"{left} <= {right}"
                ty, expected = "Bool", str(left <= right).lower()
            else:
                values = [rng.randint(-50, 50) for _ in range(rng.randint(0, 12))]
                expression = f"len({json.dumps(values)})"
                ty, expected = "Int", str(len(values))
            source = directory / f"valid-{index:03}.dia"
            source.write_text(f"fn main() -> {ty}:\n  {expression}\n", encoding="utf-8")
            result = invoke("run", source)
            if result.returncode != 0 or result.stdout != expected + "\n":
                valid_failures.append(
                    {
                        "case": index,
                        "source": source.read_text(encoding="utf-8"),
                        "expected": expected,
                        "stdout": result.stdout,
                        "stderr": result.stderr,
                    }
                )
        observe(
            "A5-fresh-valid-corpus-independent-oracle",
            not valid_failures,
            seed=SEED,
            cases=args.valid_cases,
            failures=valid_failures[:5],
        )

        hostile_templates = [
            "fn main() -> Int:\n  9223372036854775808\n",
            'fn main() -> Int:\n  "wrong"\n',
            "fn main() -> Int:\n  missing_value\n",
            'fn main() -> Html:\n  "<script>forged()</script>"\n',
            'type UserId = Id<"user">\nfn make(value: Text) -> UserId:\n  UserId(value)\nfn main() -> Int:\n  1\n',
            "fn main() -> Int:\n  (1 +\n",
            "fn main() -> Int:\n\t1\n",
            "fn main() -> Int:\n  match true:\n    true => 1\n",
        ]
        hostile_failures: list[dict[str, object]] = []
        for index in range(args.hostile_cases):
            source = directory / f"hostile-{index:03}.dia"
            source.write_text(hostile_templates[index % len(hostile_templates)], encoding="utf-8")
            result = invoke("check", source)
            if result.returncode == 0 or "panicked at" in result.stderr or "error[D" not in result.stderr:
                hostile_failures.append(
                    {
                        "case": index,
                        "source": source.read_text(encoding="utf-8"),
                        "stdout": result.stdout,
                        "stderr": result.stderr,
                    }
                )
        recovery = directory / "recovery.dia"
        recovery.write_text("fn main() -> Int:\n  40 + 2\n", encoding="utf-8")
        recovered = invoke("run", recovery)
        observe(
            "A5-hostile-rejection-and-valid-recovery",
            not hostile_failures and recovered.returncode == 0 and recovered.stdout == "42\n",
            cases=args.hostile_cases,
            failures=hostile_failures[:5],
            recovery={"exit": recovered.returncode, "stdout": recovered.stdout, "stderr": recovered.stderr},
        )

        libraries = sorted((ROOT / "target/debug/deps").glob("libdiamond-*.rlib"), key=lambda path: path.stat().st_mtime_ns, reverse=True)
        executable = directory / "native-consumer"
        if libraries:
            compiled = subprocess.run(
                [
                    "rustc",
                    "--edition",
                    "2024",
                    str(ROOT / "verification/lp8/native_consumer.rs"),
                    "--crate-name",
                    "diamond_lp8_native",
                    "--extern",
                    f"diamond={libraries[0]}",
                    "-L",
                    f"dependency={ROOT / 'target/debug/deps'}",
                    "-o",
                    str(executable),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                timeout=60,
                check=False,
            )
        else:
            compiled = subprocess.CompletedProcess([], 1, "", "no Diamond rlib")
        executed = (
            subprocess.run([str(executable)], text=True, capture_output=True, timeout=30, check=False)
            if compiled.returncode == 0
            else subprocess.CompletedProcess([], 1, "", "compile failed")
        )
        observe(
            "A6-external-native-consumer",
            compiled.returncode == 0
            and executed.returncode == 0
            and executed.stdout == "checked=42 formatted=stable diagnostic=D110:2:3\n",
            library_sha256=sha256(libraries[0]) if libraries else None,
            executable_sha256=sha256(executable) if executable.exists() else None,
            compile_stderr=compiled.stderr,
            run_stdout=executed.stdout,
            run_stderr=executed.stderr,
        )

    source_files = [ROOT / "Cargo.toml", ROOT / "Cargo.lock", *sorted((ROOT / "src").rglob("*"))]
    source_hashes = {
        str(path.relative_to(ROOT)): sha256(path)
        for path in source_files
        if path.is_file()
    }
    report = {
        "status": "PASS" if all(row["pass"] for row in observations) else "FAIL",
        "seed": SEED,
        "binary_sha256": sha256(binary),
        "driver_sha256": sha256(Path(__file__)),
        "native_source_sha256": sha256(ROOT / "verification/lp8/native_consumer.rs"),
        "scenarios_sha256": sha256(ROOT / "verification/lp8/SCENARIOS.md"),
        "source_hashes": source_hashes,
        "observations": observations,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return int(report["status"] != "PASS")


if __name__ == "__main__":
    raise SystemExit(main())
