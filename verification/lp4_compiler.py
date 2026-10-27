#!/usr/bin/env python3
"""Replay LP4 source/SSR contracts through the actual CLI and public Rust API."""
from __future__ import annotations
import argparse, hashlib, json, re, shutil, subprocess, tempfile
from html.parser import HTMLParser
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'examples/components'
class Markup(HTMLParser):
    def __init__(self):super().__init__();self.ids=[];self.links=[];self.controls=[]
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if 'id' in attrs:self.ids.append(attrs['id'])
        if 'for' in attrs:self.links.append(attrs['for'])
        self.links+=attrs.get('aria-describedby','').split()
        if 'data-diamond-managed' in attrs:self.controls.append(attrs)
def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--binary',type=Path,default=ROOT/'target/debug/diamond');parser.add_argument('--report',type=Path,default=ROOT/'verification/lp4/compiler.json');args=parser.parse_args();observations=[];binary=args.binary.resolve()
    def run(*values):return subprocess.run([str(binary),*map(str,values)],cwd=ROOT,capture_output=True,text=True,timeout=40)
    def record(name,passed,**detail):observations.append({'name':name,'pass':bool(passed),**detail})
    cases=[
      ('missing-label','ui.dia',' label="Title"','','label'),
      ('blank-label','ui.dia','label="Title"','label=" "','nonempty'),
      ('label-type','ui.dia','label="Title"','label={1}','Text'),
      ('authored-id','ui.dia','name="title" label=','name="title" id="manual" label=','owned by the compiler'),
      ('field-unknown','ui.dia','name="title"','name="absent"','undeclared schema'),
      ('field-duplicate','ui.dia','name="note"','name="title"','duplicate schema'),
      ('field-checked-type','ui.dia','bind-checked={enabled}','bind-checked={title}','binding type'),
      ('field-readonly','ui.dia','bind-value={title}','bind-value={name}','binding type'),
      ('field-no-binding','ui.dia',' bind-value={title}','','exactly one'),
      ('field-two-bindings','ui.dia','bind-value={title}','bind-value={title} bind-checked={enabled}','exactly one'),
      ('multiline-int','ui.dia','name="count" label="Count"','name="count" label="Count" multiline="true"','scalar schema'),
      ('field-outside','ui.dia','      <h2>{name}</h2>','      <h2>{name}</h2><field name="title" label="Title" bind-value={title}></field>','own schema'),
      ('submit-outside','ui.dia','      <h2>{name}</h2>','      <h2>{name}</h2><submit label="Save"></submit>','own schema'),
      ('submit-no-name','ui.dia','label="Save details"','','label'),
      ('image-description','ui.dia','      <h2>{name}</h2>','      <h2>{name}</h2><img src="logo.svg">','alt'),
      ('handler-nominal','ui.dia','on save(value: Details):','on save(value: IssueId):','field'),
      ('output-unbound','ui.dia',' on-saved={saved}','','outputs'),
      ('output-restricted','ui.dia','output saved(value: Details)','output saved(value: Option<Blob<"private">>)','managed'),
      ('recursive-components','ui.dia','<h2>{name}</h2>','<h2>{name}</h2><use component="Fields" name={name} on-saved={save}></use>','cycle'),
    ]
    render_cases=[
      ('dynamic-empty-name','label="Title"','label={""}','accessible name'),
      ('long-name','label="Title"','label="'+'x'*257+'"','accessible name'),
      ('status-budget','state message: Text = "Ready"','state message: Text = "'+'x'*513+'"','status text'),
      ('reserved-id','<h2>{name}</h2>','<h2 id="diamond-control-manual">{name}</h2>','reserved'),
      ('duplicate-identity','["one", "雪😀!\'"]','["one", "one"]','duplicate key'),
    ]
    with tempfile.TemporaryDirectory(prefix='diamond-lp4-compiler-') as tmp:
        root=Path(tmp)
        for name,filename,before,after,expected in cases:
            case=root/name;shutil.copytree(BASE,case);path=case/filename;source=path.read_text();assert before in source,name;path.write_text(source.replace(before,after));result=run('check',case/'diamond.toml');record(name,result.returncode!=0 and expected.lower() in result.stderr.lower() and 'panicked' not in result.stderr,expected=expected,stderr=result.stderr.strip())
        for name,before,after,expected in render_cases:
            case=root/name;shutil.copytree(BASE,case);path=case/'ui.dia';source=path.read_text();assert before in source,name;path.write_text(source.replace(before,after));result=run('build',case/'diamond.toml','--out',root/(name+'-out'));record(name,result.returncode!=0 and expected in result.stderr,expected=expected,stderr=result.stderr.strip())
        # Large expansion was found by an independent verifier: count native nodes,
        # not just the single source <field> that expands to many native options.
        case=root/'large-choice';shutil.copytree(BASE,case);(case/'shared.dia').write_text('export enum Many:\n'+''.join(f'  Item{i:02d}\n' for i in range(64))+'export form Data(choice: Many)\n');(case/'ui.dia').write_text('export component Child:\n  state choice: Text = "Item00"\n  on save(value: Data):\n    choice = choice\n  html:\n    <form schema="Data" as="value" on-submit={save(value)}><field name="choice" label="Choice" bind-value={choice}></field><submit label="Save"></submit></form>\nexport component App:\n  state rows: List<Int> = ['+','.join(map(str,range(100)))+']\n  html:\n    <each items={rows} as="row" key={row}><use component="Child"></use></each>\n');result=run('build',case/'diamond.toml','--out',root/'large-out');record('expanded-native-node-budget',result.returncode!=0 and 'render node capacity' in result.stderr,stderr=result.stderr.strip())
        # Authentication must have one root-owned, direct transition before remote
        # work so no private local value survives the identity barrier.
        auth=ROOT/'verification/lp4/project'
        for name,before,after in [('child-identity','  on report:\n','  on bad:\n    draft = session.logout()\n  on report:\n'),('identity-after-remote','  on login:\n','  on login:\n    layout = rpc.layout(false, false, false)\n'),('nested-identity','session.login(username, password)','text_trim(session.login(username, password))')]:
            case=root/name;shutil.copytree(auth,case);path=case/'ui.dia';path.write_text(path.read_text().replace(before,after));result=run('check',case/'diamond.toml');record(name,result.returncode!=0 and 'direct root-component' in result.stderr,stderr=result.stderr.strip())
        out=root/'valid';result=run('build',BASE/'diamond.toml','--out',out);record('valid-controls-build',result.returncode==0,stderr=result.stderr)
        if result.returncode==0:
            contract=json.loads((out/'contract.json').read_text());record('versions',[contract['language_version'],contract['format_version'],contract['wire_version']]==['0.20.0',5,7],observed=[contract['language_version'],contract['format_version'],contract['wire_version']]);markup=Markup();markup.feed((out/'index.html').read_text());record('ssr-unique-associated-controls',len(markup.controls)==14 and len(markup.ids)==len(set(markup.ids)) and all(link in markup.ids for link in markup.links),controls=len(markup.controls),control_ids=[c['id'] for c in markup.controls]);result=run('check',out);record('artifact-recheck',result.returncode==0,stderr=result.stderr)
        # Exercise the exported native renderer, not only the CLI HTTP adapter.
        consumer=root/'consumer.rs';consumer.write_text('fn main()->Result<(),Box<dyn std::error::Error>>{let args=std::env::args().collect::<Vec<_>>();let p=diamond::compile_project(std::path::Path::new(&args[1]))?;print!("{}",p.render_page("/")?);Ok(())}')
        built=subprocess.run(['rustc','--edition=2024',str(consumer),'--extern','diamond='+str(ROOT/'target/debug/libdiamond.rlib'),'-L','dependency='+str(ROOT/'target/debug/deps'),'-o',str(root/'consumer')],capture_output=True,text=True,timeout=30);record('public-native-consumer-build',built.returncode==0,stderr=built.stderr)
        if built.returncode==0:
            native=subprocess.run([str(root/'consumer'),str(BASE/'diamond.toml')],capture_output=True,text=True,timeout=10);exported=(out/'index.html').read_text();fragment=re.search(r'<div id="diamond-app" data-contract="[0-9a-f]+">(.*)</div><aside id="diamond-recovery"',exported,re.S).group(1);record('public-native-render-parity',native.returncode==0 and native.stdout==fragment,stderr=native.stderr,native_length=len(native.stdout),fragment_length=len(fragment))
    status='PASS' if all(x['pass'] for x in observations) else 'FAIL';args.report.parent.mkdir(parents=True,exist_ok=True);args.report.write_text(json.dumps({'status':status,'binary_sha256':hashlib.sha256(binary.read_bytes()).hexdigest(),'driver_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'observations':observations},indent=2)+'\n');print(json.dumps({'status':status,'cases':len(observations),'failures':[x for x in observations if not x['pass']]}));return int(status!='PASS')
if __name__=='__main__':raise SystemExit(main())
