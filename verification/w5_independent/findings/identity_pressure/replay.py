#!/usr/bin/env python3
"""Bounded admitted identity aborts and safe pressure rejection, not an SLO."""
from __future__ import annotations
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import shutil
import sys
import tempfile
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from accounts import begin_window,client,issue_token,provision
from common import Receipt,app_id,quote
from fixture import write
from model import PASSWORD
from service import ENV_NAME,Service,sha256


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary',type=Path,required=True);parser.add_argument('--report',type=Path,required=True)
    options=parser.parse_args()
    root=Path(tempfile.mkdtemp(prefix='diamond-w5-identity-pressure-'));binary=root/'diamond';shutil.copy2(options.binary,binary)
    project=write(root/'project');service=Service(root,binary,project);service.extra_serve=('--identity-mailbox',str(service.mailbox))
    receipt,fatal=Receipt(),None;receipt.secret(PASSWORD)
    try:
        service.postgres();contract,_=service.build();service.cli('project-init',project,'--postgres-env',ENV_NAME);provision(service)
        service.start_node('A');service.start_node('B');owner=client(service,contract['rpc_id'],'alice')
        begin_window(service,receipt,'pressure-start')
        service.sql("CREATE FUNCTION diamond_identity.independent_abort() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'synthetic confirmed identity abort' USING ERRCODE='40001'; END $$")
        service.sql("CREATE TRIGGER independent_abort BEFORE INSERT ON diamond_identity.credentials FOR EACH ROW EXECUTE FUNCTION diamond_identity.independent_abort()")
        body={'label':'pressure aborted','operations':['read'],'scopes':{'workspace':['red']},'lifetime_seconds':'3600'}
        def attempt(index):
            start=time.monotonic();result=owner.account('create_token',body,node='A' if index%2 else 'B')
            return {'elapsed_seconds':time.monotonic()-start,'status':result[0],'response':result[1]}
        started=time.monotonic()
        with ThreadPoolExecutor(max_workers=8) as executor:outcomes=list(executor.map(attempt,range(14)))
        elapsed=time.monotonic()-started;statuses=Counter(row['status'] for row in outcomes)
        count=int(service.sql("SELECT count(*) FROM diamond_identity.credentials WHERE app="+quote(app_id())))
        attempts=int(service.sql("SELECT attempts FROM diamond_identity.rates WHERE app="+quote(app_id())+" AND bucket='auth:global'"))
        okay=set(statuses)<={409,503} and statuses.get(409,0)>0 and count==0 and statuses.get(409,0)<=attempts<=14 and elapsed<45
        receipt.record('bounded-pressure-confirmed-abort-or-safe-unavailable',okay,
                       {'outcomes':outcomes,'campaign_elapsed_seconds':elapsed,'committed_credentials':count,'committed_global_attempts':attempts,'statuses':dict(statuses)},
                       'admitted confirmed aborts409; bounded admission/deadline failures503; no credential writes; failed main operations consume independently committed attempts; eight clients/14 calls finish within driver deadlines')
        service.sql("DROP TRIGGER independent_abort ON diamond_identity.credentials;DROP FUNCTION diamond_identity.independent_abort()")
        secret,identity=issue_token(owner,receipt,operations=['read'],label='fresh-after-pressure')
        revoked=owner.account('revoke_token',{'id':identity},node='B')
        receipt.record('fresh-work-recovers-after-pressure',bool(secret) and revoked[0]==200,{'revocation':revoked},'owned workers and connections recover; one fresh issuance and cross-node revocation finish')
    except Exception as failure:fatal=repr(failure)
    finally:
        try:service.close()
        except Exception as cleanup:fatal=(fatal or '')+' cleanup='+repr(cleanup)
        report=receipt.report(service,sha256(binary),error=fatal,temporary_root=str(root),scope='safe bounded pressure behavior only; no production-capacity or strict409-under-overload claim')
        options.report.write_text(json.dumps(report,indent=2)+'\n')
        if report['status']=='PASS':shutil.rmtree(root)
    print(json.dumps({'status':report['status'],'error':report['error']}))
    return report['status']!='PASS'


if __name__=='__main__':raise SystemExit(main())
