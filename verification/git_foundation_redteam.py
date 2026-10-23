#!/usr/bin/env python3
import base64, http.client, json, os, pathlib, re, shutil, socket, subprocess, sys, tempfile, time, urllib.parse

ROOT=pathlib.Path(__file__).resolve().parents[1]
BIN=ROOT/'target/debug/diamond'
TMP=pathlib.Path(tempfile.mkdtemp(prefix='diamond-git-redteam-'))
PG=TMP/'pg'; SOCK=TMP/'sock'; GITROOT=TMP/'git'; MAIL=TMP/'mail'; LOG=TMP/'server.log'
for p in (SOCK,GITROOT,MAIL): p.mkdir(mode=0o700)

def free_port():
    s=socket.socket(); s.bind(('127.0.0.1',0)); p=s.getsockname()[1]; s.close(); return p
pgport=free_port(); port=free_port()
pgcfg=f'host={SOCK} port={pgport} user=diamond_rt dbname=postgres'
runtime={
 'git':{'repos':{'root':str(GITROOT),'executable':'/usr/bin/git'}},
 'oauth':{
  'transaction_key_env':'DIA_OAUTH_KEY',
  'google':{'client_id':'synthetic-google-client','client_secret_env':'DIA_GOOGLE_SECRET'},
  'github':{'client_id':'synthetic-github-client','client_secret_env':'DIA_GITHUB_SECRET'}},
 'http':{}}
env=os.environ.copy(); env.update(DIAMOND_POSTGRES=pgcfg,DIAMOND_RUNTIME=json.dumps(runtime),DIA_OAUTH_KEY='K'*48,DIA_GOOGLE_SECRET='google-test-secret',DIA_GITHUB_SECRET='github-test-secret',GIT_TERMINAL_PROMPT='0',GIT_CONFIG_COUNT='1',GIT_CONFIG_KEY_0='credential.helper',GIT_CONFIG_VALUE_0='')
results=[]; server=None; metrics={}
def check(name, cond, detail=''):
    results.append({'name':name,'pass':bool(cond)})
    if not cond: raise AssertionError(name+': '+detail)
def run(args, **kw):
    cwd=kw.pop('cwd',ROOT)
    return subprocess.run(args, cwd=cwd, env=env, text=kw.pop('text',True), capture_output=True, timeout=kw.pop('timeout',60), **kw)
def cmd_ok(name,args,**kw):
    r=run(args,**kw); check(name,r.returncode==0,(r.stdout+r.stderr)[-1000:]); return r
def req(method,path,body=None,headers=None,cookie=None):
    c=http.client.HTTPConnection('127.0.0.1',port,timeout=15)
    h=dict(headers or {})
    if cookie: h['Cookie']=cookie
    data=None
    if body is not None:
        data=json.dumps(body).encode(); h.setdefault('Content-Type','application/json'); h.setdefault('Origin',f'http://127.0.0.1:{port}')
    c.request(method,path,body=data,headers=h)
    r=c.getresponse(); b=r.read(); hs=dict(r.getheaders()); c.close()
    try: value=json.loads(b)
    except Exception: value=b
    return r.status,hs,value
def session():
    st,h,b=req('GET','/_diamond/session',headers={'X-Diamond-Session':'1'})
    check('session bootstrap',st==200,str((st,b)))
    return h['set-cookie'].split(';',1)[0],b['csrf']
def rpc(cookie,csrf,contract,name,args):
    return req('POST','/_diamond/rpc/'+name,{'contract':contract,'arguments':args},{'X-Diamond-CSRF':csrf},cookie)
def psql(sql):
    return subprocess.run(['psql',pgcfg,'-Atqc',sql],env=env,text=True,capture_output=True,timeout=20,check=True).stdout.strip()
def rss_kib(pid):
    try:
        for line in pathlib.Path(f'/proc/{pid}/status').read_text().splitlines():
            if line.startswith('VmRSS:'): return int(line.split()[1])
    except FileNotFoundError: pass
    return 0
def children(pid):
    result=[]
    try:
        for task in pathlib.Path(f'/proc/{pid}/task').iterdir():
            try: result.extend((task/'children').read_text().split())
            except FileNotFoundError: pass
    except FileNotFoundError: pass
    return sorted(set(result))

try:
    cmd_ok('postgres init',['initdb','-D',str(PG),'-A','trust','--no-locale','-E','UTF8','-U','diamond_rt','--no-sync'])
    cmd_ok('postgres start',['pg_ctl','-D',str(PG),'-l',str(TMP/'postgres.log'),'-o',f'-k {SOCK} -p {pgport} -c listen_addresses=', '-w','start'])
    cmd_ok('project schema init',[str(BIN),'project-init','examples/git-browser/diamond.toml','--postgres-env','DIAMOND_POSTGRES'])
    cmd_ok('git store init',[str(BIN),'project-git-init','examples/git-browser/diamond.toml','--postgres-env','DIAMOND_POSTGRES','--runtime-env','DIAMOND_RUNTIME'])
    pw='Synthetic browser password 91!'
    r=run([str(BIN),'project-admin-add','examples/git-browser/diamond.toml','--postgres-env','DIAMOND_POSTGRES','--user','alice','--email','alice@example.test','--password-stdin'],input=pw+'\n')
    check('admin provision',r.returncode==0,(r.stdout+r.stderr)[-1000:])
    bob_pw='Synthetic browser password 92!'
    r=run([str(BIN),'project-admin-add','examples/git-browser/diamond.toml','--postgres-env','DIAMOND_POSTGRES','--user','bob','--email','bob@example.test','--password-stdin'],input=bob_pw+'\n')
    check('second user provision',r.returncode==0,(r.stdout+r.stderr)[-1000:])
    lf=open(LOG,'wb')
    server=subprocess.Popen([str(BIN),'serve','examples/git-browser/diamond.toml','--listen',f'127.0.0.1:{port}','--origin',f'http://127.0.0.1:{port}','--postgres-env','DIAMOND_POSTGRES','--runtime-env','DIAMOND_RUNTIME','--identity-mailbox',str(MAIL)],cwd=ROOT,env=env,stdout=lf,stderr=subprocess.STDOUT)
    for _ in range(600):
        try:
            st,_,_=req('GET','/');
            if st==200: break
        except Exception: pass
        time.sleep(.05)
    else: raise AssertionError('server did not start poll='+str(server.poll())+': '+LOG.read_text(errors='replace')[-2000:])
    st,_,html=req('GET','/')
    html=html.decode() if isinstance(html,bytes) else str(html)
    contract=re.search(r'data-contract="([0-9a-f]{64})"',html).group(1)

    ck,csrf=session()
    st,h,b=req('POST','/_diamond/login',{'username':'alice','password':pw},{'X-Diamond-CSRF':csrf},ck)
    check('password login',st==200,str((st,b))); ck=h['set-cookie'].split(';',1)[0]; csrf=b['csrf']
    st,_,b=rpc(ck,csrf,contract,'register_repository',['public-repo',True])
    check('register public repository',st==200 and isinstance(b.get('value'),str),str((st,b))); repo=b['value']
    st,_,b=rpc(ck,csrf,contract,'initialize_repository',[repo])
    check('initialize bare repository',st==200 and isinstance(b.get('value'),str),str((st,b)))
    check('opaque repository path',list(GITROOT.glob('*.git')) and repo in list(GITROOT.glob('*.git'))[0].name)
    bare=list(GITROOT.glob('*.git'))[0]
    check('bare fsck empty',run(['git','--git-dir',str(bare),'fsck','--strict']).returncode==0)

    # Token creation through the real account interface.
    token_body={'label':'redteam-rw','operations':['git:repos:read','git:repos:write'],'scopes':{'repository':[repo]},'lifetime_seconds':'3600'}
    st,_,b=req('POST','/_diamond/accounts/create_token',token_body,{'X-Diamond-CSRF':csrf},ck)
    check('scoped Git token create',st==200 and b.get('value','').startswith('dia_'),str((st,b))); token=b['value']; token_id=b['id']
    ro_body={'label':'redteam-ro','operations':['git:repos:read'],'scopes':{'repository':[repo]},'lifetime_seconds':'3600'}
    st,_,b=req('POST','/_diamond/accounts/create_token',ro_body,{'X-Diamond-CSRF':csrf},ck)
    check('read-only Git token create',st==200 and b.get('value','').startswith('dia_'),str((st,b))); ro=b['value']

    public_url=f'http://127.0.0.1:{port}/_diamond/git/repos/{repo}.git'
    auth_url=f'http://diamond:{token}@127.0.0.1:{port}/_diamond/git/repos/{repo}.git'
    ro_url=f'http://diamond:{ro}@127.0.0.1:{port}/_diamond/git/repos/{repo}.git'
    clone=TMP/'clone'
    r=run(['git','clone',public_url,str(clone)],timeout=60)
    check('anonymous stock Git clone',r.returncode==0,(r.stdout+r.stderr)[-1000:])
    run(['git','config','user.email','alice@example.test'],cwd=clone)
    run(['git','config','user.name','Alice'],cwd=clone)
    (clone/'README.md').write_text('# Diamond\nfirst\n')
    cmd_ok('commit first',['git','add','README.md'],cwd=clone); cmd_ok('commit first object',['git','commit','-m','first'],cwd=clone)
    r=run(['git','push',ro_url,'HEAD:main'],cwd=clone)
    check('read token push denied',r.returncode!=0)
    cmd_ok('write token push',['git','push',auth_url,'HEAD:main'],cwd=clone,timeout=60)
    cmd_ok('bare fsck after push',['git','--git-dir',str(bare),'fsck','--strict'])
    cmd_ok('create feature ref',['git','branch','feature'],cwd=clone)
    cmd_ok('create tag ref',['git','tag','v1'],cwd=clone)
    cmd_ok('atomic multi-ref push',['git','push','--atomic',auth_url,'refs/heads/feature:refs/heads/feature','refs/tags/v1:refs/tags/v1'],cwd=clone,timeout=60)
    refs=run(['git','ls-remote',public_url],timeout=30).stdout
    check('atomic branches and tags visible','refs/heads/feature' in refs and 'refs/tags/v1' in refs)
    cmd_ok('public clone populated',['git','clone','--filter=blob:none','--depth=1',public_url,str(TMP/'clone2')],timeout=60)
    check('clone content exact',(TMP/'clone2'/'README.md').read_text()=='# Diamond\nfirst\n')

    # Browser Git views compare to native Git.
    st,_,b=rpc(ck,csrf,contract,'repository_head',[repo])
    check('head typed view',st==200 and b['value']['reference']=='refs/heads/main',str(b))
    st,_,b=rpc(ck,csrf,contract,'repository_tree',[repo,'main',''])
    check('tree typed view',st==200 and b['value']['entries'][0]['name']=='README.md',str(b))
    st,_,b=rpc(ck,csrf,contract,'repository_blob',[repo,'main','README.md'])
    check('blob typed view',st==200 and b['value']['text']=='# Diamond\nfirst\n',str(b))
    st,_,b=rpc(ck,csrf,contract,'repository_history',[repo,'main'])
    check('history typed view',st==200 and b['value']['commits'][0]['subject']=='first',str(b))

    # Scope boundaries and hidden private repositories.
    st,_,b=rpc(ck,csrf,contract,'register_repository',['private-repo',False]); check('register private repository',st==200,str((st,b))); private_repo=b['value']
    st,_,b=rpc(ck,csrf,contract,'initialize_repository',[private_repo]); check('initialize private repository',st==200,str((st,b)))
    private_url=f'http://127.0.0.1:{port}/_diamond/git/repos/{private_repo}.git'
    st,_,_=req('GET',f'/_diamond/git/repos/{private_repo}.git/info/refs?service=git-upload-pack')
    check('private repository anonymous concealment',st==401,f'status {st}')
    wrong_basic='Basic '+base64.b64encode(('diamond:'+token).encode()).decode()
    st,_,_=req('GET',f'/_diamond/git/repos/{private_repo}.git/info/refs?service=git-upload-pack',headers={'Authorization':wrong_basic})
    check('cross-repository token scope denied',st==403,f'status {st}')
    private_body={'label':'private-read','operations':['git:repos:read'],'scopes':{'repository':[private_repo]},'lifetime_seconds':'3600'}
    st,_,b=req('POST','/_diamond/accounts/create_token',private_body,{'X-Diamond-CSRF':csrf},ck); check('private scoped token create',st==200,str((st,b))); private_token=b['value']
    cmd_ok('private scoped stock clone',['git','clone',f'http://diamond:{private_token}@127.0.0.1:{port}/_diamond/git/repos/{private_repo}.git',str(TMP/'private-clone')],timeout=60)
    bad_scope={'label':'bad','operations':['git:repos:write'],'scopes':{},'lifetime_seconds':'3600'}
    st,_,_=req('POST','/_diamond/accounts/create_token',bad_scope,{'X-Diamond-CSRF':csrf},ck)
    check('missing Git scope rejected',st>=400,f'status {st}')

    # A synthetically provisioned second account may mint a syntactically valid
    # scoped credential, but repository policy still owns the final decision.
    bck,bcsrf=session()
    st,h,b=req('POST','/_diamond/login',{'username':'bob','password':bob_pw},{'X-Diamond-CSRF':bcsrf},bck)
    check('second user login',st==200,str((st,b))); bck=h['set-cookie'].split(';',1)[0]; bcsrf=b['csrf']
    bob_public={'label':'bob-public','operations':['git:repos:read','git:repos:write'],'scopes':{'repository':[repo]},'lifetime_seconds':'3600'}
    st,_,b=req('POST','/_diamond/accounts/create_token',bob_public,{'X-Diamond-CSRF':bcsrf},bck)
    check('second user scoped credential created',st==200,str((st,b))); bob_token=b['value']
    bob_public_url=f'http://diamond:{bob_token}@127.0.0.1:{port}/_diamond/git/repos/{repo}.git'
    cmd_ok('second user may read public repository',['git','ls-remote',bob_public_url],timeout=30)
    r=run(['git','push',bob_public_url,'HEAD:main'],cwd=clone,timeout=30)
    check('non-owner write denied by resource policy',r.returncode!=0)
    bob_private={'label':'bob-private','operations':['git:repos:read'],'scopes':{'repository':[private_repo]},'lifetime_seconds':'3600'}
    st,_,b=req('POST','/_diamond/accounts/create_token',bob_private,{'X-Diamond-CSRF':bcsrf},bck)
    check('second user private credential created',st==200,str((st,b))); bob_private_token=b['value']
    r=run(['git','ls-remote',f'http://diamond:{bob_private_token}@127.0.0.1:{port}/_diamond/git/repos/{private_repo}.git'],timeout=30)
    check('non-owner private read denied by resource policy',r.returncode!=0)

    first=run(['git','rev-parse','HEAD'],cwd=clone).stdout.strip()
    (clone/'README.md').write_text('# Diamond\nsecond\n')
    cmd_ok('commit second',['git','commit','-am','second'],cwd=clone)
    second=run(['git','rev-parse','HEAD'],cwd=clone).stdout.strip()
    cmd_ok('push second',['git','push',auth_url,'HEAD:main'],cwd=clone)
    st,_,b=rpc(ck,csrf,contract,'repository_diff',[repo,first,second])
    check('diff typed view',st==200 and '-first' in b['value']['text'] and '+second' in b['value']['text'],str(b))

    # Git permits the old field-separator byte in identity and subject text.
    # The typed history framing must preserve it rather than losing the view.
    cmd_ok('set hostile author separator',['git','config','user.name','Alice\x1fInjected'],cwd=clone)
    (clone/'README.md').write_text('# Diamond\ndelimiter\n')
    cmd_ok('commit delimiter probe',['git','commit','-am','delimiter\x1fsubject'],cwd=clone)
    cmd_ok('push with push-option',['git','push','-o','diamond.probe=1',auth_url,'HEAD:main'],cwd=clone,timeout=60)
    st,_,b=rpc(ck,csrf,contract,'repository_history',[repo,'main'])
    check('history preserves hostile separator',st==200 and b['value']['commits'][0]['subject']=='delimiter\x1fsubject' and b['value']['commits'][0]['author_name']=='Alice\x1fInjected',str(b))
    cmd_ok('restore ordinary author',['git','config','user.name','Alice'],cwd=clone)

    # Hook bypass attempt in repository-local default hooks path.
    sentinel=TMP/'HOOK_EXECUTED'; hook=bare/'hooks'/'pre-receive'; hook.write_text(f'#!/bin/sh\ntouch {sentinel}\nexit 0\n'); hook.chmod(0o755)
    (clone/'README.md').write_text('# Diamond\nthird\n'); cmd_ok('commit hook probe',['git','commit','-am','third'],cwd=clone); cmd_ok('push hook probe',['git','push',auth_url,'HEAD:main'],cwd=clone)
    check('repository hook ignored',not sentinel.exists())

    # Local configuration cannot install an upload-pack process hook, and missing
    # mandatory settings are reconciled by the idempotent create operation.
    pack_sentinel=TMP/'PACK_HOOK_EXECUTED'; pack_hook=TMP/'pack-hook'; pack_hook.write_text(f'#!/bin/sh\ntouch {pack_sentinel}\nexit 1\n'); pack_hook.chmod(0o755)
    cmd_ok('install hostile packObjectsHook',['git','--git-dir',str(bare),'config','--local','uploadpack.packObjectsHook',str(pack_hook)])
    r=run(['git','ls-remote',public_url],timeout=30)
    check('hostile repository config denied',r.returncode!=0)
    check('hostile pack hook never executed',not pack_sentinel.exists())
    cmd_ok('remove hostile packObjectsHook',['git','--git-dir',str(bare),'config','--local','--unset-all','uploadpack.packObjectsHook'])
    global_sentinel=TMP/'GLOBAL_PACK_HOOK_EXECUTED'; global_hook=TMP/'global-pack-hook'; global_hook.write_text(f'#!/bin/sh\ntouch {global_sentinel}\nexit 1\n'); global_hook.chmod(0o755)
    (GITROOT/'.gitconfig').write_text(f'[uploadpack]\n\tpackObjectsHook = {global_hook}\n')
    cmd_ok('global Git config ignored',['git','clone',public_url,str(TMP/'global-config-clone')],timeout=60)
    check('global pack hook never executed',not global_sentinel.exists())
    (GITROOT/'.gitconfig').unlink()
    cmd_ok('remove mandatory config',['git','--git-dir',str(bare),'config','--local','--unset-all','uploadpack.allowFilter'])
    st,_,b=rpc(ck,csrf,contract,'initialize_repository',[repo])
    check('idempotent Git config reconciliation',st==200 and b['value']=='existing',str((st,b)))
    cmd_ok('clone after config reconciliation',['git','ls-remote',public_url],timeout=30)

    # Credential/path/protocol attacks.
    probes=[
      ('traversal','GET',f'/_diamond/git/repos/../{repo}.git/info/refs?service=git-upload-pack',{}),
      ('encoded traversal','GET',f'/_diamond/git/repos/%2e%2e.git/info/refs?service=git-upload-pack',{}),
      ('service injection','GET',f'/_diamond/git/repos/{repo}.git/info/refs?service=git-upload-pack%26x',{}),
      ('unknown binding','GET',f'/_diamond/git/evil/{repo}.git/info/refs?service=git-upload-pack',{}),
      ('cookie ambient auth','GET',f'/_diamond/git/repos/{repo}.git/info/refs?service=git-upload-pack',{'Cookie':ck}),
      ('bad protocol','GET',f'/_diamond/git/repos/{repo}.git/info/refs?service=git-upload-pack',{'Git-Protocol':'version=999'}),
      ('basic smuggling','GET',f'/_diamond/git/repos/{repo}.git/info/refs?service=git-upload-pack',{'Authorization':'Basic '+base64.b64encode(b'diamond:not-a-token').decode()}),
    ]
    for name,method,path,headers in probes:
        st,_,_=req(method,path,headers=headers)
        check('reject '+name,st>=400,f'status {st}')
    c=http.client.HTTPConnection('127.0.0.1',port,timeout=15)
    c.request('GET',f'/_diamond/git/repos/{repo}.git/info/refs?service=git-upload-pack',body=b'x',headers={'Content-Length':'1'})
    get_with_body=c.getresponse(); get_with_body.read(); c.close()
    check('reject Git discovery body',get_with_body.status==400,f'status {get_with_body.status}')
    st,_,_=req('GET',f'/_diamond/git/repos/{repo}.git/info/refs?service=git-receive-pack')
    check('receive advertisement needs auth',st==401,f'status {st}')
    st,_,b=rpc(ck,csrf,contract,'repository_blob',[repo,'--help','../config'])
    check('revision and path injection denied',st>=400,str((st,b)))

    # Oversized browser blob remains bounded while transport can carry it.
    (clone/'large.txt').write_bytes(b'x'*(1024*1024+1)); cmd_ok('commit large',['git','add','large.txt'],cwd=clone); cmd_ok('commit large object',['git','commit','-m','large'],cwd=clone); cmd_ok('push large',['git','push',auth_url,'HEAD:main'],cwd=clone,timeout=60)
    st,_,b=rpc(ck,csrf,contract,'repository_blob',[repo,'main','large.txt'])
    check('oversized blob denied',st>=400,str((st,b)))

    # Incompressible pack traffic stays out of the Diamond heap and canceled
    # clients release their Git subprocesses.
    (clone/'random.bin').write_bytes(os.urandom(24*1024*1024)); cmd_ok('stage random pack',['git','add','random.bin'],cwd=clone); cmd_ok('commit random pack',['git','commit','-m','random-pack'],cwd=clone); cmd_ok('push random pack',['git','push',auth_url,'HEAD:main'],cwd=clone,timeout=120)
    baseline=rss_kib(server.pid); big_clone=TMP/'big-clone'; proc=subprocess.Popen(['git','clone',public_url,str(big_clone)],cwd=ROOT,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    peak=baseline
    while proc.poll() is None:
        peak=max(peak,rss_kib(server.pid)); time.sleep(.01)
    out,err=proc.communicate(timeout=10); check('large streamed clone',proc.returncode==0,(out+err)[-1000:].decode(errors='replace'))
    check('server RSS bounded during 24 MiB pack',peak-baseline < 32*1024,f'baseline={baseline} peak={peak}')
    metrics['server_rss_baseline_kib']=baseline
    metrics['server_rss_peak_kib']=peak
    metrics['server_rss_delta_kib']=peak-baseline
    slow=socket.create_connection(('127.0.0.1',port),timeout=5)
    slow.sendall((f'POST /_diamond/git/repos/{repo}.git/git-upload-pack HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nContent-Type: application/x-git-upload-pack-request\r\nContent-Length: 100000\r\nConnection: close\r\n\r\n0032').encode())
    observed=False
    for _ in range(200):
        if children(server.pid): observed=True; break
        time.sleep(.005)
    slow.close(); time.sleep(.5)
    check('interrupted request reached Git backend',observed)
    check('interrupted request released backend',not children(server.pid),str(children(server.pid)))

    # Repository symlink swap is detected before Git execution.
    moved=bare.with_suffix('.saved'); bare.rename(moved); bare.symlink_to(moved, target_is_directory=True)
    st,_,_=req('GET',f'/_diamond/git/repos/{repo}.git/info/refs?service=git-upload-pack')
    check('repository symlink swap denied',st==401,f'status {st}')
    bare.unlink(); moved.rename(bare)
    cmd_ok('fsck after tamper replay',['git','--git-dir',str(bare),'fsck','--strict'])

    # Revocation crosses directly into Git auth.
    st,_,_=req('POST','/_diamond/accounts/revoke_token',{'id':token_id},{'X-Diamond-CSRF':csrf},ck)
    check('token revoke endpoint',st==200,f'status {st}')
    revoked_basic='Basic '+base64.b64encode(('diamond:'+token).encode()).decode()
    st,_,_=req('GET',f'/_diamond/git/repos/{repo}.git/info/refs?service=git-receive-pack',headers={'Authorization':revoked_basic})
    check('revoked token denied',st==401,f'status {st}')

    # OAuth start/state tests through public HTTP and direct DB observations.
    ock,ocsrf=session()
    st,oauth_headers,b=req('POST','/_diamond/oauth/google/start',{'invite':''},{'X-Diamond-CSRF':ocsrf},ock)
    check('Google OAuth start',st==200 and b.get('authorization_url','').startswith('https://accounts.google.com/'),str((st,b)))
    check('OAuth start is not cacheable',oauth_headers.get('cache-control')=='no-store',str(oauth_headers))
    q=dict(urllib.parse.parse_qsl(urllib.parse.urlparse(b['authorization_url']).query)); state=q['state']
    check('OAuth PKCE and nonce',q.get('code_challenge_method')=='S256' and len(q.get('code_challenge',''))==43 and len(q.get('nonce',''))==64,str(q))
    rows=psql("SELECT count(*),bool_and(digest<>'"+state+"' AND envelope NOT LIKE '%"+q['nonce']+"%') FROM diamond_identity.oauth_transactions;")
    check('OAuth state is digested and transaction secrets encrypted',rows=='1|t',rows)
    other,_=session()
    st,_,_=req('GET',f'/_diamond/oauth/google/callback?state={state}&error=access_denied',cookie=other)
    check('OAuth state bound to browser session',st==403,f'status {st}')
    check('mismatched session did not consume state',psql('SELECT count(*) FROM diamond_identity.oauth_transactions;')=='1')
    st,_,_=req('GET',f'/_diamond/oauth/github/callback?state={state}&error=access_denied',cookie=ock)
    check('OAuth provider mix-up denied',st==403,f'status {st}')
    check('provider mix-up did not consume state',psql('SELECT count(*) FROM diamond_identity.oauth_transactions;')=='1')
    st,_,_=req('GET',f'/_diamond/oauth/google/callback?state={state}&state={state}&error=access_denied',cookie=ock)
    check('duplicate OAuth callback field denied',st==403,f'status {st}')
    check('malformed callback did not consume state',psql('SELECT count(*) FROM diamond_identity.oauth_transactions;')=='1')
    st,_,_=req('GET',f'/_diamond/oauth/google/callback?state={state}&error=access_denied',cookie=ock)
    check('OAuth cancellation consumes exact transaction',st==403,f'status {st}')
    check('OAuth transaction consumed',psql('SELECT count(*) FROM diamond_identity.oauth_transactions;')=='0')
    st,_,_=req('GET',f'/_diamond/oauth/google/callback?state={state}&error=access_denied',cookie=ock)
    check('OAuth state replay denied',st==403,f'status {st}')
    st,_,_=req('POST','/_diamond/oauth/google/start',{'invite':''},{'X-Diamond-CSRF':ocsrf,'Sec-Fetch-Site':'cross-site'},ock)
    check('cross-site OAuth start denied',st==400 or st==403,f'status {st}')
    st,_,_=req('POST','/_diamond/oauth/evil/start',{'invite':''},{'X-Diamond-CSRF':ocsrf},ock)
    check('unknown OAuth provider denied',st==403,f'status {st}')

    # Store invariants and redaction.
    db_secrets=psql("SELECT count(*) FROM diamond_identity.credentials WHERE digest LIKE 'dia_%';")
    check('credential stored digest-only',db_secrets=='0',db_secrets)
    logs=LOG.read_text(errors='replace')
    check('tokens absent from logs',token not in logs and ro not in logs and state not in logs)
    print(json.dumps({
      'profile':'Diamond Git and OAuth foundation red-team campaign',
      'versions':{
        'diamond':run([str(BIN),'--version']).stdout.strip(),
        'git':run(['git','--version']).stdout.strip(),
        'postgres':run(['postgres','--version']).stdout.strip(),
        'python':sys.version.split()[0],
      },
      'passed':sum(1 for result in results if result['pass']),
      'total':len(results),
      'metrics':metrics,
      'results':results,
    },indent=2))
finally:
    if server is not None:
        server.terminate()
        try: server.wait(timeout=8)
        except subprocess.TimeoutExpired: server.kill(); server.wait()
    try: subprocess.run(['pg_ctl','-D',str(PG),'-m','fast','-w','stop'],capture_output=True,timeout=20)
    except Exception: pass
    shutil.rmtree(TMP, ignore_errors=True)
