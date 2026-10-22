"""Real account boundary helpers with explicit, fixed W5 response expectations."""
from __future__ import annotations
import json
from pathlib import Path
import re

from common import app_id, quote
from model import PASSWORD
from service import ENV_NAME, Client


def provision(service, username="alice", email=None, *, project=None):
    service.cli("project-admin-add", project or service.project, "--postgres-env", ENV_NAME,
                "--user", username, "--email", email or username + "@qualification.invalid",
                "--password-stdin", input=PASSWORD + "\n", configuration=service.owner)


def client(service, contract, username=None, *, password=PASSWORD, node="A"):
    result = Client(service, contract)
    if username is not None:
        result.login(username, password, node=node)
    else:
        status, value = result.bootstrap(node)
        if status != 200:
            raise AssertionError(("bootstrap", status, value))
    return result


def delivered(service, before, *, recipient, purpose, receipt):
    paths = service.mailbox_delta(before)
    if len(paths) != 1:
        raise AssertionError(("expected one real delivery", len(paths), purpose, recipient))
    path = paths[0]
    if not re.fullmatch(r"[0-9a-f]{64}\.json", path.name):
        raise AssertionError("delivery filename must not disclose its credential or recipient")
    value = json.loads(path.read_text())
    if set(value) != {"recipient", "purpose", "token", "profile"}:
        raise AssertionError(("explicit actual delivery shape", sorted(value)))
    if value["recipient"] != recipient or value["purpose"] != purpose or value["profile"] != "development-mailbox-v1":
        raise AssertionError(("delivery binding", value["recipient"], value["purpose"], value["profile"]))
    token = value["token"]
    if not isinstance(token, str) or not token:
        raise AssertionError("actual delivery must contain a one-use secret")
    receipt.secret(token)
    return token


def register(service, anonymous, username, *, node="A", receipt, email=None, invite=""):
    email = email or username + "@qualification.invalid"
    before = service.mailbox_snapshot()
    status, value = anonymous.account("register", {"username": username, "email": email,
                                                  "password": PASSWORD, "invite": invite}, node=node)
    receipt.record("register-accepted-" + username, status == 200 and set(value) == {"value"} and isinstance(value["value"], str),
                   {"status": status, "value": value}, "generic accepted Text response; no authority from registration")
    if status != 200:
        raise AssertionError(("registration setup failed", username, status))
    return delivered(service, before, recipient=email.lower(), purpose="verify", receipt=receipt)


def activate(service, anonymous, username, *, node="A", receipt):
    token = register(service, anonymous, username, node=node, receipt=receipt)
    status, value = anonymous.account("verify", {"token": token}, node="B" if node == "A" else "A")
    receipt.record("verification-activates-" + username, status == 200,
                   {"status": status, "value": value}, "one-use secret activates the exact pending account")
    if status != 200:
        raise AssertionError(("activation setup failed", username, status))


def issue_token(owner, receipt, *, operations, scopes=None, lifetime="3600", label="Independent synthetic credential", node="A"):
    status, value = owner.account("create_token", {"label": label, "operations": operations,
                                                 "scopes": scopes if scopes is not None else {"workspace": ["red"]},
                                                 "lifetime_seconds": lifetime}, node=node)
    okay = status == 200 and isinstance(value, dict) and set(value) == {"value", "id"}
    okay = okay and isinstance(value["value"], str) and re.fullmatch(r"dia_[0-9a-f]{64}", value["value"]) is not None
    okay = okay and isinstance(value["id"], str) and re.fullmatch(r"[0-9a-f]{64}", value["id"]) is not None
    if isinstance(value, dict) and isinstance(value.get("value"), str):
        receipt.secret(value["value"])
    receipt.record("issue-scoped-credential-" + label, okay,
                   {"status": status, "shape": sorted(value) if isinstance(value, dict) else None,
                    "id": value.get("id") if isinstance(value, dict) else None},
                   "one visible raw dia_ credential plus separate nominal metadata ID")
    if not okay:
        raise AssertionError(("credential setup failed", status, sorted(value) if isinstance(value, dict) else None))
    return value["value"], value["id"]


def user_row(service, username, *, application=None):
    return service.sql_json("SELECT username,email,verified,disabled,administrator,epoch,password_hash IS NOT NULL AS has_hash FROM diamond_identity.users "
                            "WHERE app=" + quote(application or app_id()) + " AND username=" + quote(username))


def begin_window(service, receipt, label, *, application=None):
    """Separate functional phases; never used inside the rate-qualification phase.

    This controlled fixture expires the owned application's rows using DB time.
    It avoids conflating lifecycle gates with rate-capacity tests. Receipts record
    it explicitly; no claim is made that wall time advanced or counters survived
    this intentional fixture intervention.
    """
    touched = service.sql("WITH changed AS (UPDATE diamond_identity.rates SET expires=clock_timestamp()-interval '1 second' WHERE app="
                          + quote(application or app_id()) + " RETURNING 1) SELECT count(*) FROM changed")
    receipt.record("controlled-functional-rate-window-" + label, True, {"expired_rows": int(touched)},
                   "owner-only fixture intervention between independent functional phases; rate survival is tested separately without it")
