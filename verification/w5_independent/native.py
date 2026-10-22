#!/usr/bin/env python3
"""Compile and run a real external consumer of the public W5 Rust interfaces."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

from accounts import client, issue_token
from common import Receipt, app_id, quote
from fixture import write
from model import PASSWORD
from service import ENV_NAME, Service, sha256

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--library', type=Path, required=True)
    parser.add_argument('--dependencies', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    options = parser.parse_args()
    root = Path(tempfile.mkdtemp(prefix='diamond-w5-independent-native-'))
    binary, library = root / 'diamond', root / 'libdiamond.rlib'
    shutil.copy2(options.binary, binary); shutil.copy2(options.library, library)
    executable = root / 'native-consumer'
    project = write(root / 'project', app='IndependentNativeAccounts')
    service = Service(root, binary, project)
    receipt, fatal = Receipt(), None
    receipt.secret(PASSWORD)
    try:
        compiled = subprocess.run(['rustc','--edition','2024',str(Path(__file__).with_suffix('.rs')),
                                   '--crate-name','independent_w5_native','--extern','diamond='+str(library),
                                   '-L','dependency='+str(options.dependencies.resolve()),'-o',str(executable)],
                                  cwd=ROOT,capture_output=True,text=True,timeout=60)
        if compiled.returncode != 0: raise AssertionError(('external native compile',compiled.stderr))
        service.postgres()
        contract, _ = service.build()
        environment = os.environ | {ENV_NAME:service.owner, 'DIAMOND_W5_NATIVE_PASSWORD':PASSWORD}
        arguments = [str(project),str(service.mailbox),f"127.0.0.1:{service.ports['A']}",service.origin]
        initialized = subprocess.run([str(executable),'initialize',*arguments],env=environment,capture_output=True,text=True,timeout=30)
        row = service.sql_json("SELECT username,email,verified,disabled,administrator FROM diamond_identity.users WHERE app="+quote(app_id('IndependentNativeAccounts')))
        receipt.record('external-native-admin-provisioning', initialized.returncode == 0 and len(row) == 1 and row[0]['verified'] and row[0]['administrator']
                       and row[0]['email'] == 'native-admin@qualification.invalid',
                       {'exit':initialized.returncode,'stdout':initialized.stdout,'stderr':initialized.stderr,'account':row},
                       'native initializer provisions one verified administrator and rejects duplicate replacement')
        if initialized.returncode != 0: raise AssertionError(('native initializer failed',initialized.stderr))
        log = (root/'native-server.log').open('w'); service.logs.append(log); service.node_logs['A']=Path(log.name)
        service.nodes['A'] = subprocess.Popen([str(executable),'serve',*arguments],env=environment,stdout=log,stderr=log)
        deadline = time.monotonic()+8
        while time.monotonic()<deadline:
            try:
                if service.request('A','/')[0] == 200: break
            except OSError: pass
            if service.nodes['A'].poll() is not None: raise AssertionError(('native serving exited',Path(log.name).read_text()))
            time.sleep(.025)
        else: raise AssertionError('native serving readiness deadline')
        owner = client(service, contract['rpc_id'], 'native-admin')
        bearer, _ = issue_token(owner, receipt, operations=['count'], label='native-issued')
        count = owner.api('count',bearer)
        public = owner.api('public_value',None)
        receipt.record('external-native-serving-accounts-api', count == (200,{'api_version':1,'value':'0'}) and public == (200,{'api_version':1,'value':'Public declared read'}),
                       {'count':count,'public':public}, 'real native serve_accounts exposes the compiled app with authoritative shared accounts and explicit JSON API')
    except Exception as failure:
        fatal = repr(failure)
    finally:
        try: service.close()
        except Exception as cleanup: fatal = (fatal or '')+' cleanup='+repr(cleanup)
        report = receipt.report(service,sha256(binary),error=fatal,library_sha256=sha256(library),native_sha256=sha256(executable) if executable.exists() else None,
                                temporary_root=str(root))
        options.report.write_text(json.dumps(report,indent=2)+'\n')
        if report['status']=='PASS': shutil.rmtree(root)
    print(json.dumps({'status':report['status'],'observations':len(receipt.results),'error':report['error']}))
    return report['status']!='PASS'


if __name__=='__main__': raise SystemExit(main())
