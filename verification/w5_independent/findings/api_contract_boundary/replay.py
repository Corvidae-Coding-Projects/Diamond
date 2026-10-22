#!/usr/bin/env python3
"""Reserved metadata route cannot be an operation; explicit bad version rejects."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import shutil
import sys
import tempfile

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from common import Receipt
from fixture import api_config,write
from service import ENV_NAME,Service,sha256


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary',type=Path,required=True);parser.add_argument('--report',type=Path,required=True)
    options=parser.parse_args()
    root=Path(tempfile.mkdtemp(prefix='diamond-w5-api-contract-boundary-'));binary=root/'diamond';shutil.copy2(options.binary,binary)
    project=write(root/'project');service=Service(root,binary,project);service.extra_serve=('--identity-mailbox',str(service.mailbox))
    receipt,fatal=Receipt(),None
    try:
        reserved=write(root/'reserved',api=api_config().replace('[http_api.operations.read]','[http_api.operations._contract]',1))
        checked=service.cli('check',reserved,success=None)
        receipt.record('reserved-metadata-name-rejected-at-check',checked.returncode!=0,
                       {'exit':checked.returncode,'stdout':checked.stdout,'stderr':checked.stderr},
                       'declared API operation cannot occupy the reserved metadata route')
        service.postgres();service.build();service.cli('project-init',project,'--postgres-env',ENV_NAME);service.start_node('A')
        baseline_status,_,baseline=service.request('A','/api/v1/_contract')
        current_status,_,current=service.request('A','/api/v1/_contract',headers={'X-Diamond-API-Version':'1'})
        receipt.record('metadata-optional-current-version',baseline_status==current_status==200 and baseline==current,
                       {'unversioned_status':baseline_status,'current_status':current_status,'same_metadata':baseline==current},
                       'public metadata permits omitted header or explicit current version, returning same contract')
        for version in ['2','0','invalid','01']:
            status,_,raw=service.request('A','/api/v1/_contract',headers={'X-Diamond-API-Version':version})
            receipt.record('metadata-rejects-explicit-version-'+version,status==400,{'status':status,'response':raw.decode()},
                           'explicit unsupported/noncanonical API version is not ignored at reserved metadata boundary')
    except Exception as failure:fatal=repr(failure)
    finally:
        try:service.close()
        except Exception as cleanup:fatal=(fatal or '')+' cleanup='+repr(cleanup)
        report=receipt.report(service,sha256(binary),error=fatal,temporary_root=str(root))
        options.report.write_text(json.dumps(report,indent=2)+'\n')
        if report['status']=='PASS':shutil.rmtree(root)
    print(json.dumps({'status':report['status'],'observations':len(receipt.results),'error':report['error']}))
    return report['status']!='PASS'


if __name__=='__main__':raise SystemExit(main())
