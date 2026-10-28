// Compiler-owned typed navigation, form input, and component/action messages.
const __actionEmpty=new Set(['ActionIdle','ActionSubmitting','ActionInvalid','ActionDenied','ActionAborted','ActionUnknown','ActionExpired','ActionConflict','ActionCorrupt']);
const __actionNames=new Set([...__actionEmpty,'ActionSucceeded','ActionRejected']);
function __action(tag,fields=[]){if(!__actionNames.has(tag)||fields.length!==((tag==='ActionSucceeded'||tag==='ActionRejected')?1:0))throw Error('action shape');return{$:'variant',name:'__diamond_action',tag,fields};}
function __actionTypes(tag,types){if(tag==='ActionSucceeded')return[types[0]];if(tag==='ActionRejected')return[types[1]];if(__actionEmpty.has(tag))return[];throw Error('action tag');}
function __validateAction(value,types,depth){
 if(!value||value.$!=='variant'||value.name!=='__diamond_action'||!Array.isArray(value.fields))throw Error('action type');
 const fields=__actionTypes(value.tag,types);if(value.fields.length!==fields.length)throw Error('action payload');
 fields.forEach((type,i)=>__validate(value.fields[i],type,depth+1));
}
function __unwireAction(value,types,depth){
 if(!value||Array.isArray(value)||typeof value!=='object'||Object.keys(value).length!==2||!Object.hasOwn(value,'tag')||!Array.isArray(value.fields)||['ActionIdle','ActionSubmitting'].includes(value.tag))throw Error('action wire shape');
 const fields=__actionTypes(value.tag,types);if(fields.length!==value.fields.length)throw Error('action wire payload');
 return __action(value.tag,fields.map((type,i)=>__unwire(value.fields[i],type,depth+1)));
}
async function __actionRemote(name,args,types,result,ctx){
 if(Object.hasOwn(__program.actions,name))return __durableAction(name,args,types,result,ctx);
 const active=()=>{if(ctx.epoch!==__authEpoch||ctx.controller.signal.aborted)throw Error('stale action');};
 const post=async(path,body)=>{
  active();const encoded=JSON.stringify(body);if(new TextEncoder().encode(encoded).length>16384){const error=Error('Action input exceeds the request limit.');error.status=400;error.diamondRemote=true;throw error;}
  const response=await __fetch(path,{method:'POST',credentials:'same-origin',cache:'no-store',signal:ctx.controller.signal,headers:{'Content-Type':'application/json','X-Diamond-CSRF':__csrf},body:encoded},ctx);
  const value=await response.json();active();if(!response.ok){const error=Error(value.error==='contract_mismatch'?'Application version changed. Reload this page.':'Action request rejected.');error.status=response.status;error.code=value.error;error.diamondRemote=true;error.lostIdentity=response.status===401;throw error;}return value;
 };
 try{
  if(++ctx.calls>32)throw Error('event remote-call budget');
  const arguments_=args.map((value,i)=>{__validate(value,types[i]);return __wireAny(value);});
  const path='/_diamond/actions/'+name,body={contract:__contract,arguments:arguments_};
  const prepared=await post(path+'/prepare',body);
  if(Object.keys(prepared).length!==1||typeof prepared.ticket!=='string'||!/^[0-9a-f]{64}$/.test(prepared.ticket))throw Error('action submission proof');
  const value=await post(path,{...body,ticket:prepared.ticket});return __unwire(value.value,result);
 }catch(error){
  if(ctx.epoch!==__authEpoch||ctx.controller.signal.aborted||error.code==='contract_mismatch')throw error;
  if(error.status===401){__forgetIdentity(error);throw error;}
  if(error.status===403)return __action('ActionDenied');
  if([400,413,415,422].includes(error.status))return __action('ActionInvalid');
  if(error.status===409&&error.code==='aborted')return __action('ActionAborted');
  if(error.status===503&&error.code==='overloaded')return __action('ActionAborted');
  return __action('ActionUnknown');
 }
}
function __isRoute(path){return __program.routes.includes(path)||__routeMatch(path)!==null;}
function __handle(frame,handler,scope,element,form){
 if(!frame.active||__authBusy)return;
 try{__reset();const environment=Object.assign(__dict(),scope);if(form){const decoded=__decodeForm(element,form),view=__meta.get(element);frame.formErrors.set(view.key,decoded.errors);__presentForm(element,view);if(!decoded.value){element.querySelector('[data-diamond-field][aria-invalid="true"]')?.focus();return;}environment[form.binding]=decoded.value;}
  const arguments_=handler.arguments.map(value=>__eval(value.ast,environment));__dispatch(frame,handler.name,arguments_);
 }catch(error){__fail(frame,error);}
}
function __sameOutputs(a,b){const names=Object.keys(a);return names.length===Object.keys(b).length&&names.every(name=>a[name].parent===b[name].parent&&a[name].handler===b[name].handler);}
function __cancelActions(frame){
 __cancelRecovery(frame);
 if(frame.actionTargets?.size){const state=Object.assign(__dict(),frame.state);for(const target of frame.actionTargets)state[target]=__action('ActionUnknown');frame.state=state;frame.actionTargets.clear();}
}
function __hasAction(value){return value.Remote?.operation.startsWith('action/')||value.Async&&Object.values(value.Async.remotes).some(__hasAction);}
async function __deliverOutputs(frame,generation,binding,epoch,emissions,chain){
 for(const[name,args]of emissions){
  if(--chain.remaining<0){__fail(frame,Error('component output budget'));break;}
  const link=frame.outputs[name];
  if(!link||!frame.active||frame.generation!==generation||frame.bindingGeneration!==binding||epoch!==__authEpoch||!link.parent.active||Date.now()>chain.deadline)continue;
  await __dispatch(link.parent,link.handler,args,chain);
 }
}
