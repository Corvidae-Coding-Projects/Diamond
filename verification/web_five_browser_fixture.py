#!/usr/bin/env python3
"""Owned real PostgreSQL and generated Diamond W5 UI; no substituted responses."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
sys.path.insert(0, str(Path(__file__).parent/'w5_independent'))
from service import Service, ENV_NAME, sha256
from common import restrict_runtime

options=argparse.ArgumentParser(description=__doc__)
options.add_argument('--gated',action='store_true',help='listen on39174 for the transparent browser delivery gate')
arguments=options.parse_args()
ROOT=Path(__file__).resolve().parents[1]
root=Path(tempfile.mkdtemp(prefix='diamond-w5-browser-'))
binary=root/'diamond'
shutil.copy2(ROOT/'target/debug/diamond',binary)
project=ROOT/'examples/accounts-workspace/diamond.toml'
service=Service(root,binary,project)
service.ports['A']=39174 if arguments.gated else 39171
service.origin='http://127.0.0.1:39171'
service.authority='127.0.0.1:39171'
service.extra_serve=('--identity-mailbox',str(service.mailbox))
password='Browser fixture password 51!'
app=hashlib.sha256(b'AccountsWorkspace').hexdigest()
try:
    service.postgres()
    contract,build=service.build()
    service.cli('project-init',project,'--postgres-env',ENV_NAME)
    service.cli('project-admin-add',project,'--postgres-env',ENV_NAME,'--user','alice','--email','alice@browser.invalid','--password-stdin',input=password+'\n')
    privileges=restrict_runtime(service)
    service.start_node('A')
    print(json.dumps({'ready':True,'root':str(root),'origin':service.origin,'binary_sha256':sha256(binary),'rpc_id':contract['rpc_id'],'app':app}),flush=True)
    for line in sys.stdin:
        command=line.strip()
        if command=='stop':break
        if command=='restart':
            service.stop_node('A')
            shutil.copy2(ROOT/'target/debug/diamond',binary)
            contract,build=service.build()
            service.start_node('A')
            print(json.dumps({'restarted':True,'binary_sha256':sha256(binary),'rpc_id':contract['rpc_id']}),flush=True)
        if command=='mail':
            messages=[]
            for file in sorted(service.mailbox.glob('*.json')):
                message=json.loads(file.read_text());messages.append({k:v for k,v in message.items() if k!='token'})
            print(json.dumps({'messages':messages}),flush=True)
        if command=='observe':
            rows=service.sql_json("SELECT username,email,verified,disabled,administrator,epoch FROM diamond_identity.users WHERE app='"+app+"' ORDER BY username")
            observations={'users':rows,'sessions':service.sql_json("SELECT username,count(*) AS count FROM diamond_identity.sessions WHERE app='"+app+"' GROUP BY username"),'credentials':service.sql_json("SELECT username,label,operations,revoked,epoch FROM diamond_identity.credentials WHERE app='"+app+"'"),'trace':service.trace}
            (ROOT/'verification/web_five_browser_database.json').write_text(json.dumps(observations,indent=2)+'\n')
            print(json.dumps(observations),flush=True)
        if command=='window':
            service.sql("UPDATE diamond_identity.rates SET expires=pg_catalog.clock_timestamp()-interval '1 second' WHERE app='"+app+"'")
            print(json.dumps({'rate_window':'expired by test operator'}),flush=True)
finally:
    service.close()
    receipt={'binary_sha256':sha256(binary),'cleanup_errors':service.cleanup_errors,'shutdowns':service.shutdowns}
    (ROOT/'verification/web_five_browser_lifecycle.json').write_text(json.dumps(receipt,indent=2)+'\n')
    shutil.rmtree(root)
