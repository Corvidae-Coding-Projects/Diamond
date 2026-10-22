#!/usr/bin/env python3
"""Pin and run the bounded independent W6 real-interface qualification."""
from concurrent.futures import ThreadPoolExecutor
import argparse,hashlib,json,shutil,subprocess,sys,tempfile,time
from pathlib import Path
BASE=Path(__file__).resolve().parent
ROOT=BASE.parents[1]


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--binary',type=Path,required=True);p.add_argument('--library',type=Path,required=True)
    p.add_argument('--output-dir',type=Path);p.add_argument('--dependencies',type=Path,required=True);p.add_argument('--report',type=Path,default=BASE/'results.json')
    a=p.parse_args();output=(a.output_dir or BASE).resolve();output.mkdir(parents=True,exist_ok=True);start=time.monotonic();root=Path(tempfile.mkdtemp(prefix='diamond-w6-independent-run-'))
    binary=root/'diamond';library=root/'libdiamond.rlib';shutil.copy2(a.binary,binary);shutil.copy2(a.library,library)
    definitions=[('files',[]),('outbound',[]),('limits',[]),('native',['--library',str(library),'--dependencies',str(a.dependencies.resolve())]),('checker',[]),('dns',['--async-resolver','--expect-getaddrinfo','0']),('dns_async',[]),('reserved_ipv6',[])]
    def run(step):
        name,extra=step;report=output/(name+'_results.json')
        command=[sys.executable,str(BASE/(name+'.py')),'--binary',str(binary),'--report',str(report),*extra]
        process=subprocess.run(command,cwd=ROOT,capture_output=True,text=True,timeout=180)
        value=json.loads(report.read_text()) if report.exists() else {}
        result={'name':name,'exit':process.returncode,'status':value.get('status'),'observations':len(value.get('results',[])),'http_requests':value.get('http_requests',0),'shutdowns':len(value.get('shutdowns',[])),'report':str(report.relative_to(ROOT)),'stdout':process.stdout.strip(),'stderr':process.stderr.strip()}
        print(json.dumps({key:result[key] for key in ['name','exit','status','observations','http_requests']}),flush=True)
        return result
    results=[];fatal=None
    try:
        # Two owned isolated clusters at a time; deadline/reconcile fixture gets a full pass.
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(run,definitions))
    except Exception as error:fatal=repr(error)
    sources=[path for path in BASE.iterdir() if path.suffix in ['.py','.rs','.c'] or path.name=='ORACLES.md']
    sources += [ROOT/'verification/w5_independent'/name for name in ['service.py','common.py','fixture.py']]
    report={'status':'PASS' if not fatal and len(results)==len(definitions) and all(r['exit']==0 and r['status']=='PASS' for r in results) else 'FAIL',
            'binary_sha256':sha(binary),'library_sha256':sha(library),'error':fatal,'elapsed_seconds':time.monotonic()-start,
            'observations':sum(r['observations'] for r in results),'http_requests':sum(r['http_requests'] for r in results),'shutdowns':sum(r['shutdowns'] for r in results),'steps':results,
            'source_sha256':{str(path.relative_to(ROOT)):sha(path) for path in sorted(sources)},'temporary_root':str(root)}
    a.report.write_text(json.dumps(report,indent=2)+'\n')
    if report['status']=='PASS':shutil.rmtree(root)
    print(json.dumps({key:report[key] for key in ['status','observations','http_requests','shutdowns','elapsed_seconds','error']}));return report['status']!='PASS'
if __name__=='__main__':raise SystemExit(main())
