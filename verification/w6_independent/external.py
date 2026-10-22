"""Actual controlled outside HTTP/TLS service; never substitutes Diamond code."""
from __future__ import annotations
import gzip
import hashlib
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
from pathlib import Path
import socket
import ssl
import subprocess
import threading
import time


class External:
    def __init__(self,root,credential,*,tls=False,wrong_hostname=False,hostname='localhost'):
        self.root=Path(root);self.root.mkdir(mode=0o700)
        self.credential=credential;self.events=[];self.lock=threading.Lock();self.active=0;self.transport_closes=0
        self.stop=threading.Event();self.redirect_origin=None
        outer=self
        class Handler(BaseHTTPRequestHandler):
            protocol_version='HTTP/1.1'
            def handle(self):
                try:super().handle()
                except (ConnectionError,ssl.SSLError):
                    # Native cancellation intentionally closes these actual TLS sockets.
                    with outer.lock:outer.transport_closes+=1
            def log_message(self,*args):pass
            def do_GET(self):self.operate()
            def do_POST(self):self.operate()
            def do_CONNECT(self):self.operate()
            def operate(self):
                body=self.rfile.read(int(self.headers.get('Content-Length','0')))
                auth=self.headers.get('Authorization','')
                with outer.lock:
                    outer.active+=1
                    outer.events.append({'method':self.command,'path':self.path.replace(credential,'<secret>'),
                         'body_sha256':hashlib.sha256(body).hexdigest(),'body_bytes':len(body),
                         'authorization_sha256':hashlib.sha256(auth.encode()).hexdigest(),
                         'expected_authorization':auth=='Bearer '+credential,
                         'accept_encoding':self.headers.get('Accept-Encoding'),
                         'peer':self.client_address[0]})
                try:
                    path=self.path.split('?',1)[0]
                    if path.endswith('/drop'):
                        self.connection.shutdown(socket.SHUT_RDWR);self.connection.close();return
                    if path.endswith('/slow'):outer.stop.wait(2)
                    if path.startswith('/v1/redirect/'):
                        count=int(path.rsplit('/',1)[1])
                        if count:
                            self.send_response(302);self.send_header('Location','/v1/redirect/'+str(count-1));self.send_header('Content-Length','0');self.end_headers();return
                    if path.endswith('/cross') or path.endswith('/outside'):
                        target=(outer.redirect_origin+'/v1/ok') if path.endswith('/cross') else '/not-granted'
                        self.send_response(302);self.send_header('Location',target);self.send_header('Content-Length','0');self.end_headers();return
                    if path.endswith('/post_redirect'):
                        self.send_response(307);self.send_header('Location','/v1/ok');self.send_header('Content-Length','0');self.end_headers();return
                    payload=json.dumps('external exact 雪\0').encode()
                    if path.endswith('/echo'):payload=body
                    if path.endswith('/malformed'):payload=b'{"broken":'
                    if path.endswith('/wrong_type'):payload=b'42'
                    if path.endswith('/secret'):payload=json.dumps(credential).encode()
                    if path.endswith('/escaped_secret'):payload=('"'+''.join('\\u%04x'%ord(c) for c in credential)+'"').encode()
                    if path.endswith('/oversize'):payload=json.dumps('x'*131072).encode()
                    if path.endswith('/gzip'):payload=gzip.compress(payload)
                    self.send_response(200);self.send_header('Content-Type','application/json')
                    if path.endswith('/gzip'):self.send_header('Content-Encoding','gzip')
                    self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)
                except (BrokenPipeError,ConnectionError,OSError):pass
                finally:
                    with outer.lock:outer.active-=1
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.server.daemon_threads=False
        self.ca=None
        if tls:
            self.ca,self.leaf,self.key=self.certificates(wrong_hostname,hostname)
            context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain(self.leaf,self.key)
            self.server.socket=context.wrap_socket(self.server.socket,server_side=True)
        self.origin=('https' if tls else 'http')+'://'+hostname+':'+str(self.server.server_address[1])
        self.thread=threading.Thread(target=self.server.serve_forever,name='w6-external-fixture');self.thread.start()

    def certificates(self,wrong_hostname,hostname):
        ca,key,leaf,leafkey,csr,ext=[self.root/name for name in ['ca.pem','ca.key','leaf.pem','leaf.key','leaf.csr','leaf.ext']]
        def run(*args):subprocess.run(['openssl',*map(str,args)],check=True,capture_output=True,timeout=20)
        run('req','-x509','-newkey','rsa:2048','-nodes','-keyout',key,'-out',ca,'-days','1','-subj','/CN=Diamond W6 synthetic CA')
        run('req','-newkey','rsa:2048','-nodes','-keyout',leafkey,'-out',csr,'-subj','/CN=localhost')
        ext.write_text('subjectAltName=DNS:'+('wrong.invalid' if wrong_hostname else hostname)+'\nextendedKeyUsage=serverAuth\n')
        run('x509','-req','-in',csr,'-CA',ca,'-CAkey',key,'-CAcreateserial','-out',leaf,'-days','1','-extfile',ext)
        key.chmod(0o600);leafkey.chmod(0o600)
        return ca,leaf,leafkey

    def count(self,*,path=None,method=None):
        with self.lock:return sum((path is None or row['path']==path) and (method is None or row['method']==method) for row in self.events)

    def close(self):
        self.stop.set();self.server.shutdown();self.server.server_close();self.thread.join(timeout=5)
        if self.thread.is_alive() or self.active:raise AssertionError('external fixture did not drain')
