"""Independent ordinary-data oracle for W4; no production imports or SQL parsing."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import random

SEED = 0xD1A04004
WORKSPACES = tuple(f"workspace-{n}" for n in range(7))
GRANTS = {
    "alice": {"workspace-0": "reader", "workspace-1": "editor", "workspace-3": "admin"},
    "bob": {"workspace-0": "editor", "workspace-2": "reader", "workspace-4": "admin"},
    "carol": {"workspace-1": "reader", "workspace-5": "editor"},
    "outsider": {},
    None: {},
}


@dataclass(frozen=True)
class Issue:
    id: str
    workspace: str
    owner: str | None
    rank: int
    title: str
    optional: str | None
    mirror: str | None
    amount: Decimal
    timestamp: int
    parent: str | None


@dataclass(frozen=True)
class Project:
    id: str
    workspace: str
    owner: str
    public_to_members: bool


def projects():
    """A distinct parent resource keeps the declared relationship graph acyclic."""
    return [Project(f"project-{n:03d}", WORKSPACES[n % 7], ("alice", "bob", "carol")[n % 3], n % 4 != 0)
            for n in range(70)]


def corpus(count=100_003, seed=SEED):
    """Distinct deterministic values; sorting by rank alone must not be unique."""
    rng = random.Random(seed)
    texts = ("", "雪", "a\0b", "é", "e\u0301", "<script>literal</script>", "🐦\nline")
    rows = []
    for n in range(count):
        workspace = WORKSPACES[n % len(WORKSPACES)]
        rows.append(Issue(
            id=f"issue-{n:06d}", workspace=workspace,
            owner=(None, "alice", "bob", "carol")[n % 4],
            rank=rng.randrange(-41, 42), title=f"{texts[n % len(texts)]}:{n}",
            optional=None if n % 3 == 0 else texts[n % len(texts)],
            mirror=None if n % 5 == 0 else texts[n % len(texts)] if n % 11 == 0 else texts[(n + 3) % len(texts)],
            amount=Decimal((n % 2001) - 1000) / Decimal(1_000_000),
            timestamp=-1_234_567 + n * 97,
            parent=None if n % 5 == 0 else f"project-{n % 70:03d}",
        ))
    return rows


def read_allowed(principal, row, grants=GRANTS):
    return row.workspace in grants.get(principal, {}) and (not isinstance(row, Issue) or row.rank < 1000)


def write_allowed(principal, row, grants=GRANTS):
    return grants.get(principal, {}).get(row.workspace) in ("editor", "admin")


def authorized(rows, principal, workspace=None, predicate=lambda row: True, grants=GRANTS):
    return [row for row in rows if read_allowed(principal, row, grants)
            and (workspace is None or row.workspace == workspace) and predicate(row)]


def page(rows, principal, workspace=None, after=None, size=31, grants=GRANTS):
    selected = sorted(authorized(rows, principal, workspace, grants=grants),
                      key=lambda row: (row.rank, row.id))
    return [row for row in selected if after is None or (row.rank, row.id) > after][:size]


def aggregate(rows, principal, workspace=None, grants=GRANTS):
    visible = authorized(rows, principal, workspace, grants=grants)
    return {"count": len(visible), "sum_rank": sum(row.rank for row in visible),
            "sum_amount": format(sum((row.amount for row in visible), Decimal(0)), ".6f")}


def parent_read_allowed(principal, row, grants=GRANTS):
    return read_allowed(principal, row, grants) and (row.public_to_members or row.owner == principal
            or grants.get(principal, {}).get(row.workspace) == "admin")


def join_parents(rows, principal, workspace=None, grants=GRANTS):
    visible = authorized(rows, principal, workspace, grants=grants)
    parents = {row.id: row for row in projects() if parent_read_allowed(principal, row, grants)}
    return [(child, parents[child.parent]) for child in visible if child.parent in parents]


def revoke(grants, principal, workspace):
    result = {user: dict(scopes) for user, scopes in grants.items()}
    result[principal].pop(workspace, None)
    return result


def require_mutation(principal, before, after, parents, grants=GRANTS):
    """The fixture's chosen complete pre/post/return-read policy, not runtime code."""
    if not (write_allowed(principal, before, grants) and write_allowed(principal, after, grants)
            and read_allowed(principal, after, grants) and before.workspace == after.workspace
            and before.owner == after.owner):
        return False
    if after.parent is not None:
        parent = next((row for row in parents if row.id == after.parent), None)
        if parent is None or parent.workspace != after.workspace:
            return False
    return True
