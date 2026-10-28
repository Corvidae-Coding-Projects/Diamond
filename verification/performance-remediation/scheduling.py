#!/usr/bin/env python3
"""Real HTTP/SQL deadline, fairness and node-wide retry-budget qualification."""
import argparse
import concurrent.futures
import hashlib
import json
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'benchmarks/rails-comparison'))
from run import Client,DIAMOND_PROJECT,ENV_NAME,PASSWORD,Service,diamond_setup,diamond_table

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary',type=Path,default=ROOT/'target/release/diamond')
    parser.add_argument('--report',type=Path,default=Path(__file__).with_name('scheduling-final.json'))
    options=parser.parse_args()
    root=Path(tempfile.mkdtemp(prefix='diamond-scheduling-'))
    project=root/'app';shutil.copytree(DIAMOND_PROJECT.parent,project)
    manifest=project/'diamond.toml';original=manifest.read_text()
    service=Service(root,options.binary.resolve(),manifest)
    service.extra_serve=('--identity-mailbox',str(service.mailbox))
    receipt={'status':'FAIL','binary_sha256':hashlib.sha256(options.binary.read_bytes()).hexdigest(),
             'driver_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'observations':[]}
    holder=None
    def record(name,**observed):
        receipt['observations'].append({'name':name,'status':'PASS',**observed})
        print(json.dumps({'name':name,'status':'PASS'}),flush=True)
    def metrics():
        status,_,body=service.request('A','/_diamond/health/metrics')
        assert status==200,(status,body)
        return json.loads(body)
    def wait_for(predicate,label,timeout=3):
        until=time.monotonic()+timeout
        while time.monotonic()<until:
            if predicate():return
            time.sleep(.01)
        raise AssertionError(label)
    def configure(text):
        if 'A' in service.nodes:service.stop_node('A')
        manifest.write_text(original+'\n'+text)
        service.cli('check',manifest)
        service.start_node('A')
    def api(token,operation,arguments,headers=None):
        return service.request('A','/api/v1/'+operation,{'arguments':arguments},
            {'Authorization':'Bearer '+token,'X-Diamond-API-Version':'1',**(headers or {})})
    def lock(sql):
        nonlocal holder
        holder=subprocess.Popen(['psql',service.owner,'-X','-qAt','-v','ON_ERROR_STOP=1'],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
        holder.stdin.write("BEGIN; "+sql+"; SELECT 'locked';\n");holder.stdin.flush()
        assert holder.stdout.readline().strip()=='locked'
    def unlock():
        nonlocal holder
        holder.stdin.write('COMMIT;\n\\q\n');holder.stdin.flush();holder.communicate(timeout=3);holder=None
    def title(identifier):
        return service.sql(f"SELECT convert_from(decode(title,'hex'),'UTF8') FROM {table} WHERE id='{identifier}'")
    def sql_locks():
        return int(service.sql("SELECT count(*) FROM pg_stat_activity WHERE wait_event_type='Lock'"))
    def create_token(username,workspace):
        client=Client(service,contract);assert client.login(username,PASSWORD)[0]==200
        status,body=client.account('create_token',{'label':'Scheduling fixture','operations':['read','update','create','page'],
            'scopes':{'workspace':[workspace]},'lifetime_seconds':'3600'})
        assert status==200,(status,body)
        return body['value'],client
    try:
        service.postgres()
        target,_,seed=diamond_setup(service,100)
        contract=json.loads(next((root).glob('build-*/contract.json')).read_text())['rpc_id']
        alice=target.bearer
        bob,_=create_token('bob',seed['denied_workspace'])
        service.cli('project-user-add',manifest,'--postgres-env',ENV_NAME,'--user','charlie','--password-stdin',input=PASSWORD+'\n')
        charlie_client=Client(service,contract);assert charlie_client.login('charlie',PASSWORD)[0]==200
        status,workspace=charlie_client.rpc('create_workspace','Charlie workspace');assert status==200
        status,issue=charlie_client.rpc('create_issue',workspace['value'],'Charlie issue','Charlie note','7');assert status==200
        charlie,_=create_token('charlie',workspace['value']);charlie_issue=issue['value']
        table=diamond_table('Issues')
        common='''[runtime]
request_limit=8
blocking_threads=4
database_connections=4
request_timeout_ms=2000
queue_timeout_ms=250
[runtime.fairness]
principal_limit=1
[runtime.fairness.tenants]
alice="acme"
bob="acme"
'''
        configure(common)
        lock(f"UPDATE {table} SET title='{'External fairness'.encode().hex()}' WHERE id='{target.issue}'")
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            held=pool.submit(api,alice,'update',[target.issue,'Alice saved','note'])
            wait_for(lambda:sql_locks()>0,'Alice update did not reach real PostgreSQL lock')
            same_group=pool.submit(api,bob,'read',[target.denied_issue],{'X-Tenant':'invented'})
            wait_for(lambda:metrics()['queued_requests']>=1,'same-group request was not queued')
            started=time.monotonic()
            status,_,body=api(charlie,'read',[charlie_issue],{'X-Tenant':'acme'})
            elapsed=time.monotonic()-started
            assert status==200 and json.loads(body)['value']=='Charlie issue\nCharlie note',(status,body)
            assert elapsed<.6,elapsed
            status,headers,body=same_group.result(timeout=1)
            assert status==503 and json.loads(body)['error']=='overloaded',(status,body)
            unlock()
            assert held.result(timeout=3)[0]==200
        assert title(target.issue)=='Alice saved'
        assert api(bob,'read',[target.issue])[0]==403
        assert api('dia_'+'1'*64,'read',[charlie_issue],{'X-Tenant':'acme'})[0]==401
        record('verified-tenant-fairness-and-no-header-authority',other_group_seconds=round(elapsed,3))
        _,build=service.build()
        emitted=next((build/'assets').glob('*.js')).read_text()
        runtime_line=next(line for line in emitted.splitlines() if line.startswith('const __runtime='))
        assert not any(value in runtime_line for value in ('acme','alice','bob','tenants'))
        record('client-configuration-does-not-expose-tenant-membership',configuration=runtime_line)
        app=hashlib.sha256(b'DiamondRailsBench').hexdigest()
        counter_sql=f"SELECT attempts FROM diamond_identity.rates WHERE app='{app}' AND bucket='api:global'"
        before=int(service.sql(counter_sql))
        assert api(charlie,'read',[charlie_issue])[0]==200
        assert int(service.sql(counter_sql))==before+1
        lock('LOCK TABLE diamond_identity.credentials IN ACCESS EXCLUSIVE MODE')
        started=time.monotonic()
        assert api(charlie,'missing_endpoint',[])[0]==404
        assert time.monotonic()-started<.4
        unlock()
        assert int(service.sql(counter_sql))==before+1
        record('preflight-and-dispatch-charge-once-missing-route-skips-authentication')

        configure('''[runtime]
queue_timeout_ms=200
request_timeout_ms=2000
[runtime.fairness]
peer_limit=1
principal_limit=4
''')
        stream=socket.create_connection(('127.0.0.1',service.ports['A']))
        stream.sendall((f'POST /_diamond/login HTTP/1.1\r\nHost: {service.authority}\r\nOrigin: {service.origin}\r\n'
            'Content-Type: application/json\r\nContent-Length: 16000\r\n\r\n').encode())
        try:
            wait_for(lambda:metrics()['active_requests']>=1,'held peer body was not admitted')
            status,_,_=service.request('A','/_diamond/session',headers={'X-Diamond-Session':'1'})
            assert status==503,status
            status,_,_=service.request('A','/_diamond/session',headers={'X-Diamond-Session':'1'},source='127.0.0.2')
            assert status==200,status
        finally:stream.close()
        wait_for(lambda:metrics()['active_requests']==0,'peer/body permits did not recover')
        record('tcp-peer-fairness-and-disconnect-recovery')

        configure('''[runtime]
request_limit=4
blocking_threads=1
database_connections=1
request_timeout_ms=150
queue_timeout_ms=1000
[runtime.fairness]
peer_limit=4
principal_limit=4
[runtime.workloads.control]
timeout_ms=1000
''')
        lock('LOCK TABLE diamond_identity.sessions IN ACCESS EXCLUSIVE MODE')
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            held=pool.submit(service.request,'A','/_diamond/session',None,{'X-Diamond-Session':'1'})
            wait_for(lambda:sql_locks()>0,'control request did not occupy the executor')
            started=time.monotonic()
            status,headers,body=api(alice,'create',[target.workspace,'Never execute','late executor','1900000001'])
            elapsed=time.monotonic()-started
            assert status==503 and json.loads(body)['error']=='deadline_exceeded',(status,body)
            assert .08<elapsed<.7,elapsed
            assert 'x-diamond-retry' not in headers
            assert held.result(timeout=2)[0]==503
            unlock()
        time.sleep(.2)
        assert service.sql(f'SELECT count(*) FROM {table} WHERE rank=1900000001')=='0'
        wait_for(lambda:metrics()['executing_requests']==0,'expired executor work leaked')
        assert api(alice,'read',[target.issue])[0]==200
        record('one-deadline-including-executor-wait-no-late-mutation',elapsed_seconds=round(elapsed,3))

        configure('''[runtime]
request_timeout_ms=300
[runtime.workloads.control]
timeout_ms=1500
''')
        service.sql(f"CREATE FUNCTION public.scheduling_pause() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN PERFORM pg_sleep(2); RETURN NEW; END $$; CREATE TRIGGER scheduling_pause BEFORE INSERT ON {table} FOR EACH ROW EXECUTE FUNCTION public.scheduling_pause()")
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            started=time.monotonic()
            future=pool.submit(api,alice,'create',[target.workspace,'Abort sleeping SQL','note','1900000002'])
            wait_for(lambda:int(service.sql("SELECT count(*) FROM pg_stat_activity WHERE wait_event='PgSleep'"))>0,
                'SQL never entered pg_sleep',timeout=.25)
            status,headers,body=future.result(timeout=1)
            elapsed=time.monotonic()-started
            assert status==503 and 'x-diamond-retry' not in headers,(status,headers,body)
            assert elapsed<.9,elapsed
            wait_for(lambda:int(service.sql("SELECT count(*) FROM pg_stat_activity WHERE wait_event='PgSleep'"))==0,
                'deadline did not cancel the PostgreSQL statement',timeout=.6)
        service.sql(f'DROP TRIGGER scheduling_pause ON {table}; DROP FUNCTION public.scheduling_pause()')
        assert service.sql(f'SELECT count(*) FROM {table} WHERE rank=1900000002')=='0'
        assert api(alice,'read',[target.issue])[0]==200
        record('sql-deadline-cancellation-and-independent-readback',elapsed_seconds=round(elapsed,3))

        configure('''[runtime]
request_timeout_ms=2000
[runtime.fairness]
principal_limit=2
[runtime.retry]
budget_capacity=1
budget_refill_per_second=0
''')
        lock(f"UPDATE {table} SET title='{'External budget'.encode().hex()}' WHERE id IN ('{target.issue}','{target.denied_issue}')")
        before=metrics()['transaction_retries']
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            futures=[pool.submit(api,token,'update',[identifier,label,'note']) for token,identifier,label in
                [(alice,target.issue,'Alice budget saved'),(bob,target.denied_issue,'Bob budget saved')]]
            wait_for(lambda:sql_locks()>=2,'concurrent conflicts did not reach independent row locks')
            unlock()
            outcomes=[future.result(timeout=3) for future in futures]
        assert sorted(status for status,_,_ in outcomes)==[200,409],outcomes
        assert metrics()['transaction_retries']-before==1
        assert metrics()['retry_budget_denied']>=1
        for index,(identifier,label) in enumerate([(target.issue,'Alice budget saved'),(target.denied_issue,'Bob budget saved')]):
            assert title(identifier)==(label if outcomes[index][0]==200 else 'External budget')
        record('node-wide-budget-under-concurrent-conflicts',statuses=[x[0] for x in outcomes],retries=1)

        def force_one(label):
            lock(f"UPDATE {table} SET title='{'External refill'.encode().hex()}' WHERE id='{target.issue}'")
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future=pool.submit(api,alice,'update',[target.issue,label,'note'])
                wait_for(lambda:sql_locks()>0,'refill case did not block')
                unlock();return future.result(timeout=3)[0]
        configure('''[runtime]
[runtime.retry]
budget_capacity=1
budget_refill_per_second=10
''')
        assert force_one('Refill one')==200
        time.sleep(.15)
        assert force_one('Refill two')==200
        assert metrics()['transaction_retries']==2
        record('retry-budget-refills-at-configured-rate')
        for setting in ('max_retries=0','budget_capacity=0'):
            configure('[runtime]\n[runtime.retry]\n'+setting+'\n')
            assert force_one('Must not retry')==409
            assert metrics()['transaction_retries']==0
        record('zero-retries-or-zero-budget-disable-replay')

        for mode,retries in [('configured',1),('disabled',0)]:
            configure('''[runtime]
request_limit=1
queue_limit=0
blocking_threads=1
database_connections=1
maintenance_threads=1
cleanup_timeout_ms=100
client_event_timeout_ms=65000
[runtime.retry]
jitter_ms=0
client_retries='''+str(retries)+'''
client_max_backoff_ms=100
client_jitter_ms=0
retry_after_seconds=2
''')
            _,build=service.build()
            artifact=next((build/'assets').glob('*.js'))
            stream=socket.create_connection(('127.0.0.1',service.ports['A']))
            stream.sendall((f'POST /_diamond/login HTTP/1.1\r\nHost: {service.authority}\r\nOrigin: {service.origin}\r\n'
                'Content-Type: application/json\r\nContent-Length: 16000\r\n\r\n').encode())
            try:
                wait_for(lambda:metrics()['active_requests']==1,'configured-client holder not admitted')
                config={'path':'/api/v1/_contract','method':'GET',
                    'headers':{'Host':service.authority,'X-Diamond-API-Version':'1'}}
                result=subprocess.run(['node',str(Path(__file__).with_name('client.cjs')),str(artifact),
                    service.origin,mode],input=json.dumps(config),capture_output=True,text=True,timeout=4)
                assert result.returncode==0,(result.stdout,result.stderr)
                record('generated-client-'+mode,observed=json.loads(result.stdout.splitlines()[-1]))
            finally:stream.close()

        invalid=['[runtime]\nrequest_timeout_ms=0','[runtime]\nstream_timeout_ms=600001',
            '[runtime]\nclient_event_timeout_ms=100','[runtime.workloads.transfer]\nexecution_limit=65',
            '[runtime.workloads.interactive]\nreserved_requests=18446744073709551615',
            '[runtime.workloads.interactive]\nreserved_requests=16',
            '[runtime.retry]\nmax_retries=6','[runtime.retry]\nbudget_capacity=4097',
            '[runtime.retry]\nmax_backoff_ms=1','[runtime.retry]\nclient_jitter_ms=5001',
            '[runtime.fairness]\nmax_peers=0','[runtime.fairness]\nunknown=true',
            '[runtime]\ncleanup_timeout_ms=0','[runtime.retry]\njitter_ms=1001',
            '[runtime.workloads.background]\nrequest_limit=1']
        for config in invalid:
            manifest.write_text(original+'\n'+config+'\n')
            result=service.cli('check',manifest,success=False)
            assert result.returncode!=0 and 'panicked' not in result.stderr
        record('invalid-and-overflowing-configuration-rejected',cases=len(invalid))
        receipt['status']='PASS'
    except BaseException as error:
        receipt['error']=repr(error);raise
    finally:
        if holder:
            holder.kill();holder.communicate(timeout=3)
        service.close()
        receipt['shutdowns']=service.shutdowns;receipt['cleanup_errors']=service.cleanup_errors
        receipt['temporary_root']=str(root)
        if receipt['status']=='PASS' and not service.cleanup_errors:
            shutil.rmtree(root);receipt['temporary_root_removed']=True
        options.report.parent.mkdir(parents=True,exist_ok=True)
        options.report.write_text(json.dumps(receipt,indent=2)+'\n')
        print('Report:',options.report,flush=True)
    assert not service.cleanup_errors

if __name__=='__main__':main()
