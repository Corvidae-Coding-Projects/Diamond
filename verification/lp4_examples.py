#!/usr/bin/env python3
"""Own native adapters and PostgreSQL for focused, unmodified LP4 example journeys."""
from __future__ import annotations
import argparse, hashlib, json, os, shutil, sys, tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'verification'))
from w5_independent.service import Client,Service,ENV_NAME,sha256
PASSWORD='LP4 example fixture password 91!'
def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('example',choices=['forge','workspace']);parser.add_argument('--port',type=int,default=39172);parser.add_argument('--binary',type=Path,default=ROOT/'target/debug/diamond');args=parser.parse_args()
    root=Path(tempfile.mkdtemp(prefix='diamond-lp4-example-'));binary=root/'diamond';shutil.copy2(args.binary,binary)
    project=ROOT/'examples'/('git-browser' if args.example=='forge' else 'live-workspace')/'diamond.toml'
    service=Service(root,binary,project);service.origin=f'http://127.0.0.1:{args.port}';service.authority=service.origin.removeprefix('http://');service.ports['A']=args.port
    runtime={'objects':str(root/'objects')};error=None;observations=[]
    for name in ['objects','git','delivery','operations']:(root/name).mkdir(mode=0o700)
    if args.example=='forge':
        runtime.update(git={'repos':{'root':str(root/'git'),'executable':'/usr/bin/git'}},delivery={'builds':{'root':str(root/'delivery'),'sandbox':'/usr/bin/systemd-run','limit':'/usr/bin/prlimit'}},operations={'control':{'root':str(root/'operations')}},oauth={'transaction_key_env':'LP4_OAUTH_KEY','google':{'client_id':'synthetic-google','client_secret_env':'LP4_GOOGLE_SECRET'},'github':{'client_id':'synthetic-github','client_secret_env':'LP4_GITHUB_SECRET'}})
        os.environ.update(LP4_OAUTH_KEY='K'*48,LP4_GOOGLE_SECRET='synthetic-google',LP4_GITHUB_SECRET='synthetic-github')
        binding,paths,methods='webhook',['/hooks/'],['POST']
    else:binding,paths,methods='inventory',['/v1/'],['GET','POST']
    runtime['http']={binding:{'origin':'http://127.0.0.1:9','network':'loopback','paths':paths,'methods':methods,'credential_env':'LP4_HTTP_SECRET','timeout_ms':1000,'max_response_bytes':65536,'max_redirects':0}}
    os.environ.update(LP4_RUNTIME=json.dumps(runtime),LP4_HTTP_SECRET='synthetic-http')
    service.extra_serve=('--runtime-env','LP4_RUNTIME','--identity-mailbox',str(service.mailbox))
    try:
        service.postgres();metadata,build=service.build();contract=metadata['rpc_id'];service.cli('project-init',project,'--postgres-env',ENV_NAME)
        for command in ['project-files-init']+(['project-git-init','project-delivery-init','project-ops-init'] if args.example=='forge' else []):service.cli(command,project,'--postgres-env',ENV_NAME,'--runtime-env','LP4_RUNTIME')
        service.cli('project-user-add',project,'--postgres-env',ENV_NAME,'--user','alice','--password-stdin',input=PASSWORD+'\n');service.start_node('A');client=Client(service,contract);client.login('alice',PASSWORD)
        seeded={}
        if args.example=='forge':
            def rpc(name,*values):
                status,reply=client.rpc(name,*values)
                if status!=200:raise AssertionError((name,status,reply))
                return reply['value']
            organization=rpc('create_organization','LP4 organization');alpha=rpc('create_repository',organization,'Alpha repository','First repository',False);target=rpc('create_repository',organization,'Zebra repository','Exact card target',False)
            issue_a=rpc('create_issue',target,'Forge alpha','First issue');issue_b=rpc('create_issue',target,'Forge target','Second issue');seeded={'organization':organization,'other_repository':alpha,'repository':target,'other_issue':issue_a,'issue':issue_b}
        app=hashlib.sha256(('DiamondForge' if args.example=='forge' else 'LiveWorkspace').encode()).hexdigest();table='diamond_w4."r_'+app[:16]+'_'+hashlib.sha256(b'Issues').hexdigest()[:16]+'"';body='body' if args.example=='forge' else 'details'
        def rows():return service.sql_json(f"SELECT id,convert_from(decode(title,'hex'),'UTF8') AS title,convert_from(decode({body},'hex'),'UTF8') AS body FROM {table} ORDER BY id")
        source_hashes={str(p.relative_to(ROOT)):sha256(p) for p in project.parent.iterdir() if p.is_file()}
        print(json.dumps({'ready':True,'example':args.example,'origin':service.origin,'root':str(root),'password':PASSWORD,'binary_sha256':sha256(binary),'contract':contract,'seeded':seeded}),flush=True)
        for line in sys.stdin:
            command=line.strip()
            if command=='stop':break
            if command=='observe':
                observed=rows();observations.append({'name':'independent-sql-readback','rows':observed});print(json.dumps({'rows':observed}),flush=True)
        print('{"status":"PASS"}',flush=True)
    except Exception as failure:error=repr(failure);print(json.dumps({'status':'FAIL','error':error}),flush=True)
    finally:
        service.close();report=ROOT/'verification/lp4'/f'{args.example}-fixture.json';report.parent.mkdir(parents=True,exist_ok=True);report.write_text(json.dumps({'status':'PASS' if error is None and not service.cleanup_errors else 'FAIL','error':error,'binary_sha256':sha256(binary),'driver_sha256':sha256(Path(__file__)),'source_hashes':locals().get('source_hashes',{}),'observations':observations,'shutdowns':service.shutdowns,'cleanup_errors':service.cleanup_errors},indent=2)+'\n');shutil.rmtree(root)
    return int(error is not None or bool(service.cleanup_errors))
if __name__=='__main__':raise SystemExit(main())
