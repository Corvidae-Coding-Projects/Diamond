from pathlib import Path
import hashlib,json,os,shutil,subprocess,tempfile
D=Path(tempfile.mkdtemp(prefix='diamond-w2-independent-transitive-'));B=Path(os.environ['DIAMOND_W2_BINARY']);R=Path(__file__).resolve().parents[2];out=[]
p=D/'project';p.mkdir();server=(R/'examples/workspace/server.dia').read_text().split('export fn main()')[0]+'export fn main() -> Text:\n  "test"\n\nexport fn wrapper(w: Text<length: 1..64>, t: Text<length: 1..120>, d: Text<length: 0..4000>) -> Result<Text, DataError>:\n  helper_create(w, t, d)\n';(p/'server.dia').write_text(server);(p/'helper.dia').write_text('export fn helper_create(w: Text<length: 1..64>, t: Text<length: 1..120>, d: Text<length: 0..4000>) -> Result<Text, DataError>:\n  let issue = issues.create(NewIssue(workspace: w, title: t, details: d))?\n  Ok(id_text(issue.id))\n');(p/'ui.dia').write_text('export component UI:\n  state text: Text = "test"\n  html:\n    <p>{text}</p>\n')
m='''schema_version = 1
name = "Transitive"
server = "server"
browser = "ui"
component = "UI"
provides = ["DbRead<Issues>", "DbWrite<Issues>"]
[modules.server]
path = "server.dia"
target = "server"
imports = ["helper"]
provides = ["DbRead<Issues>", "DbWrite<Issues>"]
[modules.helper]
path = "helper.dia"
target = "server"
provides = ["DbRead<Issues>", "DbWrite<Issues>"]
[modules.ui]
path = "ui.dia"
target = "browser"
[rpc]
wrapped = "server.wrapper"
'''
for name,changed,accepted in [('full',m,True),('wrapper_without_write',m.replace('imports = ["helper"]\nprovides = ["DbRead<Issues>", "DbWrite<Issues>"]','imports = ["helper"]\nprovides = ["DbRead<Issues>"]'),False),('helper_without_write',m.replace('path = "helper.dia"\ntarget = "server"\nprovides = ["DbRead<Issues>", "DbWrite<Issues>"]','path = "helper.dia"\ntarget = "server"\nprovides = ["DbRead<Issues>"]'),False)]:
 (p/'diamond.toml').write_text(changed);c=[str(B),'check',str(p/'diamond.toml')];x=subprocess.run(c,text=True,capture_output=True);out.append(dict(case=name,command=c,status='PASS' if (x.returncode==0)==accepted else 'FAIL',exit=x.returncode,stdout=x.stdout,stderr=x.stderr));shutil.copy2(p/'diamond.toml',D/(name+'.toml'))
report=dict(root=str(D),binary_sha256=hashlib.sha256(B.read_bytes()).hexdigest(),results=out);(D/'report.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))

raise SystemExit(int(any(row["status"]!="PASS" for row in out)))
