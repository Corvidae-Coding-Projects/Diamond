#!/usr/bin/env python3
"""Independent LP3 CLI/native/W1/connected/URL-encoded input campaign."""
import copy, hashlib, html, http.client, json, os, random, shutil, signal, subprocess, sys, tempfile, time
from pathlib import Path
from urllib.parse import quote, urlencode
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'verification'))
from w5_independent.service import Service,Client,ENV_NAME,free_port,sha256
SEED=0xD1A30AC4
PASSWORD='Independent LP3 disposable 19!'
APP=hashlib.sha256(b'TypedInputs').hexdigest()
def table(name):return 'diamond_w4."r_'+APP[:16]+'_'+hashlib.sha256(name.encode()).hexdigest()[:16]+'"'
def main():
    owned=Path(tempfile.mkdtemp(prefix='diamond-lp3-independent-'));binary=owned/'diamond';shutil.copy2(ROOT/'target/debug/diamond',binary)
    fixture=owned/'inputs';shutil.copytree(ROOT/'examples/typed-inputs',fixture)
    shared=fixture/'shared.dia';shared.write_text(shared.read_text()+'''
export route RequiredPage(text: Text<length: 0..40>, enabled: Bool, state: Status, scope: WorkspaceId) = "/required?text={text}&enabled={enabled}&state={state}&scope={scope}"
export route DefaultPage(enabled: Bool = true, text: Text<length: 0..40> = "雪😀") = "/defaults?enabled={enabled}&text={text}"
''')
    extra='''      <route name="RequiredPage" as="required"><p id="required-text">{required.text}</p><p id="required-enabled">{required.enabled}</p><p id="required-state">{status_text(required.state)}</p><p id="required-scope">{required.scope}</p></route>
      <route name="DefaultPage" as="defaults"><p id="default-enabled">{defaults.enabled}</p><p id="default-text">{defaults.text}</p><a href={DefaultPage()}>Default URL</a></route>
'''
    ui=fixture/'ui.dia';ui.write_text(ui.read_text().replace('      <route path="/optional-form">',extra+'      <route path="/optional-form">'))
    service=Service(owned,binary,fixture/'diamond.toml');service.extra_serve=('--identity-mailbox',str(service.mailbox))
    processes=[];obs=[];error=None;start=time.monotonic()
    def record(name,passed,observed,expected):
        obs.append({'name':name,'pass':bool(passed),'observed':observed,'expected':expected})
        if not passed:print(json.dumps({'finding':name,'observed':observed,'expected':expected},ensure_ascii=True),flush=True)
    def request(port,path,body=None,headers=None):
        c=http.client.HTTPConnection('127.0.0.1',port,timeout=15)
        try:
            c.request('GET' if body is None else 'POST',path,body,headers or {});r=c.getresponse();return r.status,r.read().decode('utf-8','replace')
        finally:c.close()
    def launch(args,port):
        log=(owned/('process-'+str(port)+'.log')).open('w');service.logs.append(log)
        p=subprocess.Popen(list(map(str,args)),stdout=log,stderr=log);processes.append(p)
        for _ in range(200):
            try:
                if request(port,'/')[0]==200:return
            except OSError:pass
            if p.poll() is not None:raise RuntimeError(Path(log.name).read_text())
            time.sleep(.025)
        raise RuntimeError('startup deadline')
    def sqlrows():return service.sql_json(f"SELECT id,convert_from(decode(title,'hex'),'UTF8') AS title,revision,estimate,published,parent,status,convert_from(decode(note,'hex'),'UTF8') AS note FROM {table('Issues')} ORDER BY id")
    def nom(tag,value):return {'tag':tag,'value':value}
    def value(number='17',title='雪😀'):return {'title':title,'estimate':number,'published':False,'parent':nom('workspace','alpha'),'status':{'tag':'Closed','fields':[]},'note':{'tag':'None'}}
    def action(client,fields):
        body={'contract':client.contract,'arguments':[nom('issue','alpha-a'),fields]}
        status,prepared=client.request('A','/_diamond/actions/rename/prepare',body)
        return (status,prepared) if status!=200 else client.request('A','/_diamond/actions/rename',{**body,'ticket':prepared['ticket']})
    try:
        meta,build=service.build();contract=meta['rpc_id'];native=owned/'native-render'
        subprocess.run(['rustc','--edition=2024',ROOT/'verification/lp3/independent_render.rs','--extern','diamond='+str(ROOT/'target/debug/libdiamond.rlib'),'-L','dependency='+str(ROOT/'target/debug/deps'),'-o',native],check=True,capture_output=True,timeout=60)
        static=owned/'static';shutil.copytree(fixture,static)
        manifest=(static/'diamond.toml').read_text().split('[rpc]')[0].replace('provides = ["DbRead<Members>", "DbRead<Issues>", "DbWrite<Issues>"]','provides = []')
        (static/'diamond.toml').write_text(manifest);(static/'server.dia').write_text('export fn main() -> Bool:\n  true\n')
        (static/'ui.dia').write_text('''export component Workspace:
  html:
    <main>
      <h1>Independent static route consumer</h1>
      <route name="IssuePage" as="detail"><p id="route-id">{detail.id}</p><p id="page">{detail.page}</p><p id="search">{note_text(detail.search)}</p></route>
      <route name="ValuePage" as="values"><p id="scalar-count">{values.count}</p><p id="scalar-enabled">{values.enabled}</p><p id="scalar-label">{values.label}</p><p id="scalar-status">{status_text(values.status)}</p><p id="scalar-scope">{values.scope}</p><p id="scalar-extra">{optional_int(values.extra)}</p><p id="scalar-flag">{optional_bool(values.flag)}</p><p id="scalar-owner">{optional_id(values.owner)}</p><p id="scalar-mode">{optional_status(values.mode)}</p></route>
'''+extra+'''    </main>
''')
        port=free_port();launch([binary,'serve',static/'diamond.toml','--listen','127.0.0.1:'+str(port)],port)
        service.postgres();service.cli('project-init',service.project,'--postgres-env',ENV_NAME)
        service.cli('project-user-add',service.project,'--postgres-env',ENV_NAME,'--user','alice','--password-stdin',input=PASSWORD+'\n')
        service.sql(f'''INSERT INTO {table('Members')}(id,workspace,"user",role) VALUES ('m-a','alpha','alice','Editor'); INSERT INTO {table('Issues')}(id,workspace,title,revision,estimate,published,parent,status,note) VALUES ('alpha-a','alpha','4f6c64',0,0,false,'alpha','Open',NULL)''')
        service.start_node('A');client=Client(service,contract);client.login('alice',PASSWORD);routes=[]
        def valid(path,fields):routes.append((path,True,fields))
        def invalid(path):routes.append((path,False,{}))
        valid('/issues/alpha-a',{'page':'1','search':'','route-id':'alpha-a'})
        valid('/issues/alpha-a?page=2&search='+quote('雪😀',safe=''),{'page':'2','search':'雪😀'})
        valid('/defaults',{'default-enabled':'true','default-text':'雪😀'})
        valid('/defaults?enabled=false&text=',{'default-enabled':'false','default-text':''})
        valid('/required?enabled=false&scope=alpha&state=Open&text=',{'required-enabled':'false','required-scope':'alpha','required-state':'Open','required-text':''})
        rng=random.Random(SEED)
        for i in range(18):
            count=rng.choice([-9223372036854775808,9223372036854775807,-17,0,17]);enabled=bool(i%2);label=rng.choice(['雪😀','e\u0301','é','!$&=;:@[]()*','a b','\u2028\u2029','\u200e','ß','~._-']);status='Closed' if i%3 else 'Open';tail='' if i%2 else '?extra=-17&flag=false&mode=Closed&owner=alpha'
            valid(f'/values/{count}/{str(enabled).lower()}/{quote(label,safe="-._~")}/{status}/alpha'+tail,{'scalar-count':str(count),'scalar-enabled':str(enabled).lower(),'scalar-label':label,'scalar-status':status,'scalar-scope':'alpha','scalar-extra':'absent' if not tail else '-17','scalar-flag':'absent' if not tail else 'false','scalar-mode':'absent' if not tail else 'Closed','scalar-owner':'absent' if not tail else 'alpha'})
        for path in ['/defaults?enabled=true','/defaults?state=Closed','/defaults?text='+quote('雪😀',safe=''),'/required','/required?enabled=false&scope=alpha&state=Open','/issues/alpha-a?page=1','/issues/alpha-a?search=','/issues/alpha-a?','/issues/alpha-a?page=02','/issues/alpha-a?page=-0','/issues/alpha-a?page=%32','/issues/alpha-a?page=9223372036854775808','/issues/alpha-a?search=x&page=2','/issues/alpha-a?page=2&page=3','/issues/alpha-a?%70age=2','/issues/alpha-a?unknown=x','/issues/alpha-a?search=a+b','/issues/alpha-a?search=%2f','/issues/alpha-a?search=%252F','/issues/alpha-a?search=%C0%AF','/issues/alpha-a?search=%ED%A0%80','/issues/alpha-a?search=%EF%BF%BD%FF','/issues/alpha-a?search=%23','/issues/alpha-a?search=%3F','/issues/alpha-a?search=%5C','/issues/alpha-a?search=%00','/issues/alpha-a?search=%7F','/issues/alpha-a?search=%C2%85','/issues/a%2Fb','/issues/%61lpha-a','//issues/alpha-a','/values/17/true/./Open/alpha','/values/17/true/../Open/alpha','/values/17/True/x/Open/alpha','/values/17/true/x/Unknown/alpha','/issues/alpha-a?search='+quote('雪'*81,safe='')]:invalid(path)
        run=subprocess.run([native,service.project],input='\n'.join(p for p,_,_ in routes)+'\n',capture_output=True,text=True,timeout=45,check=True);lines=run.stdout.splitlines()
        record('native-result-count',len(lines)==len(routes),len(lines),len(routes))
        for i,(path,good,fields) in enumerate(routes):
            for mode in ['native','static-http','connected-http']:
                if mode=='native':
                    line=lines[i];status=200 if line.startswith('OK ') else 400;body=bytes.fromhex(line[3:]).decode() if status==200 else line
                elif mode=='static-http':status,body=request(port,path)
                else:
                    status,_,raw=service.request('A',path,headers={'Sec-Fetch-Site':'cross-site','Sec-Fetch-Mode':'navigate','Sec-Fetch-Dest':'document'});body=raw.decode()
                passed=(status==200 if good else status in [400,404]) and (not good or all('id="'+key+'">'+html.escape(text,quote=False)+'</p>' in body for key,text in fields.items()))
                record(mode+' '+path,passed,{'status':status,'body':body[:250] if not passed else None},'200 and exact declared fields' if good else '400/404')
        status,_,raw=service.request('A','/optional-form');body=raw.decode()
        record('schema-prehydration-method-post',status==200 and '<form method="post"' in body,body[body.find('<form method='):body.find('<form method=')+150],'schema form method=post')
        expected=sqlrows();outcome=action(client,value());expected[0].update(title='雪😀',revision=1,estimate=17,published=False,status='Closed')
        record('wire-all-scalars-sql',outcome[0]==200 and outcome[1]['value']['tag']=='ActionSucceeded' and sqlrows()==expected,{'outcome':outcome,'rows':sqlrows()},expected)
        for number in ['+17','017','-0','1.0','1e2','9223372036854775808','-9223372036854775809','-',' 17']:
            outcome=action(client,value(number));record('wire-invalid-int '+number,outcome[0]==400 and sqlrows()==expected,outcome,'400 SQL unchanged')
        for label,fields in [('wrong-nominal',{**value(),'parent':nom('user','alpha')}),('extra',{**value(),'created_by':nom('user','alice')}),('enum-payload',{**value(),'status':{'tag':'Open','fields':['x']}}),('empty-title',value(title='')),('surrogate',value(title='\ud800'))]:
            outcome=action(client,fields);record('wire-'+label,outcome[0]==400 and sqlrows()==expected,outcome,'400 SQL unchanged')
        legacy=owned/'forms.dia';legacy.write_text('''enum Status:
  Open
  Closed
type WorkspaceId = Id<"workspace">
type Input:
  title: Text<length: 1..120>
  estimate: Int
  published: Bool
  parent: WorkspaceId
  status: Status
  note: Option<Text<length: 0..80>>
fn render(value: Input, state: Text) -> Html:
  html:
    <p id="title">{value.title}</p><p id="estimate">{value.estimate}</p><p id="published">{value.published}</p><p id="parent">{value.parent}</p><p id="status">{state}</p>
public route GET "/":
  "ready"
public route POST "/decode":
  let value = request.form<Input>()?
  match value.status:
    Open => render(value, "Open")
    Closed => render(value, "Closed")
''')
        service.cli('check',legacy);lp=free_port();launch([binary,'run',legacy,'--listen','127.0.0.1:'+str(lp)],lp)
        ordinary={'title':'雪😀','estimate':'17','published':'false','parent':'alpha','status':'Closed','note':''};goodbody=urlencode(ordinary).encode()
        status,body=request(lp,'/decode',goodbody,{'Content-Type':'application/x-www-form-urlencoded'})
        record('urlencoded-all-scalars',status==200 and all('id="'+key+'">'+text+'</p>' in body for key,text in ordinary.items() if key!='note'),{'status':status,'body':body},'200 exact decoded fields')
        for label,data in [('duplicate',goodbody+b'&estimate=18'),('extra',goodbody+b'&created_by=alice'),('bad-percent',goodbody+b'&unknown=%'),('invalid-utf8',goodbody.replace(b'title=',b'title=%FF')),('surrogate',goodbody.replace(b'title=',b'title=%ED%A0%80'))]+[(n,urlencode({**ordinary,'estimate':n}).encode()) for n in ['+17','017','-0','9223372036854775808','-']]:
            status,body=request(lp,'/decode',data,{'Content-Type':'application/x-www-form-urlencoded'});record('urlencoded-invalid-'+label,status==400,{'status':status,'body':body},'400 before action continuation')
        reject=owned/'reject';shutil.copytree(fixture,reject)
        for label,filename,old,new in [('password-control','ui.dia','name="title" type="text"','name="title" type="password"'),('secret-form','shared.dia','title: Text<length: 1..120>, estimate: Int','title: Secret<"credential">, estimate: Int'),('nested-option','shared.dia','estimate: Int, published: Bool','estimate: Option<Option<Int>>, published: Bool')]:
            base=fixture if label=='password-control' else static
            shutil.rmtree(reject);shutil.copytree(base,reject)
            path=reject/filename;path.write_text((base/filename).read_text().replace(old,new));result=service.cli('check',reject/'diamond.toml',success=None)
            record('compile-reject-'+label,result.returncode!=0,{'exit':result.returncode,'stderr':result.stderr},'explicit compiler rejection');path.write_text((fixture/filename).read_text())
        bad=owned/'created-by.dia';bad.write_text(legacy.read_text().replace('parent: WorkspaceId','parent: Id<"user"> @created_by_principal'))
        result=service.cli('check',bad,success=None);record('compile-reject-server-derived',result.returncode!=0,{'exit':result.returncode,'stderr':result.stderr},'compiler rejection')
        outcome=action(client,value(number='-17',title='Final ordinary'));record('valid-followup',outcome[0]==200 and sqlrows()[0]['title']=='Final ordinary',{'outcome':outcome,'rows':sqlrows()},'later valid action works')
        record('runtime-log-no-password',all(PASSWORD not in p.read_text() for p in owned.glob('*.log')),True,'synthetic password absent')
    except Exception as failure:error=repr(failure);print(json.dumps({'error':error}),flush=True)
    finally:
        for p in processes:
            if p.poll() is None:p.send_signal(signal.SIGINT)
            try:p.wait(timeout=10)
            except subprocess.TimeoutExpired:p.kill();p.wait()
        service.close()
        report={'status':'PASS' if error is None and all(o['pass'] for o in obs) and not service.cleanup_errors else 'FAIL','error':error,'binary_sha256':sha256(binary),'native_library_sha256':sha256(ROOT/'target/debug/libdiamond.rlib'),'driver_sha256':sha256(__file__),'native_driver_sha256':sha256(ROOT/'verification/lp3/independent_render.rs'),'seed':SEED,'observations':obs,'elapsed_seconds':time.monotonic()-start,'request_trace':service.trace,'shutdowns':service.shutdowns,'cleanup_errors':service.cleanup_errors,'gaps':['No browser operated here; parent owns browser verification.','Legacy URL-encoded fields carry no nominal wire tags; declaration assigns their nominal type.','URL identifiers confer no access authority.','Schema password controls reject; dedicated account draft lifecycle outside this campaign.']}
        destination=Path(sys.argv[1]) if len(sys.argv)>1 else ROOT/'verification/lp3/independent.json';destination.write_text(json.dumps(report,ensure_ascii=True,indent=2)+'\n')
        print(json.dumps({'status':report['status'],'observations':len(obs),'failures':sum(not o['pass'] for o in obs),'error':error,'report':str(destination)}),flush=True);shutil.rmtree(owned)
    return int(report['status']!='PASS')
if __name__=='__main__':raise SystemExit(main())
