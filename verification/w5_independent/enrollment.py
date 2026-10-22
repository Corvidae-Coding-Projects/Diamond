#!/usr/bin/env python3
"""Actual invitation recipient/purpose/one-use and closed enrollment gates."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import shutil
import tempfile

from accounts import begin_window, client, delivered, provision, user_row
from common import Receipt, app_id, quote
from fixture import write
from model import PASSWORD
from service import ENV_NAME, Service, free_port, sha256

ROOT = Path(__file__).resolve().parents[2]
APP = "IndependentInviteAccounts"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    options = parser.parse_args()
    root = Path(tempfile.mkdtemp(prefix="diamond-w5-independent-enrollment-"))
    binary = root / "diamond"
    shutil.copy2(options.binary, binary)
    project = write(root / "project", app=APP, enrollment="invite")
    service = Service(root, binary, project)
    service.extra_serve = ("--identity-mailbox", str(service.mailbox))
    receipt, fatal = Receipt(), None
    receipt.secret(PASSWORD)
    application = app_id(APP)
    try:
        service.postgres()
        contract, _ = service.build()
        rpc = contract["rpc_id"]
        service.cli("project-init", project, "--postgres-env", ENV_NAME)
        provision(service)
        service.start_node("A")
        service.start_node("B")
        admin, anonymous = client(service, rpc, "alice"), client(service, rpc)
        before = service.mailbox_snapshot()
        anonymous_invite = anonymous.account("invite", {"email": "invitee@qualification.invalid"}, node="B")
        receipt.record("invitation-admin-authority-required", anonymous_invite[0] == 403 and not service.mailbox_delta(before),
                       {"status": anonymous_invite[0], "value": anonymous_invite[1]}, "anonymous caller cannot issue invitation or delivery")
        no_invite = anonymous.account("register", {"username": "uninvited", "email": "uninvited@qualification.invalid", "password": PASSWORD, "invite": ""})
        receipt.record("invite-profile-denies-uninvited-enrollment", no_invite[0] in [200, 403] and not user_row(service, "uninvited", application=application),
                       {"status": no_invite[0], "value": no_invite[1]}, "no account or administrative authority without invitation; safe generic acceptance or policy denial")
        before = service.mailbox_snapshot()
        invitation = admin.account("invite", {"email": "INVITEE@Qualification.INVALID"}, node="B")
        secret = delivered(service, before, recipient="invitee@qualification.invalid", purpose="invite", receipt=receipt)
        receipt.record("admin-invitation-delivered", invitation[0] == 200, {"status": invitation[0], "value": invitation[1]}, "real private one-use invitation to canonical recipient")
        wrong_recipient = anonymous.account("register", {"username": "thief", "email": "thief@qualification.invalid", "password": PASSWORD, "invite": secret})
        untouched = service.sql("SELECT count(*) FROM diamond_identity.challenges WHERE app=" + quote(application) + " AND purpose='invite' AND email='invitee@qualification.invalid' AND NOT consumed")
        receipt.record("invitation-recipient-binding", wrong_recipient[0] in [400, 403] and not user_row(service, "thief", application=application) and untouched == "1",
                       {"status": wrong_recipient[0], "value": wrong_recipient[1], "unconsumed_correct_invites": untouched},
                       "wrong recipient cannot create account or consume invitation intended for another address")
        before = service.mailbox_snapshot()
        accepted = anonymous.account("register", {"username": "invitee", "email": "invitee@qualification.invalid", "password": PASSWORD, "invite": secret}, node="B")
        verification = delivered(service, before, recipient="invitee@qualification.invalid", purpose="verify", receipt=receipt)
        row = user_row(service, "invitee", application=application)
        consumed = service.sql("SELECT count(*) FROM diamond_identity.challenges WHERE app=" + quote(application) + " AND purpose='invite' AND email='invitee@qualification.invalid' AND consumed")
        receipt.record("invitation-consumed-pending-verification", accepted[0] == 200 and len(row) == 1 and not row[0]["verified"] and not row[0]["administrator"] and consumed == "1",
                       {"status": accepted[0], "value": accepted[1], "account": row, "consumed_invites": consumed}, "single registration atomically consumes invitation and creates pending account with separate verification purpose")
        replay = anonymous.account("register", {"username": "replay", "email": "invitee@qualification.invalid", "password": PASSWORD, "invite": secret})
        receipt.record("invitation-replay-denied", not user_row(service, "replay", application=application), {"status": replay[0], "value": replay[1]}, "consumed invitation cannot create another account")
        wrong_purpose = anonymous.account("reset", {"token": verification, "password": PASSWORD})
        verified = anonymous.account("verify", {"token": verification}, node="B")
        receipt.record("invited-verification-purpose", wrong_purpose[0] in [400, 403] and verified[0] == 200 and user_row(service, "invitee", application=application)[0]["verified"],
                       {"wrong_purpose": wrong_purpose, "verification": verified}, "verification credential cannot reset password, then activates only invited account")
        client(service, rpc, "invitee", node="B")

        begin_window(service, receipt, "invitation-expiry", application=application)
        before = service.mailbox_snapshot()
        admin.account("invite", {"email": "expired@qualification.invalid"})
        expired_secret = delivered(service, before, recipient="expired@qualification.invalid", purpose="invite", receipt=receipt)
        service.sql("UPDATE diamond_identity.challenges SET expires=clock_timestamp()-interval '1 second' WHERE app=" + quote(application) + " AND purpose='invite' AND email='expired@qualification.invalid'")
        expired = anonymous.account("register", {"username": "expired", "email": "expired@qualification.invalid", "password": PASSWORD, "invite": expired_secret}, node="B")
        receipt.record("invitation-database-expiry", expired[0] in [400, 403] and not user_row(service, "expired", application=application),
                       {"status": expired[0], "value": expired[1]}, "database-time expired invitation cannot enroll")
        closed_app = "IndependentClosedAccounts"
        closed = write(root / "closed", app=closed_app, enrollment="closed")
        service.cli("project-init", closed, "--postgres-env", ENV_NAME, configuration=service.owner)
        provision(service, "closed-admin", project=closed)
        closed_contract, _ = service.build(closed)
        service.ports["C"] = free_port()
        service.start_node("C", project=closed)
        closed_anon = client(service, closed_contract["rpc_id"], node="C")
        closed_request = closed_anon.account("register", {"username": "closed-user", "email": "closed-user@qualification.invalid", "password": PASSWORD, "invite": ""}, node="C")
        closed_admin = client(service, closed_contract["rpc_id"], "closed-admin", node="C")
        receipt.record("closed-profile-no-public-enrollment", closed_request[0] in [200, 403] and not user_row(service, "closed-user", application=app_id(closed_app))
                       and bool(closed_admin.cookie), {"status": closed_request[0], "value": closed_request[1]}, "closed public registration creates nothing; explicit trusted administrator still authenticates")
        service.stop_node("C")
    except Exception as failure:
        fatal = repr(failure)
    finally:
        try:
            service.close()
        except Exception as cleanup:
            fatal = (fatal or "") + " cleanup=" + repr(cleanup)
        report = receipt.report(service, sha256(binary), error=fatal, temporary_root=str(root) if fatal else None,
                                scope="real invite/closed profiles, recipient/purpose/expiry/one-use constraints and CLI administrator boundary")
        options.report.write_text(json.dumps(report, indent=2, ensure_ascii=True) + "\n")
        if report["status"] == "PASS":
            shutil.rmtree(root)
    print(json.dumps({"status": report["status"], "observations": len(receipt.results), "error": report["error"]}))
    return report["status"] != "PASS"


if __name__ == "__main__":
    raise SystemExit(main())
