#!/usr/bin/env python3
"""Exactly two requested reserved IPv6 literal-origin public-policy probes."""
import argparse,json,shutil,tempfile,time
from pathlib import Path
from common import Receipt,client,provision
from fixture import PASSWORD,write
from service import ENV_NAME,Service,sha256


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary',type=Path,required=True);parser.add_argument('--report',type=Path,required=True)
    args=parser.parse_args();root=Path(tempfile.mkdtemp(prefix='diamond-w6-independent-reserved-ipv6-'));binary=root/'diamond';shutil.copy2(args.binary,binary)
    service=Service(root,binary,write(root/'project'));receipt=Receipt();receipt.secret(PASSWORD);fatal=None
    try:
        def grants(address):
            destination={'origin':'https://['+address+']','network':'public','paths':['/v1/'],'methods':['GET'],'timeout_ms':1000}
            service.runtime['http']={name:destination.copy() for name in ['inventory','secondary']}
        grants('3ffe::1');service.postgres();contract,_=service.build();service.cli('project-init',service.project,'--postgres-env',ENV_NAME);service.files_cli(initialize=True);provision(service,'alice')
        service.start_node('A');owner=client(service,contract['rpc_id'],'alice')
        for node,address in [('A','3ffe::1'),('B','3fff::1')]:
            if node=='B':grants(address);service.start_node('B')
            start=time.monotonic();result=owner.rpc('external_get','/v1/ok',node=node);elapsed=time.monotonic()-start
            receipt.record('public-reserved-ipv6-'+address,result==(403,{'error':'denied'}),{'literal_origin':'https://['+address+']','rpc':result,'elapsed_seconds':elapsed,'network':'public'},'reserved/deprecated3ffe::/16 and documentation3fff::/20 must be rejected by policy before a network attempt')
    except Exception as error:fatal=repr(error)
    finally:
        try:service.close()
        except Exception as error:fatal=(fatal or '')+' cleanup='+repr(error)
        result=receipt.report(service,sha256(binary),error=fatal,temporary_root=str(root),runner_sha256=sha256(Path(__file__)))
        args.report.write_text(json.dumps(result,indent=2)+'\n')
        if result['status']=='PASS':shutil.rmtree(root)
    print(json.dumps({'status':result['status'],'observations':len(receipt.results),'error':fatal}));return result['status']!='PASS'
if __name__=='__main__':raise SystemExit(main())
