#!/usr/bin/env python3
"""Build and execute an independent external W6 native consumer."""
from __future__ import annotations
import argparse,json,shutil,subprocess,tempfile,time
from pathlib import Path
from common import Receipt,client,objects,provision,quote,seed
from fixture import PASSWORD,app_id,write
from external import External
from service import ENV_NAME,Service,sha256
PREFIX='/_diamond/files/issue_files/'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ['binary','library','dependencies','report']:parser.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args();root=Path(tempfile.mkdtemp(prefix='diamond-w6-independent-native-'))
    binary=root/'diamond';library=root/'libdiamond.rlib';exe=root/'native-consumer'
    shutil.copy2(args.binary,binary);shutil.copy2(args.library,library)
    service=Service(root,binary,write(root/'project'));receipt=Receipt();receipt.secret(PASSWORD);fatal=None;external=None
    def native(mode):
        return subprocess.run([str(exe),mode,str(service.project),str(service.mailbox),f"127.0.0.1:{service.ports['A']}",service.origin],env=service.process_env(),capture_output=True,text=True,timeout=30)
    try:
        compile=subprocess.run(['rustc','--edition','2024','--crate-name','w6_independent_native',str(Path(__file__).with_suffix('.rs')),'--extern','diamond='+str(library),'-L','dependency='+str(args.dependencies.resolve()),'-o',str(exe)],capture_output=True,text=True,timeout=60)
        if compile.returncode:raise AssertionError(('external Rust consumer compile',compile.stderr))
        external=External(root/'external','W6_native_synthetic')
        grant={'origin':external.origin,'network':'loopback','paths':['/v1/'],'methods':['GET','POST']}
        service.runtime['http']={name:grant for name in ['inventory','secondary']}
        service.postgres();contract,_=service.build();service.cli('project-init',service.project,'--postgres-env',ENV_NAME)
        initialized=native('initialize')
        receipt.record('native-maintain-initialize',initialized.returncode==0 and json.loads(initialized.stdout)=={'initialized':True},{'exit':initialized.returncode,'stdout':initialized.stdout,'stderr':initialized.stderr},'public native maintain_files initializes actual shared directory binding')
        provision(service,'alice');seed(service)
        log=(root/'native-server.log').open('w');service.logs.append(log);service.node_logs['A']=Path(log.name)
        service.nodes['A']=subprocess.Popen([str(exe),'serve',str(service.project),str(service.mailbox),f"127.0.0.1:{service.ports['A']}",service.origin],env=service.process_env(),stdout=log,stderr=log)
        deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            try:
                if service.request('A','/')[0]==200:break
            except OSError:pass
            if service.nodes['A'].poll() is not None:raise AssertionError(('native serving exited',Path(log.name).read_text()))
            time.sleep(.025)
        else:raise AssertionError('native serving readiness deadline')
        owner=client(service,contract['rpc_id'],'alice')
        remote=owner.rpc('external_get','/v1/ok');reservation=owner.request('A',PREFIX+'begin',{'parent':'issue-red-one','filename':'native.bin','length':'4','media_type':'application/octet-stream'})
        identifier=reservation[1]['id'];secret=reservation[1]['upload'];receipt.secret(secret)
        put=service.transfer('A',owner,PREFIX+identifier,b'\0\xffok',upload=secret)
        finish=owner.request('A',PREFIX+identifier+'/finish',{}, {'X-Diamond-Upload':secret})
        got=service.request('A',PREFIX+identifier,headers={'Cookie':owner.cookie})
        receipt.record('native-serve-runtime-actual-http-and-files',remote==(200,{'value':'external exact 雪\0'}) and put[0]==200 and finish[0]==200 and got[2]==b'\0\xffok',{'remote':remote,'put_status':put[0],'finish':finish,'download_length':len(got[2]),'events':external.events},'external native RuntimeGrants and serve_with_runtime expose real typed outbound call and shared upload')
        owner.request('A',PREFIX+identifier+'/delete',{})
        clean=native('clean')
        receipt.record('native-bounded-clean',clean.returncode==0 and not objects(service,identifier),{'exit':clean.returncode,'stdout':clean.stdout,'rows':objects(service,identifier)},'native maintenance removes actual deleted metadata and bytes')
        service.stop_node('A')
        saved=json.loads(json.dumps(service.runtime))
        invalids=[('missing-destinations',{'objects':str(service.objects),'http':{}}),('unknown-field',saved|{'unknown':'denied'}),('duplicate-config','{"http":{},"http":{}}')]
        for name,value in invalids:
            if isinstance(value,str):
                old=service.process_env
                service.process_env=lambda:old()|{'DIAMOND_W6_INDEPENDENT_RUNTIME':value}
            else:service.runtime=value
            refused=native('serve')
            receipt.record('native-config-'+name,refused.returncode!=0,{'exit':refused.returncode,'stderr':refused.stderr},'missing/ambiguous native grants fail closed before listening')
            if isinstance(value,str):service.process_env=old
            service.runtime=json.loads(json.dumps(saved))
    except Exception as error:fatal=repr(error)
    finally:
        try:service.close()
        except Exception as error:fatal=(fatal or '')+' cleanup='+repr(error)
        if external:
            try:external.close()
            except Exception as error:fatal=(fatal or '')+' external='+repr(error)
        result=receipt.report(service,sha256(binary),error=fatal,library_sha256=sha256(library),native_sha256=sha256(exe) if exe.exists() else None,temporary_root=str(root))
        args.report.write_text(json.dumps(result,indent=2)+'\n')
        if result['status']=='PASS':shutil.rmtree(root)
    print(json.dumps({'status':result['status'],'observations':len(receipt.results),'error':fatal}));return result['status']!='PASS'
if __name__=='__main__':raise SystemExit(main())
