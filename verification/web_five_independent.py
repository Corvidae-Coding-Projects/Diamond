#!/usr/bin/env python3
"""Pin and operate the independent W5 CLI/PG/two-node/API/native qualification."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
DRIVERS = ROOT / 'verification/w5_independent'


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=Path, default=ROOT / 'target/debug/diamond')
    parser.add_argument('--library', type=Path, default=ROOT / 'target/debug/libdiamond.rlib')
    parser.add_argument('--dependencies', type=Path, default=ROOT / 'target/debug/deps')
    parser.add_argument('--report', type=Path, default=DRIVERS / 'results.json')
    options = parser.parse_args()
    root = Path(tempfile.mkdtemp(prefix='diamond-w5-independent-run-'))
    binary, library = root / 'diamond', root / 'libdiamond.rlib'
    shutil.copy2(options.binary, binary); shutil.copy2(options.library, library)
    reports, failures, error = {}, [], None
    driver_hashes = {str(path.relative_to(ROOT)):digest(path) for path in sorted(DRIVERS.rglob('*'))
                     if path.is_file() and path.suffix in ['.py','.rs','.md','.dia','.toml'] and '__pycache__' not in path.parts and path.name != 'COVERAGE.md'}
    driver_hashes[str(Path(__file__).relative_to(ROOT))] = digest(__file__)
    try:
        drivers = [(name, DRIVERS / (name + '.py')) for name in ['checker','lifecycle','enrollment','api','rates','faults','native']]
        drivers += [('api_mutation_regression', DRIVERS / 'findings/api_mutation/replay.py'),
                    ('identity_pressure', DRIVERS / 'findings/identity_pressure/replay.py'),
                    ('api_contract_boundary', DRIVERS / 'findings/api_contract_boundary/replay.py')]
        for name, driver in drivers:
            report_path = root / (name + '.json')
            command = ['python3', str(driver), '--binary', str(binary), '--report', str(report_path)]
            if name == 'native':
                command += ['--library',str(library),'--dependencies',str(options.dependencies.resolve())]
            run = subprocess.run(command,cwd=ROOT,capture_output=True,text=True,timeout=360)
            if not report_path.is_file():
                failures.append({'name':name,'error':'missing result receipt','exit':run.returncode,'stdout':run.stdout,'stderr':run.stderr})
                continue
            report = json.loads(report_path.read_text())
            reports[name] = report
            if report.get('binary_sha256') != digest(binary):
                failures.append({'name':name,'error':'binary fingerprint mismatch'})
            if name == 'native' and report.get('library_sha256') != digest(library):
                failures.append({'name':name,'error':'library fingerprint mismatch'})
            if run.returncode != 0 or report.get('status') != 'PASS':
                failures.append({'name':name,'error':'qualification did not pass','exit':run.returncode,'stdout':run.stdout,'stderr':run.stderr})
        for path, expected in driver_hashes.items():
            if digest(ROOT / path) != expected:
                failures.append({'name':'driver-state','error':'driver changed during execution','path':path})
    except Exception as failure:
        error = repr(failure)
    finally:
        receipt = {'status':'FAIL' if error or failures else 'PASS','binary_sha256':digest(binary),'library_sha256':digest(library),
                   'error':error,'failures':failures,'reports':reports,'driver_sha256':driver_hashes,
                   'scope':'accounts-v1 relational current schema; actual CLI, Unix-socket PG, private real mailbox, two direct real app nodes, strict raw HTTP API and native public consumer; browser operated separately; no upgrade compatibility, TLS/SMTP/external mail, capacity or exhaustive-security claim',
                   'temporary_root':str(root) if error or failures else None}
        options.report.write_text(json.dumps(receipt,indent=2,ensure_ascii=True)+'\n')
        if not error and not failures:shutil.rmtree(root)
    print(json.dumps({'status':receipt['status'],'observations':{name:len(report.get('results',[])) for name,report in reports.items()},
                      'http_requests':sum(report.get('http_requests',0) for report in reports.values()),'error':error,'failures':len(failures),'report':str(options.report)}))
    return receipt['status']!='PASS'


if __name__=='__main__':raise SystemExit(main())
