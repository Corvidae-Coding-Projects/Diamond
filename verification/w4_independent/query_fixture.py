"""Independent real Diamond workspace fixture; no generated expected responses."""
from pathlib import Path

MANIFEST = '''schema_version = 1
name = "IndependentRelationalQueries"
server = "server"
browser = "ui"
component = "App"
provides = ["DbRead<Issues>", "DbWrite<Issues>", "DbRead<Projects>"]

[modules.server]
path = "server.dia"
target = "server"
provides = ["DbRead<Issues>", "DbWrite<Issues>", "DbRead<Projects>"]
[modules.ui]
path = "ui.dia"
target = "browser"

[rpc]
page = "server.page"
cursor = "server.cursor"
count = "server.count"
sum_rank = "server.sum_rank"
filtered = "server.filtered"
optional_rows = "server.optional_rows"
joined = "server.joined"
read_issue = "server.read_issue"
edit_issue = "server.edit_issue"
increment = "server.increment"
write_then_hidden = "server.write_then_hidden"
delete_issue = "server.delete_issue"

[storage]
profile = "relational-v1"
version = 1
[storage.resources.Memberships]
scope = "workspace"
unique = [["workspace", "principal"]]
[storage.resources.Projects]
scope = "workspace"
unique = [["workspace", "id"]]
[storage.resources.Issues]
scope = "workspace"
indexes = [["workspace", "rank", "id"], ["workspace", "title", "id"], ["workspace", "optional", "id"]]
unique = [["workspace", "id"]]
[storage.resources.Issues.references.project]
resource = "Projects"
fields = {workspace = "workspace", parent = "id"}
[storage.lookups.member]
resource = "Memberships"
scope = "workspace"
principal = "principal"
role = "role"
purpose = "Decide workspace membership and role without disclosing membership rows"
[storage.joins.issue_project]
resource = "Issues"
reference = "project"
output = "server.IssueProject"
'''

SERVER = '''type UserId = Id<"user">
type WorkspaceId = Id<"workspace">
type MembershipId = Id<"membership">
type ProjectId = Id<"project">
type IssueId = Id<"issue">

enum Role:
  Reader
  Editor
  Admin

type Membership:
  id: MembershipId
  workspace: WorkspaceId
  principal: UserId
  role: Role

type Project:
  id: ProjectId
  workspace: WorkspaceId
  owner: UserId
  public_to_members: Bool

type Issue:
  id: IssueId
  workspace: WorkspaceId
  owner: Option<UserId>
  rank: Int
  title: Text<length: 1..120>
  optional: Option<Text<length: 0..80>>
  mirror: Option<Text<length: 0..80>>
  amount: Decimal
  timestamp: Timestamp
  parent: Option<ProjectId>

type EditIssue:
  rank: Int
  title: Text<length: 1..120>
  workspace: WorkspaceId
  parent: Option<ProjectId>

type RankEdit:
  rank: Int

type IssueProject:
  left: Issue
  right: Project

resource Memberships: Membership
resource Projects: Project
resource Issues: Issue

policy Memberships:
  read(row, by principal):
    false

policy Projects:
  read(project, by principal):
    principal.is_related("member", project.workspace) and (project.public_to_members or principal.is_user(project.owner) or principal.has_role_in("member", project.workspace, .Admin))

policy Issues:
  read(issue, by principal):
    principal.is_related("member", issue.workspace) and issue.optional == issue.optional and not (issue.owner != issue.owner) and (issue.optional == issue.mirror or issue.optional != issue.mirror) and issue.rank < 1000
  create(issue, by principal):
    principal.has_role_in("member", issue.workspace, .Editor) or principal.has_role_in("member", issue.workspace, .Admin)
  update(before, after, by principal):
    (principal.has_role_in("member", before.workspace, .Editor) or principal.has_role_in("member", before.workspace, .Admin)) and before.workspace == after.workspace and before.owner == after.owner
  delete(issue, by principal):
    principal.has_role_in("member", issue.workspace, .Editor) or principal.has_role_in("member", issue.workspace, .Admin)

export fn main() -> Text:
  "Independent relational query application"

export fn page(workspace: WorkspaceId, after: Text, size: Int, descending: Bool) -> Result<List<Issue>, DataError>:
  issues.page(workspace: workspace, order: "rank", descending: descending, after: after, limit: size)

export fn cursor(row: Issue, descending: Bool) -> Result<Text, DataError>:
  issues.cursor(row: row, order: "rank", descending: descending)

export fn count(workspace: WorkspaceId) -> Result<Int, DataError>:
  issues.count(workspace: workspace)

export fn sum_rank(workspace: WorkspaceId) -> Result<Int, DataError>:
  issues.sum(workspace: workspace, field: "rank")

export fn filtered(workspace: WorkspaceId, low: Int, high: Int) -> Result<List<Issue>, DataError>:
  issues.select(workspace: workspace, rank__gte: low, rank__lt: high, limit: 100)

export fn optional_rows(workspace: WorkspaceId, value: Option<Text<length: 0..80>>) -> Result<List<Issue>, DataError>:
  issues.select(workspace: workspace, optional: value, limit: 100)

export fn joined(workspace: WorkspaceId, after: Text, size: Int) -> Result<List<IssueProject>, DataError>:
  issues.join(relation: "issue_project", workspace: workspace, order: "rank", after: after, limit: size)

export fn read_issue(id: Text) -> Result<Issue, DataError>:
  issues.get(IssueId(id))

export fn edit_issue(id: Text, value: EditIssue) -> Result<Issue, DataError>:
  issues.update(IssueId(id), value)

export fn increment(id: Text) -> Result<Issue, DataError>:
  let row = issues.get(IssueId(id))?
  issues.update(row.id, RankEdit(rank: row.rank + 1))

export fn write_then_hidden(id: Text) -> Result<Issue, DataError>:
  let row = issues.get(IssueId(id))?
  let changed = issues.update(row.id, RankEdit(rank: row.rank + 1))?
  issues.update(changed.id, RankEdit(rank: 1001))

export fn delete_issue(id: Text) -> Result<Text, DataError>:
  issues.delete(IssueId(id))?
  Ok("Deleted")
'''

UI = '''export component App:
  html:
    <main><h1>Independent W4 workspace query fixture</h1></main>
'''


def write(root, manifest=MANIFEST, server=SERVER):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    for name, content in [("diamond.toml", manifest), ("server.dia", server), ("ui.dia", UI)]:
        (root / name).write_text(content)
    return root / "diamond.toml"
