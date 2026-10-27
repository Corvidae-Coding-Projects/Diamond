#!/usr/bin/env python3
"""LP5 negative compiler corpus and emitted durable-action contract checks."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "verification/lp5/project"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, default=ROOT / "target/debug/diamond")
    parser.add_argument("--report", type=Path, default=ROOT / "verification/lp5/compiler.json")
    args = parser.parse_args()
    binary = args.binary.resolve()
    observations: list[dict[str, object]] = []

    def invoke(*values: object) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(binary), *map(str, values)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=45,
        )

    def record(name: str, passed: bool, **details: object) -> None:
        observations.append({"name": name, "pass": bool(passed), **details})

    cases = [
        ("invalid-replay", "diamond.toml", 'replay = "transactional"', 'replay = "durable"', "transactional or never"),
        ("missing-resource", "diamond.toml", 'resource = "Issues"\nparent = "id"\nlabel = "Save issue"', 'parent = "id"\nlabel = "Save issue"', "require a parent resource"),
        ("missing-parent", "diamond.toml", 'resource = "Issues"\nparent = "id"\nlabel = "Save issue"', 'resource = "Issues"\nlabel = "Save issue"', "require a parent resource"),
        ("unknown-resource", "diamond.toml", 'resource = "Issues"\nparent = "id"\nlabel = "Save issue"', 'resource = "Absent"\nparent = "id"\nlabel = "Save issue"', "resource is unknown"),
        ("wrong-parent-parameter", "diamond.toml", 'parent = "id"\nlabel = "Save issue"', 'parent = "value"\nlabel = "Save issue"', "match its resource nominal ID"),
        ("wrong-parent-nominal", "diamond.toml", 'resource = "Issues"\nparent = "id"\nlabel = "Save issue"', 'resource = "Members"\nparent = "id"\nlabel = "Save issue"', "match its resource nominal ID"),
        ("never-cannot-claim-parent", "diamond.toml", 'replay = "transactional"', 'replay = "never"', "requires transactional replay"),
        ("blank-label", "diamond.toml", 'label = "Save issue"', 'label = " "', "1..80 nonblank"),
        ("long-label", "diamond.toml", 'label = "Save issue"', 'label = "' + "x" * 81 + '"', "1..80 nonblank"),
        ("raw-rpc-action-bypass", "diamond.toml", '[rpc]\n', '[rpc]\nrename_bypass = "server.rename"\n', "cannot use ordinary RPC"),
        ("public-api-action", "diamond.toml", '[http_api.operations.rename]\nfunction = "server.rename"\nauth = "token"', '[http_api.operations.rename]\nfunction = "server.rename"\nauth = "public"', "require token authentication"),
        ("duplicate-action-binding", "diamond.toml", '[actions.protected_scalar]', '[actions.rename_alias]\nfunction = "server.rename"\nresource = "Issues"\nparent = "id"\n\n[actions.protected_scalar]', "one unique actions binding"),
        ("unbound-declared-action", "diamond.toml", '[actions.rename]\nfunction = "server.rename"\nreplay = "transactional"\nresource = "Issues"\nparent = "id"\nlabel = "Save issue"\n\n', '', "RPC is not declared"),
    ]

    with tempfile.TemporaryDirectory(prefix="diamond-lp5-compiler-") as temporary:
        root = Path(temporary)
        valid = root / "valid"
        shutil.copytree(FIXTURE, valid)
        result = invoke("check", valid / "diamond.toml")
        record("valid-transactional-source", result.returncode == 0, stderr=result.stderr)

        for name, filename, before, after, expected in cases:
            case = root / name
            shutil.copytree(FIXTURE, case)
            path = case / filename
            source = path.read_text()
            if before not in source:
                raise AssertionError((name, "mutation anchor missing"))
            path.write_text(source.replace(before, after, 1))
            result = invoke("check", case / "diamond.toml")
            record(
                name,
                result.returncode != 0
                and expected.lower() in result.stderr.lower()
                and "panicked" not in result.stderr.lower(),
                expected=expected,
                stderr=result.stderr.strip(),
                source_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            )

        # Eligibility walks helper bodies rather than trusting only the action's
        # transitive effect summary or its outermost expression.
        helper_cases = [
            (
                "helper-set-query",
                'export action rename(id: IssueId, value: EditIssue) -> Result<Issue, EditError>:\n',
                'fn queried_get(id: IssueId) -> Result<Issue, DataError>:\n  let ignored = issues.select(workspace: WorkspaceId("alpha"), limit: 1)?\n  issues.get(id)\n\nexport action rename(id: IssueId, value: EditIssue) -> Result<Issue, EditError>:\n',
                "issues.get(id)",
                "queried_get(id)",
                "D334",
            ),
            (
                "nested-external-effect",
                '            true => Ok(issue)',
                '            true =>\n              match publish(false, false, false):\n                Ok(message) => Ok(issue)\n                Err(problem) => Err(SaveFailed)',
                None,
                None,
                "no external effects",
            ),
        ]
        for name, anchor, replacement, second_anchor, second_replacement, expected in helper_cases:
            case = root / name
            shutil.copytree(FIXTURE, case)
            path = case / "server.dia"
            source = path.read_text()
            if anchor not in source:
                raise AssertionError((name, "mutation anchor missing"))
            source = source.replace(anchor, replacement, 1)
            if second_anchor:
                start = source.index("export action rename")
                prefix, action = source[:start], source[start:]
                if second_anchor not in action:
                    raise AssertionError((name, "action anchor missing"))
                source = prefix + action.replace(second_anchor, second_replacement, 1)
            path.write_text(source)
            result = invoke("check", case / "diamond.toml")
            record(name, result.returncode != 0 and expected.lower() in result.stderr.lower() and "panicked" not in result.stderr.lower(), expected=expected, stderr=result.stderr.strip())

        # All eleven states are language values and exhaustive matching remains
        # mandatory in the reference evaluator, independently of the web runtime.
        action_source = root / "actions.dia"
        action_source.write_text(
            'enum Problem:\n  Failed\n'
            'fn describe(value: Action<Text, Problem>) -> Text:\n'
            '  match value:\n'
            '    ActionIdle => "idle"\n'
            '    ActionSubmitting => "submitting"\n'
            '    ActionSucceeded(text) => text\n'
            '    ActionRejected(problem) => "rejected"\n'
            '    ActionInvalid => "invalid"\n'
            '    ActionDenied => "denied"\n'
            '    ActionAborted => "aborted"\n'
            '    ActionUnknown => "unknown"\n'
            '    ActionExpired => "expired"\n'
            '    ActionConflict => "conflict"\n'
            '    ActionCorrupt => "corrupt"\n'
            'fn main() -> Text:\n  describe(ActionCorrupt)\n'
        )
        result = invoke("run", action_source)
        record("reference-all-action-states", result.returncode == 0 and result.stdout == "corrupt\n", stdout=result.stdout, stderr=result.stderr)

        output = root / "artifact"
        result = invoke("build", FIXTURE / "diamond.toml", "--out", output)
        record("real-artifact-build", result.returncode == 0, stderr=result.stderr)
        if result.returncode == 0:
            contract = json.loads((output / "contract.json").read_text())
            api = contract["http_api"]
            rename = contract["rpc"]["rename"]
            record("artifact-versions", [contract["language_version"], contract["format_version"], contract["wire_version"]] == ["0.20.0", 5, 7], observed=[contract["language_version"], contract["format_version"], contract["wire_version"]])
            record("durable-contract-advertised", "durable invocation" in rename["submission"] and "same invocation" in rename["replay"], observed=rename)
            record("api-contract-terminal-states", all(tag in api["codecs"]["Action"] for tag in ["ActionExpired", "ActionConflict", "ActionCorrupt"]) and "prepare/execute/status/ack" in api["replay"], observed={"action": api["codecs"]["Action"], "replay": api["replay"]})
            result = invoke("check", output)
            record("artifact-recheck", result.returncode == 0, stderr=result.stderr)
            artifact = json.loads((output / "server.json").read_text())
            artifact["format_version"] = 4
            (output / "server.json").write_text(json.dumps(artifact))
            result = invoke("check", output)
            record("old-artifact-rejected", result.returncode != 0 and "incompatible" in result.stderr.lower(), stderr=result.stderr.strip())

    status = "PASS" if all(item["pass"] for item in observations) else "FAIL"
    report = {
        "status": status,
        "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
        "driver_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "observations": observations,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"status": status, "cases": len(observations), "failures": [item for item in observations if not item["pass"]]}))
    return int(status != "PASS")


if __name__ == "__main__":
    raise SystemExit(main())
