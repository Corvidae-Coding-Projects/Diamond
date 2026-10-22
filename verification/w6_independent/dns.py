#!/usr/bin/env python3
"""Real resolver-latency injection confined to the disposable Diamond process."""
import argparse,json,shutil,subprocess,tempfile,time
from pathlib import Path
from common import Receipt,client,provision
from fixture import PASSWORD,write
from external import External
from service import ENV_NAME,Service,sha256
from resolver import DNS


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--binary',type=Path,required=True);p.add_argument('--report',type=Path,required=True);p.add_argument('--expect-getaddrinfo',type=int,default=1);p.add_argument('--async-resolver',action='store_true');a=p.parse_args()
    root=Path(tempfile.mkdtemp(prefix='diamond-w6-independent-dns-'));binary=root/'diamond';shutil.copy2(a.binary,binary)
    service=Service(root,binary,write(root/'project'));receipt=Receipt();receipt.secret(PASSWORD);external=None;dns=None;fatal=None;shim=root/'dns-delay.so';trace=root/'resolver-invocations'
    try:
        subprocess.run(['cc','-Wall','-Wextra','-Werror','-fPIC','-shared',str(Path(__file__).with_name('dns_delay.c')),'-ldl','-o',str(shim)],check=True,capture_output=True,timeout=30)
        external=External(root/'external','W6_dns_synthetic')
        service.runtime['http']={'inventory':{'origin':'https://w6-stall.invalid','network':'loopback','paths':['/v1/'],'methods':['GET'],'timeout_ms':1000},'secondary':{'origin':external.origin,'network':'loopback','paths':['/v1/'],'methods':['GET','POST']}}
        if a.async_resolver:
            dns=DNS();dns.mode='drop';service.runtime['http']['inventory']['dns_server']=dns.address
        service.environment={'LD_PRELOAD':str(shim),'W6_DNS_DELAY_TRACE':str(trace)}
        service.postgres();contract,_=service.build();service.cli('project-init',service.project,'--postgres-env',ENV_NAME);service.files_cli(initialize=True);provision(service,'alice');service.start_node('A');owner=client(service,contract['rpc_id'],'alice')
        start=time.monotonic();result=owner.rpc('external_get','/v1/ok');elapsed=time.monotonic()-start
        calls=len(trace.read_bytes()) if trace.exists() else 0
        receipt.record('resolver-stall-request-deadline',result[0]>=400 and elapsed<4 and calls==a.expect_getaddrinfo,{'rpc':result,'elapsed_seconds':elapsed,'grant_timeout_ms':1000,'injected_delay_seconds':8,'getaddrinfo_calls':calls,'expected_getaddrinfo_calls':a.expect_getaddrinfo,'owned_dns_queries':len(dns.events) if dns else None},'1-second operation deadline must bound real response and cleanup despite a delayed resolver; asynchronous operations must not invoke blocking getaddrinfo')
        healthy=owner.rpc('count')
        receipt.record('resolver-stall-followup-health',healthy==(200,{'value':'0'}),healthy,'fresh ordinary same-process request remains healthy after resolver cancellation')
    except Exception as error:fatal=repr(error)
    finally:
        try:service.close()
        except Exception as error:fatal=(fatal or '')+' cleanup='+repr(error)
        if external:
            try:external.close()
            except Exception as error:fatal=(fatal or '')+' external='+repr(error)
        if dns:
            try:dns.close()
            except Exception as error:fatal=(fatal or '')+' dns='+repr(error)
        result=receipt.report(service,sha256(binary),error=fatal,temporary_root=str(root),shim_source_sha256=sha256(Path(__file__).with_name('dns_delay.c')),shim_sha256=sha256(shim) if shim.exists() else None);a.report.write_text(json.dumps(result,indent=2)+'\n')
        if result['status']=='PASS':shutil.rmtree(root)
    print(json.dumps({'status':result['status'],'observations':len(receipt.results),'error':fatal}));return result['status']!='PASS'
if __name__=='__main__':raise SystemExit(main())
