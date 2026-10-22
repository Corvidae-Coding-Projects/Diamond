"""Real source fixture builder for independent storage-shape qualification."""
from pathlib import Path

MANIFEST = '''schema_version = 1
name = "IndependentRelationalSchema"
server = "server"
browser = "ui"
component = "App"
provides = ["DbRead<Rows>", "DbWrite<Rows>"]

[modules.server]
path = "server.dia"
target = "server"
provides = ["DbRead<Rows>", "DbWrite<Rows>"]

[modules.ui]
path = "ui.dia"
target = "browser"

[rpc]
echo = "server.echo"
create_row = "server.create_row"
read_row = "server.read_row"
edit_row = "server.edit_row"
delete_row = "server.delete_row"
list_rows = "server.list_rows"

[storage]
profile = "relational-v1"
version = 1

[storage.resources.Rows]
scope = "workspace"
indexes = [["workspace", "number", "id"]]
unique = [["workspace", "title"]]
'''

SERVER = '''type UserId = Id<"user">
type WorkspaceId = Id<"workspace">
type RowId = Id<"row">

enum Phase:
  Open
  Closed

type StoredRow:
  id: RowId
  owner: UserId @created_by_principal
  workspace: WorkspaceId
  title: Text<length: 1..80>
  number: Int
  active: Bool
  amount: Decimal
  stamp: Timestamp
  wait: Duration
  maybe: Option<Text<length: 0..80>>
  phase: Phase

resource Rows: StoredRow

type NewRow:
  workspace: WorkspaceId
  title: Text<length: 1..80>
  number: Int
  active: Bool
  amount: Decimal
  stamp: Timestamp
  wait: Duration
  maybe: Option<Text<length: 0..80>>
  phase: Phase

type EditRow:
  title: Text<length: 1..80>
  number: Int

policy Rows:
  read(row, by principal):
    principal.is_user(row.owner)
  create(row, by principal):
    principal.is_user(row.owner)
  update(before, after, by principal):
    principal.is_user(before.owner) and before.owner == after.owner and before.workspace == after.workspace
  delete(row, by principal):
    principal.is_user(row.owner)

export fn main() -> Text:
  "Independent relational schema"

export fn echo(value: Text) -> Text:
  value

export fn create_row(value: NewRow) -> Result<StoredRow, DataError>:
  rows.create(value)

export fn read_row(id: Text) -> Result<StoredRow, DataError>:
  rows.get(RowId(id))

export fn edit_row(id: Text, value: EditRow) -> Result<StoredRow, DataError>:
  rows.update(RowId(id), value)

export fn delete_row(id: Text) -> Result<Text, DataError>:
  rows.delete(RowId(id))?
  Ok("Deleted")

export fn list_rows(workspace: WorkspaceId, limit: Int) -> Result<List<StoredRow>, DataError>:
  rows.select(workspace: workspace, limit: limit)
'''

UI = '''export component App:
  html:
    <main><h1>Independent relational schema</h1></main>
'''


def write(root, manifest=MANIFEST, server=SERVER, ui=UI):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    for name, text in [("diamond.toml", manifest), ("server.dia", server), ("ui.dia", ui)]:
        (root / name).write_text(text)
    return root / "diamond.toml"
