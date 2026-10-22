"""Independent Diamond business fixture, with ordinary real database seed data."""
from pathlib import Path
import hashlib

APP = "IndependentAccountsAPI"
APPLICATION_CANARY = "W5_SERVER_ONLY_CANARY_9cb3e0a4"
OPERATIONS = ["read", "page", "count", "joined", "create", "move", "edit", "delete"]
MANIFEST = '''schema_version = 1
name = "IndependentAccountsAPI"
server = "server"
browser = "ui"
component = "App"
provides = ["DbRead<Issues>", "DbWrite<Issues>", "DbRead<Projects>", "DbRead<Globals>"]
[modules.server]
path = "server.dia"
target = "server"
provides = ["DbRead<Issues>", "DbWrite<Issues>", "DbRead<Projects>", "DbRead<Globals>"]
[modules.ui]
path = "ui.dia"
target = "browser"
[rpc]
read = "server.read"
page = "server.page"
count = "server.count"
joined = "server.joined"
create = "server.create"
move = "server.move"
edit = "server.edit"
delete = "server.delete"
write_then_hidden = "server.write_then_hidden"
codec = "server.codec"
[identity]
profile = "accounts-v1"
enrollment = "open"
[storage]
profile = "relational-v1"
version = 1
[storage.resources.Globals]
[storage.resources.Memberships]
scope = "workspace"
unique = [["workspace", "user"]]
[storage.resources.Projects]
scope = "workspace"
unique = [["workspace", "id"]]
[storage.resources.Issues]
scope = "workspace"
indexes = [["workspace", "rank", "id"]]
[storage.resources.Issues.references.project]
resource = "Projects"
fields = { workspace = "workspace", project = "id" }
[storage.lookups.member]
resource = "Memberships"
scope = "workspace"
principal = "user"
role = "role"
purpose = "Authorize workspace operations without a general membership directory read grant"
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

type GlobalId = Id<"global_item">

type Global:
  id: GlobalId
  label: Text<length: 1..80>

enum Role:
  Reader
  Editor

type Membership:
  id: MembershipId
  workspace: WorkspaceId
  user: UserId
  role: Role

type Project:
  id: ProjectId
  workspace: WorkspaceId
  owner: UserId
  public: Bool

type Issue:
  id: IssueId
  workspace: WorkspaceId
  project: ProjectId
  rank: Int
  title: Text<length: 1..100>

type NewIssue:
  workspace: WorkspaceId
  project: ProjectId
  rank: Int
  title: Text<length: 1..100>

type IssueEdit:
  rank: Int

type IssueMove:
  workspace: WorkspaceId
  project: ProjectId

type IssueProject:
  left: Issue
  right: Project

enum Status:
  Ready
  Detail(detail: Text)

type Codec:
  number: Int
  text: Text<length: 0..80>
  active: Bool
  amount: Decimal
  timestamp: Timestamp
  duration: Duration
  workspace: WorkspaceId
  optional: Option<Text>
  status: Status

resource Globals: Global
resource Memberships: Membership
resource Projects: Project
resource Issues: Issue

policy Globals:
  read(row, by principal):
    principal.authenticated

policy Memberships:
  read(row, by principal):
    false

policy Projects:
  read(project, by principal):
    principal.is_related("member", project.workspace) and (project.public or principal.is_user(project.owner))

policy Issues:
  read(issue, by principal):
    principal.is_related("member", issue.workspace) and issue.rank < 1000
  create(issue, by principal):
    principal.has_role_in("member", issue.workspace, .Editor)
  update(before, after, by principal):
    principal.has_role_in("member", before.workspace, .Editor) and principal.has_role_in("member", after.workspace, .Editor)
  delete(issue, by principal):
    principal.has_role_in("member", issue.workspace, .Editor)

export fn main() -> Text:
  "Independent W5 actual application"

export fn global_count() -> Result<Int, DataError>:
  globals.count()

export fn public_value() -> Text:
  "Public declared read"

export fn hidden() -> Text:
  "W5_SERVER_ONLY_CANARY_9cb3e0a4"

export fn codec(value: Codec) -> Codec:
  value

export fn read(id: IssueId) -> Result<Issue, DataError>:
  issues.get(id)

export fn page(workspace: WorkspaceId, after: Text) -> Result<List<Issue>, DataError>:
  issues.page(workspace: workspace, order: "rank", after: after, limit: 2)

export fn count() -> Result<Int, DataError>:
  issues.count()

export fn joined(workspace: WorkspaceId) -> Result<List<IssueProject>, DataError>:
  issues.join(relation: "issue_project", workspace: workspace, order: "rank", limit: 100)

export fn create(value: NewIssue) -> Result<Issue, DataError>:
  issues.create(value)

export fn move(id: IssueId, value: IssueMove) -> Result<Issue, DataError>:
  issues.update(id, value)

export fn edit(id: IssueId, value: IssueEdit) -> Result<Issue, DataError>:
  issues.update(id, value)

export fn delete(id: IssueId) -> Result<Text, DataError>:
  issues.delete(id)?
  Ok("Deleted")

export fn write_then_hidden(id: IssueId) -> Result<Issue, DataError>:
  let row = issues.get(id)?
  let changed = issues.update(row.id, IssueEdit(rank: row.rank + 1))?
  issues.update(changed.id, IssueEdit(rank: 1001))
'''

UI = '''export component App:
  html:
    <main><h1>Independent W5 account and API fixture</h1></main>
'''

MEMBERSHIPS = [("member-alice-red", "red", "alice", "Editor"),
               ("member-alice-blue", "blue", "alice", "Editor"),
               ("member-bob-red", "red", "bob", "Editor"),
               ("member-carol-red", "red", "carol", "Reader")]
PROJECTS = [("project-red-visible", "red", "alice", True),
            ("project-red-hidden", "red", "bob", False),
            ("project-blue-visible", "blue", "alice", True),
            ("project-private-visible", "private", "outsider", True)]
ISSUES = [("issue-red-one", "red", "project-red-visible", 11, "red exact 雪"),
          ("issue-red-two", "red", "project-red-hidden", 12, "red hidden parent"),
          ("issue-blue-one", "blue", "project-blue-visible", 21, "blue exact NUL\0"),
          ("issue-private-one", "private", "project-private-visible", 31, "private exact")]


def table(resource, app=APP):
    return 'diamond_w4."r_' + hashlib.sha256(app.encode()).hexdigest()[:16] + '_' + hashlib.sha256(resource.encode()).hexdigest()[:16] + '"'


def api_config():
    value = "[http_api]\nversion = 1\n"
    for operation in [*OPERATIONS, "write_then_hidden", "codec", "public_value"]:
        auth = "public" if operation == "public_value" else "token"
        value += f'[http_api.operations.{operation}]\nfunction = "server.{operation}"\nauth = "{auth}"\n'
    value += '[http_api.operations.global_count_allowed]\nfunction = "server.global_count"\nauth = "token"\nallow_unscoped = true\n'
    return value


def write(root, *, api=None, enrollment="open", app=APP):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    api = api_config() if api is None else api
    manifest = MANIFEST.replace('name = "' + APP + '"', 'name = "' + app + '"').replace('enrollment = "open"', 'enrollment = "' + enrollment + '"')
    for name, content in [("diamond.toml", manifest + "\n" + api), ("server.dia", SERVER), ("ui.dia", UI)]:
        (root / name).write_text(content)
    return root / "diamond.toml"
