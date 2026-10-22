#!/usr/bin/env python3
"""Own a disposable two-node W6 demo while the real browser exercises its UI."""
import argparse, hashlib, importlib.util, json, re, shutil, socket, sys, tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'verification/w6_independent'))
from service import Service,Client,ENV_NAME
from external import External
PASSWORD='Synthetic W6 browser password 86!'
def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--root',type=Path);parser.add_argument('--report',type=Path);options=parser.parse_args()
    if options.root:options.root.mkdir(mode=0o700)
    root=options.root or Path(tempfile.mkdtemp(prefix='diamond-w6-browser-'));service=None;outside=None
    report={'observations':[],'binary_sha256':hashlib.sha256((ROOT/'target/debug/diamond').read_bytes()).hexdigest()}
    path=options.report or ROOT/'verification/w6_browser_results.json'
    try:
        binary=root/'diamond';shutil.copy2(ROOT/'target/debug/diamond',binary)
        project=root/'project';shutil.copytree(ROOT/'examples/files-workspace',project)
        probe=socket.socket();probe.bind(('127.0.0.1',39171));probe.close()
        service=Service(root,binary,project/'diamond.toml');service.ports['A']=39171;service.origin='http://127.0.0.1:39171';service.authority='127.0.0.1:39171'
        outside=External(root/'external','W6_BROWSER_SYNTHETIC_TOKEN')
        service.environment={'DIAMOND_W6_BROWSER_TOKEN':'W6_BROWSER_SYNTHETIC_TOKEN'}
        service.runtime['http']={'inventory':{'origin':outside.origin,'network':'loopback','paths':['/v1/'],'methods':['GET','POST'],'credential_env':'DIAMOND_W6_BROWSER_TOKEN'}}
        service.postgres();service.cli('project-init',service.project,'--postgres-env',ENV_NAME);service.files_cli(initialize=True)
        service.cli('project-admin-add',service.project,'--postgres-env',ENV_NAME,'--user','alice','--email','alice@browser.invalid','--password-stdin',input=PASSWORD+'\n')
        contract,build=service.build();service.start_node('A');service.start_node('B');client=Client(service,contract['rpc_id']);client.login('alice',PASSWORD)
        def rpc(n,*args):
            status,value=client.rpc(n,*args)
            if status!=200:raise AssertionError((n,status,value))
            return value['value']
        workspace=rpc('create_workspace','W6 Browser workspace');project_id=rpc('create_project',workspace,'Attachments');issue=rpc('create_issue',workspace,project_id,'W6 Browser attachment','Only synthetic fixture data.')
        upload=root/'w6-browser-synthetic.txt';upload.write_bytes(b'Diamond W6 synthetic browser attachment\n'+bytes(range(256))*512)
        report.update({'root':str(root),'origin':service.origin,'issue':issue,'upload':str(upload),'upload_sha256':hashlib.sha256(upload.read_bytes()).hexdigest(),'length':upload.stat().st_size,'node_B_port':service.ports['B']})
        path.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report),flush=True)
        for line in sys.stdin:
            request=json.loads(line)
            if request.get('command')=='stop':break
            if request.get('command')=='observe':
                attachment=request['id'];assert re.fullmatch('[0-9a-f]{64}',attachment)
                app=hashlib.sha256(b'FilesWorkspace').hexdigest()
                rows=service.sql_json("SELECT id,binding,parent,owner,name,media,length,state,sha256 FROM diamond_w6.objects WHERE app='"+app+"' AND id='"+attachment+"'")
                status,headers,data=service.request('B','/_diamond/files/issue_files/'+attachment,headers={'Cookie':client.cookie})
                observation={'id':attachment,'rows':rows,'node_B_status':status,'node_B_sha256':hashlib.sha256(data).hexdigest(),'node_B_bytes':len(data),'headers':headers,'matches_uploaded_file':data==upload.read_bytes()}
                report['observations'].append(observation);path.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(observation),flush=True)
            if request.get('command')=='note':
                report['observations'].append(request['observation']);path.write_text(json.dumps(report,indent=2)+'\n');print('{"noted":true}',flush=True)
            if request.get('command')=='restart':
                for n in ['A','B']:service.stop_node(n)
                shutil.copy2(ROOT/'target/debug/diamond',binary)
                report['binary_sha256']=hashlib.sha256(binary.read_bytes()).hexdigest()
                for n in ['A','B']:service.start_node(n)
                path.write_text(json.dumps(report,indent=2)+'\n');print('{"restarted":true}',flush=True)
    finally:
        if service:service.close();report['shutdowns']=service.shutdowns;report['cleanup_errors']=service.cleanup_errors
        if outside:outside.close()
        report['cleaned_root']=str(root);shutil.rmtree(root);path.write_text(json.dumps(report,indent=2)+'\n')
if __name__=='__main__':main()
