"""Contract-only W5 account, revocation and token attenuation reference state.

No production modules, SQL, HTTP responses or generated contract are imported.
The API driver compares current policy/credential intersections with this
reference state. Lifecycle sequences separately check explicit committed-state
requirements; they never treat responses as the expected state.
"""
from __future__ import annotations
from dataclasses import dataclass, field

SEED = 0xD1A05005
PASSWORD = "Synthetic W5 qualification password 73!"
SECOND_PASSWORD = "Replacement W5 qualification password 91!"


@dataclass
class Account:
    name: str
    email: str
    state: str = "pending"
    admin: bool = False
    session_epoch: int = 0
    api_epoch: int = 0


@dataclass(frozen=True)
class Challenge:
    application: str
    purpose: str
    recipient: str
    expires_at: int
    consumed: bool = False


@dataclass(frozen=True)
class Session:
    owner: str
    epoch: int
    expires_at: int
    revoked: bool = False


@dataclass(frozen=True)
class Credential:
    owner: str
    operations: frozenset[str]
    scopes: frozenset[str]
    epoch: int
    expires_at: int
    revoked: bool = False


@dataclass
class IdentityModel:
    accounts: dict[str, Account] = field(default_factory=dict)
    # These roles are independent fixture data, not a copy of production policy.
    roles: dict[tuple[str, str], str] = field(default_factory=lambda: {
        ("alice", "red"): "Editor", ("alice", "blue"): "Editor",
        ("bob", "red"): "Editor", ("carol", "red"): "Reader",
    })

    def enroll(self, name, email, *, policy="open", invited=False, admin=False):
        if admin or name in self.accounts or any(a.email == email for a in self.accounts.values()):
            return False
        if policy == "closed" or policy == "invite" and not invited:
            return False
        self.accounts[name] = Account(name, email)
        return True

    def challenge_allowed(self, challenge, *, application, purpose, recipient, now):
        return (not challenge.consumed and now < challenge.expires_at
                and challenge.application == application and challenge.purpose == purpose
                and challenge.recipient == recipient)

    def activate(self, name):
        account = self.accounts[name]
        if account.state != "pending":
            return False
        account.state = "active"
        return True

    def password_changed(self, name):
        account = self.accounts[name]
        account.session_epoch += 1
        account.api_epoch += 1

    def disable(self, name):
        self.accounts[name].state = "disabled"
        self.password_changed(name)

    def enable(self, name):
        self.accounts[name].state = "active"

    def logout_all(self, name):
        self.accounts[name].session_epoch += 1

    def session_allowed(self, session, now):
        account = self.accounts.get(session.owner)
        return bool(account and account.state == "active" and not session.revoked
                    and session.epoch == account.session_epoch and now < session.expires_at)

    def api_allowed(self, credential, operation, scope, now, *, mutate=False):
        account = self.accounts.get(credential.owner)
        role = self.roles.get((credential.owner, scope))
        return bool(account and account.state == "active" and not credential.revoked
                    and credential.epoch == account.api_epoch and now < credential.expires_at
                    and operation in credential.operations and scope in credential.scopes
                    and role in (["Editor"] if mutate else ["Reader", "Editor"]))

    def visible(self, credential, operation, rows, now):
        return [row for row in rows if self.api_allowed(credential, operation, row["workspace"], now)]

    def joined(self, credential, operation, rows, parents, now):
        visible = {row["id"]: row for row in self.visible(credential, operation, parents, now)
                   if row.get("public", True) or row.get("owner") == credential.owner}
        return [{"left": row, "right": visible[row["parent"]]}
                for row in self.visible(credential, operation, rows, now) if row["parent"] in visible]
