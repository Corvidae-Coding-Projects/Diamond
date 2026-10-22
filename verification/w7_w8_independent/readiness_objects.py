#!/usr/bin/env python3
"""Supplementary final-native-change check: real unavailable shared object root."""
import argparse
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import time

from fixture import write
from receiver import Receiver
from service import ENV_NAME, Service, sha256

BASE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("_w8_readiness_common", BASE.parent / "w5_independent/common.py")
previous = importlib.util.module_from_spec(spec)
spec.loader.exec_module(previous)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--report", type=Path, default=BASE / "readiness_objects_results.json")
    options = parser.parse_args()
    root = Path(tempfile.mkdtemp(prefix="diamond-w8-readiness-objects-"))
    binary = root / "diamond"
    shutil.copy2(options.binary, binary)
    project = write(root / "project")
    service, receipt, receiver = Service(root, binary, project), previous.Receipt(), None
    hidden = root / "temporarily-unavailable-objects"
    fatal = None
    try:
        receiver = Receiver()
        service.runtime["http"] = {"inventory": {"origin": receiver.origin, "network": "loopback", "paths": ["/v1/"], "methods": ["POST"]}}
        service.postgres()
        service.cli("project-init", project, "--postgres-env", ENV_NAME)
        service.files_cli(initialize=True)
        service.start_node("A")
        service.start_node("B")
        service.objects.rename(hidden)
        observed = {}
        try:
            for node in ["A", "B"]:
                live = service.request(node, "/_diamond/health/live")
                ready = service.request(node, "/_diamond/health/ready")
                observed[node] = {"live": live[0], "ready": ready[0], "body": json.loads(ready[2])}
        finally:
            hidden.rename(service.objects)
        receipt.record("missing-shared-store-removes-readiness-not-liveness", all(row["live"] == 200 and row["ready"] >= 400 for row in observed.values()),
                       observed, "actual missing shared filesystem root makes both nodes unready without disclosing paths; processes remain live")
        recovery = {node: service.request(node, "/_diamond/health/ready")[0] for node in ["A", "B"]}
        receipt.record("shared-store-recovery-restores-readiness", recovery == {"A": 200, "B": 200},
                       recovery, "restoring the same private store returns both nodes to readiness without process replacement")
    except Exception as error:
        fatal = repr(error)
    finally:
        if hidden.exists() and not service.objects.exists():
            hidden.rename(service.objects)
        service.close()
        if receiver:
            receiver.close()
    report = receipt.report(service, sha256(binary), error=fatal, source_sha256={Path(__file__).name: sha256(__file__)}, temporary_root=str(root))
    options.report.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"status": report["status"], "observations": len(receipt.results), "error": fatal, "http_requests": report["http_requests"]}), flush=True)
    if report["status"] == "PASS":
        shutil.rmtree(root)
    return report["status"] != "PASS"


if __name__ == "__main__":
    raise SystemExit(main())
