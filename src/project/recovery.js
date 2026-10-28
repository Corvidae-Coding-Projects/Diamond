// Server-owned recovery: no browser storage, form body, or protected result cache.
const __recoveries=new Map(),__recoveryNodes=new Map(),__recoveryRequests=new Set();
const __recoveryRoot=document.getElementById('diamond-recovery');
const __recoveryMessages={ActionSucceeded:'Saved successfully. Reload the view to see the saved data.',ActionRejected:'Not saved: the application rejected this change.',ActionInvalid:'Not saved: input was rejected.',ActionDenied:'Access changed. The saved result is unavailable.',ActionAborted:'Not saved. The server confirmed that no change committed.',ActionUnknown:'The outcome is unknown. Check it before submitting again.',ActionExpired:'Recovery expired. Check the record before submitting again.',ActionConflict:'The application or request changed. Check the record before submitting again.',ActionCorrupt:'Recovery is unavailable. Check the record before submitting again.'};
function __paintRecovery(){
 if(!__recoveryRoot)return;__recoveryRoot.hidden=!__recoveries.size||__authBusy;
 for(const[id,node]of __recoveryNodes)if(!__recoveries.has(id)){node.root.remove();__recoveryNodes.delete(id);}
 for(const[id,meta]of __recoveries){let node=__recoveryNodes.get(id);if(!node){const root=document.createElement('section'),heading=document.createElement('h2'),status=document.createElement('p'),check=document.createElement('button'),dismiss=document.createElement('button');heading.textContent=__program.actions[meta.name].label;root.setAttribute('role','group');root.setAttribute('aria-label',heading.textContent);status.setAttribute('role','status');status.setAttribute('aria-live','polite');status.setAttribute('aria-atomic','true');check.type=dismiss.type='button';check.textContent='Check outcome';dismiss.textContent='Dismiss notice';check.onclick=()=>__recoverAction(meta);dismiss.onclick=()=>__dismissRecovery(meta);root.append(heading,status,check,dismiss);__recoveryRoot.append(root);node={root,status,check,dismiss};__recoveryNodes.set(id,node);}
  const message=meta.pending?'Saving changes…':__recoveryMessages[meta.tag||'ActionUnknown'];if(node.status.textContent!==message)node.status.textContent=message;node.check.disabled=meta.pending||meta.checking||__authBusy;node.dismiss.disabled=meta.pending||meta.checking||__authBusy;
 }
}
function __clearRecovery(){for(const controller of __recoveryRequests)controller.abort();__recoveryRequests.clear();__recoveries.clear();__paintRecovery();}
function __recoveryAdd(name,id,frame,pending){if(!Object.hasOwn(__program.actions,name)||!/^[0-9a-f]{64}$/.test(id))throw Error('invalid recovery reference');let meta=__recoveries.get(id);if(!meta){if(__recoveries.size>=16)throw Error('recovery reference capacity');meta={name,id,pending,checking:false,tag:'ActionUnknown',frame};__recoveries.set(id,meta);}else{meta.pending=pending;meta.frame=frame;}__paintRecovery();return meta;}
function __cancelRecovery(frame){for(const meta of __recoveries.values())if(meta.frame===frame){meta.frame=null;meta.pending=false;meta.tag='ActionUnknown';}__paintRecovery();}
async function __actionPost(path,body,ctx){
 if(ctx.epoch!==__authEpoch||ctx.controller.signal.aborted)throw Error('stale action');if(++ctx.calls>32)throw Error('event remote-call budget');const encoded=JSON.stringify(body);if(new TextEncoder().encode(encoded).length>16384)throw Error('action request limit');
 const response=await __fetch(path,{method:'POST',credentials:'same-origin',cache:'no-store',signal:ctx.controller.signal,headers:{'Content-Type':'application/json','X-Diamond-CSRF':__csrf},body:encoded},ctx);const value=await response.json();if(ctx.epoch!==__authEpoch||ctx.controller.signal.aborted)throw Error('stale action');
 if(!response.ok){const error=Error('Action request rejected.');error.status=response.status;error.code=value.error;error.diamondRemote=true;throw error;}return value;
}
async function __durableAction(name,args,types,result,ctx){
 let meta;const path='/_diamond/actions/'+name,body={contract:__contract,arguments:args.map((value,i)=>{__validate(value,types[i]);return __wireAny(value);})};
 if([...__recoveries.values()].some(m=>m.name===name&&!m.pending)){__paintRecovery();return __action('ActionUnknown');}
 try{
  const prepared=await __actionPost(path+'/prepare',body,ctx);if(prepared.value)return __unwire(prepared.value,result);
  if(Object.keys(prepared).length!==1)throw Error('invalid invocation reference');meta=__recoveryAdd(name,prepared.invocation,ctx.frame,true);ctx.frame.recoveryIds??=new Set();ctx.frame.recoveryIds.add(meta.id);
  const value=await __actionPost(path,{...body,invocation:meta.id},ctx);const outcome=__unwire(value.value,result);meta.pending=false;meta.tag=outcome.tag;ctx.settled.push(meta);__paintRecovery();return outcome;
 }catch(error){
  if(meta){meta.pending=false;meta.tag='ActionUnknown';__paintRecovery();}
  if(ctx.epoch!==__authEpoch||ctx.controller.signal.aborted)throw error;
  if(error.status===401){__forgetIdentity(error);throw error;}
  if(error.code==='contract_mismatch'){if(meta)meta.tag='ActionConflict';__paintRecovery();return __action('ActionConflict');}
  if(meta){try{const value=await __actionPost(path+'/status',{contract:__contract,invocation:meta.id},ctx),outcome=__unwire(value.value,result);meta.tag=outcome.tag;ctx.settled.push(meta);__paintRecovery();return outcome;}catch(failure){if(failure.status===403){meta.tag='ActionDenied';__paintRecovery();return __action('ActionDenied');}}return __action('ActionUnknown');}
  // A prepare reply can itself be lost. Discovery finds its server-owned ID;
  // never manufacture another invocation to compensate for a transport failure.
  queueMicrotask(()=>__discoverRecovery());
  if(error.status===403)return __action('ActionDenied');if([400,413,415,422].includes(error.status))return __action('ActionInvalid');return __action('ActionUnknown');
 }
}
async function __recoveryTask(work){
 if(__authBusy||!__alive)return;const ctx={controller:new AbortController(),epoch:__authEpoch,calls:0};__recoveryRequests.add(ctx.controller);const timer=setTimeout(()=>ctx.controller.abort(),__runtime.event_timeout_ms);
 try{await work(ctx);}catch(error){if(ctx.epoch===__authEpoch&&!ctx.controller.signal.aborted&&error.status===401)__forgetIdentity(error);}finally{clearTimeout(timer);__recoveryRequests.delete(ctx.controller);}
}
async function __discoverRecovery(){
 if(!Object.keys(__program.actions).length)return;
 await __recoveryTask(async ctx=>{const user=await __remote('session/user',[],[],'Text',ctx);if(!user){__clearRecovery();return;}const value=await __actionPost('/_diamond/recovery',{contract:__contract},ctx);if(!Array.isArray(value.references)||value.references.length>16)throw Error('recovery reference shape');for(const reference of value.references)if(Object.hasOwn(__program.actions,reference.action)&&!__recoveries.has(reference.invocation))__recoveryAdd(reference.action,reference.invocation,null,false);});
}
async function __recoverAction(meta){
 if(__recoveries.get(meta.id)!==meta||meta.checking||meta.pending)return;meta.checking=true;__paintRecovery();
 await __recoveryTask(async ctx=>{try{const value=await __actionPost('/_diamond/actions/'+meta.name+'/status',{contract:__contract,invocation:meta.id},ctx);meta.tag=__unwire(value.value,__program.actions[meta.name].result).tag;}catch(error){meta.tag=error.status===403?'ActionDenied':error.code==='contract_mismatch'?'ActionConflict':'ActionUnknown';throw error;}finally{if(ctx.epoch===__authEpoch){meta.checking=false;__paintRecovery();}}});
}
async function __dismissRecovery(meta){
 await __recoveryTask(async ctx=>{await __actionPost('/_diamond/actions/'+meta.name+'/ack',{contract:__contract,invocation:meta.id},ctx);if(__recoveries.get(meta.id)===meta)__recoveries.delete(meta.id);__paintRecovery();});
}
function __ackActions(ctx){for(const meta of ctx.settled){meta.frame=null;if(!['ActionUnknown','ActionExpired','ActionConflict','ActionCorrupt'].includes(meta.tag))void __dismissRecovery(meta);}ctx.settled.length=0;}
