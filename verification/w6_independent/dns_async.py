#!/usr/bin/env python3
"""Actual independent DNS/TLS responses, deadlines and complete-address denial."""
import argparse,json,shutil,tempfile,time
from pathlib import Path
from common import Receipt,client,provision
from fixture import PASSWORD,write
from external import External
from resolver import DNS
from service import ENV_NAME,Service,sha256


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--binary',type=Path,required=True);p.add_argument('--report',type=Path,required=True);a=p.parse_args()
    root=Path(tempfile.mkdtemp(prefix='diamond-w6-independent-async-dns-'));binary=root/'diamond';shutil.copy2(a.binary,binary)
    service=Service(root,binary,write(root/'project'));receipt=Receipt();receipt.secret(PASSWORD);external=None;dns=None;fatal=None
    try:
        dns=DNS();external=External(root/'external','W6_dns_synthetic',tls=True,hostname='w6-ok.test')
        destination={'origin':external.origin,'network':'loopback','paths':['/v1/'],'methods':['GET','POST'],'ca_file':str(external.ca),'dns_server':dns.address,'timeout_ms':1000}
        service.runtime['http']={n:destination.copy() for n in ['inventory','secondary']}
        service.postgres();contract,_=service.build();service.cli('project-init',service.project,'--postgres-env',ENV_NAME);service.files_cli(initialize=True);provision(service,'alice');service.start_node('A');service.start_node('B');owner=client(service,contract['rpc_id'],'alice')
        result=owner.rpc('external_get','/v1/ok')
        receipt.record('owned-dns-real-tls-success',result==(200,{'value':'external exact 雪\0'}) and len(dns.events)>0 and external.count()==1,{'rpc':result,'dns':list(dns.events),'http_calls':external.count()},'real UDP DNS answer plus original-hostname TLS and CA validation reaches only the granted destination')
        for mode in ['cname','mixed','private','drop','loopback']:
            service.stop_node('B');dns.mode=mode;service.start_node('B');before=len(dns.events);http_before=external.count()
            start=time.monotonic();result=owner.rpc('external_get','/v1/ok',node='B');elapsed=time.monotonic()-start;new=dns.events[before:];calls=external.count()-http_before
            if mode in ['loopback','cname']:
                okay=result==(200,{'value':'external exact 雪\0'}) and calls==1 and bool(new)
            else:okay=result[0]>=400 and calls==0 and bool(new) and elapsed<4
            receipt.record('dns-'+mode,okay,{'rpc':result,'elapsed_seconds':elapsed,'dns':new,'http_calls':calls},'CNAME/fresh valid answers preserve original TLS identity; mixed or private answers never connect; dropped DNS stays within the1-second grant deadline')
        service.stop_node('B');service.runtime['http']['inventory']['network']='public'
        refused=service.cli('serve',service.project,'--listen',f"127.0.0.1:{service.ports['B']}",'--origin',service.origin,'--postgres-env',ENV_NAME,'--identity-mailbox',service.mailbox,'--runtime-env','DIAMOND_W6_INDEPENDENT_RUNTIME',success=False)
        receipt.record('custom-dns-loopback-only',refused.returncode!=0,{'exit':refused.returncode,'stderr':refused.stderr},'custom resolver grant cannot alter the public network authority profile')
    except Exception as error:fatal=repr(error)
    finally:
        try:service.close()
        except Exception as error:fatal=(fatal or '')+' cleanup='+repr(error)
        for resource in [external,dns]:
            if resource:
                try:resource.close()
                except Exception as error:fatal=(fatal or '')+' fixture='+repr(error)
        result=receipt.report(service,sha256(binary),error=fatal,temporary_root=str(root),dns_events=dns.events if dns else []);a.report.write_text(json.dumps(result,indent=2)+'\n')
        if result['status']=='PASS':shutil.rmtree(root)
    print(json.dumps({'status':result['status'],'observations':len(receipt.results),'error':fatal}));return result['status']!='PASS'
if __name__=='__main__':raise SystemExit(main())
