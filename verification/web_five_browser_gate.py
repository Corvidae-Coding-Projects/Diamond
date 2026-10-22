#!/usr/bin/env python3
"""Forward real local Diamond HTTP; optionally hold one completed identity reply.

No page, application handler, authentication or expected result is supplied here.
Use with the real browser fixture's node on39174 and declared origin39171.
"""
import http.client
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import sys
import threading

lock=threading.Lock()
release=threading.Event()
armed=False
drop_reply=False
close_reply=False
class Forward(BaseHTTPRequestHandler):
    protocol_version='HTTP/1.1'
    def log_message(self,*args):pass
    def forward(self):
        global armed,drop_reply,close_reply
        size=int(self.headers.get('Content-Length','0'))
        if size>16384:self.close_connection=True;return
        body=self.rfile.read(size)
        connection=http.client.HTTPConnection('127.0.0.1',39174,timeout=15)
        try:
            connection.request(self.command,self.path,body,{key:value for key,value in self.headers.items() if key.lower() not in ['connection','transfer-encoding']})
            response=connection.getresponse();data=response.read();headers=response.getheaders()
            print(json.dumps({'forwarded':self.path,'actual_status':response.status}),flush=True)
            with lock:
                hold=armed and self.path=='/_diamond/accounts/logout_all'
                if hold:armed=False
            if hold:
                print(json.dumps({'held':self.path,'actual_status':response.status,'actual_bytes':len(data)}),flush=True)
                release.wait(25)
                if close_reply:
                    self.close_connection=True;return
            self.send_response(response.status)
            for key,value in headers:
                if key.lower() not in ['content-length','transfer-encoding','connection','server','date']:self.send_header(key,value)
            self.send_header('Content-Length',str(len(data)))
            self.end_headers()
            if hold and drop_reply:
                self.wfile.write(data[:1]);self.wfile.flush();self.close_connection=True;return
            self.wfile.write(data)
        except (OSError,http.client.HTTPException):self.close_connection=True
        finally:connection.close()
    do_GET=forward
    do_POST=forward
    do_HEAD=forward
server=ThreadingHTTPServer(('127.0.0.1',39171),Forward)
thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
print(json.dumps({'ready':True,'origin':'http://127.0.0.1:39171','backend':'127.0.0.1:39174'}),flush=True)
try:
    for line in sys.stdin:
        command=line.strip()
        if command=='stop':break
        if command=='arm':
            with lock:armed=True;drop_reply=False;close_reply=False;release.clear()
            print(json.dumps({'armed':True}),flush=True)
        if command=='release':release.set();print(json.dumps({'released':True}),flush=True)
        if command=='drop':drop_reply=True;release.set();print(json.dumps({'dropped':'partial real body'}),flush=True)
        if command=='close':close_reply=True;release.set();print(json.dumps({'dropped':'zero bytes'}),flush=True)
finally:
    release.set();server.shutdown();server.server_close();thread.join()
