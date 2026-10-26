#!/usr/bin/env python3
"""Exercise Git/OAuth authority boundaries through the real Diamond checker."""

import json
import hashlib
import pathlib
import shutil
import subprocess
import tempfile


ROOT = pathlib.Path(__file__).resolve().parents[1]
BINARY = ROOT / "target" / "debug" / "diamond"
EXAMPLE = ROOT / "examples" / "git-browser"


def check(project: pathlib.Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(BINARY), "check", str(project / "diamond.toml")],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )


def replace(path: pathlib.Path, old: str, new: str) -> None:
    source = path.read_text()
    if old not in source:
        raise AssertionError(f"fixture text is missing from {path.name}")
    path.write_text(source.replace(old, new, 1))


def rejected(name: str, mutate, expected: str, results: list[dict[str, object]]) -> None:
    with tempfile.TemporaryDirectory(prefix="diamond-git-checker-") as temporary:
        project = pathlib.Path(temporary) / "project"
        shutil.copytree(EXAMPLE, project)
        mutate(project)
        process = check(project)
        diagnostic = process.stdout + process.stderr
        passed = process.returncode != 0 and expected in diagnostic
        results.append({"name": name, "pass": passed})
        if not passed:
            raise AssertionError(
                f"{name}: expected rejection containing {expected!r}, got {diagnostic[-2000:]!r}"
            )


def main() -> None:
    results: list[dict[str, object]] = []
    base = check(EXAMPLE)
    if base.returncode != 0:
        raise AssertionError((base.stdout + base.stderr)[-2000:])
    results.append({"name": "reference Git project accepted", "pass": True})

    rejected(
        "dynamic Git binding rejected",
        lambda project: replace(
            project / "server.dia",
            "export fn initialize_repository(repository: Text) -> Result<Text, DataError>:\n  host.git_create(\"repos\", repository)",
            "export fn initialize_repository(repository: Text, binding: Text) -> Result<Text, DataError>:\n  host.git_create(binding, repository)",
        ),
        "Git binding requires a static name",
        results,
    )
    rejected(
        "dynamic Git operation rejected",
        lambda project: replace(
            project / "server.dia",
            'host.git_query<GitHead>("repos", repository, "head", "HEAD", "", 1)',
            'host.git_query<GitHead>("repos", repository, operation, "HEAD", "", 1)',
        )
        or replace(
            project / "server.dia",
            "export fn repository_head(repository: Text) -> Result<GitHead, DataError>:",
            "export fn repository_head(repository: Text, operation: Text) -> Result<GitHead, DataError>:",
        ),
        "Git query operation requires a static name",
        results,
    )
    rejected(
        "non-text Git repository rejected",
        lambda project: replace(
            project / "server.dia",
            'host.git_create("repos", repository)',
            'host.git_create("repos", 1)',
        ),
        "Git arguments require Text values",
        results,
    )
    rejected(
        "browser Git host access rejected",
        lambda project: replace(
            project / "ui.dia",
            'output = "Repository metadata created. Initialize Git before pushing."',
            'output = host.git_create("repos", repository)',
        ),
        "host operations require a server project module",
        results,
    )
    rejected(
        "undeclared Git write capability rejected",
        lambda project: replace(
            project / "diamond.toml",
            ', "GitWrite<repos>"',
            "",
        ),
        "module host budget exceeds the application budget",
        results,
    )
    rejected(
        "undeclared Git administration capability rejected",
        lambda project: replace(
            project / "diamond.toml",
            ', "GitAdmin<repos>"',
            "",
        ),
        "module host budget exceeds the application budget",
        results,
    )
    rejected(
        "undeclared Git review capability rejected",
        lambda project: replace(
            project / "diamond.toml",
            ', "GitReview<repos>"',
            "",
        ),
        "module host budget exceeds the application budget",
        results,
    )
    rejected(
        "non-text Git integration object rejected",
        lambda project: replace(
            project / "server.dia",
            'host.git_integrate("repos", id_text(row.repository), row.source_reference, row.target_reference, row.source_object, row.target_object, merge_strategy_text(row.strategy), message, id_text(row.id))',
            'host.git_integrate("repos", id_text(row.repository), row.source_reference, row.target_reference, 1, row.target_object, merge_strategy_text(row.strategy), message, id_text(row.id))',
        ),
        "Git arguments require Text values",
        results,
    )
    rejected(
        "non-boolean Git protection flag rejected",
        lambda project: replace(
            project / "server.dia",
            'host.git_protect("repos", repository, pattern, required_role, allow_force, allow_delete, required_approvals, require_pipeline, require_discussions, require_signed_commits, scan_secrets)',
            'host.git_protect("repos", repository, pattern, required_role, "yes", allow_delete, required_approvals, require_pipeline, require_discussions, require_signed_commits, scan_secrets)',
        ),
        "Git policy flag requires Bool",
        results,
    )
    rejected(
        "Git write and database write cannot share a function",
        lambda project: (project / "server.dia").write_text(
            (project / "server.dia").read_text()
            + "\nexport fn unsafe_combined_effect(repository: Text) -> Result<Text, DataError>:\n"
            + "  let saved = repositories.create(NewRepository(organization: OrganizationId(repository), name: \"unsafe\", description: \"\", public: false))?\n"
            + "  host.git_create(\"repos\", repository)\n"
        ),
        "Git writes cannot share application writes or explicit transactions",
        results,
    )
    rejected(
        "Git administration and database write cannot share a function",
        lambda project: (project / "server.dia").write_text(
            (project / "server.dia").read_text()
            + "\nexport fn unsafe_admin_effect(repository: Text) -> Result<Text, DataError>:\n"
            + "  let saved = repositories.create(NewRepository(organization: OrganizationId(repository), name: \"unsafe\", description: \"\", public: false))?\n"
            + "  host.git_protect(\"repos\", repository, \"refs/heads/main\", \"authenticated\", false, false, 0, false, false, false, false)\n"
        ),
        "Git writes cannot share application writes or explicit transactions",
        results,
    )
    rejected(
        "restricted OAuth without an admission rule rejected",
        lambda project: replace(
            project / "diamond.toml", 'admission = "public"', 'admission = "restricted"'
        ),
        "invalid OAuth identity policy",
        results,
    )
    rejected(
        "uppercase OAuth policy boundary rejected",
        lambda project: replace(
            project / "diamond.toml",
            'admission = "public"',
            'admission = "restricted"\nemail_domains = ["Example.COM"]',
        ),
        "invalid OAuth identity policy",
        results,
    )

    receipt = {
        "status": "PASS" if all(result["pass"] for result in results) else "FAIL",
        "binary_sha256": hashlib.sha256(BINARY.read_bytes()).hexdigest(),
        "profile": "Diamond Git and OAuth compiler boundaries",
        "passed": sum(1 for result in results if result["pass"]),
        "total": len(results),
        "results": results,
    }
    (ROOT / "verification/git_compiler_boundaries_results.json").write_text(
        json.dumps(receipt, indent=2) + "\n"
    )
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    main()
