#!/usr/bin/env python3
"""Check typed accessibility primitives and final reference-app artifacts."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[2]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Document(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: Counter[str] = Counter()
        self.ids: list[str] = []
        self.labels: set[str] = set()
        self.controls: list[dict[str, str | None]] = []
        self.sections: list[dict[str, str | None]] = []
        self.links: list[dict[str, str | None]] = []
        self.statuses = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        self.tags[tag] += 1
        if values.get("id"):
            self.ids.append(str(values["id"]))
        if tag == "label" and values.get("for"):
            self.labels.add(str(values["for"]))
        if tag in {"input", "textarea", "select"}:
            self.controls.append(values)
        if tag == "section":
            self.sections.append(values)
        if tag == "link":
            self.links.append(values)
        if values.get("role") in {"status", "alert"} or values.get("aria-live"):
            self.statuses += 1


def invoke(binary: Path, arguments: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run([str(binary), *arguments], cwd=cwd, text=True, capture_output=True, timeout=120)


def inspect_app(binary: Path, root: Path, template: str) -> tuple[dict[str, object], list[str]]:
    failures = []
    source = root / template
    build = root / f"{template}-build"
    created = invoke(binary, ["new", str(source), "--template", template], root)
    checked = invoke(binary, ["check", str(source / "diamond.toml")], root)
    built = invoke(binary, ["build", str(source / "diamond.toml"), "--out", str(build)], root)
    if any(result.returncode != 0 for result in [created, checked, built]):
        failures.append("scaffold/check/build failed")
        return {"commands": [created.returncode, checked.returncode, built.returncode]}, failures
    document = Document()
    index = build / "index.html"
    document.feed(index.read_text())
    duplicate_ids = sorted(name for name, count in Counter(document.ids).items() if count > 1)
    unlabeled = sorted(
        str(control.get("id") or "<missing id>")
        for control in document.controls
        if not control.get("id") or control.get("id") not in document.labels
    )
    bad_sections = [section for section in document.sections if not section.get("aria-labelledby")]
    stylesheet_links = [link for link in document.links if link.get("rel") == "stylesheet"]
    stylesheet_paths = [build / str(link.get("href", "")).removeprefix("/") for link in stylesheet_links]
    style_text = "\n".join(path.read_text() for path in stylesheet_paths if path.is_file())
    hashed_styles = all(re.fullmatch(r"[0-9a-f]{64}\.css", path.name) for path in stylesheet_paths)
    relationships = []
    for identifier in [section.get("aria-labelledby") for section in document.sections]:
        if identifier and identifier not in document.ids:
            relationships.append(identifier)
    checks = {
        "one_main": document.tags["main"] == 1,
        "one_h1": document.tags["h1"] == 1,
        "unique_ids": not duplicate_ids,
        "all_controls_labeled": not unlabeled,
        "sections_named": not bad_sections,
        "aria_relationships_resolve": not relationships,
        "status_feedback": document.statuses >= 2,
        "one_hashed_stylesheet": len(stylesheet_paths) == 1 and hashed_styles and stylesheet_paths[0].is_file(),
        "visible_focus_rule": ":focus-visible" in style_text and "outline:3px" in style_text,
        "small_screen_rule": "@media(max-width:40rem)" in style_text,
    }
    failures.extend(name for name, accepted in checks.items() if not accepted)
    return {
        "template": template,
        "checks": checks,
        "tags": dict(document.tags),
        "control_count": len(document.controls),
        "status_regions": document.statuses,
        "duplicate_ids": duplicate_ids,
        "unlabeled_controls": unlabeled,
        "unresolved_relationships": relationships,
        "index_sha256": digest(index),
        "stylesheet": str(stylesheet_paths[0].relative_to(build)) if stylesheet_paths else None,
        "stylesheet_sha256": digest(stylesheet_paths[0]) if stylesheet_paths and stylesheet_paths[0].is_file() else None,
    }, failures


def primitive_cases(binary: Path, root: Path) -> list[dict[str, object]]:
    project = root / "primitives"
    shutil.copytree(ROOT / "examples/foundations", project)
    source = project / "counter.dia"
    source.write_text(
        "export component Counter:\n"
        "  state value: Text = \"\"\n"
        "  on save:\n"
        "    value = value\n"
        "  html:\n"
        "    <main><h1>Accessible primitives</h1><form on-submit={save}><fieldset aria-labelledby=\"group\"><legend id=\"group\">Profile</legend><label for=\"name\">Name</label><input id=\"name\" autocomplete=\"name\" aria-describedby=\"help\" bind-value={value}><p id=\"help\" role=\"status\" aria-live=\"polite\">Required profile name</p><button type=\"submit\" aria-controls=\"help\">Save</button></fieldset></form></main>\n"
    )
    accepted = invoke(binary, ["check", str(project / "diamond.toml")], root)
    invalid = source.read_text().replace('aria-controls="help"', 'aria-bogus="help"')
    source.write_text(invalid)
    rejected = invoke(binary, ["check", str(project / "diamond.toml")], root)
    return [
        {"name": "declared semantic primitives", "accepted": accepted.returncode == 0, "exit": accepted.returncode, "stderr": accepted.stderr},
        {"name": "undeclared ARIA attribute rejected", "accepted": rejected.returncode != 0 and "HTML attribute aria-bogus" in rejected.stderr, "exit": rejected.returncode, "stderr": rejected.stderr},
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--report", type=Path, default=ROOT / "verification/w10/accessibility_results.json")
    options = parser.parse_args()
    binary = options.binary.resolve()
    failures = []
    with tempfile.TemporaryDirectory(prefix="diamond-w10-accessibility-") as temporary:
        root = Path(temporary)
        apps = []
        for template in ["workspace", "catalog"]:
            observed, app_failures = inspect_app(binary, root, template)
            apps.append(observed)
            failures.extend(f"{template}: {failure}" for failure in app_failures)
        primitives = primitive_cases(binary, root)
        failures.extend(case["name"] for case in primitives if not case["accepted"])
    report = {
        "status": "PASS" if not failures else "FAIL",
        "binary_sha256": digest(binary),
        "applications": apps,
        "primitive_cases": primitives,
        "failures": failures,
        "browser_boundary": "Static final-artifact checks; real Chrome focus, validation, layout and live feedback are retained separately.",
    }
    options.report.parent.mkdir(parents=True, exist_ok=True)
    options.report.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"status": report["status"], "applications": len(apps), "controls": sum(int(app.get("control_count", 0)) for app in apps), "failures": failures}))
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
