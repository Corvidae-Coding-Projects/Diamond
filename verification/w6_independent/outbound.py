#!/usr/bin/env python3
"""Actual named TLS calls, forbidden destinations, limits and POST uncertainty."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import shutil
import socket
import tempfile
import time

from common import Receipt,client,provision,seed,wait_for
from external import External
from fixture import PASSWORD,write
from service import ENV_NAME,Service,sha256

SECRET='w6-synthetic-native-credential-f9a8c3'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary',type=Path,required=True);parser.add_argument('--report',type=Path,required=True)
    options=parser.parse_args();root=Path(tempfile.mkdtemp(prefix='diamond-w6-independent-http-'))
    binary=root/'diamond';shutil.copy2(options.binary,binary);project=write(root/'project')
    service=Service(root,binary,project);receipt=Receipt();receipt.secret(PASSWORD);receipt.secret(SECRET)
    fixtures=[];fatal=None
    try:
        inventory=External(root/'inventory',SECRET,tls=True);fixtures.append(inventory)
        other=External(root/'other',SECRET);fixtures.append(other)
        wronghost=External(root/'wrong-host',SECRET,tls=True,wrong_hostname=True);fixtures.append(wronghost)
        inventory.redirect_origin=other.origin
        service.environment={'W6_SYNTHETIC_TOKEN':SECRET,'HTTP_PROXY':other.origin,'HTTPS_PROXY':other.origin,'ALL_PROXY':other.origin,
                             'http_proxy':other.origin,'https_proxy':other.origin,'all_proxy':other.origin,'NO_PROXY':'','no_proxy':''}
        destination={'origin':inventory.origin,'network':'loopback','paths':['/v1'],'methods':['GET','POST'],
                     'credential_env':'W6_SYNTHETIC_TOKEN','ca_file':str(inventory.ca),'timeout_ms':1000,'max_response_bytes':65536,'max_redirects':2}
        service.runtime['http']={'inventory':destination,'secondary':destination.copy()}
        service.postgres();contract,build=service.build();service.cli('project-init',project,'--postgres-env',ENV_NAME);service.files_cli(initialize=True)
        for name in ['alice','outsider']:provision(service,name)
        seed(service);service.start_node('A');service.start_node('B')
        alice,outsider=client(service,contract['rpc_id'],'alice'),client(service,contract['rpc_id'],'outsider')
        expected='external exact 雪\0'
        for node in ['A','B']:
            result=alice.rpc('external_get','/v1/ok',node=node)
            receipt.record('trusted-local-ca-typed-get-'+node,result==(200,{'value':expected}),result,'actual TLS certificate/hostname and wire Text decode succeed')
        result=alice.rpc('external_post','/v1/echo','typed post 雪\0')
        receipt.record('typed-post-real-receiver',result==(200,{'value':'typed post 雪\0'}) and inventory.events[-1]['expected_authorization'],
                       {'rpc':result,'receiver':inventory.events[-1]},'exact typed JSON body and native-only bearer credential reach the declared destination')
        before=inventory.count();denied=outsider.rpc('authorized_get',{'tag':'issue','value':'issue-red-one'},'/v1/ok')
        receipt.record('parent-denial-before-outbound',denied[0]==403 and inventory.count()==before,{'rpc':denied,'new_calls':inventory.count()-before},'failed current parent read policy sends no external request')
        allowed=alice.rpc('authorized_get',{'tag':'issue','value':'issue-red-one'},'/v1/ok')
        receipt.record('read-snapshot-before-outbound',allowed==(200,{'value':expected}),allowed,'authorized read-only snapshot may precede the call')
        for path in ['https://attacker.invalid/v1/ok','//localhost/v1/ok','/v10/ok','/v1evil','/v1/../not-granted','/v1/%2e%2e/not-granted','/v1\\..\\not-granted','/v1/#fragment']:
            before=inventory.count();result=alice.rpc('external_get',path)
            receipt.record('path-authority-'+path,result[0]>=400 and inventory.count()==before,{'status':result[0],'new_calls':inventory.count()-before},'path input cannot escape the native destination/segment prefix')
        result=alice.rpc('external_get','/v1/redirect/2')
        receipt.record('two-same-origin-redirects',result==(200,{'value':expected}),result,'at most two allowed GET redirects work')
        before=inventory.count(path='/v1/redirect/0');result=alice.rpc('external_get','/v1/redirect/3')
        receipt.record('redirect-budget',result[0]>=400 and inventory.count(path='/v1/redirect/0')==before,{'rpc':result,'new_final_target_calls':inventory.count(path='/v1/redirect/0')-before},'a third redirect is not followed')
        for path,target,targetpath in [('/v1/cross',other,'/v1/ok'),('/v1/outside',inventory,'/not-granted')]:
            before=target.count(path=targetpath);result=alice.rpc('external_get',path)
            receipt.record('redirect-authority-'+path,result[0]>=400 and target.count(path=targetpath)==before,{'rpc':result,'target_delta':target.count(path=targetpath)-before},'each redirect must stay within the original origin and path prefix')
        for name in ['malformed','wrong_type','oversize','gzip','secret','escaped_secret','slow']:
            start=time.monotonic();result=alice.rpc('external_get','/v1/'+name);elapsed=time.monotonic()-start
            plain=json.dumps(result,ensure_ascii=False)
            receipt.record('response-boundary-'+name,result[0]>=400 and SECRET not in plain and inventory.origin not in plain and elapsed<4,
                           {'rpc':result,'elapsed_seconds':elapsed},'strict typed/size/encoding/secret/deadline rejection with safe errors')
        before=inventory.count(path='/v1/drop',method='POST');result=alice.rpc('external_post','/v1/drop','effect once')
        receipt.record('post-delivery-unknown-no-adapter-replay',result==(503,{'error':'outcome_unknown'}) and inventory.count(path='/v1/drop',method='POST')==before+1,
                       {'rpc':result,'actual_post_count':inventory.count(path='/v1/drop',method='POST')-before},'receiving fixture observes one real POST; lost reply is unknown, never rollback or automatic retry')
        before=inventory.count(path='/v1/ok',method='POST');result=alice.rpc('external_post','/v1/post_redirect','effect redirect')
        receipt.record('post-never-follows-redirect',result[0]>=400 and inventory.count(path='/v1/ok',method='POST')==before,{'rpc':result,'new_target_posts':inventory.count(path='/v1/ok',method='POST')-before},'POST redirect cannot replay the side effect at another path')
        recovery=alice.rpc('external_get','/v1/ok',node='B')
        receipt.record('fresh-call-after-cancellation',recovery==(200,{'value':expected}),recovery,'fresh work recovers after limits/deadlines/disconnected upstream')
        descriptor_directory=Path('/proc',str(service.nodes['A'].pid),'fd')
        counts=[len(list(descriptor_directory.iterdir()))];batches=[]
        before=inventory.count(path='/v1/slow')
        for batch in range(3):
            for index in range(3):
                body=json.dumps({'contract':contract['rpc_id'],'arguments':['/v1/slow']}).encode()
                connection=socket.create_connection(('127.0.0.1',service.ports['A']),timeout=5)
                request=(f'POST /_diamond/rpc/external_get HTTP/1.1\r\nHost: {service.authority}\r\nOrigin: {service.origin}\r\nCookie: {alice.cookie}\r\nX-Diamond-CSRF: {alice.csrf}\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n').encode()
                connection.sendall(request+body)
                wait_for(lambda:inventory.count(path='/v1/slow')>=before+batch*3+index+1,'real upstream request starts before downstream disconnect')
                connection.close()
            wait_for(lambda:inventory.active==0,'canceled callers release bounded upstream work',timeout=5)
            fresh=alice.rpc('external_get','/v1/ok');time.sleep(.05)
            counts.append(len(list(descriptor_directory.iterdir())))
            batches.append({'fds':counts[-1],'fresh':fresh,'upstream_active':inventory.active})
        receipt.record('downstream-disconnect-bounded-resources',inventory.count(path='/v1/slow')==before+9 and max(counts[2:])<=counts[1]+2 and all(batch['fresh']==(200,{'value':expected}) and batch['upstream_active']==0 for batch in batches),
                       {'upstream_calls':inventory.count(path='/v1/slow')-before,'fds_serial_then_three_concurrency_batches':counts,'batches':batches},'three repeated concurrent batches reach actual upstream once each, drain work and stabilize descriptors after bounded pool warmup')
        # One changed native profile at a time isolates independent authority gates.
        for label,replacements,target in [('public-denies-loopback',{'network':'public'},inventory),
              ('untrusted-ca',{'ca_file':None},inventory),('hostname-mismatch',{'origin':wronghost.origin,'ca_file':str(wronghost.ca)},wronghost),
              ('method-denied',{'methods':['POST']},inventory)]:
            service.stop_node('B');modified=destination.copy();modified.update(replacements)
            if modified.get('ca_file') is None:modified.pop('ca_file',None)
            service.runtime['http']['inventory']=modified;service.start_node('B')
            before=target.count();result=alice.rpc('external_get','/v1/ok',node='B')
            receipt.record('native-authority-'+label,result[0]>=400 and target.count()==before,{'rpc':result,'actual_http_calls':target.count()-before},'native network, trust, hostname or method authority rejects before an HTTP request')
        receipt.record('ambient-proxies-unused',other.count()==0,{'proxy_requests':other.events},'ambient HTTP/HTTPS/ALL proxy settings do not redirect credentials or requests')
        public=[p for p in build.rglob('*') if p.is_file() and p.suffix in ['.js','.html','.css','.map']]
        leaks=[str(p.relative_to(build)) for p in public if SECRET.encode() in p.read_bytes()]
        logs='\n'.join(Path(log.name).read_text() for log in service.logs)
        receipt.record('native-secret-not-artifact-or-log',not leaks and SECRET not in logs,{'leaked_artifacts':leaks,'log_secret_present':SECRET in logs},'native env credential is absent from source/browser artifacts and actual server logs')
    except Exception as failure:fatal=repr(failure)
    finally:
        try:service.close()
        except Exception as cleanup:fatal=(fatal or '')+' cleanup='+repr(cleanup)
        for fixture in fixtures:
            try:fixture.close()
            except Exception as cleanup:fatal=(fatal or '')+' external='+repr(cleanup)
        report=receipt.report(service,sha256(binary),error=fatal,temporary_root=str(root),external_events=[fixture.events for fixture in fixtures],
                               external_transport_closes=[fixture.transport_closes for fixture in fixtures],
                               scope='actual local TLS/HTTP destinations, native grants, strict typed calls and delivery faults; dynamic DNS rebinding/public Internet and browser separate')
        options.report.write_text(json.dumps(report,indent=2)+'\n')
        if report['status']=='PASS':shutil.rmtree(root)
    print(json.dumps({'status':report['status'],'observations':len(receipt.results),'error':report['error']}))
    return report['status']!='PASS'


if __name__=='__main__':raise SystemExit(main())
