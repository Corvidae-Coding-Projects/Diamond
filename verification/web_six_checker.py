#!/usr/bin/env python3
"""Invoke the real CLI against requirement-derived W6 authority rejection cases."""
import argparse, hashlib, json, shutil, subprocess, tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def main():
    p=argparse.ArgumentParser();p.add_argument('--binary',type=Path,default=ROOT/'target/debug/diamond');p.add_argument('--report',type=Path,default=ROOT/'verification/w6_checker_results.json');a=p.parse_args()
    original=ROOT/'examples/files-workspace'
    manifest=(original/'diamond.toml').read_text();server=(original/'server.dia').read_text();browser=(original/'tracker.dia').read_text()
    get='host.http_get<Text>("inventory", path, credential)'
    def extra(body,returns='Result<Text, DataError>'):
        return server+'\nexport fn bad(path: Text) -> '+returns+':\n'+body+'\n'
    cases=[
      ('baseline',manifest,server,browser,True),
      ('missing-app-and-module-budget',manifest.replace('"Http<inventory>", ',''),server,browser,False),
      ('dynamic-destination',manifest,server.replace(get,'host.http_get<Text>(path, path, credential)'),browser,False),
      ('wrong-destination-secret',manifest.replace('"Http<inventory>"','"Http<other>", "Http<inventory>"'),server.replace(get,'host.http_get<Text>("other", path, credential)'),browser,False),
      ('secret-comparison',manifest,extra('  let s = host.secret("inventory")\n  s == s','Bool'),browser,False),
      ('secret-display',manifest,extra('  let s = host.secret("inventory")\n  Ok(int_text(s))'),browser,False),
      ('secret-in-wire-request',manifest,server.replace('host.http_post<Text, Text>("inventory", path, credential, body)','host.http_post<Secret<"inventory">, Text>("inventory", path, credential, credential)'),browser,False),
      ('secret-as-reply',manifest,server.replace(get,'host.http_get<Secret<"inventory">>("inventory", path, credential)'),browser,False),
      ('secret-entry-escape',manifest,server.replace('export fn main() -> Text:\n  heading()','export fn main() -> Secret<"inventory">:\n  host.secret("inventory")'),browser,False),
      ('secret-rpc-escape',manifest.replace('[rpc]\n','[rpc]\nbad = "server.bad"\n'),extra('  host.secret("inventory")','Secret<"inventory">'),browser,False),
      ('secret-nested-rpc-escape',manifest.replace('[rpc]\n','[rpc]\nbad = "server.bad"\n'),extra('  Some(host.secret("inventory"))','Option<Secret<"inventory">>'),browser,False),
      ('application-write-with-http',manifest,server.replace('  let credential = host.secret("inventory")','  let workspace = create_workspace("forbidden")?\n  let credential = host.secret("inventory")'),browser,False),
      ('unknown-file-binding',manifest,server,browser.replace('files.upload("issue_files"','files.upload("missing"'),False),
      ('file-input-cannot-bind-text',manifest,server,browser.replace('id="issue_attachment" type="file"','id="issue_attachment" type="file" bind-value={selected}'),False),
      ('file-input-requires-grant',manifest[:manifest.index('\n[files]')],server,browser,False),
      ('bad-file-resource',manifest.replace('resource = "Issues"\nmax_bytes','resource = "Missing"\nmax_bytes'),server,browser,False),
      ('oversized-file-binding',manifest.replace('max_bytes = 8388608','max_bytes = 8388609'),server,browser,False),
      ('browser-host-authority',manifest,server,browser.replace('attachment_status = rpc.external_get(external_path)','attachment_status = host.secret("inventory")'),False),
      ('public-api-host-call',manifest+'\n[http_api.operations.external_get]\nfunction="server.external_get"\nauth="public"\n',server,browser,False),
    ]
    results=[]
    with tempfile.TemporaryDirectory(prefix='diamond-w6-checker-') as tmp:
        tmp=Path(tmp);binary=tmp/'diamond';shutil.copy2(a.binary,binary)
        for i,(name,m,s,b,accept) in enumerate(cases):
            case=tmp/str(i);shutil.copytree(original,case);(case/'diamond.toml').write_text(m);(case/'server.dia').write_text(s);(case/'tracker.dia').write_text(b)
            r=subprocess.run([binary,'check',case/'diamond.toml'],capture_output=True,text=True,timeout=10)
            results.append({'case':name,'expected_accept':accept,'exit':r.returncode,'pass':(r.returncode==0)==accept,'diagnostic':r.stderr.strip()})
        report={'binary_sha256':hashlib.sha256(binary.read_bytes()).hexdigest(),'observations':results,'pass':all(x['pass'] for x in results)}
    a.report.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({'pass':report['pass'],'observations':len(results),'failed':[x for x in results if not x['pass']]}));return 0 if report['pass'] else 1
if __name__=='__main__':raise SystemExit(main())
