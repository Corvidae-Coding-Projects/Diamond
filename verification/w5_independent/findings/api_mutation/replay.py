#!/usr/bin/env python3
"""Valid scoped update/delete must commit through API as they do through RPC."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import shutil
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from accounts import client, issue_token, provision
from common import Receipt, nominal, restrict_runtime, seed
from fixture import table, write
from model import PASSWORD
from service import ENV_NAME, Service, sha256


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    options = parser.parse_args()
    root = Path(tempfile.mkdtemp(prefix='diamond-w5-api-mutation-replay-'))
    binary = root / 'diamond'; shutil.copy2(options.binary, binary)
    receipt, fatal, services = Receipt(), None, []
    receipt.secret(PASSWORD)
    try:
        for restricted in [False, True]:
            subroot = root / ('restricted' if restricted else 'owner'); subroot.mkdir()
            project = write(subroot / 'project')
            service = Service(subroot, binary, project); services.append(service)
            service.extra_serve = ('--identity-mailbox', str(service.mailbox))
            service.postgres()
            contract, _ = service.build()
            service.cli('project-init', project, '--postgres-env', ENV_NAME)
            provision(service)
            seed(service)
            if restricted: restrict_runtime(service)
            service.start_node('A'); service.start_node('B')
            owner = client(service, contract['rpc_id'], 'alice')
            token, _ = issue_token(owner, receipt, operations=['read','edit','delete'])
            for interface in ['rpc','api']:
                call = (lambda op, *args: owner.rpc(op, *args)) if interface == 'rpc' else (lambda op, *args: owner.api(op, token, args))
                before = service.sql(f"SELECT rank FROM {table('Issues')} WHERE id='issue-red-one'")
                outcome = call('edit', nominal('issue','issue-red-one'), {'rank':'49'})
                after = service.sql(f"SELECT rank FROM {table('Issues')} WHERE id='issue-red-one'")
                receipt.record(f'update-{interface}-restricted-{restricted}', outcome[0] == 200 and after == '49',
                               {'before':before,'after':after,'http':outcome}, 'valid authorized update commits rank49')
                service.sql(f"UPDATE {table('Issues')} SET rank=11 WHERE id='issue-red-one'")
                if interface == 'api':
                    outcome = call('delete', nominal('issue','issue-red-one'))
                    count = service.sql(f"SELECT count(*) FROM {table('Issues')} WHERE id='issue-red-one'")
                    receipt.record(f'delete-{interface}-restricted-{restricted}', outcome[0] == 200 and count == '0',
                                   {'remaining_rows':count,'http':outcome}, 'valid authorized deletion commits once')
            service.close()
            errors = [line for line in (subroot/'postgres.log').read_text().splitlines() if any(marker in line for marker in ['ERROR:', 'FATAL:', 'PANIC:'])]
            receipt.record('postgres-error-log-'+str(restricted), True, errors, 'raw owned PG errors retained; parameter logging is not enabled')
    except Exception as failure:
        fatal = repr(failure)
    finally:
        for service in services:
            try: service.close()
            except Exception as cleanup: fatal = (fatal or '') + ' cleanup=' + repr(cleanup)
        report = receipt.report(services[-1], sha256(binary), error=fatal, temporary_root=str(root))
        report['shutdowns'] = [row for service in services for row in service.shutdowns]
        report['cleanup_errors'] = [row for service in services for row in service.cleanup_errors]
        if report['cleanup_errors']: report['status'] = 'FAIL'
        options.report.write_text(json.dumps(report,indent=2)+'\n')
        if report['status'] == 'PASS': shutil.rmtree(root)
    print(json.dumps({'status':report['status'],'error':report['error'],'report':str(options.report)}))
    return report['status'] != 'PASS'


if __name__ == '__main__': raise SystemExit(main())
