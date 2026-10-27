// Native controls share their identity, codec and names with the server renderer.
function __accessibleName(raw){__text(raw);if(__builtin('text_trim',[raw])===''||Array.from(raw).length>256)throw Error('accessible name bounds');return raw;}
function __statusMessage(raw){__text(raw);if(Array.from(raw).length>512)throw Error('status text budget');return raw;}
function __fieldCost(control){let codec=control.definition.codec;if(codec.kind==='Optional')codec=codec.inner;return 12+2*(codec.kind==='Bool'?2:codec.kind==='Choice'?Object.keys(codec.choices).length:0);}
function __fieldNode(control,env,key,frame){
 const definition=control.definition,codec=definition.codec.kind==='Optional'?definition.codec.inner:definition.codec,id='diamond-control-'+__encodeURL(key),label=__accessibleName(__eval(control.label.ast,env)),description=control.description?__eval(control.description.ast,env):'',value=env[control.binding];
 const text=(value,key)=>({text:value,key}),node=(tag,attrs,children,key)=>({tag,attrs,children,key});
 const attrs={id,name:control.name,'data-diamond-field':control.name,'data-diamond-managed':'true','aria-describedby':(description?id+'-description ':'')+id+'-error','aria-invalid':'false'};
 let input;
 if(control.checked){attrs.type='checkbox';attrs.checked=value;input=node('input',attrs,[],key+'/control');input.checked=control.binding;}
 else if(['Bool','Choice'].includes(codec.kind)){
  const choices=codec.kind==='Bool'?['false','true']:Object.keys(codec.choices).sort();if(definition.codec.kind==='Optional')choices.unshift('');if(!choices.includes(value))throw Error('select draft has no allowed option');attrs.value=value;
  input=node('select',attrs,choices.map((choice,i)=>node('option',{value:choice,selected:choice===value},[text(choice||'Not set',key+'/option/'+i+'/text')],key+'/option/'+i)),key+'/control');input.binding=control.binding;
 }else{attrs.maxlength=String(codec.kind==='Text'?Math.min(codec.max*2,8192):128);attrs.value=value;if(!control.multiline)attrs.type='text';input=node(control.multiline?'textarea':'input',attrs,control.multiline&&value!==''?[text(value,key+'/value')]:[],key+'/control');input.binding=control.binding;}
 input.frame=frame;
 const children=[node('label',{'for':id},[text(label,key+'/label/text')],key+'/label'),input];
 if(description)children.push(node('span',{id:id+'-description'},[text(description,key+'/description/text')],key+'/description'));
 children.push(node('span',{id:id+'-error','data-diamond-field-error':control.name,role:'alert','aria-atomic':'true'},[],key+'/error'));
 return node('div',{class:'diamond-field'},children,key);
}
function __submitNode(expression,env,key,frame){return{tag:'button',attrs:{type:'submit',disabled:frame.pending},children:[{text:__accessibleName(__eval(expression.ast,env)),key:key+'/text'}],key};}
function __statusNode(expression,env,key){const value=__statusMessage(__eval(expression.ast,env));return{tag:'p',attrs:{role:'status','aria-live':'polite','aria-atomic':'true'},children:value===''?[]:[{text:value,key:key+'/text'}],key};}
// Preserve the actual surviving control. A removed or replaced editor never inherits focus.
function __focusSnapshot(){const element=document.activeElement;if(!element||!__root.contains(element))return null;return{element,start:element.selectionStart,end:element.selectionEnd,direction:element.selectionDirection};}
function __restoreFocus(saved){if(!saved||!saved.element.isConnected||!__root.contains(saved.element))return;const el=saved.element;if(document.activeElement!==el)el.focus({preventScroll:true});if(typeof saved.start==='number'&&typeof el.setSelectionRange==='function'&&(el.selectionStart!==saved.start||el.selectionEnd!==saved.end||el.selectionDirection!==saved.direction))el.setSelectionRange(saved.start,saved.end,saved.direction);}
function __beginIdentity(ctx){
 __clearRecovery();
 __clearSelectedFiles();__closeWatches();__bootstrapController?.abort();__bootstrap=null;
 const frame=ctx.frame;for(const other of __frames.values())if(other!==frame)__dispose(other);
 const fresh=__initial(frame.name,frame.props,frame.id).state;for(const key of Object.keys(ctx.next))delete ctx.next[key];Object.assign(ctx.next,fresh);
 frame.state=Object.assign(__dict(),fresh);frame.formErrors.clear();frame.actionTargets.clear();__frames=new Map([[frame.id,frame]]);__render();
}
