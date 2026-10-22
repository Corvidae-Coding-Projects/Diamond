"""Owned real UDP DNS service: delays/answers only synthetic .test names."""
import socket,struct,threading


def encoded(name):
    return b''.join(bytes([len(label)])+label.encode() for label in name.split('.'))+b'\0'


class DNS:
    def __init__(self):
        self.socket=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);self.socket.bind(('127.0.0.1',0));self.socket.settimeout(.1)
        self.address='127.0.0.1:'+str(self.socket.getsockname()[1]);self.mode='loopback';self.events=[];self.stop=threading.Event();self.error=None
        self.thread=threading.Thread(target=self.run,name='w6-owned-dns');self.thread.start()

    def run(self):
        try:
            while not self.stop.is_set():
                try:query,peer=self.socket.recvfrom(4096)
                except socket.timeout:continue
                offset=12;labels=[]
                while query[offset]:
                    length=query[offset];offset+=1;labels.append(query[offset:offset+length].decode('ascii'));offset+=length
                offset+=1;kind,klass=struct.unpack('!HH',query[offset:offset+4]);question=query[12:offset+4];name='.'.join(labels)
                mode=self.mode;self.events.append({'name':name,'type':kind,'mode':mode})
                if mode=='drop':continue
                answers=[]
                if not name.endswith('.test'):
                    raise AssertionError('fixture received non-synthetic DNS query')
                if kind==1:
                    if mode=='cname' and name=='w6-ok.test':
                        target=encoded('target.w6.test');answers=[b'\xc0\x0c'+struct.pack('!HHIH',5,1,0,len(target))+target]
                    else:
                        addresses={'loopback':['127.0.0.1'],'cname':['127.0.0.1'],'mixed':['127.0.0.1','192.0.2.123'],'private':['10.23.45.67']}[mode]
                        answers=[b'\xc0\x0c'+struct.pack('!HHIH',1,1,0,4)+socket.inet_aton(address) for address in addresses]
                response=query[:2]+struct.pack('!HHHHH',0x8180,1,len(answers),0,0)+question+b''.join(answers)
                self.socket.sendto(response,peer)
        except Exception as error:
            if not self.stop.is_set():self.error=repr(error)

    def close(self):
        self.stop.set();self.thread.join(timeout=3);self.socket.close()
        if self.thread.is_alive() or self.error:raise AssertionError(('owned DNS fixture cleanup',self.error))
