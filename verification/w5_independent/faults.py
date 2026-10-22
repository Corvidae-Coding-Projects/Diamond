#!/usr/bin/env python3
"""Real postcommit-delivery, commit-ack, abort, revocation and store faults."""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import shutil
import tempfile

from accounts import begin_window, client, delivered, issue_token, provision
from common import Receipt, app_id, nominal, quote, seed, wait_for
from fixture import write
from model import PASSWORD
from pg_proxy import PostgresProxy
from service import ENV_NAME, Service, sha256

ROOT=Path(__file__).resolve().parents[2]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary',type=Path,required=True)
    parser.add_argument('--report',type=Path,required=True)
    options=parser.parse_args()
    root=Path(tempfile.mkdtemp(prefix='diamond-w5-independent-faults-'))
    binary=root/'diamond';shutil.copy2(options.binary,binary)
    project=write(root/'project')
    service=Service(root,binary,project);service.extra_serve=('--identity-mailbox',str(service.mailbox))
    receipt,fatal,proxy=Receipt(),None,None;receipt.secret(PASSWORD)
    application=app_id()

    def challenges(email):
        return service.sql_json("SELECT purpose,email,username,epoch,consumed,expires>clock_timestamp() AS live FROM diamond_identity.challenges WHERE app="+quote(application)+" AND email="+quote(email)+" ORDER BY digest")

    def attempts():
        return int(service.sql("SELECT coalesce(sum(attempts),0) FROM diamond_identity.rates WHERE app="+quote(application)))

    try:
        service.postgres();contract,_=service.build()
        service.cli('project-init',project,'--postgres-env',ENV_NAME)
        for username in ['alice','bob','carol']:provision(service,username)
        seed(service)
        directory=root/'proxy';directory.mkdir(mode=0o700)
        proxy=PostgresProxy(directory/'.s.PGSQL.55459',service.sockets/'.s.PGSQL.55459')
        proxy_configuration=service.configuration.replace(str(service.sockets),str(directory))
        service.start_node('A',configuration=proxy_configuration);service.start_node('B')
        alice,bob=client(service,contract['rpc_id'],'alice'),client(service,contract['rpc_id'],'bob',node='B')
        anonymous=client(service,contract['rpc_id'])
        alice_bearer,_=issue_token(alice,receipt,operations=['read'],label='fault-alice')
        bob_bearer,_=issue_token(bob,receipt,operations=['read'],label='fault-bob',node='B')
        begin_window(service,receipt,'delivery-fault')
        original_mailbox=root/'mailbox-held';service.mailbox.rename(original_mailbox)
        service.mailbox.write_text('controlled path is now a regular file');service.mailbox.chmod(0o600)
        try:
            delivery_failure=anonymous.account('recover',{'email':'bob@qualification.invalid'})
            committed=challenges('bob@qualification.invalid')
        finally:
            service.mailbox.unlink();original_mailbox.rename(service.mailbox)
        existing_api=bob.api('read',bob_bearer,[nominal('issue','issue-red-one')],node='B')
        receipt.record('delivery-failure-after-commit',delivery_failure[0]==503 and len(committed)==1 and not committed[0]['consumed'] and existing_api[0]==200,
                       {'http':delivery_failure,'committed_challenge':committed,'existing_api_status':existing_api[0]},
                       'delivery fails safely after actual issuance commit; committed pending challenge remains, existing account authority unchanged')
        before=service.mailbox_snapshot()
        recovery=anonymous.account('recover',{'email':'bob@qualification.invalid'},node='B')
        recovered_secret=delivered(service,before,recipient='bob@qualification.invalid',purpose='recover',receipt=receipt)
        receipt.record('delivery-restored-explicit-resubmission',recovery[0]==200 and bool(recovered_secret),{'status':recovery[0],'value':recovery[1]},
                       'caller explicitly requests new delivery after restoration; no implicit retry from failed request')

        begin_window(service,receipt,'commit-ack-fault')
        before=service.mailbox_snapshot()
        proxy.arm_commit_loss(lambda statements:any('diamond_identity.challenges' in sql and 'INSERT' in sql for sql in statements))
        unknown=anonymous.account('recover',{'email':'carol@qualification.invalid'})
        wait_for(proxy.dropped_commit.is_set,'real identity COMMIT acknowledgment withheld')
        carol=challenges('carol@qualification.invalid')
        receipt.record('identity-commit-ack-loss-is-unknown',unknown==(503,{'error':'outcome_unknown'}) and len(carol)==1 and not carol[0]['consumed'] and not service.mailbox_delta(before),
                       {'http':unknown,'committed_challenge':carol,'actual_delivery_files':len(service.mailbox_delta(before))},
                       'PG really committed once but caller cannot confirm; no delivery or automatic replay after unknown commit')
        recovered=alice.api('read',alice_bearer,[nominal('issue','issue-red-one')])
        receipt.record('uncertain-connection-replaced',recovered[0]==200,{'status':recovered[0],'value':recovered[1]},'fresh owned connection safely resumes service after discarded uncertain transport')

        before_challenges=challenges('carol@qualification.invalid');before_mail=service.mailbox_snapshot();before_attempts=attempts()
        service.sql("CREATE FUNCTION diamond_identity.independent_commit_abort() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'synthetic confirmed identity serialization abort' USING ERRCODE='40001'; END $$")
        service.sql("CREATE CONSTRAINT TRIGGER independent_commit_abort AFTER INSERT ON diamond_identity.challenges DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION diamond_identity.independent_commit_abort()")
        aborted=anonymous.account('recover',{'email':'carol@qualification.invalid'},node='B')
        service.sql("DROP TRIGGER independent_commit_abort ON diamond_identity.challenges;DROP FUNCTION diamond_identity.independent_commit_abort()")
        after_challenges=challenges('carol@qualification.invalid')
        receipt.record('confirmed-identity-abort-distinct-from-unknown',aborted==(409,{'error':'aborted'}) and after_challenges==before_challenges and not service.mailbox_delta(before_mail) and attempts()>before_attempts,
                       {'http':aborted,'state_unchanged':after_challenges==before_challenges,'attempts_before':before_attempts,'attempts_after':attempts()},
                       'real confirmed COMMIT abort rolls back identity changes and delivery, independently committed rate attempts remain')

        begin_window(service,receipt,'held-snapshot-revocation')
        credential_query=lambda sql:'FROM diamond_identity.credentials' in sql and sql.startswith('SELECT ')
        proxy.hold(credential_query)
        with ThreadPoolExecutor(max_workers=1) as executor:
            earlier=executor.submit(bob.api,'read',bob_bearer,[nominal('issue','issue-red-one')],node='A')
            wait_for(lambda:proxy.gated>=1,'earlier token request read its real authoritative credential snapshot')
            disabled=alice.account('disable',{'username':'bob','disabled':True},node='B')
            proxy.release();earlier_outcome=earlier.result(timeout=20)
        receipt.record('inflight-revocation-declared-snapshot-boundary',disabled[0]==200 and earlier_outcome[0] in [200,409],
                       {'disable':disabled,'earlier_request':earlier_outcome},'earlier serializable request may finish in serial order or distinctly abort; no real-time cancellation promise')
        for node in ['A','B']:
            denied=bob.api('read',bob_bearer,[nominal('issue','issue-red-one')],node=node)
            receipt.record('new-request-after-committed-revocation-'+node,denied[0]==401,{'status':denied[0],'value':denied[1]},'new request rechecks shared revoked identity on either node')

        service.stop_postgres()
        for node in ['A','B']:
            denied=alice.api('read',alice_bearer,[nominal('issue','issue-red-one')],node=node)
            receipt.record('authoritative-store-failure-no-fallback-'+node,denied==(503,{'error':'unavailable'}),{'status':denied[0],'value':denied[1]},'store unavailable grants no cached bearer identity')
        service.start_postgres()
        for node in ['A','B']:
            observed=[]
            for attempt in range(6):
                outcome=alice.api('read',alice_bearer,[nominal('issue','issue-red-one')],node=node);observed.append(outcome)
                if outcome[0]==200:break
            receipt.record('store-restart-explicit-read-recovery-'+node,observed[-1][0]==200 and all(code in [200,503] for code,_ in observed),
                           observed,'bounded explicit read probes replace stale connections; no mutation replay')
            revoked=bob.api('read',bob_bearer,[nominal('issue','issue-red-one')],node=node)
            receipt.record('revocation-survives-store-restart-'+node,revoked[0]==401,{'status':revoked[0],'value':revoked[1]},'database restart preserves committed disablement and revocation')
    except Exception as failure:
        fatal=repr(failure)
    finally:
        if proxy is not None:proxy.release()
        try:service.close()
        except Exception as cleanup:fatal=(fatal or '')+' cleanup='+repr(cleanup)
        if proxy is not None:
            proxy.close()
            if proxy.errors:fatal=(fatal or '')+' proxy='+repr(proxy.errors)
        report=receipt.report(service,sha256(binary),error=fatal,proxy_events=proxy.events if proxy else [],temporary_root=str(root),
                               scope='real committed delivery/transport/serialization/store faults and held shared-authority snapshot; no fake PG responses or production resilience claim')
        options.report.write_text(json.dumps(report,indent=2)+'\n')
        if report['status']=='PASS':shutil.rmtree(root)
    print(json.dumps({'status':report['status'],'observations':len(receipt.results),'error':report['error']}))
    return report['status']!='PASS'


if __name__=='__main__':raise SystemExit(main())
