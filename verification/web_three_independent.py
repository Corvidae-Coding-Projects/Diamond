#!/usr/bin/env python3
"""Pin and operate the independently designed W3 CLI, HTTP/PostgreSQL and native oracles."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
DRIVERS = ROOT / 'verification/w3_independent'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=Path, default=ROOT / 'target/debug/diamond')
    parser.add_argument('--library', type=Path, default=ROOT / 'target/debug/libdiamond.rlib')
    parser.add_argument('--report', type=Path, default=ROOT / 'verification/web_three_independent_results.json')
    options = parser.parse_args()
    root = Path(tempfile.mkdtemp(prefix='diamond-w3-independent-run-'))
    binary, library = root / 'diamond', root / 'libdiamond.rlib'
    shutil.copy2(options.binary.resolve(), binary)
    shutil.copy2(options.library.resolve(), library)
    reports = {}
    native = None
    corpus = None
    regressions = []
    error = None
    try:
        minimal_cases = [
            ('result-error-bridge', 'check', DRIVERS / 'findings/result_error_bridge.dia', False, None),
            ('private-generated-type', 'check', DRIVERS / 'findings/type_privacy/diamond.toml', False, None),
            ('shrinking-near-limit-replacement', 'run', DRIVERS / 'findings/text_replace_shrink.dia', True, '524288\n'),
        ]
        for name, operation, path, success, expected in minimal_cases:
            run = subprocess.run([str(binary), operation, str(path)], cwd=ROOT, capture_output=True, text=True, timeout=30)
            okay = (run.returncode == 0) == success and (expected is None or run.stdout == expected)
            regressions.append({'name': name, 'status': 'PASS' if okay else 'FAIL', 'exit': run.returncode,
                                'stdout': run.stdout, 'stderr': run.stderr, 'expected_compile_or_run_success': success,
                                'expected_stdout': expected, 'fixture': str(path.relative_to(ROOT))})
            if not okay:
                raise AssertionError(('minimal regression', name, run.returncode, run.stdout, run.stderr))
        for name in ['semantics', 'checker', 'wire']:
            report = root / (name + '.json')
            command = ['python3', str(DRIVERS / (name + '.py')), '--binary', str(binary), '--report', str(report)]
            run = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=240)
            if not report.is_file():
                raise AssertionError((name, run.returncode, run.stdout, run.stderr))
            reports[name] = json.loads(report.read_text())
            if run.returncode != 0 or reports[name]['status'] != 'PASS':
                raise AssertionError((name, run.returncode, run.stdout, run.stderr))
            if reports[name]['binary_sha256'] != digest(binary):
                raise AssertionError((name, 'binary fingerprint mismatch'))
        executable = root / 'native_consumer'
        command = ['rustc', '--edition', '2024', str(DRIVERS / 'native.rs'), '--crate-name', 'independent_w3_native',
                   '--extern', 'diamond=' + str(library), '-L', 'dependency=' + str(options.library.resolve().parent / 'deps'), '-o', str(executable)]
        compiled = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=120)
        if compiled.returncode != 0:
            raise AssertionError(('native compile', compiled.stdout, compiled.stderr))
        run = subprocess.run([str(executable), str(DRIVERS / 'fixtures/defaults/diamond.toml')], cwd=ROOT,
                             capture_output=True, text=True, timeout=30)
        native = {'status': 'PASS' if run.returncode == 0 else 'FAIL', 'exit': run.returncode,
                  'stdout': run.stdout, 'stderr': run.stderr, 'checks': 5,
                  'consumer_sha256': digest(executable), 'library_sha256': digest(library),
                  'source_sha256': digest(DRIVERS / 'native.rs')}
        if run.returncode != 0 or run.stdout != 'native W3 consumer: 5 observable checks passed\n':
            raise AssertionError(('native run', run.returncode, run.stdout, run.stderr))
        corpus_executable = root / 'native_corpus_consumer'
        command = ['rustc', '--edition', '2024', str(DRIVERS / 'corpus_consumer.rs'), '--crate-name', 'independent_w3_corpus',
                   '--extern', 'diamond=' + str(library), '-L', 'dependency=' + str(options.library.resolve().parent / 'deps'), '-o', str(corpus_executable)]
        compiled = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=120)
        if compiled.returncode != 0:
            raise AssertionError(('corpus native compile', compiled.stdout, compiled.stderr))
        run = subprocess.run([str(corpus_executable), str(DRIVERS / 'browser_corpus/diamond.toml')], cwd=ROOT,
                             capture_output=True, text=True, timeout=30)
        expected = json.loads((DRIVERS / 'browser_corpus/expected.json').read_text())
        observed = run.stdout.removesuffix('\n').split('\n')
        corpus = {'status': 'PASS' if run.returncode == 0 and observed == expected else 'FAIL',
                  'exit': run.returncode, 'stderr': run.stderr, 'count': len(expected), 'observed': observed,
                  'consumer_sha256': digest(corpus_executable), 'source_sha256': digest(DRIVERS / 'corpus_consumer.rs'),
                  'expected_sha256': digest(DRIVERS / 'browser_corpus/expected.json')}
        if corpus['status'] != 'PASS':
            raise AssertionError(('corpus native run', run.returncode, run.stderr))
    except Exception as failure:
        error = repr(failure)
    finally:
        receipt = {'status': 'FAIL' if error else 'PASS', 'binary_sha256': digest(binary),
                   'library_sha256': digest(library), 'error': error, 'reports': reports, 'native': native, 'corpus_native': corpus, 'regressions': regressions,
                   'driver_sha256': {str(path.relative_to(ROOT)): digest(path)
                       for path in sorted(DRIVERS.rglob('*')) if path.is_file() and '__pycache__' not in path.parts},
                   'temporary_root': str(root) if error else None}
        options.report.write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + '\n')
        if error is None:
            shutil.rmtree(root)
    print(json.dumps({'status': receipt['status'], 'cases': {name: len(report['results']) for name, report in reports.items()},
                      'regression_replays': len(regressions), 'native_checks': native['checks'] if native else None, 'native_corpus_values': corpus['count'] if corpus else None, 'error': error, 'report': str(options.report)}))
    return int(error is not None)


if __name__ == '__main__':
    raise SystemExit(main())
