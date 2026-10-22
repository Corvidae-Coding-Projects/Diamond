#!/usr/bin/env python3
"""Independent real HTTP/psql W2 contract exploration; edits only disposable copies.
Oracle: W2 observable contract + declared workspace scalar bounds/owner policies.
No production algorithm is reimplemented; expected outcomes are exact echoes,
explicit rejection, or stable owned PostgreSQL rows after rejected mutations.
"""
from pathlib import Path
import hashlib,http.client,json,os,random,shutil,signal,socket,subprocess,sys,tempfile,time
STATE=Path(sys.argv[1]);S=json.loads(STATE.read_text());B=Path(os.environ['DIAMOND_W2_BINARY']);D=Path(tempfile.mkdtemp(prefix='diamond-w2-independent-http-'));R=Path(__file__).resolve().parents[2];PASS='Independent W2 synthetic password 87!';rng=random.Random(0x22C0DE);RESULTS=[];OWN=[]
assert hashlib.sha256(B.read_bytes()).hexdigest()==S['binary_sha256']
for pid in S['node_pids'].values():assert hashlib.sha256(Path(f'/proc/{pid}/exe').read_bytes()).hexdigest()==S['binary_sha256']
def sql(stmt):return subprocess.check_output(['psql',S['configuration'],'-XAt','-v','ON_ERROR_STOP=1','-c',stmt],text=True).strip()
def quoted(x):return "'"+x.replace("'","''")+"'"
def rows():
 raw=sql("SELECT app || chr(9) || resource || chr(9) || id || chr(9) || payload::text FROM diamond_w2.rows ORDER BY app,resource,id")
 result=[]
 for line in raw.splitlines():
  app,resource,id,payload=line.split('\t',3);result.append({'app':app,'resource':resource,'id':id,'payload':json.loads(payload)})
 return result
def snapshot():return json.dumps([r for r in rows() if r['payload'].get('owner',{}).get('value') in OWN],sort_keys=True,ensure_ascii=False)
def cli(*args,input=None):
 p=subprocess.run([str(B),*map(str,args)],input=input,text=True,capture_output=True,env=os.environ|{'W2_INDEPENDENT_PG':S['configuration']},timeout=25);assert p.returncode==0,(args,p.stderr);return p.stdout
# Add only exact echo handlers to a copied project; schema and app name stay fixed.
p=D/'project';shutil.copytree(Path(S['project']).parent,p)
with (p/'server.dia').open('a') as f:f.write('\nexport fn echo_i(value: Int) -> Int:\n  value\n\nexport fn echo_b(value: Bool) -> Bool:\n  value\n\nexport fn echo_t(value: Text) -> Text:\n  value\n')
with (p/'diamond.toml').open('a') as f:f.write('echo_i = "server.echo_i"\necho_b = "server.echo_b"\necho_t = "server.echo_t"\n')
cli('check',p/'diamond.toml');cli('build',p/'diamond.toml','--out',D/'build');contract=json.loads((D/'build/contract.json').read_text())['rpc_id']
with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
log=(D/'third-node.log').open('w');proc=subprocess.Popen([str(B),'serve',str(p/'diamond.toml'),'--listen',f'127.0.0.1:{port}','--origin',S['origin'],'--postgres-env','W2_INDEPENDENT_PG'],env=os.environ|{'W2_INDEPENDENT_PG':S['configuration']},stdout=log,stderr=log)
S['ports']['C']=port
class Client:
 def __init__(self):self.cookie=None;self.csrf='';self.flags=''
 def req(self,path,body=None,node='A',headers=None,method=None,raw=False,capture=True):
  h={'Host':S['origin'][7:]};h.update({'X-Diamond-Session':'1'} if path=='/_diamond/session' else {})
  if self.cookie:h['Cookie']=self.cookie
  if body is not None:h.update({'Content-Type':'application/json','Origin':S['origin'],'X-Diamond-CSRF':self.csrf})
  if headers:
   for k,v in headers.items():
    if v is None:h.pop(k,None)
    else:h[k]=v
  payload=body if raw or body is None else json.dumps(body,ensure_ascii=False,separators=(',',':')).encode()
  con=http.client.HTTPConnection('127.0.0.1',S['ports'][node],timeout=12)
  try:
   con.request(method or ('POST' if body is not None else 'GET'),path,body=payload,headers=h);resp=con.getresponse();data=resp.read();hdr=dict(resp.getheaders())
   if capture:
    if 'set-cookie' in hdr:self.cookie=hdr['set-cookie'].split(';')[0];self.flags=hdr['set-cookie']
    if data:
     try:
      obj=json.loads(data)
      if 'csrf' in obj:self.csrf=obj['csrf']
     except (ValueError,TypeError):pass
   try:value=json.loads(data)
   except ValueError:value=data.decode(errors='replace')
   return resp.status,value,hdr
  finally:con.close()
 def rpc(self,name,args,node='A',**kw):return self.req('/_diamond/rpc/'+name,{'contract':contract if node=='C' else S['contract'],'arguments':args},node=node,**kw)
 def login(self,user,node='A'):
  expect('bootstrap '+user,self.req('/_diamond/session',node=node),(200,None),predicate=lambda r:r[1]['user'] is None)
  old=self.cookie;expect('login '+user,self.req('/_diamond/login',{'username':user,'password':PASS},node=node),(200,{'value':user}),predicate=lambda r:r[1]['value']==user)
  assert self.cookie!=old;return old

def expect(name,observed,expected,predicate=None,input=None,stable=False):
 status,value,headers=observed;ok=status==expected[0] and (predicate(observed) if predicate else expected[1] is None or value==expected[1]);RESULTS.append({'case':name,'expected_status':expected[0],'observed_status':status,'expected_value':expected[1],'observed_value':value,'status':'PASS' if ok else 'FAIL','input':input});return observed

def reject(name,call,status=400):
 before=snapshot();r=call();expect(name,r,(status,None));after=snapshot();RESULTS[-1]['owned_rows_unchanged']=before==after
 if before!=after:RESULTS[-1]['status']='FAIL'

try:
 for _ in range(150):
  try:
   if Client().req('/',node='C')[0]==200:break
  except OSError:pass
  assert proc.poll() is None;time.sleep(.03)
 else:raise RuntimeError('third node not ready')
 prefix='iv'+str(os.getpid());OWN.extend([prefix+'a',prefix+'b'])
 for u in OWN:cli('project-user-add',S['project'],'--postgres-env','W2_INDEPENDENT_PG','--user',u,'--password-stdin',input=PASS+'\n')
 a,b,anon=Client(),Client(),Client();old=a.login(OWN[0],'A');b.login(OWN[1],'B');anon.req('/_diamond/session')
 expect('cookie mandatory flags',(200,a.flags,{}),(200,None),predicate=lambda r:all(flag in r[1] for flag in ['Secure','HttpOnly','SameSite=Strict','Path=/']) and 'Domain=' not in r[1])
 oldc=Client();oldc.cookie=old;oldc.csrf=a.csrf
 reject('rotated anonymous session cannot dispatch',lambda:oldc.rpc('create_issue',['independent','stolen','x']),403)
 text="🦉 e\u0301 漢字 '); DROP TABLE diamond_w2.rows; -- <script>\n\\\""
 r=expect('create Unicode/injection through B',a.rpc('create_issue',['independent',text,'from B'],node='B'),(200,None));iid=r[1].get('value')
 expect('read exact Unicode through A',a.rpc('read_issue',[iid]),(200,{'value':'independent | '+text+'\nfrom B'}))
 expect('DB independently derives owner',(200,next(r['payload']['owner']['value'] for r in rows() if r['id']==iid),{}),(200,OWN[0]))
 for c,label in [(b,'other user'),(anon,'anonymous session'),(Client(),'no session')]:
  for name,args in [('read_issue',[iid]),('update_issue',[iid,'stolen','forged']),('delete_issue',[iid])]:reject(label+' '+name,lambda c=c,name=name,args=args:c.rpc(name,args,node='B'),403)
 expect('other user selection excludes row',b.rpc('list_issues',['independent']),(200,{'value':''}))
 expect('update owner through A',a.rpc('update_issue',[iid,'updated','saved A']),(200,{'value':'independent | updated\nsaved A'}))
 expect('fresh node B reads commit',a.rpc('read_issue',[iid],node='B'),(200,{'value':'independent | updated\nsaved A'}))
 # Request grammar and dispatch. Each reject checks owned application rows.
 valid={'contract':S['contract'],'arguments':['independent','candidate','details']}
 for key,val in [('owner',OWN[1]),('user',OWN[1]),('id',iid),('wire_version',1)]:reject('unknown envelope field '+key,lambda key=key,val=val:a.req('/_diamond/rpc/create_issue',valid|{key:val}))
 for name in ['private_probe','main','server.create_issue','__diamond_server_create_issue','not_declared']:
  reject('undeclared RPC '+name,lambda name=name:a.rpc(name,[]),404)
 for path in ['/server.json','/contract.json','/server.dia','/project/server.dia','/_diamond/rpc/create_issue?x=1','/%73erver.json','//server.json']:
  expected=400 if ('?' in path or '%' in path or path.startswith('//')) else 404
  reject('private/invalid path '+path,lambda path=path:a.req(path),expected)
 page=a.req('/');asset=page[1].split('src="')[1].split('"')[0];js=a.req(asset)[1]
 csp=page[2].get('content-security-policy','');directives={part.strip().split()[0]:part.strip().split()[1:] for part in csp.split(';') if part.strip()}
 expect('connected HTML CSP allows same-origin fetch and keeps default deny',(200,directives,{}),(200,None),predicate=lambda r:r[1].get('connect-src')==["'self'"] and r[1].get('default-src')==["'none'"])
 expect('private canary omitted from page and JS',(200,page[1]+js,{}),(200,None),predicate=lambda r:'SERVER_PRIVATE_CANARY' not in r[1] and 'private_probe' not in r[1])
 for headers,label,status in [({'Origin':None},'missing origin',403),({'Origin':'http://localhost:1'},'wrong origin',400),({'Origin':S['origin']+'/'},'origin trailing slash',400),({'Host':'localhost:1'},'wrong Host',400),({'X-Forwarded-Host':S['origin'][7:]},'forwarded host',400),({'Forwarded':'for=127.0.0.1'},'Forwarded',400),({'Sec-Fetch-Site':'cross-site'},'cross-site fetch',400),({'X-Diamond-CSRF':None},'missing CSRF',403),({'X-Diamond-CSRF':b.csrf},'other session CSRF',403),({'Cookie':b.cookie},'other cookie with owner CSRF',403),({'Cookie':a.cookie+'; '+a.cookie},'duplicate cookie',403),({'Cookie':a.cookie.upper()},'uppercase cookie',403),({'Content-Type':'application/json; charset=utf-8'},'unsupported media suffix',415)]:
  reject(label,lambda headers=headers:a.req('/_diamond/rpc/create_issue',valid,headers=headers),status)
 reject('oversize body',lambda:a.req('/_diamond/rpc/create_issue',b'x'*16385,raw=True),400)
 reject('wrong contract',lambda:a.req('/_diamond/rpc/create_issue',valid|{'contract':'0'*64}),409)
 for args in [[],['x'],['x','y'],['x','y','z','extra'],[True,'y','z'],['x',0,'z'],['x',{},'z'],['x',['y'],'z'],['x',None,'z']]:reject('invalid argument shape '+repr(args),lambda args=args:a.req('/_diamond/rpc/create_issue',valid|{'arguments':args}))
 for key,n in [('workspace',0),('workspace',65),('title',0),('title',121),('details',4001)]:
  args=['w','t','d'];args[['workspace','title','details'].index(key)]='🦉'*n
  reject('declared text bound '+key+' '+str(n),lambda args=args:a.rpc('create_issue',args),400)
 for args,label in [(['w'*64,'🦉'*120,'d'*4000],'maximum scalar lengths'),(['w','t','🦉'*4000],'4000 astral details')]:
  r=expect(label,a.rpc('create_issue',args),(200,None));
  if r[0]==200:expect(label+' exact fresh read',a.rpc('read_issue',[r[1]['value']],node='B'),(200,{'value':args[0]+' | '+args[1]+'\n'+args[2]}))
 # Generated canonical scalar cases, independent Int64 domain oracle.
 integers=[-(2**63),-1,0,1,2**63-1]+[rng.randrange(-(2**63),2**63) for _ in range(35)]
 for n in integers:expect('Int64 canonical '+str(n),a.rpc('echo_i',[str(n)],node='C'),(200,{'value':str(n)}))
 for v in ['', '+1','01','-0','-01',' 1','1 ','1.0','1e0',str(2**63),str(-(2**63)-1),'١',0,1,True,None,[],{}]:reject('Int64 rejects '+repr(v),lambda v=v:a.rpc('echo_i',[v],node='C'))
 for v in [False,True]:expect('boolean '+repr(v),a.rpc('echo_b',[v],node='C'),(200,{'value':v}))
 for v in ['true','false','0',0,1,None,[],{}]:reject('Bool rejects '+repr(v),lambda v=v:a.rpc('echo_b',[v],node='C'))
 for v in ['',text,'\x00','\r\n\t','🦉'*50]:expect('JSON text exact '+repr(v[:35]),a.rpc('echo_t',[v],node='C'),(200,{'value':v}))
 # NUL is valid JSON text and fits all declared bounds; storage must round-trip or reject explicitly.
 before=snapshot();r=a.rpc('create_issue',['independent','nul-probe','\x00']);expect('valid bounded NUL text storage',r,(200,None));RESULTS[-1]['owned_rows_unchanged']=before==snapshot()
 if r[0]==200:expect('NUL exact read',a.rpc('read_issue',[r[1]['value']],node='B'),(200,{'value':'independent | nul-probe\n\x00'}))
 # Distinguish actual NUL from literal backslash-u text through every CRUD path.
 for where,args in [('workspace',['\x00','t','d']),('title',['nul-workspace','\x00','d']),('details',['nul-workspace','t','\x00']),('literal escape',['nul-workspace','literal','\\u0000'])]:
  probe=expect('stored text special '+where,a.rpc('create_issue',args),(200,None))
  if probe[0]==200:
   key=probe[1]['value'];expect('stored text exact '+where,a.rpc('read_issue',[key],node='B'),(200,{'value':args[0]+' | '+args[1]+'\n'+args[2]}))
   stored=next(r['payload'] for r in rows() if r['id']==key)
   expect('SQL raw JSON exact '+where,(200,[stored['workspace'],stored['title'],stored['details']],{}),(200,args))
   if where=='title':expect('NUL list exact',a.rpc('list_issues',['nul-workspace']),(200,{'value':key+' | \x00'}))
   expect('NUL-bearing update '+where,a.rpc('update_issue',[key,'updated\x00','body\x00']),(200,{'value':args[0]+' | updated\x00\nbody\x00'}))
   expect('NUL-bearing read after update '+where,a.rpc('read_issue',[key],node='B'),(200,{'value':args[0]+' | updated\x00\nbody\x00'}))
   expect('NUL-bearing delete '+where,a.rpc('delete_issue',[key]),(200,{'value':'Issue deleted.'}))
   expect('NUL-bearing delete SQL absence '+where,(200,any(r['id']==key for r in rows()),{}),(200,False))
 # Bootstrap/method grammar must not mint a session for rejected navigation.
 for extra,label,status,method,body in [({'X-Diamond-Session':None},'missing bootstrap marker',403,None,None),({'X-Diamond-Session':'2'},'wrong bootstrap marker',403,None,None),({},'HEAD session',405,'HEAD',None),({},'POST session',403,'POST',{}),({},'nonempty GET session body',400,'GET',{})]:
  before=sql('SELECT count(*) FROM diamond_w2.sessions');probe=Client().req('/_diamond/session',body,headers=extra,method=method)
  expect(label,probe,(status,None));expect(label+' no session minted',(200,sql('SELECT count(*) FROM diamond_w2.sessions')==before,{}),(200,True))
 for raw,label in [('{'+'"contract":"'+S['contract']+'","arguments":["w","t","d"],"arguments":["w","x","z"]}','duplicate arguments'),('{'+'"contract":"'+S['contract']+'","contract":"'+S['contract']+'","arguments":["w","t","d"]}','duplicate contract'),('{"contract":"'+S['contract']+'","arguments":["w","t","\\ud800"]}','unpaired surrogate')]:
  reject(label,lambda raw=raw:a.req('/_diamond/rpc/create_issue',raw.encode(),raw=True))
 # Revoke/expire only independently provisioned identity sessions.
 oldcookie=b.cookie;oldcsrf=b.csrf;expect('logout through A',b.req('/_diamond/logout',{},node='A'),(200,{'value':''}));rev=Client();rev.cookie=oldcookie;rev.csrf=oldcsrf;reject('logout revocation across B',lambda:rev.rpc('list_issues',['independent'],node='B'),403)
 b=Client();b.login(OWN[1],'B');token=b.cookie.split('=',1)[1];digest=hashlib.sha256(token.encode()).hexdigest();sql("UPDATE diamond_w2.sessions SET expires=pg_catalog.clock_timestamp()-interval '1 second' WHERE digest="+quoted(digest));reject('database expiry observed by A',lambda:b.rpc('list_issues',['independent']),403)
 before=snapshot();bad=Client();bad.req('/_diamond/session');expect('failed password denied',bad.req('/_diamond/login',{'username':OWN[0],'password':'wrong synthetic password'}),(403,{'error':'denied'}));expect('failed login leaves rows unchanged',(200,before==snapshot(),{}),(200,True))
 # Successful legitimate event after rejected requests.
 expect('service recovery after negatives',a.rpc('read_issue',[iid]),(200,{'value':'independent | updated\nsaved A'}))
finally:
 if proc.poll() is None:proc.send_signal(signal.SIGINT);proc.wait(timeout=20)
 log.close()
 report={'state':str(STATE),'root':str(D),'binary_sha256':hashlib.sha256(B.read_bytes()).hexdigest(),'seed':hex(0x22C0DE),'accounts':OWN,'third_port':port,'results':RESULTS,'log':(D/'third-node.log').read_text()}
 (D/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
 print(json.dumps({'root':str(D),'cases':len(RESULTS),'failures':[r for r in RESULTS if r['status']!='PASS'],'report':str(D/'report.json')},ensure_ascii=False,indent=2))

raise SystemExit(int(any(row["status"]!="PASS" for row in RESULTS)))
