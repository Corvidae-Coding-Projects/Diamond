#!/usr/bin/env python3
"""Own real PostgreSQL/HTTP/live-event components for LP4 browser exploration."""
from __future__ import annotations
import argparse,hashlib,json,select,socket,threading
from pathlib import Path
import shutil,sys,tempfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'verification'))
from lp2_spine import Gate as ResponseGate,literal,nominal,restrict
from w5_independent.service import Client,Service,ENV_NAME,free_port,sha256
APP=hashlib.sha256(b'ComponentLab').hexdigest()
PASSWORD='LP4 synthetic fixture password 81!'
class Gate(ResponseGate):
    """Unmodified HTTP responses plus byte-transparent native WebSocket tunnel."""
    def __init__(self,port,backend):
        super().__init__(port,backend);self.tunnels=set();self.tunnel_lock=threading.Lock();base=self.server.RequestHandlerClass;gate=self
        class Forward(base):
            def do_GET(self):
                if self.headers.get('Upgrade','').lower()!='websocket':return super().do_GET()
                self.close_connection=True
                upstream=socket.create_connection(('127.0.0.1',gate.backend),timeout=20)
                try:
                    with gate.tunnel_lock:gate.tunnels.update([upstream,self.connection])
                    request=f'{self.command} {self.path} HTTP/1.1\r\n'+''.join(f'{key}: {value}\r\n' for key,value in self.headers.items())+'\r\n'
                    upstream.sendall(request.encode('latin1'));upstream.settimeout(None)
                    while True:
                        ready,_,_=select.select([self.connection,upstream],[],[],1)
                        for source in ready:
                            data=source.recv(65536)
                            if not data:return
                            (upstream if source is self.connection else self.connection).sendall(data)
                except (OSError,ValueError):pass
                finally:
                    with gate.tunnel_lock:gate.tunnels.difference_update([upstream,self.connection])
                    upstream.close()
        self.server.RequestHandlerClass=Forward
    def close(self):
        with self.tunnel_lock:
            for connection in self.tunnels:
                try:connection.shutdown(socket.SHUT_RDWR)
                except OSError:pass
        super().close()
def table(name):return 'diamond_w4."r_'+APP[:16]+'_'+hashlib.sha256(name.encode()).hexdigest()[:16]+'"'
def rows(service):return service.sql_json(f"SELECT id,convert_from(decode(title,'hex'),'UTF8') AS title,convert_from(decode(note,'hex'),'UTF8') AS note,revision FROM {table('Issues')} ORDER BY id")
def seed(service):
    service.sql(f'DELETE FROM {table("Issues")}; DELETE FROM {table("Members")};')
    service.sql(f'INSERT INTO {table("Members")}(id,workspace,"user",role) VALUES '+"('member-alice','alpha','alice','Editor'),('member-bob','beta','bob','Editor')")
    for suffix in ['a','b','c','d']:
        title='Issue '+suffix.upper();service.sql(f'INSERT INTO {table("Issues")}(id,workspace,title,note,revision) VALUES ({literal("alpha-"+suffix)},\'alpha\',{literal(title.encode().hex())},{literal("Original note".encode().hex())},0)')
def action(client,key,title,note='Original note'):
    body={'contract':client.contract,'arguments':[nominal('issue',key),{'title':title,'note':note}]}
    status,prepared=client.request('A','/_diamond/actions/rename/prepare',body)
    if status!=200:return status,prepared
    return client.request('B','/_diamond/actions/rename',{**body,'ticket':prepared['ticket']})
def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--binary',type=Path,default=ROOT/'target/debug/diamond');parser.add_argument('--report',type=Path,default=ROOT/'verification/lp4/fixture.json');parser.add_argument('--serve',action='store_true');args=parser.parse_args()
    driver_hash=sha256(Path(__file__));root=Path(tempfile.mkdtemp(prefix='diamond-lp4-'));binary=root/'diamond';shutil.copy2(args.binary,binary);project=ROOT/'verification/lp4/project/diamond.toml';service=Service(root,binary,project);service.extra_serve=('--identity-mailbox',str(service.mailbox));port=39171 if args.serve else free_port();service.origin=f'http://127.0.0.1:{port}';service.authority=service.origin.removeprefix('http://');gate=None;error=None;observations=[]
    try:
        service.postgres();metadata,build=service.build();contract=metadata['rpc_id'];service.cli('project-init',project,'--postgres-env',ENV_NAME)
        for user in ['alice','bob']:service.cli('project-user-add',project,'--postgres-env',ENV_NAME,'--user',user,'--password-stdin',input=PASSWORD+'\n')
        seed(service);restrict(service)
        service.sql('GRANT SELECT,INSERT,UPDATE,DELETE ON diamond_w7.events TO lp2_runtime')
        service.sql('GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA diamond_w7 TO lp2_runtime')
        service.start_node('A');service.start_node('B');gate=Gate(port,service.ports['A']);alice=Client(service,contract);alice.login('alice',PASSWORD);bob=Client(service,contract);bob.login('bob',PASSWORD);reverse,extra,removed=False,False,False
        before=rows(service);outcome=action(bob,'alpha-b','Forbidden')
        if outcome[0]!=200 or outcome[1]['value']['tag']!='ActionDenied' or rows(service)!=before:raise AssertionError(('policy',outcome))
        observations.append({'name':'independent-bob-denial','pass':True,'observed':outcome})
        print(json.dumps({'ready':True,'root':str(root),'origin':service.origin,'contract':contract,'binary_sha256':sha256(binary),'fixture_password':PASSWORD,'versions':[metadata['language_version'],metadata['format_version'],metadata['wire_version']]}),flush=True)
        if args.serve:
            for line in sys.stdin:
                command=line.strip()
                if command=='stop':break
                if command=='observe':print(json.dumps({'rows':rows(service),'forwarded_actions':gate.actions_seen,'held':gate.held},ensure_ascii=False),flush=True)
                if command=='arm':gate.arm();print('{"armed":true}',flush=True)
                if command=='release':gate.release.set();print('{"released":true}',flush=True)
                if command in ['insert','reorder','remove','restore','pulse']:
                    if command=='insert':reverse,extra,removed=False,True,False
                    if command=='reorder':reverse,extra,removed=True,True,False
                    if command=='remove':removed=True
                    if command=='restore':reverse,extra,removed=False,False,False
                    if command=='pulse':
                        outcome=action(alice,'alpha-c','C unrelated update')
                        if outcome[0]!=200:raise AssertionError(outcome)
                    outcome=alice.rpc('publish',reverse,extra,removed)
                    if outcome[0]!=200:raise AssertionError(outcome)
                    print(json.dumps({'published':command,'outcome':outcome}),flush=True)
        print('{"status":"PASS"}',flush=True)
    except Exception as failure:error=repr(failure);print(json.dumps({'status':'FAIL','error':error}),flush=True)
    finally:
        if gate:gate.close()
        service.close();args.report.parent.mkdir(parents=True,exist_ok=True);args.report.write_text(json.dumps({'status':'PASS' if error is None and not service.cleanup_errors else 'FAIL','error':error,'binary_sha256':sha256(binary),'driver_sha256':driver_hash,'observations':observations,'shutdowns':service.shutdowns,'cleanup_errors':service.cleanup_errors},indent=2)+'\n');shutil.rmtree(root)
    return int(error is not None or bool(service.cleanup_errors))
if __name__=='__main__':raise SystemExit(main())
