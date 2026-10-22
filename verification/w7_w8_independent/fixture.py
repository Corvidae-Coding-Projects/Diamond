"""Ordinary Diamond fixture; runtime behavior always comes from Diamond."""
from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path

source = Path(__file__).resolve().parents[1] / "w5_independent/fixture.py"
spec = importlib.util.spec_from_file_location("_w7_w8_previous_fixture", source)
previous = importlib.util.module_from_spec(spec)
spec.loader.exec_module(previous)
APP = "IndependentJobsLiveRecovery"
PASSWORD = "Synthetic W7 W8 qualification password 87!"
SHORT_IDS = False


def identifier(label):
    return label if SHORT_IDS else hashlib.sha256(("W7 independent fixture " + label).encode()).hexdigest()


MEMBERSHIPS = [(identifier(a), identifier(b), c, d) for a, b, c, d in previous.MEMBERSHIPS]
PROJECTS = [(identifier(a), identifier(b), c, d) for a, b, c, d in previous.PROJECTS]
ISSUES = [(identifier(a), identifier(b), identifier(c), d, e) for a, b, c, d, e in previous.ISSUES]


def short_ids():
    """Select legal bounded nominal IDs used by the retained original failure."""
    global SHORT_IDS
    SHORT_IDS = True
    MEMBERSHIPS[:] = previous.MEMBERSHIPS
    PROJECTS[:] = previous.PROJECTS
    ISSUES[:] = previous.ISSUES

EXTRA = '''
export fn submit(parent: Text, payload: Text, key: Text) -> Result<Text, DataError>:
  host.job_submit<Text>("notify", parent, payload, key)
export fn submit_change(parent: Text, payload: Text, key: Text) -> Result<Text, DataError>:
  host.job_submit<Text>("change", parent, payload, key)
export fn submit_abort_change(parent: Text, payload: Text, key: Text) -> Result<Text, DataError>:
  host.job_submit<Text>("abort_change", parent, payload, key)
export fn status(id: Text) -> Result<Text, DataError>:
  host.job_status("notify", id)
export fn cancel(id: Text) -> Result<Text, DataError>:
  host.job_cancel("notify", id)
export fn emit(parent: Text, payload: Text) -> Result<Text, DataError>:
  host.event_emit<Text>("issue_changes", parent, payload)
export fn edit_and_submit(parent: Text, payload: Text, key: Text) -> Result<Text, DataError>:
  let row = issues.get(IssueId(parent))?
  let changed = issues.update(row.id, IssueEdit(rank: row.rank + 1))?
  host.job_submit<Text>("notify", parent, payload, key)
export fn submit_and_abort(parent: Text, payload: Text, key: Text) -> Result<Text, DataError>:
  let row = issues.get(IssueId(parent))?
  let changed = issues.update(row.id, IssueEdit(rank: row.rank + 1))?
  let job = host.job_submit<Text>("notify", parent, payload, key)?
  let missing = issues.get(IssueId("absent independent rollback sentinel"))?
  Ok(job)
export fn notify(parent: Text, payload: Text) -> Result<Text, DataError>:
  let row = issues.get(IssueId(parent))?
  host.http_post<Text, Text>("inventory", "/v1/dedup/" + host.job_key(), host.secret("inventory"), payload)
export fn change(parent: Text, payload: Text) -> Result<Text, DataError>:
  let row = issues.get(IssueId(parent))?
  let changed = issues.update(row.id, IssueEdit(rank: row.rank + 1))?
  let event = host.event_emit<Text>("issue_changes", parent, payload)?
  Ok(payload)
export fn abort_change(parent: Text, payload: Text) -> Result<Text, DataError>:
  let row = issues.get(IssueId(parent))?
  let changed = issues.update(row.id, IssueEdit(rank: row.rank + 1))?
  let event = host.event_emit<Text>("issue_changes", parent, payload)?
  let missing = issues.get(IssueId("absent independent rollback sentinel"))?
  Ok(payload)
'''


def write(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    manifest = previous.MANIFEST.replace(previous.APP, APP)
    manifest = manifest.replace('"DbRead<Globals>"]',
        '"DbRead<Globals>", "Http<inventory>", "Jobs", "Events", "JobKey"]')
    exports = ["submit", "submit_change", "submit_abort_change", "status", "cancel", "emit", "edit_and_submit", "submit_and_abort"]
    manifest = manifest.replace("[identity]", "\n".join(
        f'{name} = "server.{name}"' for name in exports) + "\n[identity]")
    manifest += '''
[files]
profile = "shared-fs-v1"
[files.bindings.issue_files]
resource = "Issues"
max_bytes = 8388608
[jobs.notify]
function = "server.notify"
resource = "Issues"
grants = ["DbRead<Issues>", "DbWrite<Issues>", "Http<inventory>", "JobKey", "Events"]
max_attempts = 3
delivery = "deduplicated"
[jobs.change]
function = "server.change"
resource = "Issues"
grants = ["DbRead<Issues>", "DbWrite<Issues>", "Events"]
max_attempts = 3
delivery = "transactional"
[jobs.abort_change]
function = "server.abort_change"
resource = "Issues"
grants = ["DbRead<Issues>", "DbWrite<Issues>", "Events"]
max_attempts = 3
delivery = "transactional"
[events.issue_changes]
resource = "Issues"
payload = "Text"
'''
    (root / "diamond.toml").write_text(manifest)
    (root / "server.dia").write_text(previous.SERVER + EXTRA)
    (root / "ui.dia").write_text('''export component App:
  html:
    <main><h1>Independent jobs, live events and recovery</h1></main>
''')
    return root / "diamond.toml"


def app_id():
    return hashlib.sha256(APP.encode()).hexdigest()


def table(resource):
    return ('diamond_w4."r_' + app_id()[:16] + '_' +
            hashlib.sha256(resource.encode()).hexdigest()[:16] + '"')
