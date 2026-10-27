#!/usr/bin/env python3
"""LP3 real CLI, two HTTP servers and PostgreSQL input/navigation campaign."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import random
import shutil
import sys
import tempfile
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'verification'))
from lp2_spine import Gate, restrict, literal, nominal
from w5_independent.service import Client, Service, ENV_NAME, free_port, sha256

APP = hashlib.sha256(b'TypedInputs').hexdigest()
PASSWORD = 'LP3 synthetic fixture password 71!'
SEED = 0xD1A30003

def table(name):
    return 'diamond_w4."r_' + APP[:16] + '_' + hashlib.sha256(name.encode()).hexdigest()[:16] + '"'

def rows(service):
    return service.sql_json(f"SELECT id,workspace,convert_from(decode(title,'hex'),'UTF8') AS title,revision,estimate,published,parent,status,convert_from(decode(note,'hex'),'UTF8') AS note FROM {table('Issues')} ORDER BY id")

def seed(service):
    service.sql(f'DELETE FROM {table("Issues")}; DELETE FROM {table("Members")};')
    service.sql(f'INSERT INTO {table("Members")}(id,workspace,"user",role) VALUES '+"('member-alice','alpha','alice','Editor'),('member-bob','beta','bob','Editor'),('member-reader','alpha','reader','Reader')")
    for key,workspace,title in [('alpha-a','alpha','Old A'),('alpha-b','alpha','Old B'),('beta-c','beta','Private C')]:
        service.sql(f'INSERT INTO {table("Issues")}(id,workspace,title,revision,estimate,published,parent,status,note) VALUES ({literal(key)},{literal(workspace)},{literal(title.encode().hex())},0,0,false,{literal(workspace)},\'Open\',NULL)')

def form(title='雪😀',estimate='17',published=True,parent='alpha',status='Closed',note='ordinary draft'):
    return {'title':title,'estimate':estimate,'published':published,'parent':nominal('workspace',parent),'status':{'tag':status,'fields':[]},'note':{'tag':'None'} if note is None else {'tag':'Some','value':note}}

def action(client,value,key='alpha-b',node='A',headers=None):
    body={'contract':client.contract,'arguments':[nominal('issue',key),value]}
    status,prepared=client.request(node,'/_diamond/actions/rename/prepare',body,headers)
    if status != 200:return status,prepared
    return client.request(node,'/_diamond/actions/rename',{**body,'ticket':prepared['ticket']},headers)

def backend(service,contract,record):
    clients={}
    for user in ['alice','bob','reader']:
        client=Client(service,contract);client.login(user,PASSWORD);clients[user]=client
    alice=clients['alice']; expected=rows(service)
    wanted=form(); outcome=action(alice,wanted,node='B')
    expected[1].update(title='雪😀',estimate=17,published=True,parent='alpha',status='Closed',note='ordinary draft',revision=1)
    record('all-scalars-and-optional-persist', outcome[0]==200 and outcome[1]['value']['tag']=='ActionSucceeded' and rows(service)==expected, [outcome,rows(service)],expected)
    fresh=Client(service,contract);fresh.login('alice',PASSWORD,node='B');outcome=fresh.rpc('load',nominal('issue','alpha-b'),node='B')
    record('fresh-client-typed-values',outcome[0]==200 and all(outcome[1]['value'][key]==value for key,value in wanted.items()),outcome,wanted)
    for who in ['bob','reader']:
        outcome=action(clients[who],form(title='No access'))
        record(who+'-cannot-write',outcome[0]==200 and outcome[1]['value']['tag']=='ActionDenied' and rows(service)==expected,outcome,'ActionDenied, no write')
    outcome=action(alice,form(parent='beta'))
    record('valid-id-does-not-grant-parent-authority',outcome[0]==200 and outcome[1]['value']['tag']=='ActionDenied' and rows(service)==expected,outcome,'ActionDenied, no write')
    for name,value in [('minus',form(estimate='-')),('overflow',form(estimate='9223372036854775808')),('leading-zero',form(estimate='017')),('plus',form(estimate='+17')),('negative-zero',form(estimate='-0')),('empty-title',form(title='')),('multibyte-overlong',form(title='雪'*121)),('wrong-id-tag',{**form(),'parent':nominal('user','alice')}),('enum-payload',{**form(),'status':{'tag':'Closed','fields':['forged']}}),('enum-unknown',{**form(),'status':{'tag':'Root','fields':[]}}),('extra',{**form(),'extra':'forged'}),('created-by',{**form(),'created_by':nominal('user','alice')}),('wrong-bool',{**form(),'published':'true'}),('bad-option',{**form(),'note':{'tag':'Some','value':42}})]:
        outcome=action(alice,value)
        record('raw-'+name,outcome[0]==400 and rows(service)==expected,outcome,'strict schema rejection before action; no write')
    body={'contract':contract,'arguments':[nominal('issue','alpha-b'),form()]}
    raw=json.dumps(body).replace('"estimate": "17"','"estimate":"17","estimate":"18"').encode()
    for name,data,headers in [('duplicate-json-field',raw,{}),('invalid-utf8',b'\xff',{}),('unpaired-surrogate',json.dumps(body).replace('ordinary draft','\\ud800').encode(),{}),('invalid-csrf',json.dumps(body).encode(),{'X-Diamond-CSRF':'forged'})]:
        outcome=alice.request('A','/_diamond/actions/rename/prepare',data,headers)
        record(name,outcome[0] in [400,403] and rows(service)==expected,[outcome[0],rows(service)],'reject before mutation')
    rng=random.Random(SEED)
    for index in range(8):
        number=rng.choice([-9223372036854775808,9223372036854775807,-17,0,17])
        title='Case '+str(index)+' 雪😀'; note=None if index%2 else 'note <b>&'
        wanted=form(title=title,estimate=str(number),published=bool(index%2),status='Open' if index%2 else 'Closed',note=note)
        outcome=action(alice,wanted,node='A' if index%2 else 'B')
        expected[1].update(title=title,estimate=number,published=bool(index%2),status=wanted['status']['tag'],note=note,revision=expected[1]['revision']+1)
        record('generated-valid-'+str(index),outcome[0]==200 and outcome[1]['value']['tag']=='ActionSucceeded' and rows(service)==expected,[outcome[0],rows(service)],expected)
    route_campaign(service,record)

def route_campaign(service,record):
    valid=[('/issues/alpha-a','1',''),('/issues/alpha-a?page=2','2',''),('/issues/alpha-a?page=2&search='+quote('雪😀',safe=''),'2','雪😀'),('/issues/alpha-b?page=-9223372036854775808','-9223372036854775808','')]
    for path,page,search in valid:
        status,_,raw=service.request('A',path,headers={'Sec-Fetch-Site':'cross-site','Sec-Fetch-Mode':'navigate','Sec-Fetch-Dest':'document'})
        html=raw.decode()
        record('url-valid-'+path,status==200 and f'id="page">{page}</p>' in html and f'id="search">{search}</p>' in html,status,'200 correct pure SSR typed fields')
    path='/values/-17/true/'+quote('雪 &<',safe='')+'/Closed/alpha?extra=17&flag=false&mode=Open&owner=alpha'
    status,_,raw=service.request('B',path)
    html=raw.decode()
    expected={'count':'-17','enabled':'true','label':'雪 &amp;&lt;','status':'Closed','scope':'alpha','extra':'17','flag':'false','owner':'alpha','mode':'Open'}
    record('all-route-scalars-and-optionals',status==200 and all(f'id="scalar-{key}">{value}</p>' in html for key,value in expected.items()),status,expected)
    invalid=['/issues/alpha-a?page=1','/issues/alpha-a?','/issues/alpha-a?unknown=x','/issues/alpha-a?page=2&page=3','/issues/alpha-a?%70age=2','/issues/alpha-a?page=02','/issues/alpha-a?page=-0','/issues/alpha-a?page=+2','/issues/alpha-a?page=9223372036854775808','/issues/alpha-a?search=','/issues/alpha-a?search=%','/issues/alpha-a?search=%FF','/issues/alpha-a?search=%ED%A0%80','/issues/alpha-a?search=%252F','/issues/alpha-a?search=%2F','/issues/alpha-a?search=%5C','/issues/alpha-a?search=%00','/issues/alpha-a?search=%0A','/issues/alpha-a?search=%3F','/issues/alpha-a?search=%23','/issues/alpha-a?search='+quote('雪'*81,safe=''),'/issues/alpha-a?search=a+b','/issues/alpha-a?search=x&page=2','/issues/a%2Fb','/issues/%61lpha-a','//issues/alpha-a','/issues/alpha-a/','/issues/']
    for path in invalid:
        status,_,_=service.request('B',path)
        record('url-invalid-'+path,status in [400,404],status,'bounded rejection, no app mutation')

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--binary',type=Path,default=ROOT/'target/debug/diamond');parser.add_argument('--report',type=Path,default=ROOT/'verification/lp3/results.json');parser.add_argument('--serve',action='store_true');parser.add_argument('--language-version',default='0.20.0');parser.add_argument('--format-version',type=int,default=5);parser.add_argument('--wire-version',type=int,default=7);options=parser.parse_args()
    driver_hash=sha256(Path(__file__));root=Path(tempfile.mkdtemp(prefix='diamond-lp3-'));binary=root/'diamond';shutil.copy2(options.binary,binary)
    project=ROOT/'examples/typed-inputs/diamond.toml';service=Service(root,binary,project);service.extra_serve=('--identity-mailbox',str(service.mailbox));port=39171 if options.serve else free_port();service.origin=f'http://127.0.0.1:{port}';service.authority=service.origin.removeprefix('http://');observations=[];gate=None;error=None
    def record(name,passed,observed,expected):
        observations.append({'name':name,'pass':bool(passed),'observed':copy.deepcopy(observed),'expected':copy.deepcopy(expected)})
        if not passed:raise AssertionError((name,observed,expected))
    try:
        service.postgres();metadata,build=service.build();contract=metadata['rpc_id'];record('versions',[metadata['language_version'],metadata['format_version'],metadata['wire_version']]==[options.language_version,options.format_version,options.wire_version],[metadata['language_version'],metadata['format_version'],metadata['wire_version']],[options.language_version,options.format_version,options.wire_version])
        service.cli('project-init',project,'--postgres-env',ENV_NAME)
        for user in ['alice','bob','reader']:service.cli('project-user-add',project,'--postgres-env',ENV_NAME,'--user',user,'--password-stdin',input=PASSWORD+'\n')
        seed(service);restrict(service);service.start_node('A');service.start_node('B');gate=Gate(port,service.ports['A']);backend(service,contract,record)
        if options.serve:
            seed(service)
            print(json.dumps({'ready':True,'root':str(root),'origin':service.origin,'contract':contract,'binary_sha256':sha256(binary),'fixture_password':PASSWORD}),flush=True)
            for line in sys.stdin:
                command=line.strip()
                if command=='stop':break
                if command=='observe':print(json.dumps({'rows':rows(service),'forwarded_actions':gate.actions_seen},ensure_ascii=False),flush=True)
                if command=='arm':gate.arm();print('{"armed":true}',flush=True)
                if command=='release':gate.release.set();print('{"released":true}',flush=True)
        print(json.dumps({'status':'PASS','observations':len(observations)}),flush=True)
    except Exception as failure:error=repr(failure);print(json.dumps({'status':'FAIL','error':error}),flush=True)
    finally:
        if gate:gate.close()
        service.close();report={'status':'PASS' if error is None and not service.cleanup_errors else 'FAIL','error':error,'binary_sha256':sha256(binary),'driver_sha256':driver_hash,'seed':SEED,'observations':observations,'shutdowns':service.shutdowns,'cleanup_errors':service.cleanup_errors,'source_files':{str(p.relative_to(ROOT)):sha256(p) for p in sorted(project.parent.iterdir()) if p.is_file()}}
        options.report.parent.mkdir(parents=True,exist_ok=True);options.report.write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n');shutil.rmtree(root)
    return int(error is not None or bool(service.cleanup_errors))

if __name__=='__main__':raise SystemExit(main())
