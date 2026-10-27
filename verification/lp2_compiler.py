#!/usr/bin/env python3
"""LP2 negative source corpus and real artifact/reference-evaluator checks.

Cases state expected failure classes before invoking the actual Diamond CLI.
These are bounded interface observations, not whole-compiler coverage.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "examples/language-spine"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, default=ROOT / "target/debug/diamond")
    parser.add_argument("--report", type=Path, default=ROOT / "verification/lp2/compiler.json")
    args = parser.parse_args()
    binary = args.binary.resolve()
    observations = []

    def record(name, passed, **details):
        observations.append({"name": name, "pass": bool(passed), **details})

    def invoke(*arguments):
        return subprocess.run([str(binary), *map(str, arguments)], cwd=ROOT, text=True, capture_output=True, timeout=30)

    # Each mutation changes exactly the stated boundary of a valid connected app.
    cases = [
        ("event-wrong-nominal", "ui.dia", "open(row.id)", 'open(UserId("alice"))', "handler/output argument type mismatch"),
        ("event-missing-argument", "ui.dia", "open(row.id)", "open", "argument count mismatch"),
        ("output-wrong-payload", "ui.dia", "emit changed(result)", "emit changed(issue)", "argument type mismatch"),
        ("output-parent-signature", "ui.dia", '    notification = outcome_text(value)\n    completions = completions + 1\n    rows = replace_issue(rows, value)', '    notification = "unused"', "signature mismatch"),
        ("output-unbound", "ui.dia", " on-changed={saved}", "", "properties and outputs must match"),
        ("output-undeclared", "ui.dia", "emit changed(result)", "emit absent(result)", "declared component output"),
        ("lifecycle-argument", "ui.dia", "on mount:", "on mount(id: IssueId):", "lifecycle events take no parameters"),
        ("lifecycle-output", "ui.dia", "    draft = loaded.title", "    draft = loaded.title\n    emit changed(result)", "lifecycle events cannot emit"),
        ("form-unknown-control", "ui.dia", 'name="title"', 'name="other"', "schema"),
        ("form-duplicate-control", "ui.dia", '        <button type="submit" disabled={pending}>', '        <input name="title" type="text" bind-value={draft}>\n        <button type="submit" disabled={pending}>', "schema"),
        ("form-unsupported-kind", "shared.dia", "export form EditIssue(title: Text<length: 1..120>)", "export form EditIssue(title: Text<length: 1..120>)\nexport form Unsupported(values: List<Text>)", "input fields require"),
        ("route-wrong-link-id", "ui.dia", "IssuePage(id: row.id)", 'IssuePage(id: UserId("alice"))', 'expected Id<"issue">'),
        ("route-unbounded-field", "shared.dia", "route IssuePage(id: IssueId)", "route IssuePage(id: Text)", "route requires distinct supported scalar"),
        ("route-query-mode", "shared.dia", '"/issues/{id}"', '"/issues/{id}?extra=1"', "route"),
        ("action-through-rpc", "diamond.toml", '[rpc]\n', '[rpc]\nrename_bypass = "server.rename"\n', "action"),
        ("action-source-wrapper", "server.dia", 'export fn main() -> Bool:\n  valid_title("New")', 'export fn main() -> Bool:\n  valid_title("New")\n\nfn bypass(id: IssueId, value: EditIssue) -> Result<Issue, EditError>:\n  rename(id, value)', "D333"),
        ("unsupported-replay", "diamond.toml", 'replay = "never"', 'replay = "durable"', "transactional or never"),
        ("unsupported-action-field", "diamond.toml", 'replay = "never"', 'replay = "never"\nrecovery = true', "unknown field"),
        ("missing-write-grant", "diamond.toml", ', "DbWrite<Issues>"', '', "D301"),
        ("restricted-output-nested", "ui.dia", "output changed(value: Action<Issue, EditError>)", 'output changed(value: List<Option<Secret<"inventory">>>)', "recursively safe managed data"),
        ("restricted-event-nested", "ui.dia", "on open(id: IssueId):", 'on open(id: Option<Secret<"inventory">>):', "recursively safe managed data"),
        ("restricted-state-inferred-none", "ui.dia", '  state draft: Text = ""', '  state hidden: Option<Secret<"inventory">> = None\n  state draft: Text = ""', "state"),
        ("restricted-action-empty-state", "ui.dia", 'state result: Action<Issue, EditError> = ActionIdle', 'state result: Action<Option<Secret<"inventory">>, EditError> = ActionIdle', "state"),
        ("restricted-property-type", "ui.dia", 'component Editor(issue: IssueId):', 'component Editor(issue: IssueId, hidden: Option<Secret<"inventory">>):', "component properties require finite managed browser types"),
    ]
    with tempfile.TemporaryDirectory(prefix="diamond-lp2-compiler-") as temporary:
        directory = Path(temporary)
        base = directory / "base"
        shutil.copytree(FIXTURE, base)
        result = invoke("check", base / "diamond.toml")
        record("valid-connected-source", result.returncode == 0, stderr=result.stderr)
        for name, filename, before, after, expected in cases:
            case = directory / name
            shutil.copytree(base, case)
            path = case / filename
            source = path.read_text()
            if before not in source: raise AssertionError((name, "mutation anchor missing"))
            path.write_text(source.replace(before, after))
            if name == "output-parent-signature":
                path.write_text(path.read_text().replace("on saved(value: Action<Issue, EditError>):", "on saved(value: Text):"))
            result = invoke("check", case / "diamond.toml")
            record(name, result.returncode > 0 and expected.lower() in result.stderr.lower() and "panicked" not in result.stderr,
                   expected=expected, stderr=result.stderr.strip(), source_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        # Built-in Action constructors and exhaustive matching reach the reference
        # evaluator as actual accepted programs rather than only checking syntax.
        for tag, expected in [("ActionIdle", "idle"), ("ActionSubmitting", "submitting"), ('ActionSucceeded("7")', "7"), ("ActionRejected(Failed)", "rejected"), ("ActionInvalid", "invalid"), ("ActionDenied", "denied"), ("ActionAborted", "aborted"), ("ActionUnknown", "unknown"), ("ActionExpired", "expired"), ("ActionConflict", "conflict"), ("ActionCorrupt", "corrupt")]:
            source = directory / "action.dia"
            source.write_text('enum Problem:\n  Failed\nfn describe(value: Action<Text, Problem>) -> Text:\n  match value:\n    ActionIdle => "idle"\n    ActionSubmitting => "submitting"\n    ActionSucceeded(number) => number\n    ActionRejected(problem) => "rejected"\n    ActionInvalid => "invalid"\n    ActionDenied => "denied"\n    ActionAborted => "aborted"\n    ActionUnknown => "unknown"\n    ActionExpired => "expired"\n    ActionConflict => "conflict"\n    ActionCorrupt => "corrupt"\nfn main() -> Text:\n  describe(' + tag + ')\n')
            result = invoke("run", source)
            record("reference-" + tag, result.returncode == 0 and result.stdout == expected + "\n", expected=expected, stdout=result.stdout, stderr=result.stderr)
        build = directory / "build"
        result = invoke("build", base / "diamond.toml", "--out", build)
        record("real-build", result.returncode == 0, stderr=result.stderr)
        if result.returncode == 0:
            metadata = json.loads((build / "contract.json").read_text())
            artifact = json.loads((build / "server.json").read_text())
            record("metadata-versions", [metadata["language_version"], metadata["format_version"], metadata["wire_version"]] == ["0.20.0", 5, 7], observed=[metadata["language_version"], metadata["format_version"], metadata["wire_version"]])
            record("original-source-comments-retained", all(source in artifact["sources"].values() for source in [p.read_text() for p in base.glob("*.dia")]), expected="exact input bytes including comments")
            assets = list((build / "assets").glob("*"))
            record("public-assets-exclude-server-canary", bool(assets) and all("lp2-server-only-canary" not in p.read_text() for p in assets), public_assets=len(assets))
            result = invoke("check", build)
            record("rechecked-artifact", result.returncode == 0, stderr=result.stderr)
            for key, value in [("format_version", 1), ("language_version", "0.16.0")]:
                altered = directory / key
                shutil.copytree(build, altered)
                changed = {**artifact, key: value}
                (altered / "server.json").write_text(json.dumps(changed))
                result = invoke("check", altered)
                record("incompatible-" + key, result.returncode > 0 and "incompatible" in result.stderr, stderr=result.stderr)
            (build / "contract.json").write_text((build / "contract.json").read_text() + " ")
            result = invoke("check", build)
            record("tampered-contract", result.returncode > 0 and "artifact mismatch" in result.stderr, stderr=result.stderr)
    status = "PASS" if all(row["pass"] for row in observations) else "FAIL"
    report = {"status": status, "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(), "driver_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), "observations": observations}
    args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"status": status, "observations": len(observations), "failures": [o for o in observations if not o["pass"]]}))
    return int(status != "PASS")


if __name__ == "__main__":
    raise SystemExit(main())
