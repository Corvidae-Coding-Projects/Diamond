#!/usr/bin/env python3
"""Own actual reference apps and their dependencies for Chrome acceptance."""
import argparse, hashlib, json, shutil, sys, tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'verification/w7_w8_independent'))
from service import Service,Client,ENV_NAME
from receiver import Receiver
PASSWORD='Synthetic W7 W8 browser password 88!'
def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--app',choices=['live-workspace','catalog'],required=True);parser.add_argument('--browser-host',choices=['127.0.0.1','localhost'],default='127.0.0.1');options=parser.parse_args()
    workspace=ROOT/'.diamond';workspace.mkdir(mode=0o700,exist_ok=True)
    root=Path(tempfile.mkdtemp(prefix='diamond-w7-w8-browser-',dir=workspace));s=None;receiver=None
    path=ROOT/f'verification/{options.app}_browser_results.json';report={'app':options.app,'observations':[],'binary_sha256':hashlib.sha256((ROOT/'target/debug/diamond').read_bytes()).hexdigest()}
    try:
        binary=root/'diamond';shutil.copy2(ROOT/'target/debug/diamond',binary);project=root/'project';shutil.copytree(ROOT/'examples'/options.app,project)
        s=Service(root,binary,project/'diamond.toml');s.ports['A']=39171;s.origin='http://'+options.browser_host+':39171';s.authority=options.browser_host+':39171'
        receiver=Receiver();s.runtime['http']={'inventory':{'origin':receiver.origin,'network':'loopback','paths':['/v1/'],'methods':['GET','POST']}}
        if options.app=='catalog':s.runtime.pop('objects')
        s.postgres();s.cli('project-init',s.project,'--postgres-env',ENV_NAME)
        if options.app!='catalog':s.files_cli(initialize=True)
        s.cli('project-admin-add',s.project,'--postgres-env',ENV_NAME,'--user','alice','--email','alice@browser.invalid','--password-stdin',input=PASSWORD+'\n')
        contract,_=s.build();s.start_node('A');s.start_node('B');c=Client(s,contract['rpc_id']);c.login('alice',PASSWORD)
        def rpc(name,*args,node='A'):
            status,value=c.rpc(name,*args,node=node)
            if status!=200:raise AssertionError((name,status,value))
            return value['value']
        if options.app=='catalog':report['article']=rpc('create_article','Private browser draft 雪','Hostile <script> remains literal text.')
        else:
            workspace=rpc('create_workspace','Browser workspace');project_id=rpc('create_project',workspace,'Live browser project');issue=rpc('create_issue',workspace,project_id,'Browser live issue','Before the background edit.')
            report.update(workspace=workspace,project=project_id,issue=issue)
        s.start_worker('one');s.start_worker('two');report.update(root=str(root),origin=s.origin,node_B_port=s.ports['B']);path.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report),flush=True)
        for line in sys.stdin:
            request=json.loads(line)
            if request.get('command')=='stop':break
            if request.get('command')=='rpc':print(json.dumps({'node':request.get('node','A'),'value':rpc(request['name'],*request.get('arguments',[]),node=request.get('node','A'))}),flush=True)
            if request.get('command')=='restart_A':
                before=__import__('time').monotonic();s.stop_node('A',kill=True);s.start_node('A')
                result={'restarted':'A','seconds':round(__import__('time').monotonic()-before,3)};report['observations'].append(result);path.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(result),flush=True)
            if request.get('command')=='logout_all':
                result=c.account('logout_all',{});report['observations'].append({'logout_all_status':result[0]});path.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({'status':result[0]}),flush=True)
            if request.get('command')=='relogin_fixture':
                result=c.login('alice',PASSWORD);print(json.dumps({'status':result[0]}),flush=True)
            if request.get('command')=='note':report['observations'].append(request['observation']);path.write_text(json.dumps(report,indent=2)+'\n');print('{"noted":true}',flush=True)
            if request.get('command')=='observe':
                app=hashlib.sha256(('LiveWorkspace' if options.app=='live-workspace' else 'PublishingCatalog').encode()).hexdigest()
                rows=s.sql_json("SELECT id,binding,parent,owner,state,attempts,fence,outcome FROM diamond_w7.jobs WHERE app='"+app+"'")
                result={'jobs':rows,'receiver':receiver.snapshot()};report['observations'].append(result);path.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(result),flush=True)
    finally:
        if s:s.close();report['shutdowns']=s.shutdowns;report['cleanup_errors']=s.cleanup_errors
        if receiver:report['receiver']=receiver.snapshot();receiver.close()
        shutil.rmtree(root);report['temporary_root_removed']=True
        verdicts=[row['status'] for row in report['observations'] if 'status' in row]
        report['status']='PASS' if verdicts and all(status=='PASS' for status in verdicts) and not report.get('cleanup_errors') else 'FAIL'
        path.write_text(json.dumps(report,indent=2)+'\n')
if __name__=='__main__':main()
