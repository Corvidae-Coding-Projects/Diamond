#!/usr/bin/env python3
"""Check documented configuration and the real asset-only HTTP server."""
import concurrent.futures
import hashlib
import http.client
import json
from pathlib import Path
import re
import shutil
import signal
import socket
import subprocess
import tempfile
import time

ROOT=Path(__file__).resolve().parents[2]
binary=ROOT/'target/release/diamond'
root=Path(tempfile.mkdtemp(prefix='diamond-scheduling-assets-'))
process=None
try:
    project=root/'app'
    shutil.copytree(ROOT/'examples/components',project)
    manifest=project/'diamond.toml'
    original=manifest.read_text()
    blocks=re.findall(r'```toml\n(.*?)```',(ROOT/'docs/runtime-scheduling.md').read_text(),re.S)
    for extra in ('',*blocks[1:]):
        manifest.write_text(original+'\n'+blocks[0]+'\n'+extra)
        subprocess.run([str(binary),'check',str(manifest)],check=True,capture_output=True)
    manifest.write_text(original+'\n'+blocks[0])
    subprocess.run([str(binary),'build',str(manifest),'--out',str(root/'build')],check=True,capture_output=True)
    with socket.socket() as listener:
        listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
    with (root/'server.log').open('wb') as log:
        process=subprocess.Popen([str(binary),'serve',str(root/'build'),'--listen',f'127.0.0.1:{port}'],stdout=log,stderr=log)
        def request(_):
            connection=http.client.HTTPConnection('127.0.0.1',port,timeout=5)
            try:
                connection.request('GET','/')
                response=connection.getresponse();data=response.read()
                assert response.status==200 and b'<html' in data,(response.status,data[:80])
                return hashlib.sha256(data).hexdigest()
            finally:connection.close()
        until=time.monotonic()+5
        while True:
            try:request(0);break
            except ConnectionRefusedError:
                assert time.monotonic()<until;time.sleep(.02)
        with concurrent.futures.ThreadPoolExecutor(max_workers=32) as pool:
            results=list(pool.map(request,range(128)))
        assert len(set(results))==1
        process.send_signal(signal.SIGINT)
        assert process.wait(timeout=5)==0
        receipt={'status':'PASS','binary_sha256':hashlib.sha256(binary.read_bytes()).hexdigest(),
            'driver_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'documentation_configurations_checked':len(blocks),'requests':len(results),
            'concurrency':32,'identical_html':True,'shutdown_returncode':process.returncode}
        Path(__file__).with_name('assets-scheduling.json').write_text(json.dumps(receipt,indent=2)+'\n')
        print(json.dumps(receipt))
finally:
    if process is not None and process.poll() is None:
        process.kill();process.wait()
    shutil.rmtree(root)
