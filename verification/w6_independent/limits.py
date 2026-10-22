#!/usr/bin/env python3
"""Real metadata quotas, bounded startup reconciliation and stalled-stream release."""
from __future__ import annotations
import argparse,hashlib,json,shutil,socket,tempfile,time
from pathlib import Path
from common import Receipt,client,objects,provision,quote,seed,wait_for
from fixture import PASSWORD,app_id,write
from external import External
from service import ENV_NAME,Service,sha256
PREFIX='/_diamond/files/issue_files/'


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--binary',type=Path,required=True);p.add_argument('--report',type=Path,required=True);a=p.parse_args()
    root=Path(tempfile.mkdtemp(prefix='diamond-w6-independent-limits-'));binary=root/'diamond';shutil.copy2(a.binary,binary)
    service=Service(root,binary,write(root/'project'));receipt=Receipt();receipt.secret(PASSWORD);external=None;fatal=None;raw=None
    def insert(count,state,length=0,expired=False):
        service.sql('INSERT INTO diamond_w6.objects(app,id,binding,parent,owner,name,media,length,state,token_hash,expires) SELECT '+quote(app_id())+",lpad(to_hex(n),64,'0'),'issue_files','issue-red-one','quota-fixture','metadata-only','application/octet-stream',"+str(length)+','+quote(state)+",'',clock_timestamp()+interval '"+('-1 second' if expired else '15 minutes')+"' FROM generate_series(1,"+str(count)+') n')
    def begin(name='limit',length='1'):
        return owner.request('A',PREFIX+'begin',{'parent':'issue-red-one','filename':name,'length':length,'media_type':'application/octet-stream'})
    try:
        external=External(root/'external','W6_limits_synthetic');grant={'origin':external.origin,'network':'loopback','paths':['/v1/'],'methods':['GET','POST']};service.runtime['http']={n:grant for n in ['inventory','secondary']}
        service.postgres();contract,_=service.build();service.cli('project-init',service.project,'--postgres-env',ENV_NAME);service.files_cli(initialize=True);provision(service,'alice');seed(service);service.start_node('A');service.start_node('B');owner=client(service,contract['rpc_id'],'alice')
        insert(64,'ready',8388608)
        before=len(objects(service));full=begin()
        receipt.record('application-512mib-metadata-quota',full[0]>=400 and len(objects(service))==before,{'begin':full,'rows':before,'declared_bytes':64*8388608,'physical_bytes':0},'real begin enforces the committed declared-byte quota; seeded metadata is not a disk-capacity test')
        service.sql('DELETE FROM diamond_w6.objects WHERE app='+quote(app_id()))
        insert(10000,'ready')
        full=begin();receipt.record('application-10000-metadata-quota',full[0]>=400 and len(objects(service))==10000,{'begin':full,'rows':len(objects(service))},'row quota bounds metadata independently of byte quota')
        service.sql('DELETE FROM diamond_w6.objects WHERE app='+quote(app_id()))
        insert(105,'reserved',expired=True)
        # Add real stale files in both publication phases, independently of metadata.
        for index,suffix in [(1,'part'),(2,'blob')]:
            (service.objects/(app_id()+'-'+format(index,'064x')+'.'+suffix)).write_bytes(b'owned expired bytes')
        cleaned=service.files_cli();rows=objects(service)
        receipt.record('explicit-reconciliation-batch100',json.loads(cleaned.stdout)['removed']==100 and len(rows)==5 and not list(service.objects.glob('*.part')) and not list(service.objects.glob('*.blob')),
                       {'cli':json.loads(cleaned.stdout),'remaining_rows':len(rows),'remaining_files':len(list(service.objects.glob('*.part')))+len(list(service.objects.glob('*.blob')))},'one actual CLI pass deletes at most100 expired records and both partial/renamed bytes')
        service.stop_node('B');service.start_node('B');wait_for(lambda:not objects(service),'startup reconciles remaining expired reservations')
        receipt.record('automatic-startup-reconciliation',not objects(service),{'rows':objects(service)},'startup reconciliation removes committed expired pending metadata without a manual cleanup call')
        # A real ready object is retained across reconciliation despite an expired ticket.
        result=begin('ready-retained','1');identifier=result[1]['id'];secret=result[1]['upload'];receipt.secret(secret)
        service.transfer('B',owner,PREFIX+identifier,b'x',upload=secret);owner.request('B',PREFIX+identifier+'/finish',{}, {'X-Diamond-Upload':secret})
        service.sql('UPDATE diamond_w6.objects SET expires=clock_timestamp()-interval \'1 second\' WHERE app='+quote(app_id()))
        service.files_cli();download=service.request('B',PREFIX+identifier,headers={'Cookie':owner.cookie})
        receipt.record('cleanup-never-deletes-ready',download[0]==200 and download[2]==b'x' and objects(service,identifier)[0]['state']=='ready',{'status':download[0],'rows':objects(service,identifier)},'expired upload ticket cannot cause cleanup of an already published ready object')
        owner.request('B',PREFIX+identifier+'/delete',{});service.files_cli()
        # Slow body must release its file and native request within the declared deadline.
        result=begin('stalled','8388608');identifier=result[1]['id'];secret=result[1]['upload'];receipt.secret(secret)
        baseline=len(list(Path('/proc',str(service.nodes['A'].pid),'fd').iterdir()))
        raw=socket.create_connection(('127.0.0.1',service.ports['A']),timeout=40)
        request=(f'PUT {PREFIX}{identifier} HTTP/1.1\r\nHost: {service.authority}\r\nOrigin: {service.origin}\r\nCookie: {owner.cookie}\r\nX-Diamond-CSRF: {owner.csrf}\r\nX-Diamond-Upload: {secret}\r\nContent-Type: application/octet-stream\r\nContent-Length: 8388608\r\nConnection: close\r\n\r\n').encode()
        start=time.monotonic();raw.sendall(request+b'x');part=service.objects/(app_id()+'-'+identifier+'.part');wait_for(part.exists,'stalled stream has actually opened its partial file')
        response=raw.recv(4096);elapsed=time.monotonic()-start;raw.close();raw=None
        wait_for(lambda:not part.exists(),'deadline drops partial file')
        healthy=owner.rpc('read',{'tag':'issue','value':'issue-red-one'},node='A');fds=len(list(Path('/proc',str(service.nodes['A'].pid),'fd').iterdir()))
        receipt.record('actual-upload-deadline-and-resource-release',29<=elapsed<38 and not part.exists() and healthy[0]==200 and fds<=baseline+4,
                       {'elapsed_seconds':elapsed,'http_status_line':response.split(b'\r\n',1)[0].decode(),'fds_before':baseline,'fds_after':fds,'fresh_rpc_status':healthy[0]},'actual stalled body times out near30s, drops its file/permit and permits fresh same-node work')
        service.sql('UPDATE diamond_w6.objects SET expires=clock_timestamp()-interval \'1 second\' WHERE app='+quote(app_id()));service.files_cli()
        # Wait for the real periodic minute tick; neither node is restarted.
        insert(1,'reserved',expired=True);start=time.monotonic();wait_for(lambda:not objects(service),'minute reconciliation removes expired row',timeout=65)
        receipt.record('automatic-periodic-reconciliation',not objects(service),{'elapsed_seconds':time.monotonic()-start,'remaining_rows':len(objects(service))},'running servers reconcile expiry on their minute schedule without CLI or restart')
    except Exception as error:fatal=repr(error)
    finally:
        if raw:raw.close()
        try:service.close()
        except Exception as error:fatal=(fatal or '')+' cleanup='+repr(error)
        if external:
            try:external.close()
            except Exception as error:fatal=(fatal or '')+' external='+repr(error)
        result=receipt.report(service,sha256(binary),error=fatal,temporary_root=str(root));a.report.write_text(json.dumps(result,indent=2)+'\n')
        if result['status']=='PASS':shutil.rmtree(root)
    print(json.dumps({'status':result['status'],'observations':len(receipt.results),'error':fatal}));return result['status']!='PASS'
if __name__=='__main__':raise SystemExit(main())
