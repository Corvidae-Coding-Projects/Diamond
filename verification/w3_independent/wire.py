#!/usr/bin/env python3
"""Independent real-HTTP W3 composite wire oracle, sharing only W2 service lifecycle setup."""
import argparse,copy,hashlib,importlib.util,json,os,random,shutil,subprocess,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];SEED=0xD1A09318
MANIFEST='''schema_version = 1
name = "IndependentCompositeWire"
server = "server"
browser = "ui"
component = "App"
styles = ["app.css"]
[assets]
badge = "badge.svg"
[modules.shared]
path = "shared.dia"
target = "shared"
[modules.server]
path = "server.dia"
target = "server"
imports = ["shared"]
[modules.ui]
path = "ui.dia"
target = "browser"
imports = ["shared"]
[rpc]
echo = "server.echo"
result = "server.result"
phase = "server.phase"
id_echo = "server.id_echo"
typed_failure = "server.typed_failure"
host_failure = "server.host_failure"
add = "server.add"
double = "server.double"
echo_text = "server.echo_text"
fail_int = "server.fail_int"
fail_bool = "server.fail_bool"
inspect_data = "server.inspect_data"
echo_data = "server.echo_data"
'''
SHARED='''export type TicketId = Id<"Ticket">

export enum ApplicationError:
  Rejected(reason: Text)
  Missing

export enum Phase:
  Open
  Assigned(owner: Text, count: Int)

export type Payload:
  ticket: TicketId
  title: Text<length: 1..10>
  decimal: Decimal
  stamp: Timestamp
  wait: Duration
  maybe: Option<Int>
  numbers: List<Int>
  phase: Phase

fn unused_shared_secret() -> Text:
  "INDEPENDENT_W3_UNUSED_HELPER_CANARY_6B2E"

type UnusedPrivateRecord:
  secret: Text = "INDEPENDENT_W3_UNUSED_TYPE_CANARY_6B2E"
'''
SERVER='''export fn main() -> Int:
  17

fn server_secret() -> Text:
  "INDEPENDENT_W3_SERVER_CANARY_B7F24"

export fn echo(value: Payload) -> Payload:
  value

export fn result(value: Result<Payload, ApplicationError>) -> Result<Payload, ApplicationError>:
  value

export fn phase(value: Phase) -> Phase:
  value

export fn id_echo(value: TicketId) -> TicketId:
  value

export fn typed_failure() -> Result<Int, ApplicationError>:
  Err(Rejected("specific detail"))

export fn host_failure() -> Result<Decimal, ApplicationError>:
  Ok(decimal_divide(decimal_units(1), decimal_units(0), "half_even"))

export fn add(a: Int, b: Int) -> Int:
  a + b

export fn double(value: Int) -> Int:
  value * 2

export fn echo_text(value: Text) -> Text:
  value

export fn fail_int() -> Int:
  1 / 0

export fn fail_bool() -> Bool:
  1 / 0 == 0

export fn inspect_data(value: Result<Int, DataError>) -> Text:
  match value:
    Ok(number) => int_text(number)
    Err(error) => error_message(error)

export fn echo_data(value: Result<Int, DataError>) -> Result<Result<Int, DataError>, ApplicationError>:
  Ok(value)
'''
UI='''export component App:
  state heading: Text = "Independent <script>& evidence"
  html:
    <main><h1>{heading}</h1><img src="asset:badge" alt="Independent badge"><a href="/detail">Detail</a><route path="/detail"><p id="direct">Direct route 雪</p></route></main>

component Unused:
  state hidden: Text = "INDEPENDENT_W3_UNUSED_COMPONENT_CANARY_6B2E"
  html:
    <p>{hidden}</p>
'''

def main():
    p=argparse.ArgumentParser();p.add_argument('--binary',type=Path,default=ROOT/'target/debug/diamond');p.add_argument('--report',type=Path,required=True);o=p.parse_args()
    root=Path(tempfile.mkdtemp(prefix='diamond-w3-independent-wire-'));binary=root/'diamond';shutil.copy2(o.binary,binary)
    spec=importlib.util.spec_from_file_location('w2_setup_independent',ROOT/'verification/web_two.py');setup=importlib.util.module_from_spec(spec);spec.loader.exec_module(setup);setup.BINARY=binary
    fixture_root=root/'fixture';fixture_root.mkdir();f=setup.Fixture(fixture_root)
    for name,content in [('diamond.toml',MANIFEST),('shared.dia',SHARED),('server.dia',SERVER),('ui.dia',UI),('app.css','h1 { color: #563b77; }\n.icon { background-image: url("asset:badge"); }\n'),('badge.svg','<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20"><rect width="20" height="20" fill="purple"/></svg>')]:
        (f.project.parent/name).write_text(content)
    results=[];fatal=None
    def record(name,okay,observed,expected):results.append({'name':name,'status':'PASS' if okay else 'FAIL','expected':expected,'observed':observed})
    try:
        f.start();client=setup.Client(f);client.login('alice')
        def rpc(name,args,expected,status=200,body=None,node='A'):
            s,value=client.request('/_diamond/rpc/'+name,body if body is not None else {'contract':f.contract,'arguments':args},node=node)
            record(name+'-'+str(len(results)),s==status and (expected is None or value=={'value':expected}),{'status':s,'value':value},{'status':status,'value':expected})
        def payload(i):
            return {'ticket':{'tag':'Ticket','value':f'item-{i}_x'},'title':['雪😀','Title'][i%2], 'decimal':{'tag':'Decimal','value':str([-9223372036854775808,-1,0,9223372036854775807][i%4])},'stamp':{'tag':'Timestamp','value':str(1700000000123+i)},'wait':{'tag':'Duration','value':str(-100-i)},'maybe':{'tag':'None'} if i%2 else {'tag':'Some','value':str(9223372036854775807-i)},'numbers':[str(i),str(-i),'9007199254740993'],'phase':{'tag':'Open','fields':[]} if i%2 else {'tag':'Assigned','fields':['雪 owner',str(i)]}}
        for i in range(12):
            v=payload(i);rpc('echo',[v],v,node='A' if i%2 else 'B');rpc('result',[{'tag':'Ok','value':v}],{'tag':'Ok','value':v})
        for v in [{'tag':'Err','value':{'tag':'Rejected','fields':['safe <script>\u0000 雪']}},{'tag':'Err','value':{'tag':'Missing','fields':[]}}]:rpc('result',[v],v)
        rpc('typed_failure',[],{'tag':'Err','value':{'tag':'Rejected','fields':['specific detail']}})
        rpc('host_failure',[],None,status=400)
        rpc('add',['17','9'],'26')
        rpc('double',['7'],'14')
        rpc('echo_text',['snow 雪 <literal>'],'snow 雪 <literal>')
        rpc('fail_int',[],None,status=400)
        rpc('fail_bool',[],None,status=400)
        rpc('add',['12','11'],'23',node='B')
        rpc('inspect_data',[{'tag':'Ok','value':'9007199254740993'}],'9007199254740993')
        data_errors={'Denied':'Access denied or resource not found.','InvalidInput':'Input does not satisfy its declared shape or bounds.','Aborted':'Transaction aborted.','ApplicationFailure':'Application failed.'}
        for code,message in data_errors.items():
            value={'tag':'Err','value':code}
            rpc('inspect_data',[value],message)
            rpc('echo_data',[value],{'tag':'Ok','value':value})
        for invalid in [{'tag':'Err','value':'Forged'},{'tag':'Err','value':{'message':'untrusted'}},{'tag':'Ok','value':4}]:
            rpc('inspect_data',[invalid],None,status=400)
        base=payload(0)
        mutations=[('extra-field',lambda v:v.update(extra='x')),('missing-field',lambda v:v.pop('title')),('empty-title',lambda v:v.update(title='')),('long-title',lambda v:v.update(title='😀'*11)),('wrong-id-tag',lambda v:v['ticket'].update(tag='Other')),('bad-id-text',lambda v:v['ticket'].update(value='a/b')),('id-number',lambda v:v['ticket'].update(value=7)),('numeric-decimal',lambda v:v['decimal'].update(value=100)),('noncanonical-decimal',lambda v:v['decimal'].update(value='01')),('oversized-decimal',lambda v:v['decimal'].update(value='9223372036854775808')),('decimal-extra',lambda v:v['decimal'].update(units='0')),('timestamp-tag',lambda v:v['stamp'].update(tag='Duration')),('option-extra',lambda v:v['maybe'].update(extra='x')),('option-bad-tag',lambda v:v['maybe'].update(tag='none')),('list-numeric',lambda v:v.update(numbers=[1,2])),('list-negative-zero',lambda v:v.update(numbers=['-0'])),('list-too-long',lambda v:v.update(numbers=['1']*1001)),('enum-unknown',lambda v:v['phase'].update(tag='Forged')),('enum-count',lambda v:v['phase'].update(fields=['only'])),('enum-extra',lambda v:v['phase'].update(extra='x')),('duration-bool',lambda v:v['wait'].update(value=True))]
        for name,mutate in mutations:
            v=copy.deepcopy(base);mutate(v);s,value=client.rpc('echo',v)
            record(name,s==400,{'status':s,'value':value},'HTTP 400 without invocation')
            rpc('echo',[base],base)
        for text in ['\u0000', 'line\nbreak']:
            v=copy.deepcopy(base);v['title']=text;rpc('echo',[v],v)
        for text in ['\ud800', '\udc00', '\ud800a', '\udc00\ud800']:
            v=copy.deepcopy(base);v['title']=text
            body=json.dumps({'contract':f.contract,'arguments':[v]},ensure_ascii=True).encode('ascii')
            rpc('echo',[],None,status=400,body=body)
        for v in [{'tag':'Other','value':'x'},{'tag':'Ticket','value':''},{'tag':'Ticket','value':'x'*129},{'tag':'Ticket','value':'é'},{'tag':'Ticket','value':'abc','extra':1}]:rpc('id_echo',[v],None,status=400)
        for v in [{'tag':'Other','fields':[]},{'tag':'Open','fields':['extra']},{'tag':'Assigned','fields':['x',True]},{'tag':'Assigned','fields':[],'extra':True}]:rpc('phase',[v],None,status=400)
        # Duplicate field names are malformed strict records, independently of their final value.
        body=json.dumps({'contract':f.contract,'arguments':[base]},ensure_ascii=False).replace('"title": "雪😀"','"title": "shadow", "title": "雪😀"')
        rpc('echo',[],None,status=400,body=body.encode('utf-8'))
        escaped_duplicate=body.replace('"title": "shadow"', '"\\u0074itle": "shadow"')
        rpc('echo',[],None,status=400,body=escaped_duplicate.encode('utf-8'))
        for path in ['/','/detail']:
            s,h,b=f.request(path,node='B',direct=True);text=b.decode();record('ssr-'+path,s==200 and 'Independent &lt;script&gt;&amp; evidence' in text and ('Direct route 雪' in text)==(path=='/detail'),{'status':s,'html':text},'escaped deterministic route-aware SSR')
        contract=json.loads((fixture_root/'build/contract.json').read_text());browser_files={k:v for k,v in contract['artifacts'].items() if k.startswith('assets/')};all_public=''
        for path in browser_files:
            s,h,b=f.request('/'+path,node='A',direct=True);record('artifact-'+path,s==200 and hashlib.sha256(b).hexdigest()==contract['artifacts'][path],{'status':s,'content_type':h.get('content-type'),'sha256':hashlib.sha256(b).hexdigest()},'byte-identical hashed asset');all_public+=b.decode()
            if path.endswith('.js.map'):
                m=json.loads(b);record('source-map-private',m['version']==3 and set(m['sources'])=={'shared.dia','ui.dia'} and 'INDEPENDENT_W3_SERVER_CANARY_B7F24' not in json.dumps(m),{'sources':m['sources'],'mappings_present':bool(m['mappings'].replace(';',''))},'only browser/shared source content')
        record('server-canary-public', 'INDEPENDENT_W3_SERVER_CANARY_B7F24' not in all_public,{'public_bytes':len(all_public)},'server canary excluded')
        unused_canaries=['INDEPENDENT_W3_UNUSED_HELPER_CANARY_6B2E','INDEPENDENT_W3_UNUSED_TYPE_CANARY_6B2E','INDEPENDENT_W3_UNUSED_COMPONENT_CANARY_6B2E']
        record('unused-declaration-canaries-public', all(canary not in all_public for canary in unused_canaries),{'present':[canary for canary in unused_canaries if canary in all_public]},'unused private helper/type/component excluded from bundle and source map')
        for path in ['/server.json','/contract.json','/server.dia','/app.css','/badge.svg','/not-declared']:
            s,h,b=f.request(path,node='A',direct=True);record('private-or-unknown-'+path,s==404,{'status':s},'HTTP 404')
        f.stop_node('B');f.start_node('B');rpc('echo',[base],base,node='B')
    except Exception as e:fatal=repr(e)
    finally:
        f.close()
        report={'status':'FAIL' if fatal or any(x['status']=='FAIL' for x in results) else 'PASS','seed':SEED,'binary_sha256':hashlib.sha256(binary.read_bytes()).hexdigest(),'fatal':fatal,'results':results,'temporary_root':str(root)}
        o.report.write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n')
        print(json.dumps({'status':report['status'],'cases':len(results),'fatal':fatal,'failures':[x['name'] for x in results if x['status']=='FAIL'],'report':str(o.report)}))
        if report['status']=='PASS':shutil.rmtree(root)
    return report['status']!='PASS'
if __name__=='__main__':raise SystemExit(main())
