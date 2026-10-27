#!/usr/bin/env python3
"""Run LP3 source and native-render corpus through the actual compiler/library."""
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path
import shutil, subprocess, tempfile

ROOT=Path(__file__).resolve().parents[1]
FIXTURE=ROOT/'examples/typed-inputs'

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--binary',type=Path,default=ROOT/'target/debug/diamond');parser.add_argument('--report',type=Path,default=ROOT/'verification/lp3/compiler.json');parser.add_argument('--language-version',default='0.20.0');parser.add_argument('--format-version',type=int,default=5);parser.add_argument('--wire-version',type=int,default=7);options=parser.parse_args();binary=options.binary.resolve();observations=[]
    def run(*args):return subprocess.run([str(binary),*map(str,args)],cwd=ROOT,capture_output=True,text=True,timeout=30)
    def record(name,passed,**details):observations.append({'name':name,'pass':bool(passed),**details})
    cases=[
      ('route-unbounded-text','shared.dia','page: Int = 1','page: Text = "x"','route requires'),
      ('route-list','shared.dia','page: Int = 1','page: List<Int> = 1','default'),
      ('route-optional-path','shared.dia','IssuePage(id: IssueId,','IssuePage(id: Option<IssueId>,','route requires'),
      ('route-path-default','shared.dia','count: Int, enabled: Bool','count: Int = 1, enabled: Bool','route requires'),
      ('route-unknown-query','shared.dia','?page={page}&search={search}','?unknown={page}&search={search}','route requires'),
      ('route-duplicate-query','shared.dia','?page={page}&search={search}','?page={page}&page={page}&search={search}','route requires'),
      ('route-unused-field','shared.dia','?page={page}&search={search}','?page={page}','route requires'),
      ('route-reserved','shared.dia','"/issues/{id}?','"/_diamond/{id}?','route requires'),
      ('route-overlap','shared.dia','export form EditIssue','export route Ambiguous(id: IssueId) = "/issues/{id}"\nexport form EditIssue','ambiguous'),
      ('route-wrong-id','ui.dia','IssuePage(id: row.id,','IssuePage(id: UserId("alice"),','expected Id<"issue">'),
      ('dynamic-url-text','ui.dia','href={IssuePage(id: detail.id)}','href={"//outside.invalid/"}','typed route'),
      ('form-password','ui.dia','name="title" type="text"','name="title" type="password"','password'),
      ('form-hidden','ui.dia','name="title" type="text"','name="title" type="hidden"','inputs require'),
      ('form-extra','ui.dia','name="title"','name="extra"','undeclared schema'),
      ('form-duplicate','ui.dia','<label for="estimate">','<input name="title" type="text" bind-value={draft}><label for="estimate">','duplicate schema'),
      ('form-wrong-checkbox','ui.dia','name="published"','name="estimate"','duplicate schema'),
      ('form-constraints-owned','ui.dia','name="title" type="text"','name="title" type="text" maxlength="100"','owned by the compiler'),
      ('form-method-owned','ui.dia','<form schema="EditIssue"','<form method="get" schema="EditIssue"','transport is owned'),
      ('form-select-extra-option','ui.dia','<option value="Closed">Closed</option></select>','<option value="Closed">Closed</option><option value="Root">Root</option></select>','exactly match'),
      ('form-restricted-type','shared.dia','export form OptionalFields(count: Option<Int>','export form OptionalFields(count: Option<Secret<"inventory">>','restricted fields'),
      ('form-nested-option','shared.dia','export form OptionalFields(count: Option<Int>','export form OptionalFields(count: Option<Option<Int>>','one-level Option'),
      ('form-record-type','shared.dia','export form OptionalFields(count: Option<Int>','export form OptionalFields(count: Option<Issue>','one-level Option'),
    ]
    with tempfile.TemporaryDirectory(prefix='diamond-lp3-compiler-') as tmp:
        directory=Path(tmp)
        result=run('run',ROOT/'verification/regressions/lp3_optional_bounded_literal.dia')
        record('optional-bounded-literal-reference-regression',result.returncode==0 and result.stdout=='雪😀\n',stdout=result.stdout,stderr=result.stderr)
        for name,filename,before,after,diagnostic in cases:
            project=directory/name;shutil.copytree(FIXTURE,project);path=project/filename;source=path.read_text();assert before in source,name;path.write_text(source.replace(before,after));result=run('check',project/'diamond.toml')
            record(name,result.returncode>0 and diagnostic.lower() in result.stderr.lower() and 'panicked' not in result.stderr,expected=diagnostic,stderr=result.stderr.strip())
        build=directory/'build';result=run('build',FIXTURE/'diamond.toml','--out',build);record('valid-build',result.returncode==0,stderr=result.stderr)
        if result.returncode==0:
            contract=json.loads((build/'contract.json').read_text());artifact=json.loads((build/'server.json').read_text());record('versions',[contract['language_version'],contract['format_version'],contract['wire_version']]==[options.language_version,options.format_version,options.wire_version],observed=[contract['language_version'],contract['format_version'],contract['wire_version']]);record('exact-original-sources',all(p.read_text() in artifact['sources'].values() for p in FIXTURE.glob('*.dia')))
            result=run('check',build);record('rechecked-artifact',result.returncode==0,stderr=result.stderr)
            artifact['format_version']=2;(build/'server.json').write_text(json.dumps(artifact));result=run('check',build);record('old-artifact-rejected',result.returncode>0 and 'incompatible' in result.stderr,stderr=result.stderr)
        # A real external public-library consumer renders a full URL without host
        # grants. This is independent of both HTTP server request guards.
        source=directory/'consumer.rs';source.write_text('fn main() -> Result<(),Box<dyn std::error::Error>> { let args=std::env::args().collect::<Vec<_>>(); let p=diamond::compile_project(std::path::Path::new(&args[1]))?; match p.render_page(&args[2]) { Ok(html)=>print!("{html}"), Err(_)=>std::process::exit(2), } Ok(()) }')
        consumer=directory/'consumer';result=subprocess.run(['rustc','--edition=2024',str(source),'--extern','diamond='+str(ROOT/'target/debug/libdiamond.rlib'),'-L','dependency='+str(ROOT/'target/debug/deps'),'-o',str(consumer)],capture_output=True,text=True,timeout=30);record('native-consumer-build',result.returncode==0,stderr=result.stderr)
        if result.returncode==0:
            for target,accepted,marker in [('/issues/alpha-a',True,'id="page">1</p>'),('/issues/alpha-a?page=2&search=%E9%9B%AA%F0%9F%98%80',True,'id="search">雪😀</p>'),('/issues/alpha-a?page=1',False,''),('/issues/alpha-a?search=%2f',False,''),('/issues/alpha-a?page=2#fragment',False,''),('/values/-9223372036854775808/false/%E9%9B%AA/Closed/alpha',True,'id="scalar-count">-9223372036854775808</p>')]:
                result=subprocess.run([str(consumer),str(FIXTURE/'diamond.toml'),target],capture_output=True,text=True,timeout=10);record('native-render-'+target,(result.returncode==0)==accepted and (not marker or marker in result.stdout),exit=result.returncode,expected_accept=accepted)
    status='PASS' if all(o['pass'] for o in observations) else 'FAIL';options.report.parent.mkdir(parents=True,exist_ok=True);options.report.write_text(json.dumps({'status':status,'binary_sha256':hashlib.sha256(binary.read_bytes()).hexdigest(),'driver_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'observations':observations},indent=2)+'\n');print(json.dumps({'status':status,'observations':len(observations),'failures':[o for o in observations if not o['pass']]}));return int(status!='PASS')

if __name__=='__main__':raise SystemExit(main())
