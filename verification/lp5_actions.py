#!/usr/bin/env python3
"""Drive actual LP5 action controls on two native servers and isolated PostgreSQL."""
from __future__ import annotations
import argparse,concurrent.futures,hashlib,json,shutil,sys,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'verification'))
from w5_independent.service import Client,Service,ENV_NAME,sha256
from lp2_spine import literal,nominal,restrict
from lp4_components import Gate
APP=hashlib.sha256(b'ActionLab').hexdigest();PASSWORD='LP5 synthetic fixture password 101!'
def table(name):return 'diamond_w4."r_'+APP[:16]+'_'+hashlib.sha256(name.encode()).hexdigest()[:16]+'"'
def rows(service):return service.sql_json(f"SELECT id,workspace,convert_from(decode(title,'hex'),'UTF8') AS title,convert_from(decode(note,'hex'),'UTF8') AS note,revision FROM {table('Issues')} ORDER BY id")
def seed(service):
    service.sql(f'DELETE FROM {table("Issues")}; DELETE FROM {table("Members")};')
    service.sql(f'INSERT INTO {table("Members")}(id,workspace,"user",role) VALUES '+"('member-alice','alpha','alice','Editor'),('member-bob','beta','bob','Editor')")
    for suffix in ['a','b','c','d']:
        service.sql(f'INSERT INTO {table("Issues")}(id,workspace,title,note,revision) VALUES ({literal("alpha-"+suffix)},\'alpha\',{literal(("Issue "+suffix.upper()).encode().hex())},{literal("Original note".encode().hex())},0)')
def arguments(title='LP5 saved B',parent='alpha-b',note='LP5 note'):return [nominal('issue',parent),{'title':title,'note':note}]
def control(client,mode,args=None,invocation=None,*,node='A',name='rename'):
    body={'contract':client.contract}
    if args is not None:body['arguments']=args
    if invocation is not None:body['invocation']=invocation
    return client.request(node,'/_diamond/actions/'+name+('/'+mode if mode else ''),body)
def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--binary',type=Path,default=ROOT/'target/debug/diamond');parser.add_argument('--report',type=Path,default=ROOT/'verification/lp5/actions.json');parser.add_argument('--serve',action='store_true');opts=parser.parse_args();root=Path(tempfile.mkdtemp(prefix='diamond-lp5-'));binary=root/'diamond';shutil.copy2(opts.binary,binary);project=ROOT/'verification/lp5/project/diamond.toml';service=Service(root,binary,project);service.extra_serve=('--identity-mailbox',str(service.mailbox));gate=None;error=None;observations=[]
    def record(name,condition,**details):
        observations.append({'name':name,'pass':bool(condition),**details})
        if not condition:raise AssertionError(observations[-1])
    try:
        service.postgres();metadata,build=service.build();contract=metadata['rpc_id'];service.cli('project-init',project,'--postgres-env',ENV_NAME)
        for user in ['alice','bob','reader']:service.cli('project-user-add',project,'--postgres-env',ENV_NAME,'--user',user,'--password-stdin',input=PASSWORD+'\n')
        seed(service);restrict(service);service.sql('GRANT SELECT,INSERT,UPDATE,DELETE ON diamond_actions.invocations TO lp2_runtime');service.sql('GRANT SELECT,INSERT,UPDATE,DELETE ON diamond_w7.events TO lp2_runtime');service.sql('GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA diamond_w7 TO lp2_runtime');service.start_node('A');service.start_node('B')
        alice=Client(service,contract);alice.login('alice',PASSWORD);fresh=Client(service,contract);fresh.login('alice',PASSWORD);bob=Client(service,contract);bob.login('bob',PASSWORD)
        before=rows(service);args=arguments();status,prepared=control(alice,'prepare',args);record('prepare',status==200 and len(prepared.get('invocation',''))==64,observed=(status,prepared));invocation=prepared['invocation']
        again=control(fresh,'prepare',args,node='B');record('same-principal-concurrent-intent',again==(200,prepared),observed=again)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:outcomes=list(pool.map(lambda pair:control(pair[0],'',args,invocation,node=pair[1]),[(alice,'A'),(fresh,'B')]))
        record('concurrent-single-mutation',all(s==200 and value.get('value',{}).get('tag')=='ActionSucceeded' for s,value in outcomes) and [r['revision'] for r in rows(service) if r['id']=='alpha-b']==[1],observed=outcomes,rows=rows(service))
        recovered=control(fresh,'status',invocation=invocation,node='B');record('fresh-session-recovery',recovered==outcomes[0],observed=recovered)
        changed=control(fresh,'',arguments('Changed payload'),invocation);record('changed-input-conflict',changed[0]==200 and changed[1]['value']['tag']=='ActionConflict',observed=changed)
        wrong=control(bob,'status',invocation=invocation);record('other-principal-denied',wrong[0]==403 and 'LP5 saved' not in json.dumps(wrong),observed=wrong)
        control(alice,'ack',invocation=invocation)
        rejected_args=arguments('reject');_,prepared=control(alice,'prepare',rejected_args);rejected=control(alice,'',rejected_args,prepared['invocation']);readback=control(fresh,'status',invocation=prepared['invocation']);record('application-error-rollback-receipt',rejected[0]==200 and rejected[1]['value']['tag']=='ActionRejected' and readback==rejected and [r['revision'] for r in rows(service) if r['id']=='alpha-b']==[1],observed=rejected,rows=rows(service));control(alice,'ack',invocation=prepared['invocation'])
        print(json.dumps({'backend':'PASS','observations':len(observations),'binary_sha256':sha256(binary)}),flush=True)
        from lp5_campaign import campaign
        campaign(service,contract,record)
        if opts.serve:
            seed(service);service.sql('DELETE FROM diamond_actions.invocations');service.origin='http://127.0.0.1:39171';service.authority=service.origin.removeprefix('http://');service.stop_node('A');service.stop_node('B');service.start_node('A');service.start_node('B');gate=Gate(39171,service.ports['A'])
            print(json.dumps({'ready':True,'origin':service.origin,'root':str(root),'password':PASSWORD,'binary_sha256':sha256(binary),'contract':contract}),flush=True)
            for line in sys.stdin:
                command=line.strip()
                if command=='stop':break
                if command=='observe':
                    observation={'name':'browser-independent-sql','rows':rows(service),'invocations':service.sql_json("SELECT id,username,endpoint,ack FROM diamond_actions.invocations ORDER BY id"),'forwarded_actions':gate.actions_seen,'held':gate.held};observations.append(observation);print(json.dumps(observation),flush=True)
                if command=='arm':gate.arm();print('{"armed":true}',flush=True)
                if command=='release':gate.release.set();print('{"released":true}',flush=True)
                if command=='drop':gate.arm();gate.drop=True;print('{"drop_armed":true}',flush=True)
        print('{"status":"PASS"}',flush=True)
    except Exception as failure:error=repr(failure);print(json.dumps({'status':'FAIL','error':error}),flush=True)
    finally:
        if gate:gate.close()
        service.close();opts.report.parent.mkdir(parents=True,exist_ok=True);opts.report.write_text(json.dumps({'status':'PASS' if error is None and not service.cleanup_errors else 'FAIL','error':error,'binary_sha256':sha256(binary),'driver_sha256':sha256(Path(__file__)),'observations':observations,'shutdowns':service.shutdowns,'cleanup_errors':service.cleanup_errors},indent=2)+'\n');shutil.rmtree(root)
    return int(error is not None or bool(service.cleanup_errors))
if __name__=='__main__':raise SystemExit(main())
