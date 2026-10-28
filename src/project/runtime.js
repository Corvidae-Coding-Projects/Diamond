// Diamond's compiler-owned managed browser interpreter and component runtime.
const __root=document.getElementById('diamond-app'), __error=document.getElementById('diamond-error');
let __fuel=100000,__depth=0,__weight=0;
const __dict=()=>Object.create(null),__own=(o,k)=>Object.hasOwn(o,k);
const __bytes=value=>new TextEncoder().encode(value).length;
function __step(){if(--__fuel<0)throw Error('evaluation budget exceeded');}
function __int(v){if(v< -9223372036854775808n||v>9223372036854775807n)throw Error('integer overflow');return v;}
function __text(v){if(typeof v!=='string'||/[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(v)||__bytes(v)>1048576)throw Error('text budget or Unicode');return v;}
function __valueWeight(v,d=0){if(d>32)throw Error('value depth');let n=128;
 if(typeof v==='string')n+=__bytes(__text(v));else if(Array.isArray(v)){if(v.length>1000)throw Error('list length');for(const x of v)n+=__valueWeight(x,d+1);}
 else if(v&&typeof v==='object'){switch(v.$){case'id':n+=__bytes(v.name)+__bytes(v.value);break;case'blob':n+=__bytes(v.binding)+__bytes(v.id);break;case'stream':n+=__bytes(v.channel)+__bytes(v.parent);break;case'record':n+=__bytes(v.name);for(const[k,x]of Object.entries(v.fields))n+=128+__bytes(k)+__valueWeight(x,d+1);break;case'variant':n+=__bytes(v.name)+__bytes(v.tag);for(const x of v.fields)n+=__valueWeight(x,d+1);break;case'Ok':case'Some':case'Err':n+=__valueWeight(v.value,d+1);break;case'DataError':n+=__bytes(v.value);break;}}
 if(n>4194304)throw Error('managed value size');return n;}
function __check(v){__weight+=__valueWeight(v);if(__weight>67108864)throw Error('managed value work');return v;}
function __reset(){__fuel=100000;__weight=0;}
function __tag(tag,value){return {$:tag,value};}
function __string(v){if(typeof v==='string'||typeof v==='bigint'||typeof v==='boolean')return String(v);if(v&&v.$==='id')return v.value;throw Error('explicit formatting required');}
function __canonical(v){if(Array.isArray(v))return v.map(__canonical);if(v&&typeof v==='object')return Object.fromEntries(Object.keys(v).sort().map(k=>[k,__canonical(v[k])]));return v;}
function __jsonInteger(_key,value){return typeof value==='bigint'?{integer:value.toString()}:value;}
function __equal(a,b){if(typeof a!==typeof b)return false;if(a===b)return true;if(a&&b&&typeof a==='object')return JSON.stringify(__canonical(a),__jsonInteger)===JSON.stringify(__canonical(b),__jsonInteger);return false;}
function __compare(a,b){if(typeof a!=='string')return a<b?-1:a>b?1:0;const x=Array.from(a),y=Array.from(b);for(let i=0;i<Math.min(x.length,y.length);i++){const d=x[i].codePointAt(0)-y[i].codePointAt(0);if(d)return d<0?-1:1;}return Math.sign(x.length-y.length);}
function __binary(a,op,right){if(op==='And'&&!a)return false;if(op==='Or'&&a)return true;const b=right();switch(op){case'And':case'Or':return b;case'Equal':return __equal(a,b);case'NotEqual':return !__equal(a,b);case'Less':return __compare(a,b)<0;case'LessEqual':return __compare(a,b)<=0;case'Greater':return __compare(a,b)>0;case'GreaterEqual':return __compare(a,b)>=0;case'Add':return typeof a==='string'?__text(a+b):__int(a+b);case'Subtract':return __int(a-b);case'Multiply':return __int(a*b);case'Divide':if(b===0n)throw Error('division by zero');return __int(a/b);default:throw Error('invalid operator');}}
function __round(n,d,mode){if(d===0n||!['half_even','half_away'].includes(mode))throw Error('rounding mode or divisor');let q=n/d,r=n%d;const a=x=>x<0n?-x:x,twice=a(r)*2n,den=a(d);if(twice>den||twice===den&&(mode==='half_away'||q%2n!==0n))q+=(n<0n)===(d<0n)?1n:-1n;return __int(q);}
function __range(a,n,length){if(a<0n||n<0n||a+n>BigInt(length))throw Error('slice bounds');return[Number(a),Number(a+n)];}
function __joinText(parts,separator){let size=parts.length>1?(parts.length-1)*__bytes(separator):0;for(const part of parts){size+=__bytes(part);if(size>1048576)throw Error('text utility');}return __text(parts.join(separator));}
function __textReplace(input,from,to){let count=0;if(from==='')count=Array.from(input).length+1;else for(let offset=0;(offset=input.indexOf(from,offset))!==-1;offset+=from.length)count++;const size=__bytes(input)-count*__bytes(from)+count*__bytes(to);if(size>1048576)throw Error('text utility');return __text(from===''?(input===''?to:to+Array.from(input).join(to)+to):input.split(from).join(to));}
function __builtin(name,a){switch(name){
 case'len':return BigInt(Array.from(a[0]).length);case'int_text':return a[0].toString();case'id_text':return a[0].value;
 case'error_message':return a[0].value;case'join_text':return __joinText(a[0],a[1]);
 case'decimal_units':return __tag('Decimal',a[0]);case'timestamp':return __tag('Timestamp',a[0]);case'duration':return __tag('Duration',a[0]);
 case'time_millis':case'duration_millis':return a[0].value;
 case'decimal_text':{const n=a[0].value,v=n<0n?-n:n;return(n<0n?'-':'')+(v/1000000n)+'.'+(v%1000000n).toString().padStart(6,'0');}
 case'decimal_add':return __tag('Decimal',__int(a[0].value+a[1].value));case'decimal_subtract':return __tag('Decimal',__int(a[0].value-a[1].value));
 case'decimal_multiply':return __tag('Decimal',__round(a[0].value*a[1].value,1000000n,a[2]));case'decimal_divide':return __tag('Decimal',__round(a[0].value*1000000n,a[1].value,a[2]));
 case'timestamp_add':return __tag('Timestamp',__int(a[0].value+a[1].value));case'timestamp_diff':return __tag('Duration',__int(a[0].value-a[1].value));
 case'text_slice':{const x=Array.from(a[0]),[s,e]=__range(a[1],a[2],x.length);return x.slice(s,e).join('');}
 case'text_contains':return a[0].includes(a[1]);case'text_trim':return a[0].replace(/^[\u0009-\u000d\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+|[\u0009-\u000d\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+$/gu,'');
 case'text_replace':return __textReplace(a[0],a[1],a[2]);
 case'list_append':if(a[0].length>=1000)throw Error('list length');return[...a[0],a[1]];case'list_reverse':return a[0].slice().reverse();
 case'list_slice':{const[s,e]=__range(a[1],a[2],a[0].length);return a[0].slice(s,e);}case'list_get':return a[1]<0n||a[1]>=BigInt(a[0].length)?{$:'None'}:__tag('Some',a[0][Number(a[1])]);
 case'stream':if(!/^[A-Za-z_][A-Za-z0-9_]*$/.test(a[0])||!/^$|^[A-Za-z0-9_-]{1,128}$/.test(a[1]))throw Error('stream descriptor');return{$:'stream',channel:a[0],parent:a[1]};
 default:throw Error('unknown utility');}}
const __builtins=new Set(['len','error_message','int_text','id_text','join_text','decimal_units','decimal_text','decimal_add','decimal_subtract','decimal_multiply','decimal_divide','timestamp','duration','time_millis','duration_millis','timestamp_add','timestamp_diff','text_slice','text_contains','text_trim','text_replace','list_append','list_reverse','list_slice','list_get','stream']);
function __variant(name){for(const[n,e]of Object.entries(__program.enums))if(__own(e,name))return[n,e[name]];return null;}
function __call(name,args,names){if(__builtins.has(name))return __builtin(name,args);
 if(__actionNames.has(name))return __action(name,args);
 if(name==='Err')return __tag('Err',typeof args[0]==='string'?{$:'DataError',value:args[0],code:'ApplicationFailure'}:args[0]);if(['Ok','Some'].includes(name))return __tag(name,args[0]);if(name==='None')return {$:'None'};
 const v=__variant(name);if(v)return{$:'variant',name:v[0],tag:name,fields:args};
 if(__own(__program.records,name)){const fields=__dict();for(const[n,v]of Object.entries(__program.defaults[name]||{}))fields[n]=__unwire(v,__program.records[name][n]);for(let i=0;i<args.length;i++)fields[names[i]]=args[i];return{$:'record',name,fields};}
 if(__own(__program.aliases,name)&&__program.aliases[name].Id)return{$:'id',name:__program.aliases[name].Id,value:args[0]};
 return __functions[name](...args);}
function __invoke(name,args){const f=__program.functions[name];if(!f)throw Error('unresolved function');if(++__depth>64){--__depth;throw Error('call depth');}
 try{const env=__dict();f.parameters.forEach(([n,t],i)=>{__validate(args[i],t);env[n]=args[i];});let value;try{value=__block(f.body,env);}catch(e){if(e&&e.diamondResult){value=__tag('Err',e.value);}else throw e;}__validate(value,f.result);return value;}finally{--__depth;}}
function __block(body,env){let v={$:'Unit'};for(const stmt of body){__step();const k=stmt.kind;if(k.Let){const[n,e]=k.Let;env[n]=__eval(e,env);v={$:'Unit'};}else if(k.Expression)v=__eval(k.Expression,env);else if(k.Return)return __eval(k.Return,env);else throw Error('unsupported browser statement');}return v;}
function __eval(expr,env){__step();const k=expr.kind;let v;
 if(__own(k,'Int'))v=BigInt(k.Int);else if(__own(k,'Text'))v=k.Text;else if(__own(k,'Bool'))v=k.Bool;
 else if(k.Name){if(__own(env,k.Name))v=env[k.Name];else if(k.Name==='None')v={$:'None'};else if(__actionEmpty.has(k.Name))v=__action(k.Name);else{const variant=__variant(k.Name);if(!variant)throw Error('missing local');v={$:'variant',name:variant[0],tag:k.Name,fields:[]};}}
 else if(k.Unary){const[op,e]=k.Unary;v=__eval(e,env);v=op==='Not'?!v:__int(-v);}
 else if(k.Binary){const[a,op,b]=k.Binary;v=__binary(__eval(a,env),op,()=>__eval(b,env));}
 else if(k.Field){const[a,field]=k.Field;const base=__eval(a,env);if(base.$!=='record'||!__own(base.fields,field))throw Error('invalid field');v=base.fields[field];}
 else if(k.Call){const c=k.Call;if(!c.callee.kind.Name)throw Error('invalid pure callee');v=__call(c.callee.kind.Name,c.arguments.map(([,e])=>__eval(e,env)),c.arguments.map(([n])=>n));}
 else if(k.Try){const result=__eval(k.Try,env);if(result.$==='Err')throw{diamondResult:true,value:result.value};if(result.$!=='Ok')throw Error('invalid Result');v=result.value;}
 else if(k.List)v=k.List.map(e=>__eval(e,env));
 else if(k.For){const[n,e,body]=k.For,values=__eval(e,env);if(values.length>1000)throw Error('iteration length');v=values.map(value=>__block(body,Object.assign(__dict(),env,{[n]:value})));}
 else if(k.Match){const[e,arms]=k.Match,value=__eval(e,env);let found=false;for(const arm of arms){const p=arm.pattern,next=Object.assign(__dict(),env);let fields=[];
  if(p.Binding){if(p.Binding!=='_')next[p.Binding]=value;}
  else if(__own(p,'Bool')){if(value!==p.Bool)continue;}
  else if(p.Constructor){const[tag,names]=p.Constructor;if(value.$==='variant'){if(value.tag!==tag)continue;fields=value.fields;}else{if(value.$!==tag)continue;fields=tag==='None'?[]:[value.value];}if(fields.length!==names.length)continue;names.forEach((n,i)=>{if(n!=='_')next[n]=fields[i];});}else continue;
  v=__block(arm.body,next);found=true;break;}if(!found)throw Error('non-exhaustive match');}
 else throw Error('invalid browser expression');return __check(v);
}
function __validate(v,t,d=0){if(d>32)throw Error('type depth');const name=typeof t==='string'?t:Object.keys(t)[0],a=typeof t==='string'?null:t[name];
 switch(name){case'Int':if(typeof v!=='bigint')throw Error('Int type');__int(v);break;case'Bool':if(typeof v!=='boolean')throw Error('Bool type');break;case'Text':__text(v);break;case'BoundedText':__text(v);if(Array.from(v).length<a[0]||Array.from(v).length>a[1])throw Error('Text bounds');break;
 case'Action':__validateAction(v,a,d);break;
 case'Decimal':case'Timestamp':case'Duration':if(!v||v.$!==name)throw Error('numeric type');__int(v.value);break;
 case'Id':if(!v||v.$!=='id'||v.name!==a||typeof v.value!=='string'||!/^[-_A-Za-z0-9]{1,128}$/.test(v.value))throw Error('nominal Id');break;
 case'Blob':if(!v||v.$!=='blob'||v.binding!==a||typeof v.id!=='string'||!/^[0-9a-f]{64}$/.test(v.id))throw Error('opaque Blob');break;
 case'Stream':if(!v||v.$!=='stream'||v.channel!==a||typeof v.parent!=='string'||!/^$|^[A-Za-z0-9_-]{1,128}$/.test(v.parent))throw Error('opaque Stream');break;
 case'Unit':if(v.$!=='Unit')throw Error('Unit type');break;
 case'List':if(!Array.isArray(v)||v.length>1000)throw Error('List type');v.forEach(x=>__validate(x,a,d+1));break;
 case'Option':if(!v||!['Some','None'].includes(v.$))throw Error('Option type');if(v.$==='Some')__validate(v.value,a,d+1);break;
 case'Success':case'Result':case'AppResult':if(!v||!['Ok','Err'].includes(v.$))throw Error('Result type');if(v.$==='Ok')__validate(v.value,name==='AppResult'?a[0]:a,d+1);else if(name==='AppResult')__validate(v.value,a[1],d+1);else if(!v.value||v.value.$!=='DataError')throw Error('DataError type');break;
 case'Record':{const schema=__program.records[a];if(!v||v.$!=='record'||v.name!==a||!schema||Object.keys(v.fields).length!==Object.keys(schema).length)throw Error('record type');for(const[n,t]of Object.entries(schema))__validate(v.fields[n],t,d+1);break;}
 case'Enum':{const schema=__program.enums[a];if(!v||v.$!=='variant'||v.name!==a||!schema||!__own(schema,v.tag)||v.fields.length!==schema[v.tag].length)throw Error('enum type');schema[v.tag].forEach((t,i)=>__validate(v.fields[i],t,d+1));break;}
 case'DataError':if(!v||v.$!=='DataError')throw Error('DataError type');break;case'Never':throw Error('Never value');default:throw Error('unsupported managed type');}return v;
}
function __wireAny(v){if(typeof v==='bigint')return v.toString();if(typeof v==='string'||typeof v==='boolean')return v;if(Array.isArray(v))return v.map(__wireAny);if(v.$==='record')return Object.fromEntries(Object.entries(v.fields).map(([k,v])=>[k,__wireAny(v)]));if(v.$==='variant')return{tag:__program.wire_names[v.tag]||v.tag,fields:v.fields.map(__wireAny)};if(v.$==='id')return{tag:v.name,value:v.value};if(v.$==='DataError')return v.code;if(['None','Unit'].includes(v.$))return{tag:v.$};return{tag:v.$,value:__wireAny(v.value)};}
function __unwire(v,t,d=0){if(d>32)throw Error('wire depth');const name=typeof t==='string'?t:Object.keys(t)[0],a=typeof t==='string'?null:t[name];let result;
 const object=(tag,n)=>{if(!v||Array.isArray(v)||typeof v!=='object'||v.tag!==tag||Object.keys(v).length!==n)throw Error('wire shape');};
 const integer=x=>{if(typeof x!=='string'||! /^(0|-?[1-9][0-9]*)$/.test(x))throw Error('wire Int');return __int(BigInt(x));};
 switch(name){case'Int':result=integer(v);break;case'Bool':case'Text':case'BoundedText':result=v;break;case'Decimal':case'Timestamp':case'Duration':object(name,2);result=__tag(name,integer(v.value));break;
 case'Action':result=__unwireAction(v,a,d);break;
 case'Id':object(a,2);result={$:'id',name:a,value:v.value};break;case'Unit':object('Unit',1);result={$:'Unit'};break;
 case'List':if(!Array.isArray(v)||v.length>1000)throw Error('wire list');result=v.map(x=>__unwire(x,a,d+1));break;
 case'Option':if(v&&v.tag==='None'){object('None',1);result={$:'None'};}else{object('Some',2);result=__tag('Some',__unwire(v.value,a,d+1));}break;
 case'Result':case'AppResult':object(v&&v.tag,2);if(!['Ok','Err'].includes(v.tag))throw Error('wire Result');result=__tag(v.tag,__unwire(v.value,name==='AppResult'?a[v.tag==='Ok'?0:1]:v.tag==='Ok'?a:'DataError',d+1));break;
 case'DataError':{const messages={Denied:'Access denied or resource not found.',InvalidInput:'Input does not satisfy its declared shape or bounds.',Aborted:'Transaction aborted.',RetryableAbort:'Transaction aborted by a concurrency conflict. Retry explicitly.',ApplicationFailure:'Application failed.'};if(!__own(messages,v))throw Error('wire error');result={$:'DataError',value:messages[v],code:v};break;}
 case'Record':{const schema=__program.records[a];if(!v||Array.isArray(v)||typeof v!=='object'||!schema||Object.keys(v).length!==Object.keys(schema).length)throw Error('wire record');const fields=__dict();for(const[n,t]of Object.entries(schema)){if(!__own(v,n))throw Error('wire field');fields[n]=__unwire(v[n],t,d+1);}result={$:'record',name:a,fields};break;}
 case'Enum':{const schema=__program.enums[a];object(v&&v.tag,2);const internal=schema&&Object.keys(schema).find(n=>(__program.wire_names[n]||n)===v.tag);if(!internal||!Array.isArray(v.fields)||v.fields.length!==schema[internal].length)throw Error('wire enum');result={$:'variant',name:a,tag:internal,fields:v.fields.map((x,i)=>__unwire(x,schema[internal][i],d+1))};break;}default:throw Error('wire type');}
 // Count the fully materialized root once. Nested values are validated while
 // they are decoded, but only the root contributes to the event-wide work
 // budget so direct remote assignments cannot bypass aggregate accounting.
 return d===0?__check(__validate(result,t,d)):__validate(result,t,d);
}
let __csrf='',__bootstrap=null,__bootstrapController=null,__authEpoch=0,__authBusy=false;
function __backoff(ms,signal){return new Promise((resolve,reject)=>{if(signal.aborted){reject(signal.reason);return;}const stop=()=>{clearTimeout(timer);signal.removeEventListener('abort',stop);reject(signal.reason);};const timer=setTimeout(()=>{signal.removeEventListener('abort',stop);resolve();},ms);signal.addEventListener('abort',stop,{once:true});});}
async function __fetch(path,options,ctx){
 for(let retry=0;;retry++){
  if(ctx&&(ctx.epoch!==__authEpoch||ctx.controller.signal.aborted))throw Error('stale request');
  const response=await fetch(path,options);
  // Only this server's explicit pre-execution rejection permits replay. A lost
  // reply, generic 503, or unknown commit never takes this branch.
  const seconds=response.headers.get('Retry-After');
  if(retry>=__runtime.retries||response.status!==503||response.headers.get('X-Diamond-Retry')!=='safe'||!seconds||!/^\d+$/.test(seconds)||Number(seconds)<1||Number(seconds)>30)return response;
  if(ctx&&++ctx.calls>32)return response;
  await response.arrayBuffer();
  await __backoff(Math.max(Number(seconds)*1000,Math.min(__runtime.max_backoff_ms,Number(seconds)*1000*2**retry))+Math.floor(Math.random()*__runtime.jitter_ms),options.signal);
 }
}
async function __remote(name,args,types,result,ctx){let response,identity=false;try{if(ctx.epoch!==__authEpoch||ctx.controller.signal.aborted)throw Error('stale event');if(++ctx.calls>32)throw Error('event remote-call budget');
 if(['login','logout','verify','reset','change_password','logout_all','disable'].some(n=>name==='session/'+n)){if(__authBusy)throw Error('identity update pending');__authBusy=true;identity=true;ctx.identity=true;ctx.epoch=++__authEpoch;__beginIdentity(ctx);}
 if(!__csrf||name==='session/user'){
  if(!__bootstrap){__bootstrapController=new AbortController();__bootstrap=__fetch('/_diamond/session',{signal:__bootstrapController.signal,credentials:'same-origin',cache:'no-store',headers:{'X-Diamond-Session':'1'}}).then(async r=>{if(!r.ok)throw Error('session unavailable');return r.json();}).finally(()=>{__bootstrap=null;__bootstrapController=null;});}
  const session=await __bootstrap;if(ctx.epoch!==__authEpoch)throw Error('identity changed');__csrf=session.csrf;if(name==='session/user')return session.user||'';
 }
 if(name.startsWith('action/'))return await __actionRemote(name.slice(7),args,types,result,ctx);
 if(name.startsWith('files/'))return await __files(name.slice(6),args,ctx);
 let path,body;if(name==='session/login'){path='/_diamond/login';body={username:args[0],password:args[1]};}
 else if(name==='session/oauth_login'){if(!['google','github'].includes(args[0]))throw Error('unknown OAuth provider');path='/_diamond/oauth/'+args[0]+'/start';body={invite:args[1]};}
 else if(name==='session/logout'){path='/_diamond/logout';body={};}
 else if(name.startsWith('session/')){const n=name.slice(8),fields={register:['username','email','password','invite'],verify:['token'],resend:['email'],recover:['email'],reset:['token','password'],change_password:['current_password','password'],logout_all:[],invite:['email'],disable:['username','disabled'],create_token:['label','operations','scopes','lifetime_seconds'],list_tokens:[],revoke_token:['id'],add_ssh_key:['label','public_key'],list_ssh_keys:[],revoke_ssh_key:['id']}[n];if(!fields)throw Error('unknown account operation');path='/_diamond/accounts/'+n;body=Object.fromEntries(fields.map((f,i)=>[f,f==='scopes'?JSON.parse(args[i]):f==='lifetime_seconds'?args[i].toString():args[i]]));}
 else{path='/_diamond/'+name;body={contract:__contract,arguments:args.map((v,i)=>{__validate(v,types[i]);return __wireAny(v);})};}
 response=await __fetch(path,{method:'POST',credentials:'same-origin',signal:ctx.controller.signal,headers:{'Content-Type':'application/json','X-Diamond-CSRF':__csrf},body:JSON.stringify(body)},ctx);
 const value=await response.json();if(!response.ok){const safe=response.status===503&&response.headers.get('X-Diamond-Retry')==='safe';const error=Error(safe?'Server busy. This request was not executed. Try again shortly.':value.error==='outcome_unknown'||response.status>=500?'Request failed; a mutation may have committed. Refresh before retrying.':value.error==='contract_mismatch'?'Application version changed. Reload this page.':'Request rejected. Check your session, input, and permissions. Earlier server calls may already have committed.');error.status=response.status;error.code=value.error;error.diamondRemote=true;error.uncertain=response.status>=500&&!safe;error.lostIdentity=response.status===401;throw error;}
 if(ctx.epoch!==__authEpoch)throw Error('identity changed');if(name==='session/oauth_login'){const target=new URL(value.authorization_url),expected=args[0]==='google'?['https://accounts.google.com','/o/oauth2/v2/auth']:['https://github.com','/login/oauth/authorize'];if(target.origin!==expected[0]||target.pathname!==expected[1]||target.username||target.password||target.hash)throw Error('invalid OAuth destination');location.assign(target.href);return '';}if(value.csrf)__csrf=value.csrf;if(['logout','reset','change_password','logout_all','disable'].some(n=>name==='session/'+n))__csrf='';return __unwire(value.value,result);
 }catch(error){const failure=error.diamondRemote?error:Error('Request interrupted; a mutation may have committed. Refresh before retrying.');if(!error.diamondRemote){failure.diamondRemote=true;failure.uncertain=true;}if(ctx.epoch===__authEpoch&&(identity&&failure.uncertain||failure.lostIdentity))__forgetIdentity(failure);throw failure;}finally{if(identity){__authBusy=false;queueMicrotask(()=>__discoverRecovery());}}}
async function __files(operation,args,ctx){
 const[binding,subject,selector]=args;if(!/^[A-Za-z_][A-Za-z0-9_]*$/.test(binding))throw Error('file binding');
 const base='/_diamond/files/'+binding;
 const active=()=>{if(ctx.epoch!==__authEpoch||ctx.controller.signal.aborted)throw Error('stale upload');};
 const call=async(path,method,body,upload)=>{active();const response=await __fetch(path,{method,credentials:'same-origin',cache:'no-store',signal:ctx.controller.signal,headers:{...(method==='GET'?{}:{'Content-Type':body instanceof File?'application/octet-stream':'application/json','X-Diamond-CSRF':__csrf}),...(upload?{'X-Diamond-Upload':upload}:{})},...(method==='GET'?{}:{body:body instanceof File?body:JSON.stringify(body)})},ctx);const value=await response.json();if(!response.ok){const error=Error('File request rejected. Refresh before retrying.');error.diamondRemote=true;error.uncertain=response.status>=500&&response.headers.get('X-Diamond-Retry')!=='safe';error.lostIdentity=response.status===401;throw error;}active();return value;};
 if(operation==='upload'){
  const input=document.getElementById(selector);if(!(input instanceof HTMLInputElement)||input.type!=='file'||input.files.length!==1)throw Error('Select one file first.');
  const file=input.files[0];if(file.size>8388608)throw Error('File exceeds 8 MiB.');
  const reserved=await call(base+'/begin','POST',{parent:subject,filename:file.name,length:String(file.size),media_type:file.type});
  if(!/^[0-9a-f]{64}$/.test(reserved.id)||!/^[0-9a-f]{64}$/.test(reserved.upload))throw Error('upload reservation');
  await call(base+'/'+reserved.id,'PUT',file,reserved.upload);
  const ready=await call(base+'/'+reserved.id+'/finish','POST',{},reserved.upload);
  if(ready.state!=='ready'||ready.id!==reserved.id)throw Error('upload publication');input.value='';return{$:'blob',binding,id:ready.id};
 }
 const reference=subject;__validate(reference,{Blob:binding});const id=reference.id;
 if(!/^[0-9a-f]{64}$/.test(id))throw Error('attachment id');
 if(operation==='status')return JSON.stringify(await call(base+'/'+id+'/status','GET'));
 if(operation==='delete'){await call(base+'/'+id+'/delete','POST',{});return{$:'None'};}
 if(operation==='download'){active();const link=document.createElement('a');link.href=base+'/'+id;link.download='attachment.bin';link.click();return reference;}
 throw Error('file operation');
}
function __clearSelectedFiles(){for(const input of document.querySelectorAll('input[type=file]'))input.value='';}
function __forgetIdentity(error){__clearRecovery();__closeWatches();__clearSelectedFiles();__csrf='';__bootstrapController?.abort();__bootstrap=null;__authEpoch++;for(const frame of __frames.values())__dispose(frame);__frames.clear();__render();__fail(null,error);}
let __frames=new Map(),__nodes=[],__rendering=false,__alive=true,__hydrationEdited=false;
function __environment(frame,state=frame.state,props=frame.props){const env=Object.assign(__dict(),props,state,{pending:frame.pending,error:frame.error,route:__locationURL()});for(const[n,e]of __program.components[frame.name].derived){env[n]=__eval(e.ast,env);__validate(env[n],e.ty);}return env;}
function __initial(name,props,id){const def=__program.components[name],state=__dict(),env=Object.assign(__dict(),props);for(const[n,e]of def.state){state[n]=__eval(e.ast,env);__validate(state[n],e.ty);env[n]=state[n];}return{name,id,state,props,pending:false,error:'',generation:0,bindingGeneration:0,outputs:__dict(),actionTargets:new Set(),formErrors:new Map(),controller:null,active:false};}
function __key(v){return typeof v==='bigint'?'i:'+v:typeof v==='string'?'t:'+v:v&&v.$==='id'?'d:'+v.name+':'+v.value:(()=>{throw Error('invalid key');})();}
function __compact(nodes){const result=[];for(const node of nodes){const last=result[result.length-1];if(__own(node,'text')&&last&&__own(last,'text'))last.text+=node.text;else result.push(node);}return result;}
function __selectOptions(nodes,value){const flat=[],values=new Set();let found=false;const add=node=>{if(node.tag==='diamond-fragment'){for(const child of node.children)add(child);return;}if(__own(node,'text')){if(node.text.trim()!=='')throw Error('invalid select content');flat.push(node);return;}if(node.tag!=='option')throw Error('invalid select content');const option=String(node.attrs.value);if(values.has(option))throw Error('duplicate select option value');values.add(option);const selected=option===String(value);found||=selected;flat.push({...node,attrs:{...node.attrs,selected}});};for(const node of nodes)add(node);if(!found)throw Error('select value has no option');return __compact(flat);}
function __tree(){const seen=new Map(),updates=new Map(),watches=new Map();let count=0;
 function frame(name,props,id,outputs=__dict()){if(seen.has(id))throw Error('duplicate component identity');if(seen.size>=1024)throw Error('component capacity');let f=__frames.get(id);if(!f||f.name!==name)f=__initial(name,props,id);seen.set(id,f);updates.set(f,{props,outputs});const def=__program.components[name];for(const[n,t]of def.properties)__validate(props[n],t);return{tag:'diamond-component',attrs:{'data-diamond-component':name,'aria-busy':String(f.pending)},children:nodes(def.template,__environment(f,f.state,props),id,f),key:id,frame:f};}
 function nodes(list,env,path,f){const out=[];for(let i=0;i<list.length;i++){__step();if(++count>8192)throw Error('render node capacity');const n=list[i],id=path+'/'+i;if(new TextEncoder().encode(id).length>8192)throw Error('render identity budget');
  if(__own(n,'Text')){if(n.Text!=='')out.push({text:n.Text,key:id});}else if(n.Value){const text=__string(__eval(n.Value.ast,env));if(text!=='')out.push({text,key:id});}
  else if(n.When){if(__eval(n.When[0].ast,env))out.push(...nodes(n.When[1],env,id,f));}
  else if(n.Route){if(location.pathname===n.Route[0])out.push(...nodes(n.Route[1],env,id,f));}
  else if(n.NamedRoute){const route=__routeMatch(__locationURL()),r=n.NamedRoute;if(route&&route.name===r.route)out.push(...nodes(r.children,Object.assign(__dict(),env,{[r.binding]:route.value}),id+'/'+__encodeURL(__locationURL()),f));}
  else if(n.Each){const e=n.Each,values=__eval(e.items.ast,env),keys=new Set();if(values.length>1000)throw Error('list length');for(const v of values){const nested=Object.assign(__dict(),env,{[e.binding]:v}),key=__encodeURL(__key(__eval(e.key.ast,nested)));if(keys.has(key))throw Error('duplicate key');keys.add(key);out.push({tag:'diamond-fragment',attrs:{},children:nodes(e.children,nested,id+'/'+key,f),key:id+'/'+key});}}
  else if(n.Field){count+=__fieldCost(n.Field);if(count>8192)throw Error('render node capacity');out.push(__fieldNode(n.Field,env,id,f));}
  else if(n.Submit){count++;out.push(__submitNode(n.Submit,env,id,f));}
  else if(n.Status){count++;out.push(__statusNode(n.Status,env,id));}
  else if(n.Watch){const w=n.Watch,stream=__eval(w.stream.ast,env);__validate(stream,w.stream.ty);watches.set(id,{...w,channel:stream.channel,parent:stream.parent,frame:f});}
  else if(n.Use){const props=__dict(),outputs=__dict();for(const[property,expression]of n.Use.properties)props[property]=__eval(expression.ast,env);for(const[name,handler]of Object.entries(n.Use.outputs))outputs[name]={parent:f,handler};out.push(frame(n.Use.component,props,id,outputs));}
  else if(n.Element){const e=n.Element,attrs=__dict(),events=__dict();let binding=null,checked=null,form=null;for(const[n,a]of e.attributes){if(a.Event)events[n]=a.Event;else if(a.Form)form=a.Form;else if(a.Field)attrs['data-diamond-field']=a.Field;else if(a.Route)attrs[n]=__routeUrl(__eval(a.Route.ast,env));else if(a.Binding){binding=a.Binding;attrs.value=env[binding];}else if(a.Checked){checked=a.Checked;attrs.checked=env[checked];}else attrs[n]=__own(a,'Text')?a.Text:__string(__eval(a.Value.ast,env));}if(['diamond-app','diamond-error','diamond-recovery'].includes(attrs.id)||attrs.id?.startsWith('diamond-control-')||attrs.id?.startsWith('diamond-field-'))throw Error('reserved component id');let childNodes=nodes(e.children,env,id,f);if(e.tag==='select')childNodes=__selectOptions(childNodes,attrs.value);out.push({tag:e.tag,attrs,events,binding,checked,form,scope:env,frame:f,children:e.tag==='textarea'?(attrs.value===''?[]:[{text:attrs.value,key:id+'/value'}]):childNodes,key:id});}
 }return __compact(out);}
 const root=frame(__program.component,__dict(),'root');if(count>8192)throw Error('render node capacity');return{nodes:[root],seen,updates,watches};}
const __meta=new WeakMap();
function __attribute(el,name,value){if(['checked','disabled','required','selected'].includes(name)){const on=value===true||value==='true';el[name]=on;if(on)el.setAttribute(name,'');else el.removeAttribute(name);}else if(name==='value'){if(el.value!==String(value))el.value=String(value);if(el.tagName!=='TEXTAREA'&&el.tagName!=='SELECT')el.setAttribute('value',String(value));}else el.setAttribute(name,String(value));}
function __activate(el,v){const prior=__meta.get(el);if(prior){for(const name of Object.keys(prior.attrs||{}))if(!__own(v.attrs,name))el.removeAttribute(name);}for(const[n,value]of Object.entries(v.attrs))__attribute(el,n,value);
 el.onclick=null;el.onsubmit=null;el.oninput=null;el.onchange=null;
 if(v.events&&v.events['on-click'])el.onclick=event=>{event.preventDefault();__handle(v.frame,v.events['on-click'],v.scope,el,null);};
 if(v.events&&v.events['on-submit'])el.onsubmit=event=>{event.preventDefault();if((v.form||el.checkValidity())&&!v.frame.pending)__handle(v.frame,v.events['on-submit'],v.scope,el,v.form);};
 if(v.binding||v.checked){const input=()=>{if(!v.frame.active||__authBusy)return;try{const name=v.binding||v.checked,def=__program.components[v.frame.name].state.find(([n])=>n===name)[1],value=v.checked?el.checked:__text(v.attrs['data-diamond-field']?__boundedDraft(el.value):el.value);__validate(value,def.ty);__cancelActions(v.frame);v.frame.generation++;v.frame.controller?.abort();v.frame.pending=false;const old=v.frame.state;v.frame.state=Object.assign(__dict(),old,{[name]:value});try{__render();}catch(e){v.frame.state=old;throw e;}}catch(e){__fail(v.frame,e);}};if(el.tagName==='SELECT')el.onchange=input;else el.oninput=input;}
 if(v.tag==='a'&&__isRoute(v.attrs.href))el.onclick=e=>{if(e.button!==0||e.metaKey||e.ctrlKey||e.shiftKey||e.altKey)return;e.preventDefault();__navigate(v.attrs.href);};__meta.set(el,v);if(v.form)__presentForm(el,v);}
function __create(v){if(__own(v,'text'))return document.createTextNode(v.text);const el=document.createElement(v.tag);for(const child of v.children)el.append(__create(child));__activate(el,v);return el;}
function __patch(parent,old,next){const oldNodes=Array.from(parent.childNodes),byKey=new Map(old.map((v,i)=>[v.key,{v,el:oldNodes[i]}]));const kept=new Set();for(let i=0;i<next.length;i++){const v=next[i],match=byKey.get(v.key);let el;
 if(match&&(__own(v,'text')&&__own(match.v,'text')||v.tag&&v.tag===match.v.tag)){el=match.el;kept.add(el);if(__own(v,'text')){if(el.data!==v.text)el.data=v.text;}else{if(!v.attrs['data-diamond-field-error'])__patch(el,match.v.children,v.children);__activate(el,v);}}
 else el=__create(v);if(parent.childNodes[i]!==el)parent.insertBefore(el,parent.childNodes[i]||null);kept.add(el);}
 for(const el of Array.from(parent.childNodes))if(!kept.has(el))el.remove();}
function __matches(el,v){if(__own(v,'text'))return el?.nodeType===3&&el.data===v.text.replace(/\r\n?/g,'\n').replace(/\0/g,'\ufffd');if(!el||el.nodeType!==1||el.localName!==v.tag||el.childNodes.length!==v.children.length)return false;for(const[n,value]of Object.entries(v.attrs)){if(n==='value'||n==='checked'&&v.checked)continue;const expected=['checked','disabled','required','selected'].includes(n)?value===true||value==='true'?'':null:String(value).replace(/\r\n?/g,'\n').replace(/\0/g,'\ufffd');if(el.getAttribute(n)!==expected)return false;}return v.children.every((child,i)=>__matches(el.childNodes[i],child));}
function __adopt(el,v){if(__own(v,'text')){el.data=v.text;return;}
 if(v.binding||v.checked){const name=v.binding||v.checked,value=v.checked?el.checked:__text(el.value),def=__program.components[v.frame.name].state.find(([n])=>n===name)[1];__validate(value,def.ty);if(!__equal(value,v.frame.state[name])){v.frame.state=Object.assign(__dict(),v.frame.state,{[name]:value});__hydrationEdited=true;}v.attrs[v.checked?'checked':'value']=value;}
 v.children.forEach((child,i)=>__adopt(el.childNodes[i],child));__activate(el,v);}
function __dispose(frame){try{frame.active=false;__cancelActions(frame);frame.generation++;frame.bindingGeneration++;frame.controller?.abort();frame.controller=null;frame.pending=false;const handler=__program.components[frame.name].events.unmount;if(handler){const env=__environment(frame);for(const step of handler.body){if(!step.Assign||!step.Assign[1].Pure)throw Error('invalid cleanup');env[step.Assign[0]]=__eval(step.Assign[1].Pure.ast,env);}}}catch(error){__fail(null,error);}}
function __commit(tree){const mounted=[];let changed=false;for(const[id,f]of __frames)if(!tree.seen.has(id))__dispose(f);for(const[f,{props,outputs}]of tree.updates){if(f.active&&(Object.keys(props).length!==Object.keys(f.props).length||Object.keys(props).some(n=>!__equal(props[n],f.props[n]))||!__sameOutputs(f.outputs,outputs))){__cancelActions(f);f.generation++;f.bindingGeneration++;f.controller?.abort();f.pending=false;f.error='';changed=true;}f.props=props;f.outputs=outputs;if(!f.active){f.active=true;f.mountPending=true;}if(f.mountPending&&!__authBusy){f.mountPending=false;mounted.push(f);}}__frames=tree.seen;__nodes=tree.nodes;__syncWatches(tree.watches);queueMicrotask(()=>{if(changed)__render();for(const f of mounted)if(f.active&&__program.components[f.name].events.mount)__dispatch(f,'mount');});}
function __render(){if(__rendering||!__alive)return;__rendering=true;try{__reset();const saved=__focusSnapshot(),tree=__tree();__patch(__root,__nodes,tree.nodes);__commit(tree);__restoreFocus(saved);}finally{__rendering=false;}}
function __eventFailure(error,frame,name){const failure=error&&typeof error==='object'?error:Error('declared result escaped event'),handler=frame.name+'.'+name;failure.diamondHandler=__handlerLabels[handler]||handler;failure.diamondLocation=__handlerLocations[handler]||null;return failure;}
function __fail(frame,error,calls=0){console.error('Diamond component update failed',error instanceof Error?error:Error('declared result escaped event'));const where=typeof error?.diamondHandler==='string'?' in '+error.diamondHandler:'';const message=(error?.diamondRemote?error.message:calls>0?'Browser update failed; earlier server operations may have committed. Refresh before retrying.':'Diamond component update failed; previous local state was preserved.')+where;if(frame)frame.error=message;__error.hidden=false;__error.textContent=message;}
async function __blockAsync(body,env,remotes,ctx){let v={$:'Unit'};for(const s of body){__step();const k=s.kind;if(k.Let){const[n,e]=k.Let;env[n]=await __evalAsync(e,env,remotes,ctx);v={$:'Unit'};}else if(k.Expression)v=await __evalAsync(k.Expression,env,remotes,ctx);else if(k.Return)return __evalAsync(k.Return,env,remotes,ctx);else throw Error('invalid event statement');}return v;}
async function __evalAsync(expr,env,remotes,ctx){__step();const k=expr.kind;let v;
 if(k.Name&&__own(remotes,k.Name)){const r=remotes[k.Name].Remote,args=[];for(const e of r.arguments)args.push(await __evalAsync(e.ast,env,remotes,ctx));return __remote(r.operation,args,r.types,r.result,ctx);}
 if(k.Unary){const[op,e]=k.Unary;v=await __evalAsync(e,env,remotes,ctx);v=op==='Not'?!v:__int(-v);}
 else if(k.Binary){const[a,op,b]=k.Binary,left=await __evalAsync(a,env,remotes,ctx);if(op==='And'&&!left)return false;if(op==='Or'&&left)return true;const right=await __evalAsync(b,env,remotes,ctx);v=__binary(left,op,()=>right);}
 else if(k.Call){const c=k.Call,args=[];for(const[,e]of c.arguments)args.push(await __evalAsync(e,env,remotes,ctx));v=__call(c.callee.kind.Name,args,c.arguments.map(([n])=>n));}
 else if(k.Field){const[e,n]=k.Field,base=await __evalAsync(e,env,remotes,ctx);if(base.$!=='record'||!__own(base.fields,n))throw Error('invalid field');v=base.fields[n];}
 else if(k.Try){const r=await __evalAsync(k.Try,env,remotes,ctx);if(r.$==='Err')throw{diamondResult:true,value:r.value};if(r.$!=='Ok')throw Error('invalid Result');v=r.value;}
 else if(k.List){v=[];for(const e of k.List)v.push(await __evalAsync(e,env,remotes,ctx));}
 else if(k.For){const[n,e,body]=k.For,values=await __evalAsync(e,env,remotes,ctx);if(values.length>1000)throw Error('iteration length');v=[];for(const value of values)v.push(await __blockAsync(body,Object.assign(__dict(),env,{[n]:value}),remotes,ctx));}
 else if(k.Match){const[e,arms]=k.Match,value=await __evalAsync(e,env,remotes,ctx);let found=false;for(const arm of arms){const p=arm.pattern,next=Object.assign(__dict(),env);let fields=[];
  if(p.Binding){if(p.Binding!=='_')next[p.Binding]=value;}else if(__own(p,'Bool')){if(value!==p.Bool)continue;}
  else if(p.Constructor){const[tag,names]=p.Constructor;if(value.$==='variant'){if(value.tag!==tag)continue;fields=value.fields;}else{if(value.$!==tag)continue;fields=tag==='None'?[]:[value.value];}if(fields.length!==names.length)continue;names.forEach((n,i)=>{if(n!=='_')next[n]=fields[i];});}else continue;
  v=await __blockAsync(arm.body,next,remotes,ctx);found=true;break;}if(!found)throw Error('non-exhaustive match');}
 else return __eval(expr,env);return __check(v);
}

async function __dispatch(frame,name,args=[],chain={remaining:64,deadline:Date.now()+__runtime.event_timeout_ms}){
 if(!frame.active||__authBusy)return;const def=__program.components[frame.name],handler=def.events[name];if(!handler)return;if(frame.pending&&frame.actionEvent===name)return;frame.actionEvent=handler.body.some(step=>step.Assign&&__hasAction(step.Assign[1]))?name:null;
 __cancelActions(frame);const generation=++frame.generation,binding=frame.bindingGeneration;frame.controller?.abort();
 const ctx={controller:new AbortController(),epoch:__authEpoch,identity:false,calls:0,frame,settled:[]};frame.controller=ctx.controller;
 const deadline=setTimeout(()=>ctx.controller.abort(),Math.max(0,chain.deadline-Date.now()));frame.pending=true;frame.error='';
 const next=Object.assign(__dict(),frame.state),parameters=__dict(),emissions=[];ctx.next=next;
 try{
  __reset();if(args.length!==handler.parameters.length)throw Error('event argument count');
  handler.parameters.forEach(([name,type],i)=>{__validate(args[i],type);parameters[name]=args[i];});__render();
  for(const step of handler.body){
   if(!frame.active||frame.generation!==generation||ctx.epoch!==__authEpoch||ctx.controller.signal.aborted)return;
   const env=Object.assign(__dict(),__environment(frame,next),parameters);
   if(step.Assign){const[target,e]=step.Assign,type=def.state.find(([n])=>n===target)[1].ty;
    if(type.Action&&__hasAction(e)){frame.actionTargets.add(target);frame.state=Object.assign(__dict(),frame.state,{[target]:__action('ActionSubmitting')});__render();}
    next[target]=e.Pure?__eval(e.Pure.ast,env):e.Async?await __evalAsync(e.Async.expression.ast,env,e.Async.remotes,ctx):await __remote(e.Remote.operation,e.Remote.arguments.map(e=>__eval(e.ast,env)),e.Remote.types,e.Remote.result,ctx);__validate(next[target],type);
   }else if(step.Emit){const[name,expressions]=step.Emit,types=def.outputs[name];const values=expressions.map((e,i)=>{const value=__eval(e.ast,env);__validate(value,types[i][1]);return value;});emissions.push([name,values]);}
   else throw Error('invalid event step');
  }
  if(!frame.active||frame.generation!==generation||ctx.epoch!==__authEpoch)return;
  const previous=frame.state;frame.state=next;try{__render();}catch(error){frame.state=previous;throw error;}frame.actionTargets.clear();__ackActions(ctx);__error.hidden=true;
  if(emissions.length)queueMicrotask(()=>__deliverOutputs(frame,generation,binding,ctx.epoch,emissions,chain));
 }catch(error){if(frame.active&&frame.generation===generation&&ctx.epoch===__authEpoch){__cancelActions(frame);__fail(frame,__eventFailure(error,frame,name),ctx.calls);}}
 finally{clearTimeout(deadline);if(frame.active&&frame.generation===generation){frame.pending=false;frame.actionEvent=null;frame.controller=null;try{__render();}catch(error){__fail(frame,__eventFailure(error,frame,name),ctx.calls);}}}
}
let __currentPath=__locationURL();
function __changeRoute(path,push){const previous=__currentPath,previousState=history.state;
 if(!__isRoute(path)){history.replaceState(previousState,'',previous);__fail(null,Error('unknown route'));return;}
 if(__authBusy){if(!push)history.replaceState(previousState,'',previous);return;}
 // Render against the candidate path without creating a history entry until it succeeds.
 if(push)history.replaceState(previousState,'',path);__reset();for(const f of __frames.values()){__cancelActions(f);f.generation++;f.controller?.abort();f.pending=false;}
 try{__render();if(push){history.replaceState(previousState,'',previous);history.pushState(null,'',path);}__currentPath=path;__error.hidden=true;}
 catch(error){history.replaceState(previousState,'',previous);try{__render();}catch(recovery){__fail(null,recovery);}__fail(null,error);}}
function __navigate(path){if(__isRoute(path))__changeRoute(path,true);}
function __pop(){__changeRoute(__locationURL(),false);}
function __hide(event){if(event.persisted){__clearRecovery();__closeWatches();__bootstrapController?.abort();__alive=false;removeEventListener('popstate',__pop);for(const f of __frames.values()){__cancelActions(f);f.generation++;f.controller?.abort();f.pending=false;}}else __stop();}
function __show(event){if(event.persisted){__alive=true;addEventListener('popstate',__pop);__render();queueMicrotask(()=>__discoverRecovery());}}
function __stop(){__clearRecovery();__closeWatches();__bootstrapController?.abort();__alive=false;removeEventListener('popstate',__pop);removeEventListener('pagehide',__hide);removeEventListener('pageshow',__show);for(const f of __frames.values())__dispose(f);__frames.clear();}
function __start(){try{__reset();if(__root.dataset.contract!==__contract)throw Error('incompatible hydration contract');const tree=__tree();if(__root.childNodes.length!==tree.nodes.length||!tree.nodes.every((v,i)=>__matches(__root.childNodes[i],v)))throw Error('hydration content mismatch');tree.nodes.forEach((v,i)=>__adopt(__root.childNodes[i],v));__commit(tree);if(__hydrationEdited)__render();__root.dataset.ready='true';queueMicrotask(()=>__discoverRecovery());addEventListener('popstate',__pop);addEventListener('pagehide',__hide);addEventListener('pageshow',__show);}catch(error){__error.hidden=false;__error.textContent='Application version or initial content changed. Reload this page.';__root.dataset.ready='false';}}


// Watch nodes own one bounded last-value subscription and release it with their component.
const __watches=new Map();
function __closeWatches(){for(const w of __watches.values()){w.closed=true;clearTimeout(w.timer);w.socket?.close();}__watches.clear();}
function __syncWatches(desired){
 for(const[id,w]of __watches){const d=desired.get(id);if(!d||d.frame!==w.frame||d.parent!==w.parent||d.channel!==w.channel||w.epoch!==__authEpoch||__authBusy){w.closed=true;clearTimeout(w.timer);w.socket?.close();__watches.delete(id);}}
 if(__authBusy||!__alive)return;
 for(const[id,d]of desired){if(d.parent==='')continue;if(!/^[A-Za-z0-9_-]{1,128}$/.test(d.parent))throw Error('opaque Stream');let w=__watches.get(id);if(!w){w={...d,epoch:__authEpoch,closed:false,tries:0,pending:null,timer:null,socket:null};__watches.set(id,w);__openWatch(id,w);}if(w.pending&&!w.frame.pending)__applyWatch(w);}
}
function __applyWatch(w){if(w.closed||!w.frame.active||w.epoch!==__authEpoch||w.frame.pending||!w.pending)return;const message=w.pending;w.pending=null;try{const value=__unwire(message.value,w.payload),previous=w.frame.state;w.frame.state=Object.assign(__dict(),previous,{[w.into]:value,[w.cursor]:message.cursor});try{__render();}catch(error){w.frame.state=previous;throw error;}}catch(error){w.closed=true;w.socket?.close();__fail(w.frame,error);}}
function __openWatch(id,w){if(w.closed||!w.frame.active||w.epoch!==__authEpoch)return;let cursor=w.frame.state[w.cursor];if(!/^(0|[1-9][0-9]{0,18})$/.test(cursor)){w.closed=true;__fail(w.frame,Error('Invalid live event cursor.'));return;}
 const socket=new WebSocket(location.origin.replace(/^http/,'ws')+'/_diamond/events/'+w.channel+'/'+w.parent+'/'+cursor);w.socket=socket;
 socket.onmessage=event=>{if(w.closed||w.epoch!==__authEpoch||!w.frame.active||w.socket!==socket)return;try{if(typeof event.data!=='string'||event.data.length>65536)throw Error('Live event limit.');const message=JSON.parse(event.data);if(message.resync===true){w.closed=true;socket.close();__fail(w.frame,Error('Live history expired. Reload application data.'));return;}if(Object.keys(message).length!==2||!Object.hasOwn(message,'value')||typeof message.cursor!=='string'||!/^[1-9][0-9]{0,18}$/.test(message.cursor)||BigInt(message.cursor)<=BigInt(w.pending?.cursor||w.frame.state[w.cursor]))throw Error('Invalid live event.');w.pending=message;if(!w.frame.pending)__applyWatch(w);}catch(error){w.closed=true;socket.close();__fail(w.frame,error);}};
 socket.onclose=event=>{if(w.closed||!w.frame.active||w.epoch!==__authEpoch||__watches.get(id)!==w)return;if(event.code===4001||event.code===4003){w.closed=true;const error=Error(event.code===4001?'Session ended. Sign in again.':'Access changed. Reload private data.');error.diamondRemote=true;error.lostIdentity=event.code===4001;__forgetIdentity(error);return;}if(++w.tries>3){w.closed=true;__fail(w.frame,Error('Live connection closed. Check access and reload.'));return;}w.timer=setTimeout(()=>__openWatch(id,w),Math.min(5000,500*w.tries));};
 socket.onerror=()=>{};
}

__start();
