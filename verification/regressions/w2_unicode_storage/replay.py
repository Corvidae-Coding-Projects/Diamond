#!/usr/bin/env python3
"""Fresh real CLI/HTTP/PostgreSQL reproduction for valid bounded NUL text.
Replay: python3 verification/regressions/w2_unicode_storage/replay.py [--binary PATH]
Expected: JSON text U+0000 fits Text<length:0..4000>, saves and reads exactly.
Observed on d2dc350f...: HTTP 503 unavailable, no record; next ordinary save works.
"""
from pathlib import Path
import argparse,hashlib,http.client,importlib.util,json,subprocess,tempfile
P=argparse.ArgumentParser();P.add_argument('--binary',type=Path,default=Path(__file__).resolve().parents[3]/'target/debug/diamond');A=P.parse_args()
spec=importlib.util.spec_from_file_location('fixture_only',str(Path(__file__).resolve().parents[3]/'verification/web_two.py'));m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);m.BINARY=A.binary
D=Path(tempfile.mkdtemp(prefix='diamond-w2-nul-replay-'));f=m.Fixture(D);cookie=None;csrf='';trace=[]
def req(path,body=None):
 global cookie,csrf
 h={'Host':f.authority}
 if cookie:h['Cookie']=cookie
 if path=='/_diamond/session':h['X-Diamond-Session']='1'
 if body is not None:h.update({'Origin':f.origin,'Content-Type':'application/json','X-Diamond-CSRF':csrf})
 raw=None if body is None else json.dumps(body,separators=(',',':')).encode()
 c=http.client.HTTPConnection('127.0.0.1',f.ports['A'],timeout=10)
 try:
  c.request('GET' if body is None else 'POST',path,raw,h);r=c.getresponse();v=json.loads(r.read());headers=dict(r.getheaders())
  if 'set-cookie' in headers:cookie=headers['set-cookie'].split(';')[0]
  if 'csrf' in v:csrf=v['csrf']
  return r.status,v
 finally:c.close()
try:
 f.start();assert req('/_diamond/session')[0]==200;assert req('/_diamond/login',{'username':'alice','password':m.PASSWORD})[0]==200
 before=f.sql('SELECT count(*) FROM diamond_w2.rows')
 payload={'contract':f.contract,'arguments':['w','t','\x00']};r=req('/_diamond/rpc/create_issue',payload);after=f.sql('SELECT count(*) FROM diamond_w2.rows');trace.append({'step':'minimal NUL save','request_body':json.dumps(payload,separators=(',',':')),'expected_status':200,'observed':r,'rows_before':before,'rows_after':after})
 if r[0]==200:
  read=req('/_diamond/rpc/read_issue',{'contract':f.contract,'arguments':[r[1]['value']]});stored=json.loads(f.sql("SELECT payload::text FROM diamond_w2.rows WHERE id='"+r[1]['value']+"'"));trace.append({'step':'fresh HTTP and raw SQL NUL readback','observed':read,'sql_details_codepoints':[ord(c) for c in stored['details']]});assert read==(200,{'value':'w | t\n\x00'}) and stored['details']=='\x00'
 control=req('/_diamond/rpc/create_issue',{'contract':f.contract,'arguments':['w','t','x']});assert control[0]==200;trace.append({'step':'ordinary control after failure','observed':control})
 dependency=subprocess.run(['psql',f.configuration,'-XAt','-v','ON_ERROR_STOP=1','-c',"SELECT '{\"text\":\"\\u0000\"}'::json::text"],text=True,capture_output=True);trace.append({'step':'PostgreSQL JSON representability','exit':dependency.returncode,'stderr':dependency.stderr,'stdout':dependency.stdout})
 report={'binary':str(A.binary),'sha256':hashlib.sha256(A.binary.read_bytes()).hexdigest(),'root':str(D),'trace':trace};(D/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2));raise SystemExit(0 if r[0]==200 else 1)
finally:f.close()
