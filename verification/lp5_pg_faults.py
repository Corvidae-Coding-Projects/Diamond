#!/usr/bin/env python3
"""Byte-transparent Unix PostgreSQL proxy with one-shot real COMMIT cut points.

Never manufactures a PostgreSQL response. It forwards native protocol packets,
recognizes transaction/statement boundaries, and closes sockets before COMMIT or
after the actual server's COMMIT completion but before delivering acknowledgement.
Only boundary flags are recorded; query parameters and returned data are not logged.
"""
from __future__ import annotations
import select,socket,struct,threading
from pathlib import Path

class CommitProxy:
    def __init__(self,directory:Path,upstream:Path,port:int):
        directory.mkdir(mode=0o700);self.path=directory/f'.s.PGSQL.{port}';self.upstream=upstream/f'.s.PGSQL.{port}';self.lock=threading.Lock();self.mode=None;self.events=[];self.hit=threading.Event();self.connections=set();self.workers=[];self.closed=False
        self.listener=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);self.listener.bind(str(self.path));self.listener.listen();self.listener.settimeout(.25);self.thread=threading.Thread(target=self.accept,daemon=True);self.thread.start()
    def arm(self,mode):
        assert mode in ['before_commit','after_commit_ack_loss']
        with self.lock:self.mode=mode;self.hit.clear()
    def accept(self):
        while not self.closed:
            try:client,_=self.listener.accept()
            except socket.timeout:continue
            except OSError:return
            worker=threading.Thread(target=self.forward,args=(client,),daemon=True);self.workers.append(worker);worker.start()
    def record(self,mode,committed):
        with self.lock:self.events.append({'mode':mode,'application_update_observed':True,'receipt_update_observed':True,'actual_postgres_commit_complete':committed,'commit_ack_delivered':False})
        self.hit.set()
    def forward(self,client):
        backend=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
        try:
            backend.connect(str(self.upstream))
            with self.lock:self.connections.update([client,backend])
            frontend=bytearray();replies=bytearray();startup=True;mutated=False;receipt=False;cut=None;commit_complete=False
            while not self.closed:
                ready,_,_=select.select([client,backend],[],[],.25)
                for source in ready:
                    data=source.recv(65536)
                    if not data:return
                    if source is backend:
                        if not cut:client.sendall(data);continue
                        replies.extend(data)
                        while len(replies)>=5:
                            length=struct.unpack('!I',replies[1:5])[0]
                            if length<4 or length>16777216:return
                            if len(replies)<length+1:break
                            packet=bytes(replies[:length+1]);del replies[:length+1]
                            if packet[:1]==b'C' and packet[5:].rstrip(b'\0')==b'COMMIT':commit_complete=True
                            if packet[:1]==b'Z' and commit_complete:self.record(cut,True);return
                        continue
                    frontend.extend(data)
                    while frontend:
                        if startup:
                            if len(frontend)<4:break
                            length=struct.unpack('!I',frontend[:4])[0]
                            if length<8 or length>16777216:return
                            if len(frontend)<length:break
                            packet=bytes(frontend[:length]);del frontend[:length];backend.sendall(packet);startup=length==8 and packet[4:8]==struct.pack('!I',80877103);continue
                        if len(frontend)<5:break
                        length=struct.unpack('!I',frontend[1:5])[0]
                        if length<4 or length>16777216:return
                        if len(frontend)<length+1:break
                        packet=bytes(frontend[:length+1]);del frontend[:length+1];sql=b''
                        if packet[:1]==b'Q':sql=packet[5:].rstrip(b'\0')
                        if packet[:1]==b'P':sql=packet[5:].split(b'\0',2)[1]
                        sql=sql.strip().upper()
                        if sql.startswith(b'BEGIN'):mutated=receipt=False
                        if sql.startswith(b'ROLLBACK'):mutated=receipt=False
                        if sql.startswith((b'UPDATE "DIAMOND_W4".',b'UPDATE DIAMOND_W4.')):mutated=True
                        if sql.startswith(b'UPDATE DIAMOND_ACTIONS.INVOCATIONS SET ENVELOPE'):receipt=True
                        if sql==b'COMMIT' and mutated and receipt:
                            with self.lock:cut=self.mode;self.mode=None
                            if cut=='before_commit':self.record(cut,False);return
                        backend.sendall(packet)
        except (OSError,ValueError):pass
        finally:
            with self.lock:self.connections.discard(client);self.connections.discard(backend)
            backend.close();client.close()
    def close(self):
        self.closed=True;self.listener.close()
        with self.lock:
            for connection in self.connections:
                try:connection.shutdown(socket.SHUT_RDWR)
                except OSError:pass
        self.thread.join(timeout=3)
        for worker in self.workers:worker.join(timeout=3)
        self.path.unlink(missing_ok=True)
