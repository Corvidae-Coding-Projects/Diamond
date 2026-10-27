#!/usr/bin/env python3
"""Exercise LP6 render/work allocation boundaries through the real CLI artifacts."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, default=ROOT / "target/debug/diamond")
    parser.add_argument("--report", type=Path, default=ROOT / "verification/lp6/limits.json")
    args = parser.parse_args()
    binary = args.binary.resolve()
    observations: list[dict[str, object]] = []

    def record(name: str, passed: bool, **detail: object) -> None:
        observations.append({"name": name, "pass": bool(passed), **detail})

    def invoke(*values: object) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(binary), *map(str, values)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=45,
        )

    def project(root: Path, outer: int, inner: int) -> Path:
        root.mkdir()
        (root / "diamond.toml").write_text(
            "schema_version = 1\n"
            'name = "LP6RenderBudget"\n'
            'server = "server"\n'
            'browser = "ui"\n'
            'component = "App"\n'
            "provides = []\n\n"
            "[modules.server]\n"
            'path = "server.dia"\n'
            'target = "server"\n\n'
            "[modules.ui]\n"
            'path = "ui.dia"\n'
            'target = "browser"\n'
        )
        (root / "server.dia").write_text('export fn main() -> Text:\n  "LP6"\n')
        outer_values = ", ".join(map(str, range(outer)))
        inner_values = ", ".join(map(str, range(inner)))
        (root / "ui.dia").write_text(
            "component Leaf:\n"
            "  html:\n"
            "    <span></span>\n\n"
            "component Branch:\n"
            f"  state rows: List<Int> = [{inner_values}]\n"
            "  html:\n"
            '    <each items={rows} as="row" key={row}><use component="Leaf"></use></each>\n\n'
            "export component App:\n"
            f"  state rows: List<Int> = [{outer_values}]\n"
            "  html:\n"
            '    <each items={rows} as="row" key={row}><use component="Branch"></use></each>\n'
        )
        return root / "diamond.toml"

    with tempfile.TemporaryDirectory(prefix="diamond-lp6-limits-") as temporary:
        root = Path(temporary)
        small = project(root / "small", 10, 2)
        result = invoke("build", small, "--out", root / "small-build")
        record("render-components-below-capacity", result.returncode == 0, stderr=result.stderr)

        # 1 root + 250 Branch + 1,000 Leaf instances = 1,251 components while
        # native node work remains below 8,192. The shared 1,024 component
        # budget must reject this before emitting a browser artifact.
        over = project(root / "over", 250, 4)
        result = invoke("build", over, "--out", root / "over-build")
        record(
            "render-component-capacity-1024",
            result.returncode != 0 and "component capacity" in result.stderr.lower(),
            expected="component capacity",
            returncode=result.returncode,
            stderr=result.stderr.strip(),
            emitted=(root / "over-build" / "index.html").exists(),
        )

        runtimes = [
            path
            for path in (root / "small-build" / "assets").glob("*.js")
            if not path.name.endswith(".js.map")
        ]
        runtime = runtimes[0] if len(runtimes) == 1 else None
        if runtime is not None:
            source = runtime.read_text()
            # The browser must project output size before replace/join allocate
            # it; a post-allocation __text check is not sufficient for LP6.
            record(
                "browser-text-expansion-preflight",
                "function __textReplace(" in source
                and "function __joinText(" in source
                and "case'text_replace':return __textReplace" in source
                and "case'join_text':return __joinText" in source,
                runtime_sha256=sha256(runtime),
            )
            record(
                "browser-remote-materialization-accounting",
                "return d===0?__check(__validate(result,t,d)):__validate(result,t,d);"
                in source,
                expected="root remote values count toward the per-event managed-value budget",
                runtime_sha256=sha256(runtime),
            )
        else:
            record(
                "browser-text-expansion-preflight",
                False,
                error="one runtime artifact not found",
                candidates=[str(path) for path in runtimes],
            )
            record(
                "browser-remote-materialization-accounting",
                False,
                error="one runtime artifact not found",
                candidates=[str(path) for path in runtimes],
            )

    status = "PASS" if all(item["pass"] for item in observations) else "FAIL"
    report = {
        "status": status,
        "binary_sha256": sha256(binary),
        "driver_sha256": sha256(Path(__file__)),
        "observations": observations,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"status": status, "failures": [x for x in observations if not x["pass"]]}))
    return int(status != "PASS")


if __name__ == "__main__":
    raise SystemExit(main())
