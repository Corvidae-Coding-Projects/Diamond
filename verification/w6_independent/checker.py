#!/usr/bin/env python3
"""Requirement-derived generated compiler boundary probes through the actual CLI."""
import argparse,hashlib,json,shutil,subprocess,tempfile
from pathlib import Path
from fixture import write


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary',type=Path,required=True);parser.add_argument('--report',type=Path,required=True)
    args=parser.parse_args();rows=[]
    with tempfile.TemporaryDirectory(prefix='diamond-w6-independent-checker-') as temporary:
        root=Path(temporary);binary=root/'diamond';shutil.copy2(args.binary,binary)
        project=write(root/'base');manifest=project.read_text();source=(project.parent/'server.dia').read_text();browser=(project.parent/'ui.dia').read_text()
        def fn(body,returns='Result<Text, DataError>'):
            return source+'\nexport fn probe(id: IssueId, path: Text) -> '+returns+':\n'+body+'\n'
        def bound(m):return m.replace('[rpc]','[rpc]\nprobe = "server.probe"')
        get='host.http_get<Text>("inventory", path, credential)'
        cases=[('ordinary-interface-control',manifest,source,browser,True),
          ('transaction-syntax-control',manifest,fn('  transaction db:\n    let row = issues.get(id)?\n  Ok("checked")'),browser,True),
          ('readonly-snapshot-control',manifest,source,browser,True),
          ('http-transitive-helper-control',manifest,fn('  external_get(path)'),browser,True),
          ('explicit-transaction-http',manifest,fn('  transaction db:\n    let row = external_get(path)?\n  Ok("checked")'),browser,False),
          ('transitive-write-and-host',manifest,fn('  let row = edit(id, IssueEdit(rank: 12))?\n  external_get(path)'),browser,False),
          ('transitive-host-and-write',manifest,fn('  let answer = external_get(path)?\n  let row = edit(id, IssueEdit(rank: 12))?\n  Ok(answer)'),browser,False),
          ('missing-static-budget',manifest.replace(', "Http<inventory>"',''),source,browser,False),
          ('cross-nominal-secret',manifest,source.replace(get,'host.http_get<Text>("secondary", path, credential)'),browser,False),
          ('dynamic-destination',manifest,source.replace(get,'host.http_get<Text>(path, path, credential)'),browser,False),
          ('secret-equality',manifest,fn('  let s = host.secret("inventory")\n  s == s','Bool'),browser,False),
          ('secret-through-text-function',manifest,fn('  let s = host.secret("inventory")\n  external_get(s)'),browser,False),
          ('secret-in-post-wire',manifest,source.replace('host.http_post<Text, Text>("inventory", path, credential, body)','host.http_post<Option<Secret<"inventory">>, Text>("inventory", path, credential, Some(credential))'),browser,False),
          ('secret-in-reply-wire',manifest,source.replace(get,'host.http_get<List<Secret<"inventory">>>("inventory", path, credential)'),browser,False),
          ('secret-nested-rpc-result',bound(manifest),fn('  Some(host.secret("inventory"))','Option<Secret<"inventory">>'),browser,False),
          ('secret-in-stored-record',manifest,source.replace('type Issue:\n','type Issue:\n  authority: Secret<"inventory">\n'),browser,False),
          ('browser-host-handle',manifest,source,'export component App:\n  state token: Text = ""\n  on call:\n    token = host.secret("inventory")\n  html:\n    <main>Denied</main>\n',False),
          ('public-api-host-effects',manifest+'\n[http_api]\nversion=1\n[http_api.operations.external_get]\nfunction="server.external_get"\nauth="public"\n',source,browser,False),
          ('unknown-file-resource',manifest.replace('resource = "Issues"\nmax_bytes','resource = "Absent"\nmax_bytes'),source,browser,False),
          ('excess-file-bound',manifest.replace('max_bytes = 8388608','max_bytes = 8388609'),source,browser,False)]
        for i,(name,m,s,b,allowed) in enumerate(cases):
            directory=root/str(i);directory.mkdir();(directory/'diamond.toml').write_text(m);(directory/'server.dia').write_text(s);(directory/'ui.dia').write_text(b)
            result=subprocess.run([str(binary),'check',str(directory/'diamond.toml')],capture_output=True,text=True,timeout=15)
            rows.append({'name':name,'expected_accept':allowed,'exit':result.returncode,'status':'PASS' if (result.returncode==0)==allowed else 'FAIL','diagnostic':result.stderr.strip(),'source_sha256':hashlib.sha256((m+s+b).encode()).hexdigest()})
        report={'status':'PASS' if all(row['status']=='PASS' for row in rows) else 'FAIL','binary_sha256':hashlib.sha256(binary.read_bytes()).hexdigest(),'results':rows}
    args.report.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({'status':report['status'],'observations':len(rows),'failures':[row for row in rows if row['status']!='PASS']}));return report['status']!='PASS'
if __name__=='__main__':raise SystemExit(main())
