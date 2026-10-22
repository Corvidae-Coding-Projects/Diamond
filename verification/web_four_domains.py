#!/usr/bin/env python3
"""Exercise real generated PostgreSQL field checks with rollback-only fixture writes."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--fixture-root',type=Path,required=True)
parser.add_argument('--report',type=Path,required=True)
args=parser.parse_args()
app=hashlib.sha256(b'RelationalWorkspace').hexdigest()[:16]
resource=hashlib.sha256(b'Issues').hexdigest()[:16]
table=f'diamond_w4.r_{app}_{resource}'
configuration=f'host={args.fixture_root}/socket port=55449 user=diamond_w4_runtime dbname=postgres'
def sql(statement):
    return subprocess.run(['psql',configuration,'-X','-q','-At','-v','ON_ERROR_STOP=1','-c',statement],capture_output=True,text=True,timeout=10)
row=sql(f'SELECT id,title FROM {table} ORDER BY id LIMIT 1')
if row.returncode: raise RuntimeError('fixture row unavailable')
row_id,before=row.stdout.strip().split('|',1)
cases=[('ordinary-ascii','61',True),('nul','00',True),('largest-scalar','f48fbfbf',True),('maximum-astral-bound',('😀'*120).encode().hex(),True),('combining-scalars',('e\u0301'*60).encode().hex(),True),('empty','',False),('odd-hex','1',False),('nonhex','zz',False),('uppercase-hex','4A',False),('overlong-nul','c080',False),('surrogate','eda080',False),('out-of-range','f4908080',False),('truncated','e282',False),('lone-continuation','80',False),('newline-tail','61\n',False),('carriage-return-tail','61\r',False),('over-bound',('😀'*121).encode().hex(),False)]
results=[]
for name,value,allowed in cases:
    literal="'"+value.replace("'","''")+"'"
    run=sql(f'BEGIN; UPDATE {table} SET title={literal} WHERE id=\'{row_id}\'; ROLLBACK;')
    okay=(run.returncode==0)==allowed
    after=sql(f"SELECT title FROM {table} WHERE id='{row_id}'")
    unchanged=after.returncode==0 and after.stdout.strip()==before
    results.append({'name':name,'status':'PASS' if okay and unchanged else 'FAIL','expected_allowed':allowed,'observed_exit':run.returncode,'unchanged':unchanged,'stderr':run.stderr})
report={'status':'PASS' if all(r['status']=='PASS' for r in results) else 'FAIL','binary_sha256':hashlib.sha256((args.fixture_root/'diamond').read_bytes()).hexdigest(),'cases':results,'scope':'actual generated database CHECK constraints, runtime DML role, rollback-only writes in owned isolated fixture; no application SQL API'}
args.report.write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({'status':report['status'],'cases':len(results),'failed':[r['name'] for r in results if r['status']=='FAIL']}))
raise SystemExit(report['status']!='PASS')
