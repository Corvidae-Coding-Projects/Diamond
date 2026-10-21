"""Replay the two discovered W1 failures through an explicitly supplied real CLI."""
from pathlib import Path
import hashlib,json,subprocess,sys
R=Path(__file__).resolve().parent
B=Path(sys.argv[1]).resolve() if len(sys.argv)>1 else R.parents[2]/'target/debug/diamond'
H=hashlib.sha256(B.read_bytes()).hexdigest()
cases=[('qualified-name-collision','run',True,'117\n'),('mangled-private-server','run',False,None),('mangled-private-browser','check',False,None),('mangled-missing-direct-import','run',False,None),('current-mangled-private-server','run',False,None),('current-mangled-private-browser','check',False,None),('current-mangled-missing-direct-import','run',False,None)]
failed=False
for name,command,success,expected in cases:
 p=subprocess.run([str(B),command,str(R/name/'diamond.toml')],capture_output=True,text=True,timeout=5)
 passed=(p.returncode==0)==success and (expected is None or p.stdout==expected)
 print(json.dumps(dict(fixture=name,pass_=passed,command=p.args,binary_sha256=H,returncode=p.returncode,stdout=p.stdout,stderr=p.stderr)))
 failed|=not passed
sys.exit(int(failed))
