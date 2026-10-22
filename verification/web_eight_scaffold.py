#!/usr/bin/env python3
"""Exercise embedded templates, public-operation guards and artifact loading via CLI."""
import hashlib,json,shutil,subprocess,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def main():
    root=Path(tempfile.mkdtemp(prefix='diamond-w8-scaffold-'));binary=root/'diamond';shutil.copy2(ROOT/'target/debug/diamond',binary)
    results=[]
    def run(name,*args,success=True):
        p=subprocess.run([str(binary),*map(str,args)],capture_output=True,text=True,timeout=30)
        result={'name':name,'status':'PASS' if (p.returncode==0)==success else 'FAIL','exit':p.returncode,'stdout':p.stdout,'stderr':p.stderr};results.append(result)
        if result['status']!='PASS':raise AssertionError(result)
    error=None
    try:
        for template,example in [('workspace','live-workspace'),('catalog','catalog')]:
            target=root/template;run('new-'+template,'new',target,'--template',template)
            expected={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT/'examples'/example).iterdir() if p.is_file()}
            actual={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in target.iterdir()}
            assert actual==expected
            run('check-'+template,'check',target/'diamond.toml');run('build-'+template,'build',target/'diamond.toml','--out',root/(template+'-build'));run('reload-'+template,'check',root/(template+'-build'))
            sentinel=target/'operator-sentinel.txt';sentinel.write_text('preserve existing target exactly')
            run('new-refuses-existing-'+template,'new',target,'--template',template,success=False)
            assert sentinel.read_text()=='preserve existing target exactly'
        run('invalid-template-has-no-output','new',root/'invalid','--template','unknown',success=False);assert not (root/'invalid').exists()
        manifest=root/'catalog'/'diamond.toml';original=manifest.read_text()
        for name,op in [('public-write','publish_article'),('public-host-access','job_status'),('public-unknown-operation','missing')]:
            manifest.write_text(original.replace('public_rpc = ["public_page", "read_article"]',f'public_rpc = ["{op}"]'))
            run(name,'check',manifest,success=False)
        manifest.write_text(original)
        artifact=root/'catalog-build';contract=artifact/'contract.json';data=json.loads(contract.read_text());assert data['rpc']['public_page']['authenticated'] is False and data['rpc']['publish_article']['authenticated'] is True
        contract.write_text(contract.read_text()+' ');run('tampered-build-rejected','check',artifact,success=False)
    except Exception as failure:error=repr(failure)
    finally:
        report={'status':'FAIL' if error else 'PASS','binary_sha256':hashlib.sha256(binary.read_bytes()).hexdigest(),'error':error,'results':results,'temporary_root_removed':not error}
        if not error:shutil.rmtree(root)
        (ROOT/'verification/web_eight_scaffold_results.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({'status':report['status'],'observations':len(results),'error':error}));return bool(error)
if __name__=='__main__':raise SystemExit(main())
