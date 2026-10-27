#!/usr/bin/env python3
"""Directed and seeded real-interface LP5 schedules, with independent SQL oracles."""
from __future__ import annotations
import base64,concurrent.futures,hashlib,http.client,json,random,shutil,subprocess,time
from pathlib import Path
from lp5_actions import APP,PASSWORD,arguments,control,rows,table
from lp5_pg_faults import CommitProxy
from lp2_spine import literal,nominal
from lp4_components import Gate
from w5_independent.service import Client,free_port
SEED=20261027
def tag(reply):return reply[1].get('value',{}).get('tag') if reply[0]==200 else None
def revision(service,key='alpha-b'):return next(row['revision'] for row in rows(service) if row['id']==key)
def campaign(service,contract,record):
    rng=random.Random(SEED)
    def login():
        client=Client(service,contract);client.login('alice',PASSWORD);return client
    alice=login();fresh=login()
    def prepare(args,name='rename',client=None):
        reply=control(client or alice,'prepare',args,name=name)
        if reply[0]!=200 or 'invocation' not in reply[1]:raise AssertionError(('prepare',name,reply))
        return reply[1]['invocation']
    def ack(invocation,name='rename',client=None):
        reply=control(client or alice,'ack',invocation=invocation,name=name)
        if reply!=(200,{'acknowledged':True}):raise AssertionError(('ack',reply))
    # Vary Unicode and scalar boundaries, record order, target and server schedule.
    for index,title in enumerate(['First generated','雪😀','a'*120,'Quoted " value','Leading spaces  ','A/B literal','distinct-seven','Final generated']):
        parent=rng.choice(['alpha-b','alpha-c']);args=arguments(title,parent,note='n'*80 if index==2 else 'Generated note');before=rows(service)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:prepared=list(pool.map(lambda pair:control(pair[0],'prepare',args,node=pair[1]),[(alice,'A'),(fresh,'B')]))
        record(f'generated-{index}-concurrent-prepare',prepared[0]==prepared[1] and prepared[0][0]==200 and 'invocation' in prepared[0][1],observed=prepared)
        invocation=prepared[0][1]['invocation'];order=[(alice,'A'),(fresh,'B')];rng.shuffle(order)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:outcomes=list(pool.map(lambda pair:control(pair[0],'',args,invocation,node=pair[1]),order))
        expected=[dict(row,title=title,note=args[1]['note'],revision=row['revision']+1) if row['id']==parent else row for row in before]
        reordered=[{'value':parent,'tag':'issue'},{'note':args[1]['note'],'title':title}];cached=control(fresh,'',reordered,invocation,node='B')
        record(f'generated-{index}-exactly-one-and-canonical',tag(outcomes[0])=='ActionSucceeded' and outcomes[0]==outcomes[1]==cached and rows(service)==expected,observed_tags=[tag(x) for x in outcomes+[cached]],rows=rows(service))
        conflict=control(alice,'',arguments(title,parent,'Different note'),invocation);record(f'generated-{index}-changed-input',tag(conflict)=='ActionConflict' and rows(service)==expected,observed=conflict);ack(invocation)
    alice=login()
    args=arguments('Closed before execute');before=rows(service);invocation=prepare(args);status=control(fresh,'status',invocation=invocation);later=control(alice,'',args,invocation)
    record('prepared-status-closes-and-fences',tag(status)=='ActionAborted' and status==later and rows(service)==before,observed=[status,later]);ack(invocation)
    args=arguments('Binding target');invocation=prepare(args);changed=control(alice,'',arguments('Binding target','alpha-a'),invocation);record('changed-parent-no-execution',tag(changed)=='ActionConflict' and rows(service)==before,observed=changed);control(alice,'status',invocation=invocation);ack(invocation)
    # Version binding through a second actual server, with unchanged database shape.
    args=arguments('Version bound');invocation=prepare(args);project=service.root/'version-project';shutil.copytree(service.project.parent,project);path=project/'server.dia';path.write_text(path.read_text().replace('lp2-server-only-canary','new-checked-action-version'));meta,_=service.build(project/'diamond.toml');service.ports['V']=free_port();service.start_node('V',project=project/'diamond.toml');new=Client(service,meta['rpc_id']);new.login('alice',PASSWORD,node='V');version_reply=control(new,'status',invocation=invocation,node='V');record('changed-project-version',tag(version_reply)=='ActionConflict' and rows(service)==before,observed=version_reply);service.stop_node('V');control(alice,'status',invocation=invocation);ack(invocation)
    # Current policy must protect scalar results even when the declared parent
    # remains readable and the returned value has no resource identifier.
    service.sql(f"UPDATE {table('Issues')} SET title={literal('Scalar source'.encode().hex())},note={literal('LP5-protected-scalar-canary'.encode().hex())} WHERE id='alpha-b'")
    scalar_args=[nominal('issue','alpha-a'),nominal('issue','alpha-b')];invocation=prepare(scalar_args,'protected_scalar');scalar=control(alice,'',scalar_args,invocation,name='protected_scalar');record('protected-scalar-commits',tag(scalar)=='ActionSucceeded' and scalar[1]['value']['fields']==['Scalar source: LP5-protected-scalar-canary'],observed=scalar)
    service.sql(f"UPDATE {table('Issues')} SET workspace='beta' WHERE id='alpha-b'");denied=control(fresh,'status',invocation=invocation,name='protected_scalar');record('scalar-source-current-row-reauthorized',denied[0]==403 and 'LP5-protected-scalar-canary' not in json.dumps(denied),observed=denied);service.sql(f"UPDATE {table('Issues')} SET workspace='alpha' WHERE id='alpha-b'");ack(invocation,'protected_scalar')
    args=arguments('Revocation target');invocation=prepare(args);done=control(alice,'',args,invocation);record('revocation-precondition',tag(done)=='ActionSucceeded',observed=done)
    service.sql(f"UPDATE {table('Members')} SET role='Reader' WHERE id='member-alice'");denied=control(fresh,'status',invocation=invocation);duplicate=control(alice,'',args,invocation);record('current-write-permission-reauthorized',denied[0]==duplicate[0]==403 and 'Revocation target' not in json.dumps([denied,duplicate]),observed=[denied,duplicate]);ack(invocation);service.sql(f"UPDATE {table('Members')} SET role='Editor' WHERE id='member-alice'")
    # CRUD creates/deletes, including deletion of the declared parent itself.
    create_args=[nominal('issue','alpha-a'),{'title':'Created by invocation','note':'Created note'}];invocation=prepare(create_args,'create_child');created=control(alice,'',create_args,invocation,name='create_child');recovered=control(fresh,'status',invocation=invocation,name='create_child');record('create-receipt-recovery',tag(created)=='ActionSucceeded' and recovered==created,observed=created);child=created[1]['value']['fields'][0]['id']['value'];ack(invocation,'create_child')
    delete_args=[nominal('issue',child),nominal('issue',child)];invocation=prepare(delete_args,'delete_child');deleted=control(alice,'',delete_args,invocation,name='delete_child');recovered=control(fresh,'status',invocation=invocation,name='delete_child');record('deleted-parent-tombstone-recovery',tag(deleted)=='ActionSucceeded' and recovered==deleted and not any(row['id']==child for row in rows(service)),observed=[deleted,recovered])
    service.sql(f"INSERT INTO {table('Issues')}(id,workspace,title,note,revision) VALUES ({literal(child)},'beta',{literal('Recreated'.encode().hex())},{literal('Independent row'.encode().hex())},73)")
    recreated_denied=control(fresh,'status',invocation=invocation,name='delete_child');record('recreated-parent-current-policy-denied',recreated_denied[0]==403,observed=recreated_denied)
    service.sql(f"UPDATE {table('Issues')} SET workspace='alpha' WHERE id={literal(child)}");recreated=control(fresh,'status',invocation=invocation,name='delete_child');recreated_retry=control(alice,'',delete_args,invocation,name='delete_child');record('recreated-parent-never-deleted-again',recreated==deleted and recreated_retry==deleted and service.sql(f"SELECT revision FROM {table('Issues')} WHERE id={literal(child)}")=='73',observed=[recreated,recreated_retry])
    service.sql(f"UPDATE {table('Members')} SET role='Reader' WHERE id='member-alice'");denied=control(fresh,'status',invocation=invocation,name='delete_child');record('deleted-parent-current-policy-denial',denied[0]==403,observed=denied);service.sql(f"UPDATE {table('Members')} SET role='Editor' WHERE id='member-alice'");ack(invocation,'delete_child')
    # Corruption changes real persisted control state; no mock response substitutes.
    alice=login()
    for field,value in [('envelope','broken'),('fingerprint','0'*64),('parent','alpha-a'),('resource','Members'),('authority','0'*64),('version','0'*64),('endpoint','api:rename'),('subject','0'*64)]:
        args=arguments('Corruption '+field);before=rows(service);invocation=prepare(args);original=service.sql(f"SELECT {field} FROM diamond_actions.invocations WHERE app={literal(APP)} AND id={literal(invocation)}");service.sql(f'UPDATE diamond_actions.invocations SET {field}={literal(value)} WHERE app={literal(APP)} AND id={literal(invocation)}');status=control(fresh,'status',invocation=invocation);attempt=control(alice,'',args,invocation);record('corrupt-'+field,tag(status)==tag(attempt)=='ActionCorrupt' and rows(service)==before,observed=[status,attempt]);service.sql(f'UPDATE diamond_actions.invocations SET {field}={literal(original)} WHERE app={literal(APP)} AND id={literal(invocation)}');ack(invocation)
    args=arguments('Expired committed');invocation=prepare(args);done=control(alice,'',args,invocation);expected=rows(service);service.sql(f'UPDATE diamond_actions.invocations SET expires=0 WHERE app={literal(APP)} AND id={literal(invocation)}');expired=control(fresh,'status',invocation=invocation);record('committed-receipt-expiration',tag(done)=='ActionSucceeded' and tag(expired)=='ActionExpired',observed=expired)
    cleanup_id=prepare(arguments('Cleanup trigger','alpha-a'));pruned=service.sql(f'SELECT count(*) FROM diamond_actions.invocations WHERE id={literal(invocation)}');old=control(alice,'',args,invocation);record('pruned-identity-never-resurrects',pruned=='0' and tag(old)=='ActionExpired' and rows(service)==expected,observed=old);control(alice,'status',invocation=cleanup_id);ack(cleanup_id)
    for size,expected_tag in [(1000,'ActionSucceeded'),(3000,'ActionAborted')]:
        args=[nominal('issue','alpha-b'),'z'*size];before=rows(service);invocation=prepare(args,'large_receipt');outcome=control(alice,'',args,invocation,name='large_receipt');cached=control(fresh,'status',invocation=invocation,name='large_receipt');expected_revision=revision_from(before,'alpha-b')+(expected_tag=='ActionSucceeded');record(f'receipt-size-{size}',tag(outcome)==expected_tag and outcome==cached and revision(service)==expected_revision,observed_tag=tag(outcome),returned_text_bytes=len(outcome[1].get('value',{}).get('fields',[''])[0]) if tag(outcome)=='ActionSucceeded' else 0);ack(invocation,'large_receipt')
    for index in range(63):service.sql(f"INSERT INTO {table('Issues')}(id,workspace,title,note,revision) VALUES ('bulk-{index}','alpha',{literal('Bulk row'.encode().hex())},{literal('Bulk note'.encode().hex())},0)")
    for count,expected_tag in [(62,'ActionSucceeded'),(63,'ActionAborted')]:
        args=[nominal('issue','alpha-b'),[nominal('issue',f'bulk-{index}') for index in range(count)]];before=rows(service);invocation=prepare(args,'bulk_read');outcome=control(alice,'',args,invocation,name='bulk_read');cached=control(fresh,'status',invocation=invocation,name='bulk_read');record(f'footprint-boundary-{count+2}',tag(outcome)==expected_tag and cached==outcome and revision(service)==revision_from(before,'alpha-b')+(expected_tag=='ActionSucceeded'),observed=[outcome,cached]);ack(invocation,'bulk_read')
    service.sql(f"DELETE FROM {table('Issues')} WHERE id LIKE 'bulk-%'")
    # Identity and API authority fingerprints, not opaque token equality alone.
    args=arguments('Account epoch');invocation=prepare(args);control(alice,'',args,invocation);service.sql("UPDATE diamond_identity.users SET disabled=true,epoch=epoch+1 WHERE username='alice'");old=control(alice,'status',invocation=invocation);record('disabled-account-current-auth',old[0]==401,observed=old);service.sql("UPDATE diamond_identity.users SET disabled=false WHERE username='alice'");alice=login();fresh=login();record('fresh-current-account-can-recover',tag(control(fresh,'status',invocation=invocation))=='ActionSucceeded');ack(invocation)
    operations=['rename','rename_alias','load'];issued=alice.account('create_token',{'label':'LP5 API','operations':operations,'scopes':{'workspace':['alpha']},'lifetime_seconds':'3600'});token=issued[1]['value'];other=alice.account('create_token',{'label':'LP5 other API','operations':['rename'],'scopes':{'workspace':['alpha']},'lifetime_seconds':'3600'})[1]['value'];args=arguments('API committed');prepared=alice.api('rename/prepare',token,args);record('api-prepare',prepared[0]==200 and 'invocation' in prepared[1],observed_status=prepared[0]);invocation=prepared[1]['invocation'];done=alice.api('rename',token,envelope={'arguments':args,'invocation':invocation});cached=fresh.api('rename/status',token,envelope={'invocation':invocation},node='B');record('api-shared-durable-dispatch',tag(done)=='ActionSucceeded' and done==cached,observed_tags=[tag(done),tag(cached)])
    swapped=fresh.api('rename/status',other,envelope={'invocation':invocation});alias=fresh.api('rename_alias/status',token,envelope={'invocation':invocation});cookie=control(alice,'status',invocation=invocation);record('api-profile-subject-alias-bindings',all(tag(value)=='ActionConflict' for value in [swapped,alias,cookie]),observed=[swapped,alias,cookie])
    credential=service.sql("SELECT id FROM diamond_identity.credentials WHERE label='LP5 API'");service.sql(f"UPDATE diamond_identity.credentials SET scopes='{{\"workspace\":[\"beta\"]}}' WHERE id={literal(credential)}");narrow=fresh.api('rename/status',token,envelope={'invocation':invocation});record('same-token-current-scope-fingerprint',tag(narrow)=='ActionConflict',observed=narrow);service.sql(f"UPDATE diamond_identity.credentials SET scopes='{{\"workspace\":[\"alpha\"]}}',operations=ARRAY['rename','load']::text[] WHERE id={literal(credential)}");attenuated=fresh.api('rename/status',token,envelope={'invocation':invocation});record('same-token-operation-fingerprint',tag(attenuated)=='ActionConflict',observed=attenuated);service.sql(f"UPDATE diamond_identity.credentials SET operations=ARRAY['rename','rename_alias','load']::text[] WHERE id={literal(credential)}");fresh.api('rename/ack',token,envelope={'invocation':invocation});service.sql(f'UPDATE diamond_identity.credentials SET revoked=true WHERE id={literal(credential)}');revoked=fresh.api('rename/status',token,envelope={'invocation':invocation});record('revoked-token-current-auth',revoked[0]==401,observed=revoked)
    # Process interruption at a real PostgreSQL row-lock wait.
    args=arguments('Interrupted transaction');before=rows(service);invocation=prepare(args)
    holder=subprocess.Popen(['psql',service.owner,'-X','-q','-At'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True);holder.stdin.write(f"BEGIN;SELECT id FROM {table('Issues')} WHERE id='alpha-b' FOR UPDATE;\n\\echo locked\n");holder.stdin.flush()
    while holder.stdout.readline().strip()!='locked':pass
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            def interrupted():
                try:return control(alice,'',args,invocation)
                except (OSError,http.client.HTTPException) as error:return {'transport':type(error).__name__}
            future=pool.submit(interrupted);deadline=time.monotonic()+8;waiting=False
            while time.monotonic()<deadline:
                waiting=service.sql("SELECT count(*) FROM pg_stat_activity WHERE usename='lp2_runtime' AND wait_event_type='Lock' AND query LIKE '%r_"+APP[:16]+"%' AND query LIKE '%FOR UPDATE%'")!='0'
                if waiting:break
                time.sleep(.02)
            record('actual-row-lock-wait',waiting);service.stop_node('A',kill=True);holder.stdin.write('ROLLBACK;\n\\q\n');holder.stdin.flush();holder.communicate(timeout=5);lost=future.result(timeout=5)
        service.start_node('A');status=control(fresh,'status',invocation=invocation,node='B');record('process-kill-before-commit',tag(status)=='ActionAborted' and rows(service)==before,transport=lost,observed=status);ack(invocation)
    finally:
        if holder.poll() is None:holder.kill();holder.communicate()
    # Actual lost PostgreSQL COMMIT acknowledgement, distinct from HTTP loss.
    original=service.configuration;proxy=CommitProxy(service.root/'pgproxy',service.sockets,service.pg_port)
    try:
        service.stop_node('A');service.stop_node('B');service.configuration=original.replace(str(service.sockets),str(proxy.path.parent));service.start_node('A');service.start_node('B')
        for mode,expected in [('before_commit','ActionAborted'),('after_commit_ack_loss','ActionSucceeded')]:
            args=arguments('PG fault '+mode);before=rows(service);invocation=prepare(args);proxy.arm(mode);lost=control(alice,'',args,invocation);hit=proxy.hit.wait(3);status=control(fresh,'status',invocation=invocation,node='B');record('postgres-'+mode,hit and tag(status)==expected and revision(service)==revision_from(before,'alpha-b')+(expected=='ActionSucceeded'),observed_http=lost,observed_status=status,boundary=proxy.events[-1] if proxy.events else None);ack(invocation)
    finally:
        service.stop_node('A');service.stop_node('B');service.configuration=original;proxy.close();service.start_node('A');service.start_node('B')
    # Native quota bounds: 16 actual pending identities, then count-only seeded
    # control rows for total/global capacity (never treated as valid receipts).
    alice=login();fresh=alice
    for index in range(17):service.sql(f"INSERT INTO {table('Issues')}(id,workspace,title,note,revision) VALUES ('cap-{index}','alpha',{literal('Capacity'.encode().hex())},{literal('Capacity note'.encode().hex())},0)")
    pending=[prepare(arguments('Pending',f'cap-{index}')) for index in range(16)]
    service.sql('DELETE FROM diamond_identity.rates')
    pending_count=service.sql(f"SELECT count(*) FROM diamond_actions.invocations WHERE app={literal(APP)} AND username='alice' AND NOT ack")
    blocked=control(alice,'prepare',arguments('Pending','cap-16'))
    record('pending-capacity-16',pending_count=='16' and blocked==(429,{'error':'rate_limited'}),observed=blocked,native_pending_count=pending_count,identity_rate_attempts=service.sql('SELECT max(attempts) FROM diamond_identity.rates'))
    service.sql('UPDATE diamond_actions.invocations SET expires=0 WHERE NOT ack');invocation=prepare(arguments('After cleanup','cap-16'));record('expired-pending-cleanup',tag(control(alice,'status',invocation=pending[0]))=='ActionExpired');control(alice,'status',invocation=invocation);ack(invocation)
    template=service.sql_json('SELECT * FROM diamond_actions.invocations LIMIT 1')[0]
    def seed_capacity(total,username):
        current=int(service.sql('SELECT count(*) FROM diamond_actions.invocations'+(" WHERE username='alice'" if username=='alice' else '')))
        values=','.join(literal(str(value)) for value in [username,template['endpoint'],template['subject'],template['authority'],template['version'],template['resource'],template['parent'],template['fingerprint']])
        service.sql('INSERT INTO diamond_actions.invocations(app,id,username,endpoint,subject,authority,version,resource,parent,fingerprint,expires,ack,envelope) SELECT '+literal(APP)+",'ee55'||lpad(to_hex(n),60,'0'),"+values+",floor(extract(epoch FROM clock_timestamp()))::bigint+86400,true,"+literal(template['envelope'])+f' FROM generate_series(0,{total-current-1}) AS gs(n)')
    seed_capacity(128,'alice');service.sql('DELETE FROM diamond_identity.rates');user_count=service.sql(f"SELECT count(*) FROM diamond_actions.invocations WHERE app={literal(APP)} AND username='alice'");blocked=control(alice,'prepare',arguments('User capacity','cap-16'));record('total-user-capacity-128',user_count=='128' and blocked==(429,{'error':'rate_limited'}),observed=blocked,native_user_count=user_count,identity_rate_attempts=service.sql('SELECT max(attempts) FROM diamond_identity.rates'),setup='Count-only injected control rows; envelopes are not claimed valid');service.sql("DELETE FROM diamond_actions.invocations WHERE id LIKE 'ee55%'")
    seed_capacity(4096,'capacity');service.sql('DELETE FROM diamond_identity.rates');global_count=service.sql(f"SELECT count(*) FROM diamond_actions.invocations WHERE app={literal(APP)}");blocked=control(alice,'prepare',arguments('Global capacity','cap-16'));record('global-capacity-4096',global_count=='4096' and blocked==(429,{'error':'rate_limited'}),observed=blocked,native_global_count=global_count,identity_rate_attempts=service.sql('SELECT max(attempts) FROM diamond_identity.rates'),setup='Count-only injected control rows; envelopes are not claimed valid');service.sql("DELETE FROM diamond_actions.invocations WHERE id LIKE 'ee55%'");service.sql(f"DELETE FROM {table('Issues')} WHERE id LIKE 'cap-%'")
    invocation=prepare(arguments('Valid after capacity'));record('valid-after-capacity-cleanup',tag(control(alice,'status',invocation=invocation))=='ActionAborted');ack(invocation)
    print(json.dumps({'campaign':'PASS','seed':SEED,'postgres_faults':proxy.events}),flush=True)
def revision_from(state,key):return next(row['revision'] for row in state if row['id']==key)
