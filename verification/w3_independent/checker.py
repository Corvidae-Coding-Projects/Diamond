#!/usr/bin/env python3
"""Independent W3 compile-time type and module-boundary controls."""
import argparse,hashlib,json,shutil,subprocess,tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]

HEADER='''enum AppError:
  Rejected(reason: Text)

enum OtherError:
  Different

fn data_failure() -> Result<Int, DataError>:
  Err("safe data failure")

'''

def main():
    p=argparse.ArgumentParser();p.add_argument('--binary',type=Path,default=ROOT/'target/debug/diamond');p.add_argument('--report',type=Path,required=True);o=p.parse_args()
    root=Path(tempfile.mkdtemp(prefix='diamond-w3-independent-checker-'));binary=root/'diamond';shutil.copy2(o.binary,binary);results=[]
    cases=[
     ('direct-data-result-bridge',HEADER+'''fn wrong() -> Result<Int, AppError>:
  data_failure()

fn main() -> Int:
  17
''',False),
     ('argument-data-result-bridge',HEADER+'''fn take(value: Result<Int, AppError>) -> Int:
  19

fn main() -> Int:
  take(data_failure())
''',False),
     ('option-data-result-bridge',HEADER+'''fn take(value: Option<Result<Int, AppError>>) -> Int:
  19

fn main() -> Int:
  take(Some(data_failure()))
''',False),
     ('list-data-result-bridge',HEADER+'''fn take(value: List<Result<Int, AppError>>) -> Int:
  19

fn main() -> Int:
  take([data_failure()])
''',False),
     ('try-data-error-bridge',HEADER+'''fn wrong() -> Result<Int, AppError>:
  let value = data_failure()?
  Ok(value)

fn main() -> Int:
  17
''',False),
     ('wrong-enum-error',HEADER+'''fn wrong() -> Result<Int, AppError>:
  Err(Different)

fn main() -> Int:
  17
''',False),
     ('valid-pure-ok',HEADER+'''fn good() -> Result<Int, AppError>:
  Ok(17)

fn main() -> Int:
  match good():
    Ok(value) => value
    Err(error) => 0
''',True),
     ('valid-nested-pure-ok',HEADER+'''fn take(value: Option<Result<Int, AppError>>) -> Int:
  19

fn main() -> Int:
  take(Some(Ok(17)))
''',True),
     ('bad-rounding-type','fn main() -> Decimal:\n  decimal_multiply(decimal_units(1), decimal_units(2), 3)\n',False),
     ('time-unit-mismatch','fn main() -> Timestamp:\n  timestamp_add(timestamp(1), timestamp(2))\n',False),
     ('scalar-slice-type-mismatch','fn main() -> Text:\n  text_slice("snow 雪", "1", 1)\n',False),
    ]
    for name,source,success in cases:
        f=root/(name+'.dia');f.write_text(source);r=subprocess.run([str(binary),'check',str(f)],capture_output=True,text=True,timeout=20)
        results.append({'name':name,'status':'PASS' if (r.returncode==0)==success else 'FAIL','expected_compile_success':success,'exit':r.returncode,'stdout':r.stdout,'stderr':r.stderr,'source':source})
    manifest='''schema_version=1
name="IndependentPrivateTypes"
server="server"
browser="ui"
component="App"
[modules.shared]
path="shared.dia"
target="shared"
[modules.server]
path="server.dia"
target="server"
imports=["shared"]
[modules.ui]
path="ui.dia"
target="browser"
imports=["shared"]
'''
    base='export component App:\n  state label: Text = "ok"\n  html:\n    <main><p>{label}</p></main>\n'
    projects=[
      ('public-private-record-leak','type Hidden:\n  value: Text\n\nexport fn factory() -> Hidden:\n  Hidden(value: "x")\n',base,False),
      ('public-private-enum-leak','enum Hidden:\n  Secret(value: Text)\n\nexport fn factory() -> Hidden:\n  Secret("x")\n',base,False),
      ('raw-generated-private-type','type Hidden:\n  value: Text\n', 'fn bad(value: __diamond_type_6_shared_Hidden) -> Text:\n  value.value\n\n'+base,False),
      ('unexported-type-name','type Hidden:\n  value: Text\n', 'fn bad(value: Hidden) -> Text:\n  value.value\n\n'+base,False),
      ('exported-shared-enum','export enum Visible:\n  First\n  Second(value: Text)\n', 'fn label() -> Text:\n  match Second("ok"):\n    First => "first"\n    Second(value) => value\n\nexport component App:\n  state label: Text = label()\n  html:\n    <main><p>{label}</p></main>\n',True),
      ('record-default-constructor','export type Thing:\n  value: Text = "default"\n', 'export component App:\n  state item: Thing = Thing()\n  html:\n    <main><p>{item.value}</p></main>\n',True),
    ]
    for name,shared,ui,success in projects:
        d=root/name;d.mkdir();(d/'diamond.toml').write_text(manifest);(d/'shared.dia').write_text(shared);(d/'server.dia').write_text('export fn main() -> Int:\n  17\n');(d/'ui.dia').write_text(ui)
        r=subprocess.run([str(binary),'check',str(d/'diamond.toml')],capture_output=True,text=True,timeout=20)
        results.append({'name':name,'status':'PASS' if (r.returncode==0)==success else 'FAIL','expected_compile_success':success,'exit':r.returncode,'stdout':r.stdout,'stderr':r.stderr,'fixture':{'manifest':manifest,'shared':shared,'ui':ui}})
    rpc_manifest = manifest + '\n[rpc]\nadd="server.add"\ndouble="server.double"\necho_text="server.echo_text"\nfail_int="server.fail_int"\nfail_bool="server.fail_bool"\n'
    rpc_server = '''export fn main() -> Int:
  17

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
'''
    rpc_shared = '''export fn decorate(value: Text) -> Text:
  "wrapped <" + value + ">"

export fn increment(value: Int) -> Int:
  value + 1
'''
    prefix = '''export component App:
  state number: Int = 0
  state label: Text = "prior"
  state ready: Bool = false
  state values: List<Int> = []
'''
    tail = '''  html:
    <main><p>{number}</p><p>{label}</p><p>{ready}</p><button type="button" on-click={run}>Run</button></main>
'''
    event_cases = [
        ('nested-pure-helper-rpc', '  on run:\n    label = decorate(rpc.echo_text("snow 雪"))\n', True),
        ('nested-remote-arguments', '  on run:\n    number = rpc.add(rpc.double(6), rpc.add(2, 9))\n', True),
        ('nested-rpc-arithmetic', '  on run:\n    number = increment(rpc.double(10)) + rpc.add(3, 4)\n', True),
        ('lazy-remote-bool', '  on run:\n    ready = false and rpc.fail_bool()\n', True),
        ('lazy-remote-match', '  on run:\n    number = match true:\n      true => rpc.add(17, 9)\n      false => rpc.fail_int()\n', True),
        ('nested-remote-for', '  on run:\n    values = for value in [3, 8, 11]:\n      rpc.double(value)\n', True),
        ('nested-remote-list', '  on run:\n    values = [rpc.add(2, 3), rpc.double(7)]\n', True),
        ('invalid-nested-signature', '  on run:\n    number = rpc.add(1, rpc.echo_text("wrong type"))\n', False),
        ('invalid-nested-pure-signature', '  on run:\n    label = decorate(rpc.double(1))\n', False),
        ('unmount-nested-remote', '  on run:\n    number = 1\n  on unmount:\n    number = increment(rpc.double(1))\n', False),
        ('unmount-lazy-remote', '  on run:\n    number = 1\n  on unmount:\n    ready = false and rpc.fail_bool()\n', False),
    ]
    for name, event, success in event_cases:
        d=root/name;d.mkdir();ui=prefix+event+tail
        (d/'diamond.toml').write_text(rpc_manifest);(d/'shared.dia').write_text(rpc_shared);(d/'server.dia').write_text(rpc_server);(d/'ui.dia').write_text(ui)
        run=subprocess.run([str(binary),'check',str(d/'diamond.toml')],capture_output=True,text=True,timeout=20)
        built=None
        if run.returncode==0 and success:
            b=subprocess.run([str(binary),'build',str(d/'diamond.toml'),'--out',str(d/'build')],capture_output=True,text=True,timeout=20)
            built={'exit':b.returncode,'stdout':b.stdout,'stderr':b.stderr}
        okay=(run.returncode==0)==success and (built is None or built['exit']==0)
        results.append({'name':name,'status':'PASS' if okay else 'FAIL','expected_compile_success':success,'exit':run.returncode,'stdout':run.stdout,'stderr':run.stderr,'build':built,'fixture':{'manifest':rpc_manifest,'shared':rpc_shared,'server':rpc_server,'ui':ui}})
    for name,fixture in [('legal-key-paths','key_paths'),('composed-event-project','event_composition')]:
        directory=root/name
        shutil.copytree(ROOT/'verification/w3_independent'/fixture,directory)
        run=subprocess.run([str(binary),'check',str(directory/'diamond.toml')],capture_output=True,text=True,timeout=20)
        built=subprocess.run([str(binary),'build',str(directory/'diamond.toml'),'--out',str(directory/'build')],capture_output=True,text=True,timeout=20)
        results.append({'name':name,'status':'PASS' if run.returncode==0 and built.returncode==0 else 'FAIL',
                        'expected_compile_success':True,'exit':run.returncode,'stdout':run.stdout,'stderr':run.stderr,
                        'build':{'exit':built.returncode,'stdout':built.stdout,'stderr':built.stderr},'fixture':fixture})
    report={'status':'PASS' if all(x['status']=='PASS' for x in results) else 'FAIL','binary_sha256':hashlib.sha256(binary.read_bytes()).hexdigest(),'results':results,'temporary_root':str(root)}
    o.report.write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n');print(json.dumps({'status':report['status'],'cases':len(results),'failures':[x['name'] for x in results if x['status']=='FAIL'],'report':str(o.report)}))
    if report['status']=='PASS':shutil.rmtree(root)
    return report['status']!='PASS'
if __name__=='__main__':raise SystemExit(main())
