#!/usr/bin/env python3
"""Validate and aggregate the definitive independent W9 campaign receipts."""
from __future__ import annotations

import json
from pathlib import Path

BASE = Path(__file__).resolve().parent
EXPECTED_SHA256 = "4281f59a9eebda6db8dfa9169a88b6b568898474d194ce599ff1c7a75017a57d"
CAMPAIGNS = [
    "auth_policy_races",
    "edge_campaign",
    "job_quota_dedup",
    "recover_cancel",
    "running_cancel",
    "stale_holder",
    "storage_loss",
    "websocket_malformed",
]


def main() -> int:
    campaigns = []
    for name in CAMPAIGNS:
        path = BASE / f"{name}_final.json"
        receipt = json.loads(path.read_text())
        results = receipt.get("results", receipt.get("observations", []))
        campaigns.append(
            {
                "name": name,
                "receipt": path.name,
                "status": receipt.get("status"),
                "binary_sha256": receipt.get("binary_sha256"),
                "observations": len(results),
                "passed": sum(row.get("status") == "PASS" for row in results),
                "http_requests": receipt.get("http_requests", 0),
                "raw_socket_requests": receipt.get("raw_socket_requests", 0),
                "raw_handshakes": receipt.get("raw_handshakes", 0),
                "upgraded_frame_scenarios": receipt.get(
                    "upgraded_frame_scenarios", 0
                ),
                "shutdown_receipts": len(receipt.get("shutdowns", [])),
                "unexpected_shutdowns": sum(
                    row.get("exit") != row.get("expected_exit")
                    or row.get("forced_kill") is True
                    or row.get("panic") is True
                    for row in receipt.get("shutdowns", [])
                ),
                "cleanup_errors": len(receipt.get("cleanup_errors", [])),
                "temporary_root_removed": receipt.get("temporary_root_removed"),
                "elapsed_seconds": receipt.get("elapsed_seconds", 0),
            }
        )

    totals = {
        "campaigns": len(campaigns),
        "observations": sum(row["observations"] for row in campaigns),
        "passed": sum(row["passed"] for row in campaigns),
        "http_requests": sum(row["http_requests"] for row in campaigns),
        "raw_socket_requests": sum(
            row["raw_socket_requests"] for row in campaigns
        ),
        "raw_handshakes": sum(row["raw_handshakes"] for row in campaigns),
        "upgraded_frame_scenarios": sum(
            row["upgraded_frame_scenarios"] for row in campaigns
        ),
        "shutdown_receipts": sum(row["shutdown_receipts"] for row in campaigns),
        "unexpected_shutdowns": sum(
            row["unexpected_shutdowns"] for row in campaigns
        ),
        "cleanup_errors": sum(row["cleanup_errors"] for row in campaigns),
        "elapsed_seconds": sum(row["elapsed_seconds"] for row in campaigns),
    }
    okay = (
        all(row["status"] == "PASS" for row in campaigns)
        and all(row["binary_sha256"] == EXPECTED_SHA256 for row in campaigns)
        and all(row["observations"] == row["passed"] for row in campaigns)
        and all(row["temporary_root_removed"] is True for row in campaigns)
        and totals["unexpected_shutdowns"] == 0
        and totals["cleanup_errors"] == 0
    )
    aggregate = {
        "status": "PASS" if okay else "FAIL",
        "binary_sha256": EXPECTED_SHA256,
        "campaigns": campaigns,
        "totals": totals,
        "confirmed_baseline_findings": [
            {
                "id": "W9-I-01",
                "baseline_receipt": "running_cancel_baseline.json",
                "final_receipt": "running_cancel_final.json",
            },
            {
                "id": "W9-I-02",
                "baseline_receipt": "job_quota_dedup_baseline.json",
                "final_receipt": "job_quota_dedup_final.json",
            },
        ],
        "separate_scope": {
            "long_capacity": "owned by the primary W9 campaign",
            "browser": "covered by the primary W9 Chrome replay",
        },
    }
    destination = BASE / "results.json"
    destination.write_text(json.dumps(aggregate, indent=2) + "\n")
    print(json.dumps({"status": aggregate["status"], "totals": totals}))
    return 0 if okay else 1


if __name__ == "__main__":
    raise SystemExit(main())
