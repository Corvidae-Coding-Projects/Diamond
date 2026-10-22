#!/usr/bin/env python3
"""Independent W3 CLI oracle. Python Decimal/Unicode/sequence semantics are the reference."""
import argparse, decimal, hashlib, json, os, random, shutil, subprocess, tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
SEED=0xD1A09317
MIN=-(1<<63);MAX=(1<<63)-1

def literal(v):
    if isinstance(v,str): return json.dumps(v,ensure_ascii=False)
    if isinstance(v,bool): return str(v).lower()
    if v==MIN:return '(-9223372036854775807 - 1)'
    return str(v)
def fmt(v):return ('-' if v<0 else '')+str(abs(v)//1000000)+'.'+str(abs(v)%1000000).zfill(6)
def cases():
    rng=random.Random(SEED)
    result=[]
    def add(name,expression,expected=None,ty='Text',reject=False,body=None):
        result.append(dict(name=name,source=body or f'fn main() -> {ty}:\n  {expression}\n',expected=expected,reject=reject))
    for v in [MIN,MIN+1,-1000001,-1000000,-999999,-1,0,1,999999,1000000,MAX-1,MAX]:
        add(f'decimal-format-{v}',f'decimal_text(decimal_units({literal(v)}))',fmt(v))
        add(f'time-roundtrip-{v}',f'int_text(time_millis(timestamp({literal(v)})))',str(v))
        add(f'duration-roundtrip-{v}',f'int_text(duration_millis(duration({literal(v)})))',str(v))
    with decimal.localcontext() as ctx:
        ctx.prec=100
        for i,(a,b) in enumerate([(1,500000),(3,500000),(-1,500000),(-3,500000),(500000,-1),(1234567890123,1999999)]+[(rng.randrange(-10**12,10**12),rng.randrange(-10**8,10**8)) for _ in range(16)]):
            for op in ['multiply','divide']:
                for mode,rounding in [('half_even',decimal.ROUND_HALF_EVEN),('half_away',decimal.ROUND_HALF_UP)]:
                    value=(decimal.Decimal(a)*b/1000000 if op=='multiply' else decimal.Decimal(a)*1000000/b).to_integral_value(rounding=rounding)
                    add(f'decimal-{i}-{op}-{mode}',f'decimal_text(decimal_{op}(decimal_units({literal(a)}), decimal_units({literal(b)}), "{mode}"))',fmt(int(value)))
    texts=['','a','雪😀é','𝄞A\x85z','a雪b😀','\u2003\u0085 hello \u3000','<script>& literal']
    for i,text in enumerate(texts):
        add(f'unicode-len-{i}',f'int_text(len({literal(text)}))',str(len(text)))
        # Restrict offsets to independently valid scalar positions.
        start=len(text)//2;count=len(text)-start
        add(f'unicode-slice-{i}',f'text_slice({literal(text)}, {start}, {count})',text[start:])
        add(f'unicode-trim-{i}',f'text_trim({literal(text)})',text.strip())
        for j,needle in enumerate(['','a','雪']):
            add(f'unicode-replace-{i}-{j}',f'text_replace({literal(text)}, {literal(needle)}, "X😀")',text.replace(needle,'X😀'))
    add('unicode-order-astral-vs-bmp','"😀" > "\ue000"','true',ty='Bool')
    add('list-get-negative','match list_get([31, 7], -1):\n    Some(v) => int_text(v)\n    None => "absent"','absent')
    add('list-reverse-slice','',expected='-2:7',body='fn main() -> Text:\n  let values = for x in list_slice(list_reverse([31, 7, -2, 90]), 1, 2):\n    int_text(x)\n  join_text(values, ":")\n')
    add('typed-result-propagation','',expected='rejected:detail',body='''enum AppError:
  Rejected(reason: Text)

fn fail() -> Result<Int, AppError>:
  Err(Rejected("detail"))

fn relay() -> Result<Int, AppError>:
  let value = fail()?
  Ok(value + 1)

fn main() -> Text:
  match relay():
    Ok(value) => int_text(value)
    Err(error) => match error:
      Rejected(reason) => "rejected:" + reason
''')
    for i,expr in enumerate(['decimal_text(decimal_add(decimal_units(9223372036854775807), decimal_units(1)))','decimal_text(decimal_divide(decimal_units(1), decimal_units(0), "half_even"))','decimal_text(decimal_multiply(decimal_units(1), decimal_units(2), "random"))','int_text(time_millis(timestamp_add(timestamp(9223372036854775807), duration(1))))','text_slice("😀", 1, 1)','text_slice("abc", -1, 1)','int_text(duration_millis(timestamp_diff(timestamp(9223372036854775807), timestamp(-1))))']):
        add(f'runtime-reject-{i}',expr,reject=True)
    add('legacy-data-error-text', '', expected='safe detail', body='''fn failure_text() -> Result<Int, DataError>:
  Err("safe detail")

fn main() -> Text:
  match failure_text():
    Ok(number) => int_text(number)
    Err(error) => error_message(error)
''')
    add('legacy-data-error-argument', '', expected='argument detail', body='''fn describe_data_result(value: Result<Int, DataError>) -> Text:
  match value:
    Ok(number) => int_text(number)
    Err(error) => error_message(error)

fn main() -> Text:
  describe_data_result(Err("argument detail"))
''')
    add('legacy-data-error-try', '', expected='propagated detail', body='''fn failure_for_try() -> Result<Int, DataError>:
  Err("propagated detail")

fn relay_data_result() -> Result<Int, DataError>:
  let number = failure_for_try()?
  Ok(number + 1)

fn main() -> Text:
  match relay_data_result():
    Ok(number) => int_text(number)
    Err(error) => error_message(error)
''')
    for name,seed,doubles,needle,to,expected in [('shrink-ascii','a',20,'aa','b',524288),('same-size-ascii','a',20,'a','b',1048576),('delete-ascii','a',20,'a','',0),('shrink-unicode','😀',18,'😀😀','雪',131072),('expand-rejected','a',20,'a','bb',None)]:
        lines=['fn main() -> Text:',f'  let text_0 = {literal(seed)}']
        for i in range(1,doubles+1):
            lines.append(f'  let text_{i} = text_{i-1} + text_{i-1}')
        lines.append(f'  int_text(len(text_replace(text_{doubles}, {literal(needle)}, {literal(to)})))')
        add('text-limit-'+name, '', expected=str(expected) if expected is not None else None, reject=expected is None, body='\n'.join(lines)+'\n')
    return result

def main():
    p=argparse.ArgumentParser();p.add_argument('--binary',type=Path,default=ROOT/'target/debug/diamond');p.add_argument('--report',type=Path,required=True);o=p.parse_args()
    root=Path(tempfile.mkdtemp(prefix='diamond-w3-independent-cli-'));binary=root/'diamond';shutil.copy2(o.binary,binary)
    results=[]
    for i,c in enumerate(cases()):
        file=root/f'{i}.dia';file.write_text(c['source'])
        r=subprocess.run([str(binary),'run',str(file)],text=True,capture_output=True,timeout=20)
        okay=(r.returncode!=0 if c['reject'] else r.returncode==0 and r.stdout==c['expected']+'\n')
        results.append(c|{'status':'PASS' if okay else 'FAIL','exit':r.returncode,'stdout':r.stdout,'stderr':r.stderr})
    report={'status':'PASS' if all(c['status']=='PASS' for c in results) else 'FAIL','seed':SEED,'binary_sha256':hashlib.sha256(binary.read_bytes()).hexdigest(),'results':results,'temporary_root':str(root)}
    o.report.write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n')
    print(json.dumps({'status':report['status'],'cases':len(results),'failures':[x['name'] for x in results if x['status']=='FAIL'],'report':str(o.report)}))
    if report['status']=='PASS':shutil.rmtree(root)
    return report['status']!='PASS'
if __name__=='__main__':raise SystemExit(main())
