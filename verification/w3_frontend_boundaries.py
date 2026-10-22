#!/usr/bin/env python3
"""Operate compiler/build boundaries added during W3 browser qualification."""
from pathlib import Path
import hashlib
import json
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
BINARY = ROOT / 'target/debug/diamond'
MANIFEST = '''schema_version=1
name="FrontendBoundary"
server="server"
browser="ui"
component="App"
[modules.server]
path="server.dia"
target="server"
[modules.ui]
path="ui.dia"
target="browser"
'''


def main():
    results = []
    cases = [
        ('static-reserved-id', 'export component App:\n  html:\n    <p id="diamond-error">visible</p>\n', 'check', False, ''),
        ('dynamic-reserved-id', 'export component App:\n  state name: Text = "diamond-" + "app"\n  html:\n    <p id={name}>visible</p>\n', 'build', False, ''),
        ('valid-dynamic-id', 'export component App:\n  state name: Text = "app-content"\n  html:\n    <p id={name}>visible</p>\n', 'build', True, ''),
        ('stylesheet-extension', 'export component App:\n  html:\n    <p>visible</p>\n', 'check', False, 'styles=["logo.svg"]\n'),
        ('unreachable-route', 'component Hidden:\n  html:\n    <route path="/unused-canary"><p>Unused route</p></route>\n\nexport component App:\n  html:\n    <p>visible</p>\n', 'build', True, ''),
        ('html-normalization', 'export component App:\n  state value: Text = "\\r\\nA\\rB' + chr(0) + '"\n  html:\n    <p title={value}>{value}</p>\n', 'build', True, ''),
    ]
    with tempfile.TemporaryDirectory(prefix='diamond-w3-front-boundaries-') as temporary:
        for name, source, operation, accepted, extra in cases:
            root = Path(temporary) / name
            root.mkdir()
            (root / 'diamond.toml').write_text(extra + MANIFEST)
            (root / 'server.dia').write_text('export fn main() -> Int:\n  17\n')
            (root / 'ui.dia').write_text(source)
            (root / 'logo.svg').write_text('<svg xmlns="http://www.w3.org/2000/svg"></svg>')
            command = [str(BINARY), operation, str(root / 'diamond.toml')]
            if operation == 'build':
                command += ['--out', str(root / 'build')]
            run = subprocess.run(command, text=True, capture_output=True, timeout=30)
            observed = run.returncode == 0
            good = observed == accepted
            if good and accepted and operation == 'build':
                contract = json.loads((root / 'build/contract.json').read_text())
                if name == 'unreachable-route':
                    good = contract['browser']['routes'] == ['/'] and not any((root / 'build/pages').glob('*.html'))
                elif name == 'html-normalization':
                    page = (root / 'build/index.html').read_bytes()
                    good = b'&#0;' in page and b'\x00' not in page
            results.append({'name': name, 'source': source, 'expected_accept': accepted,
                            'exit': run.returncode, 'stdout': run.stdout, 'stderr': run.stderr,
                            'status': 'PASS' if good else 'FAIL'})
    receipt = {'status': 'PASS' if all(r['status'] == 'PASS' for r in results) else 'FAIL',
               'binary_sha256': hashlib.sha256(BINARY.read_bytes()).hexdigest(), 'results': results}
    (ROOT / 'verification/web_three_frontend_boundaries.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps({'status': receipt['status'], 'cases': len(results)}))
    return int(receipt['status'] != 'PASS')


if __name__ == '__main__':
    raise SystemExit(main())
