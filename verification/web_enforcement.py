#!/usr/bin/env python3
"""Operate HTTP enforcement regressions against the actual built program."""
from __future__ import annotations

import argparse
import http.client
import json
from pathlib import Path
import re
import secrets
import select
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


class Server:
    def __init__(self, binary: Path, source: Path, arguments: list[str] | None = None):
        self.process = subprocess.Popen([str(binary), 'run', str(source), '--listen', '127.0.0.1:0', *(arguments or [])], cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.port = 0
        assert self.process.stderr is not None
        assert select.select([self.process.stderr], [], [], 8)[0], 'startup timed out'
        line = self.process.stderr.readline()
        match = re.search(r'http://localhost:(\d+)', line)
        self.ready = match is not None
        if match:
            self.port = int(match[1])

    def request(self, method: str, path: str, headers: dict[str, str] | None = None):
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=8)
        try:
            connection.request(method, path, headers={'Host':f'localhost:{self.port}', **(headers or {})})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def stop(self):
        if self.process.poll() is None:
            self.process.terminate()
        out, err = self.process.communicate(timeout=5)
        assert not out and 'panicked at' not in err


def run(binary: Path, directory: Path) -> dict[str, object]:
    observations: dict[str, object] = {}
    source = ROOT / 'verification/regressions/cp4_web/explicit_head.dia'
    server = Server(binary, source)
    try:
        assert server.ready
        observations['private HEAD status'] = server.request('HEAD','/private-head')[0]
        observations['public HEAD status'] = server.request('HEAD','/public-head')[0]
        assert server.request('GET','/private-head')[0] == 200
        assert server.request('GET','/public-head')[0] == 401
    finally:
        server.stop()
    database = directory / 'must-not-create.sqlite'
    source = ROOT / 'verification/regressions/cp4_web/undeclared_database.dia'
    server = Server(binary, source, ['--database',str(database)])
    try:
        observations['undeclared DB server started'] = server.ready
        observations['undeclared DB file created'] = database.exists()
    finally:
        server.stop()
    accounts = directory / 'accounts.sqlite'
    password = secrets.token_urlsafe(24)
    provision = subprocess.run([str(binary),'user-add','--auth',str(accounts),'--user','fixture','--password-stdin'], cwd=ROOT, input=password+'\n',text=True,capture_output=True,timeout=10)
    assert provision.returncode == 0 and password not in provision.stdout + provision.stderr
    server = Server(binary, ROOT / 'examples/message_board.dia', ['--auth',str(accounts),'--database',str(directory/'board.sqlite')])
    try:
        assert server.ready
        status, headers, body = server.request('GET','/posts/missing')
        assert status == 404 and body == b'Not found'
        cookie = headers.get('Set-Cookie')
        observations['anonymous error session transported'] = cookie is not None
        if cookie:
            opaque = cookie.split(';',1)[0]
            status, next_headers, _ = server.request('GET','/posts/missing',{'Cookie':opaque})
            assert status == 404 and 'Set-Cookie' not in next_headers
            assert all(flag in cookie for flag in ['Secure','HttpOnly','SameSite=Strict','Path=/'])
    finally:
        server.stop()
    return observations


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--binary', type=Path, default=ROOT/'target/debug/diamond')
    parser.add_argument('--observe', action='store_true')
    arguments = parser.parse_args()
    directory = Path(tempfile.mkdtemp(prefix='diamond-web-enforcement-'))
    try:
        observed = run(arguments.binary.resolve(), directory)
        print(json.dumps(observed, indent=2))
        if not arguments.observe:
            assert observed == {'private HEAD status':401,'public HEAD status':200,'undeclared DB server started':False,'undeclared DB file created':False,'anonymous error session transported':True}, observed
    except Exception:
        print(f'Failure data retained at {directory}')
        raise
    else:
        shutil.rmtree(directory)


if __name__ == '__main__':
    main()
