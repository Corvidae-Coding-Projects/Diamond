#!/usr/bin/env python3
"""Owned real PG and reference Diamond UI; no synthetic application responses."""
import json
from pathlib import Path
import shutil
import sys
import tempfile
sys.path.insert(0, str(Path(__file__).parent / 'w4_independent'))
from service import Service, Client, ENV_NAME, PASSWORD, sha256
from scalars import constrain_runtime

ROOT = Path(__file__).resolve().parents[1]
root = Path(tempfile.mkdtemp(prefix='diamond-w4-browser-'))
binary = root / 'diamond'
shutil.copy2(ROOT / 'target/debug/diamond', binary)
project = ROOT / 'examples/relational-workspace/diamond.toml'
service = Service(root, binary, project)
service.ports['A'] = 39171
service.origin = 'http://127.0.0.1:39171'
service.authority = '127.0.0.1:39171'
try:
    service.postgres()
    contract, build = service.build()
    service.cli('project-init', project, '--postgres-env', ENV_NAME)
    for user in ['alice','bob']:
        service.cli('project-user-add',project,'--postgres-env',ENV_NAME,'--user',user,'--password-stdin',input=PASSWORD+'\n')
    privileges, elevated = constrain_runtime(service)
    service.start_node('A')
    owner = Client(service, contract)
    owner.login('alice')
    def rpc(name,*args):
        status,body=owner.rpc(name,*args)
        if status!=200: raise AssertionError((name,status,body))
        return body['value']
    workspace=rpc('create_workspace','Browser workspace')
    project_id=rpc('create_project',workspace,'Browser project')
    for n in range(12): rpc('create_issue',workspace,project_id,f'Fixture issue {n:02d}','Independent setup for real pagination')
    print(json.dumps({'ready':True,'root':str(root),'origin':service.origin,'workspace':workspace,'project':project_id,'binary_sha256':sha256(binary),'contract':contract,'sources':{p.name:sha256(p) for p in project.parent.iterdir() if p.is_file()}}),flush=True)
    for line in sys.stdin:
        if line.strip()=='stop': break
        if line.strip()=='restart':
            service.stop_node('A')
            contract,build=service.build()
            service.start_node('A')
            print(json.dumps({'restarted':True,'contract':contract}),flush=True)
        if line.strip()=='observe':
            report={'status':json.loads(service.cli('project-status',project,'--postgres-env',ENV_NAME,configuration=service.owner).stdout),'audit':json.loads(service.cli('project-audit',project,'--postgres-env',ENV_NAME,configuration=service.owner).stdout),'trace':service.trace}
            (ROOT/'verification/web_four_browser_database.json').write_text(json.dumps(report,indent=2)+'\n')
            print(json.dumps(report),flush=True)
finally:
    service.close()
    receipt={'binary_sha256':sha256(binary),'cleanup_errors':service.cleanup_errors,'shutdowns':service.shutdowns,'logs':[{'name':Path(log.name).name,'text':Path(log.name).read_text()} for log in service.logs]}
    (ROOT/'verification/web_four_browser_lifecycle.json').write_text(json.dumps(receipt,indent=2)+'\n')
    shutil.rmtree(root)
