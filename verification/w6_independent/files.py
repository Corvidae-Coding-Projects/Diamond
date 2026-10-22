#!/usr/bin/env python3
"""Real cross-node files, principal/policy checks, corruption and streaming."""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import re
import shutil
import socket
import subprocess
import tempfile
import time

from common import Receipt,client,objects,provision,quote,seed,wait_for
from fixture import PASSWORD,app_id,table,write
from external import External
from service import ENV_NAME,Service,sha256,free_port

ROOT=Path(__file__).resolve().parents[2]
PREFIX='/_diamond/files/issue_files/'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary',type=Path,required=True);parser.add_argument('--report',type=Path,required=True)
    options=parser.parse_args();root=Path(tempfile.mkdtemp(prefix='diamond-w6-independent-files-'))
    binary=root/'diamond';shutil.copy2(options.binary,binary);project=write(root/'project')
    service=Service(root,binary,project);receipt=Receipt();receipt.secret(PASSWORD);fatal=None
    clients={}
    auxiliary=None

    def begin(name='fixture.bin',length=0,owner='alice',parent='issue-red-one',node='A',media='application/octet-stream'):
        before=len(objects(service))
        result=clients[owner].request(node,PREFIX+'begin',{'parent':parent,'filename':name,'length':str(length),'media_type':media})
        if result[0]!=200:return result,None,None
        value=result[1];identifier=value.get('id');secret=value.get('upload')
        if not isinstance(identifier,str) or not re.fullmatch('[0-9a-f]{64}',identifier) or not isinstance(secret,str):raise AssertionError('invalid actual reservation shape')
        receipt.secret(secret)
        row=objects(service,identifier)
        private=service.sql_json('SELECT token_hash,extract(epoch FROM (expires-clock_timestamp())) AS seconds_left FROM diamond_w6.objects WHERE app='+quote(app_id())+' AND id='+quote(identifier))[0]
        receipt.record('ticket-private-hash-and-db-expiry-'+name,secret!=identifier and private['token_hash']==hashlib.sha256(secret.encode()).hexdigest() and private['token_hash']!=secret and 890<float(private['seconds_left'])<=901,
                       {'separate_from_id':secret!=identifier,'stored_sha256_only':private['token_hash']==hashlib.sha256(secret.encode()).hexdigest(),'db_seconds_left':private['seconds_left']},'separate upload authority is hash-only and expires on PostgreSQL time near15minutes')
        receipt.record('reservation-before-bytes-'+name,len(row)==1 and row[0]['owner']==owner and row[0]['length']==length and row[0]['parent']==parent and row[0]['state']=='reserved'
             and not list(service.objects.glob(app_id()+'-'+identifier+'.*')),
             {'rows':row,'physical_objects':len(list(service.objects.glob(app_id()+'-'+identifier+'.*')))},'committed principal/parent/length metadata exists before bytes')
        return result,identifier,secret

    def finish(identifier,secret,owner='alice',node='A'):
        return clients[owner].request(node,PREFIX+identifier+'/finish',{}, {'X-Diamond-Upload':secret})

    def delete(identifier,secret,owner='alice',node='A'):
        return clients[owner].request(node,PREFIX+identifier+'/delete',{}, {'X-Diamond-Upload':secret})

    def download(identifier,owner='alice',node='B'):
        return service.request(node,PREFIX+identifier,headers={'Cookie':clients[owner].cookie or ''})

    try:
        auxiliary=External(root/'external','W6_files_auxiliary_synthetic')
        service.runtime['http']={name:{'origin':auxiliary.origin,'network':'loopback','paths':['/v1/'],'methods':['GET','POST']} for name in ['inventory','secondary']}
        service.postgres();contract,build=service.build();service.cli('project-init',project,'--postgres-env',ENV_NAME)
        service.files_cli(initialize=True)
        for name in ['alice','bob','carol','outsider']:provision(service,name)
        seed(service);service.start_node('A');service.start_node('B')
        clients={name:client(service,contract['rpc_id'],name) for name in ['alice','bob','carol','outsider']}
        for index,data in enumerate([b'',b'\0\xffbinary\r\n'+bytes(range(256))*4,bytes(range(256))*32768]):
            _,identifier,secret=begin('exact-'+str(index)+'.bin',len(data))
            if identifier is None:raise AssertionError('valid reservation denied')
            pending=download(identifier)
            receipt.record('pending-invisible-'+str(index),pending[0]>=400,{'status':pending[0],'body_bytes':len(pending[2])},'no pending object download')
            if index<2:
                wrong=service.transfer('B',clients['bob'],PREFIX+identifier,data,upload=secret)
                receipt.record('reservation-principal-binding-'+str(index),wrong[0]>=400,{'status':wrong[0]},'another Editor cannot use uploader reservation/token')
            transferred=service.transfer('B',clients['alice'],PREFIX+identifier,data,upload=secret)
            receipt.record('cross-node-transfer-'+str(index),transferred[0]==200,{'status':transferred[0],'body':transferred[2].decode()},'exact raw PUT succeeds on B')
            pending=download(identifier)
            receipt.record('staged-invisible-'+str(index),pending[0]>=400,{'status':pending[0]},'staged data remains unreadable before publication')
            with ThreadPoolExecutor(max_workers=2) as executor:
                jobs=[executor.submit(finish,identifier,secret,node=node) for node in ['A','B']]
                finalized=[job.result(timeout=20) for job in jobs]
            rows=objects(service,identifier);physical=list(service.objects.glob(app_id()+'-'+identifier+'.*'))
            receipt.record('concurrent-single-publication-'+str(index),any(status==200 for status,_ in finalized) and all(status in [200,409] for status,_ in finalized) and len(rows)==1 and rows[0]['state']=='ready' and rows[0]['sha256']==hashlib.sha256(data).hexdigest() and len(physical)==1,
                {'outcomes':finalized,'rows':rows,'physical_names':[p.name for p in physical]},'concurrent finalization publishes once; completed or distinctly aborted contenders permit explicit ready retry')
            retry=finish(identifier,secret,node='B')
            receipt.record('explicit-finish-retry-'+str(index),retry[0]==200,retry,'explicit finish retry observes existing ready object')
            got=download(identifier,owner='carol')
            headers=got[1]
            receipt.record('reader-cross-node-exact-download-'+str(index),got[0]==200 and got[2]==data and headers.get('content-type')=='application/octet-stream' and headers.get('x-content-type-options')=='nosniff' and headers.get('content-disposition','').startswith('attachment'),
                {'status':got[0],'length':len(got[2]),'sha256':hashlib.sha256(got[2]).hexdigest(),'headers':headers},'current Reader policy permits exact bytes with safe attachment headers')
            unauthorized=download(identifier,owner='outsider')
            receipt.record('unrelated-principal-denied-'+str(index),unauthorized[0]>=400 and unauthorized[2]!=data,{'status':unauthorized[0]},'no parent membership, no object access')
            if data:
                blob=service.objects/(app_id()+'-'+identifier+'.blob');before=blob.read_bytes()
                blob.write_bytes(bytes([before[0]^255])+before[1:]);corrupt=download(identifier)
                receipt.record('same-length-corruption-fails-closed-'+str(index),corrupt[0]>=400,{'status':corrupt[0],'body_bytes':len(corrupt[2])},'same-size corruption does not stream bytes')
                blob.write_bytes(before)
            deleted=delete(identifier,secret,node='B')
            receipt.record('published-delete-'+str(index),deleted[0]==200 and download(identifier)[0]>=400,deleted,'authorized delete removes public readability')
            service.files_cli()
        # Four unfinished reservations use real begin requests; fifth must not allocate.
        reservations=[begin('unfinished-'+str(index),1)[1:] for index in range(4)]
        count=len(objects(service));fifth,_,_=begin('fifth',1)
        receipt.record('four-unfinished-reservations',all(item[0] for item in reservations) and fifth[0]>=400 and len(objects(service))==count,
                       {'fifth':fifth,'rows_before':count,'rows_after':len(objects(service))},'fifth unfinished reservation rejects without allocation')
        service.sql('UPDATE diamond_w6.objects SET expires=clock_timestamp()-interval \'1 second\' WHERE app='+quote(app_id())+" AND state='reserved'")
        service.files_cli()
        # Read-only membership cannot reserve bytes; oversized metadata cannot allocate.
        count=len(objects(service));reader,_,_=begin('reader',1,owner='carol');oversize,_,_=begin('oversize',8388609)
        receipt.record('reader-and-oversize-no-write',reader[0]>=400 and oversize[0]>=400 and len(objects(service))==count,
                       {'reader':reader,'oversize':oversize},'no update authority or length above8MiB creates no reservation')
        _,identifier,secret=begin('wrong-length',1)
        mismatch=service.transfer('A',clients['alice'],PREFIX+identifier,b'xx',upload=secret)
        receipt.record('actual-length-mismatch',mismatch[0]>=400 and finish(identifier,secret)[0]>=400 and download(identifier)[0]>=400,
                       {'status':mismatch[0],'rows':objects(service,identifier)},'declared/actual length mismatch never publishes')
        service.sql('UPDATE diamond_w6.objects SET expires=clock_timestamp()-interval \'1 second\' WHERE app='+quote(app_id())+' AND id='+quote(identifier));service.files_cli()
        # Partial real socket transfer demonstrates early filesystem streaming.
        _,identifier,secret=begin('interrupted',8388608)
        raw=socket.create_connection(('127.0.0.1',service.ports['A']),timeout=5)
        headers=(f'PUT {PREFIX}{identifier} HTTP/1.1\r\nHost: {service.authority}\r\nOrigin: {service.origin}\r\nCookie: {clients["alice"].cookie}\r\nX-Diamond-CSRF: {clients["alice"].csrf}\r\nX-Diamond-Upload: {secret}\r\nContent-Type: application/octet-stream\r\nContent-Length: 8388608\r\nConnection: close\r\n\r\n').encode()
        raw.sendall(headers+b'p'*131072)
        part=service.objects/(app_id()+'-'+identifier+'.part')
        wait_for(lambda:part.exists() and part.stat().st_size>0,'real partial bytes reach shared storage before body completion')
        partial=part.stat().st_size
        healthy=clients['bob'].rpc('read',{'tag':'issue','value':'issue-red-one'},node='B')
        raw.close();wait_for(lambda:not part.exists(),'disconnected upload releases its partial file')
        receipt.record('streaming-disconnect-and-unrelated-health',partial<=131072 and partial>0 and healthy[0]==200 and download(identifier)[0]>=400,
                       {'partial_bytes_before_completion':partial,'healthy_rpc_status':healthy[0],'rows':objects(service,identifier)},'bounded partial transfer writes incrementally, stays private and releases work after disconnect')
        service.sql('UPDATE diamond_w6.objects SET expires=clock_timestamp()-interval \'1 second\' WHERE app='+quote(app_id())+' AND id='+quote(identifier));service.files_cli()
        receipt.record('expired-interrupted-cleanup',not objects(service,identifier) and not list(service.objects.glob(app_id()+'-'+identifier+'.*')),
                       {'rows':objects(service,identifier),'files':[p.name for p in service.objects.glob(app_id()+'-'+identifier+'.*')]},'bounded explicit cleanup removes expired/failed metadata and bytes')
        # Names and MIME are metadata, never path or response-header authority.
        count=len(objects(service))
        attempts=[begin(name,1)[0] for name in ['../escape','/tmp/escape','..\\escape','evil\r\nX-Test: value','x'*161]]
        receipt.record('traversal-and-header-metadata-rejection',all(result[0]>=400 for result in attempts) and len(objects(service))==count,
                       {'statuses':[result[0] for result in attempts],'row_count':len(objects(service))},'path/control/oversized names cannot allocate or escape object storage')
        _,identifier,secret=begin('untrusted.html',7,media='text/html')
        wrong=finish(identifier,'0'*64);missing=finish(identifier,'')
        csrf=clients['alice'].request('A',PREFIX+identifier+'/finish',{}, {'X-Diamond-Upload':secret,'X-Diamond-CSRF':'invalid'})
        origin=clients['alice'].request('B',PREFIX+identifier+'/finish',{}, {'X-Diamond-Upload':secret,'Origin':'https://evil.invalid'})
        receipt.record('ticket-csrf-origin-binding',all(result[0]>=400 for result in [wrong,missing,csrf,origin]),
                       {'statuses':[r[0] for r in [wrong,missing,csrf,origin]]},'forged or missing token, CSRF and Origin cannot finalize')
        service.transfer('B',clients['alice'],PREFIX+identifier,b'<html/>',upload=secret);finish(identifier,secret)
        html=download(identifier)
        receipt.record('declared-html-still-attachment',html[0]==200 and html[1].get('content-type')=='application/octet-stream' and html[1].get('content-disposition','').startswith('attachment') and html[1].get('x-content-type-options')=='nosniff',
                       {'status':html[0],'headers':html[1]},'declared HTML never authorizes inline rendering')
        reader_delete=delete(identifier,secret,owner='carol')
        receipt.record('reader-cannot-delete',reader_delete[0]>=400 and download(identifier)[0]==200,reader_delete,'Read authority cannot delete a published object')
        delete(identifier,secret);service.files_cli()
        # Force a real deferred PostgreSQL abort after rename and before commit.
        _,identifier,secret=begin('commit-abort',5)
        service.transfer('A',clients['alice'],PREFIX+identifier,b'abcde',upload=secret)
        service.sql("CREATE FUNCTION public.w6_abort_ready() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.state='ready' THEN RAISE EXCEPTION 'owned W6 deferred abort' USING ERRCODE='23514'; END IF; RETURN NEW; END $$; CREATE CONSTRAINT TRIGGER w6_abort_ready AFTER UPDATE ON diamond_w6.objects DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.w6_abort_ready()")
        aborted=finish(identifier,secret,node='B');rows=objects(service,identifier)
        renamed=(service.objects/(app_id()+'-'+identifier+'.blob')).exists()
        receipt.record('rename-survives-confirmed-db-abort',aborted[0]>=400 and rows[0]['state']=='staged' and renamed and download(identifier)[0]>=400,
                       {'finish':aborted,'rows':rows,'renamed_blob':renamed},'SQL abort does not claim to roll back filesystem rename or publish bytes')
        service.sql('DROP TRIGGER w6_abort_ready ON diamond_w6.objects; DROP FUNCTION public.w6_abort_ready()')
        recovered=finish(identifier,secret,node='A')
        receipt.record('explicit-retry-after-rename-abort',recovered[0]==200 and download(identifier)[2]==b'abcde' and len(list(service.objects.glob(app_id()+'-'+identifier+'.*')))==1,
                       {'finish':recovered,'rows':objects(service,identifier)},'explicit retry verifies renamed bytes and publishes exactly one object')
        delete(identifier,secret);service.files_cli()
        # Different shared directory fails both trusted initialization and serve.
        alternate=root/'other-objects';alternate.mkdir(mode=0o700)
        service.runtime['objects']=str(alternate)
        denied=service.cli('project-files-init',project,'--postgres-env',ENV_NAME,'--runtime-env','DIAMOND_W6_INDEPENDENT_RUNTIME',success=False)
        serving=service.cli('serve',project,'--listen',f"127.0.0.1:{free_port()}",'--origin',service.origin,'--postgres-env',ENV_NAME,'--identity-mailbox',service.mailbox,'--runtime-env','DIAMOND_W6_INDEPENDENT_RUNTIME',success=False)
        receipt.record('different-root-fails-closed',denied.returncode!=0 and serving.returncode!=0 and not list(alternate.glob('*.blob')),
                       {'initialize_exit':denied.returncode,'serve_exit':serving.returncode,'fallback_blobs':len(list(alternate.glob('*.blob')))},'a different directory identity cannot rebind app storage or create local fallback')
        service.runtime['objects']=str(service.objects)
        # Live policy changes apply after reservation too.
        _,identifier,secret=begin('revoked',1)
        service.sql(f"DELETE FROM {table('Memberships')} WHERE \"user\"='alice' AND workspace='red'")
        revoked=service.transfer('B',clients['alice'],PREFIX+identifier,b'x',upload=secret)
        receipt.record('permission-rechecked-after-reservation',revoked[0]>=400 and finish(identifier,secret)[0]>=400,{'put_status':revoked[0]},'a valid upload token cannot bypass committed parent-role revocation')
    except Exception as failure:fatal=repr(failure)
    finally:
        try:service.close()
        except Exception as cleanup:fatal=(fatal or '')+' cleanup='+repr(cleanup)
        if auxiliary is not None:
            try:auxiliary.close()
            except Exception as cleanup:fatal=(fatal or '')+' external='+repr(cleanup)
        report=receipt.report(service,sha256(binary),error=fatal,temporary_root=str(root),scope='real two-node shared files and policy/stream/corruption behavior; frontend and durable-mount operations separate')
        options.report.write_text(json.dumps(report,indent=2)+'\n')
        if report['status']=='PASS':shutil.rmtree(root)
    print(json.dumps({'status':report['status'],'observations':len(receipt.results),'error':report['error']}))
    return report['status']!='PASS'


if __name__=='__main__':raise SystemExit(main())
