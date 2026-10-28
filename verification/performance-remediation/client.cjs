// Run the generated transport helper against a real Diamond endpoint.
const fs=require('node:fs');
const assert=require('node:assert/strict');
const [artifact,base,mode]=process.argv.slice(2);
const emitted=fs.readFileSync(artifact,'utf8');
const helper=emitted.slice(emitted.indexOf('function __backoff('),emitted.indexOf('async function __remote('));
assert(helper.includes('async function __fetch('));
const runtime=emitted.match(/^const __runtime=.*;$/m)?.[0];
assert(runtime&&!runtime.includes('tenants'));
const transport=new Function('let __authEpoch=0;'+runtime+helper+';return {run:__fetch,change:()=>__authEpoch++};')();
const controller=new AbortController(),ctx={controller,epoch:0,calls:0};
const original=globalThis.fetch;
let calls=0;
globalThis.fetch=async(...args)=>{
 const response=await original(...args);calls++;
 console.log(JSON.stringify({status:response.status,attempt:calls}));
 if(calls===1&&mode==='cancel')setTimeout(()=>controller.abort(),30);
 if(calls===1&&mode==='epoch')setTimeout(()=>transport.change(),30);
 return response;
};
async function main(){
 let config=JSON.parse(fs.readFileSync(0,'utf8'));
 const started=Date.now();
 try{
  const response=await transport.run(base+config.path,{method:config.method,headers:config.headers,body:config.body,signal:controller.signal},ctx);
  await response.arrayBuffer();
  if(mode==='overload'){assert.equal(response.status,200);assert.equal(calls,2);assert(Date.now()-started>=1000);}
  else if(mode==='generic'){assert.equal(response.status,503);assert.equal(calls,1);}
  else if(mode==='budget'){assert.equal(response.status,503);assert.equal(calls,3);assert(Date.now()-started>=3000);}
  else if(mode==='configured'){
   assert.equal(response.status,503);assert.equal(calls,2);
   assert(Date.now()-started>=2000);
   assert(runtime.includes('"retries":1')&&runtime.includes('"jitter_ms":0'));
  }
  else if(mode==='disabled'){assert.equal(response.status,503);assert.equal(calls,1);}
  else assert.fail('stale transport should reject');
 }catch(error){
  if(!['cancel','epoch'].includes(mode))throw error;
  assert.equal(calls,1);
 }
 console.log(JSON.stringify({mode,calls,elapsed_ms:Date.now()-started,status:'PASS'}));
}
main().catch(error=>{console.error(error);process.exitCode=1;});
