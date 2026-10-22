#!/usr/bin/env python3
"""Real two-node account lifecycle and purpose/app/recipient challenge binding."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import shutil
import tempfile

from accounts import activate, begin_window, client, delivered, issue_token, provision, user_row
from common import Receipt, app_id, nominal, parallel, quote, seed
from fixture import APP, write
from model import PASSWORD, SECOND_PASSWORD
from service import ENV_NAME, Service, free_port, sha256

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    options = parser.parse_args()
    root = Path(tempfile.mkdtemp(prefix="diamond-w5-independent-lifecycle-"))
    binary = root / "diamond"
    shutil.copy2(options.binary, binary)
    project = write(root / "project")
    service = Service(root, binary, project)
    service.extra_serve = ("--identity-mailbox", str(service.mailbox))
    receipt, fatal = Receipt(), None
    receipt.secret(PASSWORD)
    receipt.secret(SECOND_PASSWORD)
    try:
        service.postgres()
        contract, _ = service.build()
        rpc = contract["rpc_id"]
        service.cli("project-init", project, "--postgres-env", ENV_NAME)
        provision(service)
        seed(service)
        service.start_node("A")
        service.start_node("B")
        admin = client(service, rpc, "alice")
        anonymous = client(service, rpc)
        another = client(service, rpc, node="B")

        before = service.mailbox_snapshot()
        status, accepted = anonymous.account("register", {"username": "bob", "email": "BOB@Qualification.INVALID", "password": PASSWORD, "invite": ""})
        token = delivered(service, before, recipient="bob@qualification.invalid", purpose="verify", receipt=receipt)
        rows = user_row(service, "bob")
        receipt.record("registration-pending-canonical-nonadmin", status == 200 and len(rows) == 1 and rows[0]["email"] == "bob@qualification.invalid"
                       and not rows[0]["verified"] and not rows[0]["disabled"] and not rows[0]["administrator"] and rows[0]["has_hash"],
                       {"response": accepted, "rows": rows}, "canonical ASCII email, hashed password, pending and never administrator")
        before_duplicate = service.mailbox_snapshot()
        duplicate = another.account("register", {"username": "bob", "email": "bob@qualification.invalid", "password": SECOND_PASSWORD, "invite": ""}, node="B")
        receipt.record("registration-known-generic", duplicate == (status, accepted) and not service.mailbox_delta(before_duplicate),
                       {"status": duplicate[0], "value": duplicate[1]}, "known registration has same public acceptance and does not replace credentials")
        pending = client(service, rpc)
        pending_status, pending_value = pending.login("bob", PASSWORD, success=False)
        receipt.record("pending-login-denied", pending_status == 403,
                       {"status": pending_status, "value": pending_value}, "pending account is not an authenticated identity")
        pending_issue = pending.account("create_token", {"label": "Pending must not issue", "operations": ["read"], "scopes": {"workspace": ["red"]}, "lifetime_seconds": "3600"})
        pending_credentials = service.sql("SELECT count(*) FROM diamond_identity.credentials WHERE app=" + quote(app_id()))
        receipt.record("pending-account-cannot-issue-api-credential", pending_issue[0] == 401 and pending_credentials == "0", {"status": pending_issue[0], "value": pending_issue[1], "credentials": pending_credentials}, "pending account has no authenticated identity; credential issuance returns401 and creates nothing")
        forge = anonymous.account("register", {"username": "elevated", "email": "elevated@qualification.invalid", "password": PASSWORD,
                                               "invite": "", "administrator": True})
        receipt.record("registration-administrator-forgery", forge[0] == 400 and not user_row(service, "elevated"),
                       {"status": forge[0], "value": forge[1]}, "unknown authority field rejects before account creation")
        outcomes = parallel([lambda: anonymous.account("verify", {"token": token}),
                             lambda: another.account("verify", {"token": token}, node="B")])
        rows = user_row(service, "bob")
        receipt.record("verification-one-use-race", sum(code == 200 for code, _ in outcomes) == 1 and all(code in [200, 400, 403, 409] for code, _ in outcomes)
                       and rows[0]["verified"], {"outcomes": outcomes, "rows": rows}, "one atomic redemption, no replay activation or repeated side effects")
        bob_a, bob_b = client(service, rpc, "bob"), client(service, rpc, "bob", node="B")
        before_admin_probe = service.mailbox_snapshot()
        unprivileged_invite = bob_a.account("invite", {"email": "must-not-invite@qualification.invalid"})
        unprivileged_disable = bob_a.account("disable", {"username": "alice", "disabled": True}, node="B")
        receipt.record("ordinary-account-cannot-administer-accounts", unprivileged_invite[0] == 403 and unprivileged_disable[0] == 403 and not service.mailbox_delta(before_admin_probe) and not user_row(service, "alice")[0]["disabled"],
                       {"invitation": unprivileged_invite, "disable": unprivileged_disable}, "verified ordinary account cannot invite or disable accounts; CLI administrator remains active")
        begin_window(service, receipt, "sessions")
        bearer, credential_id = issue_token(bob_a, receipt, operations=["read"], label="logout-is-separate")
        single_logout = bob_a.request("A", "/_diamond/logout", {})
        bob_a.bootstrap("A")
        logged_out = bob_a.rpc("read", nominal("issue", "issue-red-one"))
        other_session = bob_b.rpc("read", nominal("issue", "issue-red-one"), node="B")
        api_after_single = bob_b.api("read", bearer, [nominal("issue", "issue-red-one")], node="B")
        receipt.record("single-logout-revokes-only-current-session", single_logout[0] == 200 and logged_out[0] == 403 and other_session[0] == 200 and api_after_single[0] == 200,
                       {"logout": single_logout, "current_cookie_read": logged_out, "other_session_status": other_session[0], "api_status": api_after_single[0]},
                       "logout ends its cookie session while another session and independently managed API token remain valid")
        bob_a = client(service, rpc, "bob")
        logout_all = bob_a.account("logout_all", {})
        after_logout = [bob.rpc("read", nominal("issue", "issue-red-one"), node=node)
                        for node, bob in [("A", bob_a), ("B", bob_b)]]
        api_after_logout = bob_b.api("read", bearer, [nominal("issue", "issue-red-one")], node="B")
        receipt.record("logout-all-sessions-only", logout_all[0] == 200 and all(status in [401, 403] for status, _ in after_logout) and api_after_logout[0] == 200,
                       {"logout": logout_all, "cookie_reads": after_logout, "api_status": api_after_logout[0]},
                       "both node sessions revoked; separately scoped API credential remains valid")
        bob_a, bob_b = client(service, rpc, "bob"), client(service, rpc, "bob", node="B")
        disable = admin.account("disable", {"username": "bob", "disabled": True}, node="B")
        disabled = [bob_a.rpc("read", nominal("issue", "issue-red-one"), node=node) for node in ["A", "B"]]
        disabled_api = [bob_a.api("read", bearer, [nominal("issue", "issue-red-one")], node=node) for node in ["A", "B"]]
        receipt.record("disable-revokes-all-node-credentials", disable[0] == 200 and all(code == 401 for code, _ in disabled)
                       and all(code == 401 for code, _ in disabled_api) and user_row(service, "bob")[0]["disabled"],
                       {"disable": disable, "cookie": disabled, "api": disabled_api}, "committed disable prevents new session and API authority on both nodes")
        failed_login = client(service, rpc).login("bob", PASSWORD, success=False)
        enable = admin.account("disable", {"username": "bob", "disabled": False})
        revived = [bob_a.api("read", bearer, [nominal("issue", "issue-red-one")], node=node) for node in ["A", "B"]]
        old_sessions = [bob.rpc("read", nominal("issue", "issue-red-one"), node=node) for node, bob in [("A", bob_a), ("B", bob_b)]]
        receipt.record("enable-does-not-resurrect", failed_login[0] == 403 and enable[0] == 200 and all(code == 401 for code, _ in revived)
                       and all(code == 401 for code, _ in old_sessions), {"disabled_login": failed_login, "enable": enable, "old_api": revived, "old_sessions": old_sessions},
                       "reenabled account requires fresh authentication; old epochs remain revoked")

        begin_window(service, receipt, "password-change")
        bob_a, bob_b = client(service, rpc, "bob"), client(service, rpc, "bob", node="B")
        bearer, credential_id = issue_token(bob_a, receipt, operations=["read"], label="password-change")
        before_hash = service.sql("SELECT password_hash FROM diamond_identity.users WHERE app=" + quote(app_id()) + " AND username='bob'")
        wrong = bob_a.account("change_password", {"current_password": "Wrong synthetic password 00!", "password": SECOND_PASSWORD})
        same_hash = service.sql("SELECT password_hash FROM diamond_identity.users WHERE app=" + quote(app_id()) + " AND username='bob'") == before_hash
        change = bob_a.account("change_password", {"current_password": PASSWORD, "password": SECOND_PASSWORD}, node="B")
        denied_sessions = [bob_a.rpc("read", nominal("issue", "issue-red-one"), node=node) for node in ["A", "B"]]
        denied_api = [bob_a.api("read", bearer, [nominal("issue", "issue-red-one")], node=node) for node in ["A", "B"]]
        old_password = client(service, rpc).login("bob", PASSWORD, success=False)
        bob_fresh = client(service, rpc, "bob", password=SECOND_PASSWORD, node="B")
        receipt.record("password-change-revokes-all", wrong[0] in [400, 403] and same_hash and change[0] == 200 and old_password[0] == 403
                       and all(code in [401, 403] for code, _ in denied_sessions) and all(code == 401 for code, _ in denied_api),
                       {"wrong_current": wrong, "hash_unchanged_on_wrong": same_hash, "change": change, "old_password": old_password,
                        "cookie_denials": denied_sessions, "api_denials": denied_api}, "current password proves change; successful change revokes both credential classes")

        begin_window(service, receipt, "recovery")
        bearer, _ = issue_token(bob_fresh, receipt, operations=["read"], label="password-recovery")
        before = service.mailbox_snapshot()
        recovery_known = anonymous.account("recover", {"email": "bob@qualification.invalid"}, node="B")
        recovery_secret = delivered(service, before, recipient="bob@qualification.invalid", purpose="recover", receipt=receipt)
        unknown_before = service.mailbox_snapshot()
        recovery_unknown = another.account("recover", {"email": "nobody@qualification.invalid"})
        still_valid = bob_fresh.api("read", bearer, [nominal("issue", "issue-red-one")])
        receipt.record("recovery-request-generic-nonmutating", recovery_known == recovery_unknown and recovery_known[0] == 200
                       and not service.mailbox_delta(unknown_before) and still_valid[0] == 200,
                       {"known": recovery_known, "unknown": recovery_unknown, "existing_api_status": still_valid[0]},
                       "same public acceptance; unknown recipient gets no delivery and requesting recovery revokes nothing")
        wrong_purpose = anonymous.account("verify", {"token": recovery_secret})
        reset = another.account("reset", {"token": recovery_secret, "password": PASSWORD}, node="B")
        replay = anonymous.account("reset", {"token": recovery_secret, "password": SECOND_PASSWORD})
        after_reset = [bob_fresh.api("read", bearer, [nominal("issue", "issue-red-one")], node=node) for node in ["A", "B"]]
        receipt.record("recovery-purpose-one-use-revocation", wrong_purpose[0] in [400, 403] and reset[0] == 200 and replay[0] in [400, 403]
                       and all(code == 401 for code, _ in after_reset),
                       {"wrong_purpose": wrong_purpose, "reset": reset, "replay": replay, "old_api": after_reset},
                       "purpose substitution denies without consuming valid recovery; single reset revokes old authority")
        client(service, rpc, "bob", password=PASSWORD)

        begin_window(service, receipt, "challenge-expiry-resend")
        before = service.mailbox_snapshot()
        carol_registered = anonymous.account("register", {"username": "carol", "email": "carol@qualification.invalid", "password": PASSWORD, "invite": ""})
        receipt.record("carol-pending-registration", carol_registered[0] == 200, {"status": carol_registered[0], "value": carol_registered[1]}, "fresh pending account accepted before expiry/resend checks")
        carol_expired = delivered(service, before, recipient="carol@qualification.invalid", purpose="verify", receipt=receipt)
        service.sql("UPDATE diamond_identity.challenges SET expires=clock_timestamp()-interval '1 second' WHERE app=" + quote(app_id()) + " AND email='carol@qualification.invalid'")
        expired = another.account("verify", {"token": carol_expired}, node="B")
        before = service.mailbox_snapshot()
        first_resend = anonymous.account("resend", {"email": "carol@qualification.invalid"})
        first_secret = delivered(service, before, recipient="carol@qualification.invalid", purpose="verify", receipt=receipt)
        before = service.mailbox_snapshot()
        second_resend = another.account("resend", {"email": "carol@qualification.invalid"}, node="B")
        second_secret = delivered(service, before, recipient="carol@qualification.invalid", purpose="verify", receipt=receipt)
        previous = anonymous.account("verify", {"token": first_secret})
        wrong_purpose = another.account("reset", {"token": second_secret, "password": SECOND_PASSWORD}, node="B")
        current = anonymous.account("verify", {"token": second_secret})
        unknown_before = service.mailbox_snapshot()
        resend_unknown = another.account("resend", {"email": "absent@qualification.invalid"}, node="B")
        receipt.record("database-expiry-and-resend-replacement", expired[0] in [400, 403] and previous[0] in [400, 403] and wrong_purpose[0] in [400, 403]
                       and current[0] == 200 and first_secret != second_secret and first_resend == second_resend == resend_unknown and not service.mailbox_delta(unknown_before),
                       {"expired": expired, "old_resend": previous, "wrong_purpose": wrong_purpose, "current": current,
                        "resend_shapes": [first_resend, second_resend, resend_unknown]},
                       "database-time expiry denies; resend replaces old secret; generic unknown response; wrong purpose cannot consume current verification")
        client(service, rpc, "carol")

        begin_window(service, receipt, "application-binding")
        foreign_name = "IndependentForeignAccounts"
        foreign = write(root / "foreign", app=foreign_name)
        service.cli("project-init", foreign, "--postgres-env", ENV_NAME, configuration=service.owner)
        service.ports["C"] = free_port()
        service.start_node("C", project=foreign)
        foreign_contract, _ = service.build(foreign)
        foreign_anonymous = client(service, foreign_contract["rpc_id"], node="C")
        before = service.mailbox_snapshot()
        anonymous.account("register", {"username": "dave", "email": "dave@qualification.invalid", "password": PASSWORD, "invite": ""})
        dave_secret = delivered(service, before, recipient="dave@qualification.invalid", purpose="verify", receipt=receipt)
        foreign_use = foreign_anonymous.account("verify", {"token": dave_secret}, node="C")
        correct_use = another.account("verify", {"token": dave_secret}, node="B")
        receipt.record("challenge-application-binding", foreign_use[0] in [400, 403] and correct_use[0] == 200
                       and user_row(service, "dave")[0]["verified"] and not user_row(service, "dave", application=app_id(foreign_name)),
                       {"wrong_app": foreign_use, "correct_app": correct_use}, "foreign app cannot redeem or consume another app's challenge")
        service.stop_node("C")
        before_restart = user_row(service, "bob")
        service.stop_node("B")
        service.start_node("B")
        bob_restarted = client(service, rpc, "bob", node="B")
        read_after_restart = bob_restarted.rpc("read", nominal("issue", "issue-red-one"), node="B")
        receipt.record("restart-keeps-committed-identity", user_row(service, "bob") == before_restart and read_after_restart[0] == 200,
                       {"account": user_row(service, "bob"), "read_status": read_after_restart[0]}, "fresh node preserves exact committed identity and its current password")
        begin_window(service, receipt, "session-expiry")
        expiry_bearer, _ = issue_token(bob_restarted, receipt, operations=["read"], label="session-expiry-separate")
        service.sql("UPDATE diamond_identity.sessions SET expires=clock_timestamp()-interval '1 second' WHERE app=" + quote(app_id()) + " AND username='bob'")
        expired_sessions = [bob_restarted.rpc("read", nominal("issue", "issue-red-one"), node=node) for node in ["A", "B"]]
        separate_api = bob_restarted.api("read", expiry_bearer, [nominal("issue", "issue-red-one")])
        receipt.record("session-database-expiry-is-separate-from-api", all(code == 401 for code, _ in expired_sessions) and separate_api[0] == 200,
                       {"expired_cookies": expired_sessions, "api_status": separate_api[0]}, "database-time session expiry denies both nodes without revoking separately expiring API credentials")
        hashes = service.sql("SELECT count(*) FROM diamond_identity.users WHERE password_hash=" + quote(PASSWORD) + " OR password_hash=" + quote(SECOND_PASSWORD))
        plaintext = service.sql("SELECT count(*) FROM diamond_identity.challenges WHERE digest IN (" + ','.join(quote(secret) for secret in receipt.secrets if secret not in [PASSWORD, SECOND_PASSWORD]) + ")")
        log_text = '\n'.join(log.name and Path(log.name).read_text() for log in service.logs)
        leaked = [receipt.secret(secret) for secret in list(receipt.secrets) if secret in log_text]
        receipt.record("database-and-logs-no-plaintext-secrets", hashes == "0" and plaintext == "0" and not leaked,
                       {"plaintext_password_rows": hashes, "plaintext_challenge_rows": plaintext, "leaked_secret_fingerprints": leaked}, "only digests/hashes persist; no delivered or password secrets in actual server logs")
    except Exception as failure:
        fatal = repr(failure)
    finally:
        try:
            service.close()
        except Exception as cleanup:
            fatal = (fatal or "") + " cleanup=" + repr(cleanup)
        report = receipt.report(service, sha256(binary), error=fatal, temporary_root=str(root),
                                scope="actual account lifecycle, challenge bindings, two-node epochs/revocation, actual private delivery and committed SQL observations")
        options.report.write_text(json.dumps(report, indent=2, ensure_ascii=True) + "\n")
        if report["status"] == "PASS":
            shutil.rmtree(root)
    print(json.dumps({"status": report["status"], "observations": len(receipt.results), "error": report["error"]}))
    return report["status"] != "PASS"


if __name__ == "__main__":
    raise SystemExit(main())
