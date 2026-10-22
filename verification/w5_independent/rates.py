#!/usr/bin/env python3
"""Actual shared rate caps, rollback survival, races and process restart."""
from __future__ import annotations
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import shutil
import tempfile

from accounts import begin_window, client, issue_token, provision
from common import Receipt, app_id, nominal, quote, seed
from fixture import write
from model import PASSWORD
from service import ENV_NAME, Service, sha256

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary',type=Path,required=True)
    parser.add_argument('--report',type=Path,required=True)
    options=parser.parse_args()
    root=Path(tempfile.mkdtemp(prefix='diamond-w5-independent-rates-'))
    binary=root/'diamond';shutil.copy2(options.binary,binary)
    project=write(root/'project')
    service=Service(root,binary,project);service.extra_serve=('--identity-mailbox',str(service.mailbox))
    receipt,fatal=Receipt(),None;receipt.secret(PASSWORD)
    application=app_id()

    def counters():
        return service.sql_json("SELECT bucket,attempts,expires>clock_timestamp() AS live FROM diamond_identity.rates WHERE app="+quote(application)+" ORDER BY bucket")

    def generated(count, function):
        with ThreadPoolExecutor(max_workers=8) as executor:
            return list(executor.map(function,range(count)))

    def cap(name,outcomes,success,limit):
        statuses=Counter(code for code,_ in outcomes)
        receipt.record(name,statuses==Counter({success:limit,429:len(outcomes)-limit}),
                       {'statuses':dict(statuses),'counters':counters()},
                       {'accepted_or_main_failure_status':success,'within_rate_cap':limit,'rate_limited':len(outcomes)-limit,
                        'no_rate_fixture_mutation_during_this_phase':True})

    try:
        service.postgres();contract,_=service.build()
        service.cli('project-init',project,'--postgres-env',ENV_NAME)
        provision(service);provision(service,'bob');seed(service)
        service.start_node('A');service.start_node('B')
        alice=client(service,contract['rpc_id'],'alice')
        anonymous=client(service,contract['rpc_id'])
        begin_window(service,receipt,'confirmed-abort-rate-phase')
        service.sql("CREATE FUNCTION diamond_identity.independent_abort() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'synthetic confirmed identity abort' USING ERRCODE='40001'; END $$")
        service.sql("CREATE TRIGGER independent_abort BEFORE INSERT ON diamond_identity.credentials FOR EACH ROW EXECUTE FUNCTION diamond_identity.independent_abort()")
        body={'label':'Aborted issuance','operations':['read'],'scopes':{'workspace':['red']},'lifetime_seconds':'3600'}
        outcomes=[alice.account('create_token',body,node='A' if index%2 else 'B') for index in range(14)]
        statuses=Counter(code for code,_ in outcomes)
        recorded=counters()
        receipt.record('auth-global-peer-rate-survives-main-rollback',statuses==Counter({409:14}) and any(row['bucket']=='auth:global' and row['attempts']==14 for row in recorded),
                       {'statuses':dict(statuses),'counters':recorded},'all fourteen main operations distinctly abort, independently committed global/peer attempts remain14; authenticated account body has no declared subject')
        count=service.sql("SELECT count(*) FROM diamond_identity.credentials WHERE app="+quote(application))
        receipt.record('aborted-credential-issuance-has-no-row',count=='0',count,'all allowed identity transactions really aborted; independently committed attempts remain')
        post=[alice.account('create_token',body,node=node) for node in ['A','B']]
        service.stop_node('B');service.start_node('B')
        restarted=alice.account('create_token',body,node='B')
        receipt.record('auth-abort-counter-survives-node-failure',all(status==409 for status,_ in post) and restarted[0]==409 and any(row['bucket']=='auth:global' and row['attempts']==17 for row in counters()),
                       {'both_nodes':post,'fresh_process':restarted,'counters':counters()},'same shared independently committed attempts17 after restart; main credential operations continue distinctly aborting')
        service.sql("DROP TRIGGER independent_abort ON diamond_identity.credentials;DROP FUNCTION diamond_identity.independent_abort()")
        begin_window(service,receipt,'failed-password-rate-phase')
        login_body={'username':'bob','password':'Wrong synthetic password 00!'}
        outcomes=generated(14,lambda index:anonymous.request('A' if index%2 else 'B','/_diamond/login',login_body))
        cap('auth-subject-rate-counts-failed-passwords',outcomes,403,12)
        correct=anonymous.request('B','/_diamond/login',{'username':'bob','password':PASSWORD})
        receipt.record('correct-password-cannot-bypass-live-failed-attempt-cap',correct[0]==429,{'status':correct[0],'value':correct[1]},'shared subject cap persists despite main authentication failures')
        service.stop_node('B');service.start_node('B')
        restarted=anonymous.request('B','/_diamond/login',login_body)
        receipt.record('auth-subject-counter-survives-node-restart',restarted[0]==429,{'status':restarted[0],'value':restarted[1]},'fresh node observes committed failed-password subject cap')

        begin_window(service,receipt,'api-success-rate-phase')
        bearer,_=issue_token(alice,receipt,operations=['read'],label='Rate qualification')
        outcomes=generated(128,lambda index:alice.api('read',bearer,[nominal('issue','issue-red-one')],node='A' if index%2 else 'B'))
        cap('api-subject-shared-cap',outcomes,200,120)
        service.stop_node('B');service.start_node('B')
        restarted=alice.api('read',bearer,[nominal('issue','issue-red-one')],node='B')
        receipt.record('api-counter-survives-node-restart',restarted[0]==429,{'status':restarted[0],'value':restarted[1]},'fresh node observes shared cap before executing API operation')
        begin_window(service,receipt,'api-policy-rollback-rate-phase')
        outcomes=generated(128,lambda index:alice.api('read',bearer,[nominal('issue','issue-blue-one')],node='A' if index%2 else 'B'))
        cap('api-rate-survives-policy-rollback',outcomes,403,120)

        begin_window(service,receipt,'api-public-subject-rate-phase')
        def public(index,source=None):
            status,_,raw=service.request('A' if index%2 else 'B','/api/v1/public_value',{'arguments':[]},{'X-Diamond-API-Version':'1'},source=source)
            return status,json.loads(raw)
        outcomes=generated(128,public)
        cap('api-public-shared-subject-cap',outcomes,200,120)
        begin_window(service,receipt,'api-token-issuance-for-independent-caps')
        valid_bearers=[bearer]+[issue_token(alice,receipt,operations=['read'],label='rate-subject-'+str(index))[0] for index in range(5)]
        def token_read(index,subjects=3,source=None):
            status,_,raw=service.request('A' if index%2 else 'B','/api/v1/read',{'arguments':[nominal('issue','issue-red-one')]},
                                         {'X-Diamond-API-Version':'1','Authorization':'Bearer '+valid_bearers[index%subjects]},source=source)
            return status,json.loads(raw)
        begin_window(service,receipt,'api-peer-rate-phase')
        outcomes=generated(308,token_read)
        cap('api-peer-shared-cap-across-distinct-credential-subjects',outcomes,200,300)
        begin_window(service,receipt,'api-global-rate-phase')
        outcomes=generated(608,lambda index:token_read(index,6,'127.0.0.'+str(2+index%3)))
        cap('api-global-shared-cap-across-real-peers-and-credential-subjects',outcomes,200,600)
        begin_window(service,receipt,'auth-peer-rate-phase')
        def bootstrap(index,source=None):
            status,_,raw=service.request('A' if index%2 else 'B','/_diamond/session',headers={'X-Diamond-Session':'1'},source=source)
            return status,json.loads(raw)
        outcomes=generated(108,bootstrap)
        cap('auth-peer-shared-cap-without-subject',outcomes,200,100)
        begin_window(service,receipt,'auth-global-rate-phase')
        outcomes=generated(128,lambda index:bootstrap(index,'127.0.0.'+str(2+index%2)))
        cap('auth-global-shared-cap-across-distinct-real-peers',outcomes,200,120)
        row_count=service.sql("SELECT count(*) FROM diamond_identity.rates WHERE app="+quote(application))
        receipt.record('rate-keyspace-remains-bounded-in-exercised-inputs',int(row_count)<=30,{'rows':int(row_count)},'fixed finite global/peer/subject fixtures do not create an unbounded row per attempt')
    except Exception as failure:
        fatal=repr(failure)
    finally:
        try:service.close()
        except Exception as cleanup:fatal=(fatal or '')+' cleanup='+repr(cleanup)
        report=receipt.report(service,sha256(binary),error=fatal,temporary_root=str(root),
                               documented_caps={'auth':{'global':120,'peer':100,'subject':12},'api':{'global':600,'peer':300,'subject':120}},
                               scope='real shared rate decisions, eight-client bounded races, controlled independent phases, real loopback peer sources and actual rollback/restart; no capacity or distributed-edge claim')
        options.report.write_text(json.dumps(report,indent=2)+'\n')
        if report['status']=='PASS':shutil.rmtree(root)
    print(json.dumps({'status':report['status'],'observations':len(receipt.results),'requests':report['http_requests'],'error':report['error']}))
    return report['status']!='PASS'


if __name__=='__main__':raise SystemExit(main())
