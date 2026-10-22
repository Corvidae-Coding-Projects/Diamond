#!/usr/bin/env python3
from pathlib import Path
import hashlib,json,os,re,shutil,subprocess,tempfile
R=Path(__file__).resolve().parents[2]; B=Path(os.environ['DIAMOND_W2_BINARY']); D=Path(tempfile.mkdtemp(prefix='diamond-w2-independent-cli-')); results=[]
def fixture(name):
 p=D/name; shutil.copytree(R/'examples/workspace',p); return p
def edit(p,file,old,new):
 f=p/file;s=f.read_text();assert old in s;(f).write_text(s.replace(old,new))
def check(name,p,expect,message):
 c=[str(B),'check',str(p/'diamond.toml')];o=subprocess.run(c,text=True,capture_output=True,timeout=15); ok=(o.returncode==0)==expect and (message is None or message in o.stderr);results.append(dict(case=name,command=c,expected='accept' if expect else 'reject',status='PASS' if ok else 'FAIL',exit=o.returncode,stdout=o.stdout,stderr=o.stderr))
p=fixture('baseline');check('baseline checked project',p,True,None)
p=fixture('server-no-write');edit(p,'diamond.toml','imports = ["shared"]\nprovides = ["DbRead<Issues>", "DbWrite<Issues>"]','imports = ["shared"]\nprovides = ["DbRead<Issues>"]');check('server module lacks transitive write budget',p,False,'function effects exceed its module budget')
p=fixture('app-no-write');edit(p,'diamond.toml','provides = ["DbRead<Issues>", "DbWrite<Issues>"]','provides = ["DbRead<Issues>"]');check('application and module lack write budget',p,False,None)
p=fixture('module-exceeds-app');edit(p,'diamond.toml','provides = ["DbRead<Issues>", "DbWrite<Issues>"]','provides = ["DbRead<Issues>"]');f=p/'diamond.toml';s=f.read_text();i=s.index('[modules.server]');s=s[:i]+s[i:].replace('provides = ["DbRead<Issues>"]','provides = ["DbRead<Issues>", "DbWrite<Issues>"]',1);f.write_text(s);check('module grant exceeds application budget',p,False,'module host budget exceeds the application budget')
p=fixture('private-rpc');edit(p,'diamond.toml','create_issue = "server.create_issue"','create_issue = "server.private_probe"');check('RPC private target',p,False,'explicitly exported server function')
p=fixture('shared-rpc');edit(p,'diamond.toml','create_issue = "server.create_issue"','create_issue = "shared.heading"');check('RPC shared target',p,False,'explicitly exported server function')
p=fixture('browser-import-server');f=p/'diamond.toml';s=f.read_text();i=s.index('[modules.ui]');tail,count=re.subn(r'imports\s*=\s*\[[^\]]*\]', 'imports = ["shared", "parts", "server"]' if '[modules.parts]' in s else 'imports = ["shared", "server"]', s[i:], count=1);assert count==1;s=s[:i]+tail;f.write_text(s);check('browser imports server',p,False,'forbidden browser/server target boundary')
p=fixture('shared-grant');edit(p,'diamond.toml','target = "shared"','target = "shared"\nprovides = ["DbRead<Issues>"]');check('shared host authority',p,False,'browser/shared modules cannot receive host capabilities')
p=fixture('browser-grant');edit(p,'diamond.toml','target = "browser"','target = "browser"\nprovides = ["DbRead<Issues>"]');check('browser host authority',p,False,'browser/shared modules cannot receive host capabilities')
p=fixture('non-scalar-rpc');f=p/'server.dia';f.write_text(f.read_text()+'\nexport fn return_record() -> NewIssue:\n  NewIssue(workspace: "a", title: "b", details: "c")\n');f=p/'diamond.toml';f.write_text(f.read_text()+'record = "server.return_record"\n');check('wire-safe record RPC signature' if os.environ.get('DIAMOND_FRONTEND_PROFILE')=='w3' else 'non-scalar RPC signature',p,os.environ.get('DIAMOND_FRONTEND_PROFILE')=='w3',None if os.environ.get('DIAMOND_FRONTEND_PROFILE')=='w3' else 'support only Int, Bool and Text')
p=fixture('single-file-boundary');f=p/'shared.dia';s=f.read_bytes();f.write_bytes(s+b'#'+b'x'*(1048576-len(s)-2)+b'\n');check('exact one MiB source accepted',p,True,None)
p=fixture('single-file-over');f=p/'shared.dia';s=f.read_bytes();f.write_bytes(s+b'#'+b'x'*(1048576-len(s)-1)+b'\n');check('one MiB plus one source rejected',p,False,None)
p=fixture('aggregate-over');f=p/'diamond.toml';s=f.read_text();s=s.replace('imports = ["shared"]','imports = ["shared", "pad0", "pad1", "pad2", "pad3", "pad4", "pad5", "pad6", "pad7"]',1)
for i in range(8):
 s+=f'\n[modules.pad{i}]\npath = "pad{i}.dia"\ntarget = "shared"\n';(p/f'pad{i}.dia').write_bytes(b'#'+b'x'*(1048574)+b'\n')
f.write_text(s);check('aggregate source over eight MiB rejected',p,False,'project source bytes')
# Read-only command receives no connected runtime grant.
c=[str(B),'serve',str(R/'examples/workspace/diamond.toml'),'--listen','127.0.0.1:0'];o=subprocess.run(c,text=True,capture_output=True,timeout=15);results.append(dict(case='connected RPC server missing PostgreSQL grant',command=c,expected='reject',status='PASS' if o.returncode!=0 and 'explicit PostgreSQL grant' in o.stderr else 'FAIL',exit=o.returncode,stdout=o.stdout,stderr=o.stderr))
report=dict(binary_sha256=hashlib.sha256(B.read_bytes()).hexdigest(),root=str(D),results=results);(D/'report.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))

raise SystemExit(int(any(row["status"]!="PASS" for row in results)))
