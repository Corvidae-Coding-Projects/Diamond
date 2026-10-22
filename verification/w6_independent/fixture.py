"""Real Diamond business fixture derived from ordinary prior source syntax."""
from __future__ import annotations
import hashlib
import importlib.util
from pathlib import Path

path=Path(__file__).resolve().parents[1]/'w5_independent/fixture.py'
spec=importlib.util.spec_from_file_location('_w6_previous_fixture',path)
previous=importlib.util.module_from_spec(spec);spec.loader.exec_module(previous)
APP='IndependentFilesHTTP'
PASSWORD='Synthetic W6 qualification password 86!'
MEMBERSHIPS=previous.MEMBERSHIPS
PROJECTS=previous.PROJECTS
ISSUES=previous.ISSUES


def write(root,*,server_suffix='',manifest_suffix=''):
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    manifest=previous.MANIFEST.replace('name = "'+previous.APP+'"','name = "'+APP+'"')
    manifest=manifest.replace('"DbRead<Globals>"]','"DbRead<Globals>", "Http<inventory>", "Http<secondary>"]')
    manifest=manifest.replace('[identity]','external_get = "server.external_get"\nexternal_post = "server.external_post"\nauthorized_get = "server.authorized_get"\n\n[identity]')
    manifest += '\n[files]\nprofile = "shared-fs-v1"\n[files.bindings.issue_files]\nresource = "Issues"\nmax_bytes = 8388608\n'+manifest_suffix
    server=previous.SERVER+'''\nexport fn external_get(path: Text) -> Result<Text, DataError>:
  let credential = host.secret("inventory")
  host.http_get<Text>("inventory", path, credential)
export fn external_post(path: Text, body: Text) -> Result<Text, DataError>:
  let credential = host.secret("inventory")
  host.http_post<Text, Text>("inventory", path, credential, body)
export fn authorized_get(id: IssueId, path: Text) -> Result<Text, DataError>:
  let row = issues.get(id)?
  host.http_get<Text>("inventory", path, host.secret("inventory"))
'''+server_suffix
    for name,text in [('diamond.toml',manifest),('server.dia',server),('ui.dia','''export component App:
  html:
    <main><h1>Independent W6 files and HTTP fixture</h1></main>
''')]: (root/name).write_text(text)
    return root/'diamond.toml'


def app_id():return hashlib.sha256(APP.encode()).hexdigest()
def table(resource):return 'diamond_w4."r_'+app_id()[:16]+'_'+hashlib.sha256(resource.encode()).hexdigest()[:16]+'"'
