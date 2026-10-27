// Finite checked input metadata. Field failures are fixed codes, never raw data.
function __fieldFailure(code){throw{diamondField:true,code};}
function __decodeScalar(codec,raw){
 if(typeof raw!=='string'||new TextEncoder().encode(raw).length>16384)__fieldFailure('TooLong');__text(raw);
 if(codec.kind==='Optional')return raw===''?{$:'None'}:__tag('Some',__decodeScalar(codec.inner,raw));
 if(raw===''&&!(codec.kind==='Text'&&codec.min===0))__fieldFailure('Required');
 switch(codec.kind){
  case'Text':{const n=Array.from(raw).length;if(n<codec.min)__fieldFailure('TooShort');if(n>codec.max)__fieldFailure('TooLong');return raw;}
  case'Int':try{if(!/^(0|-?[1-9][0-9]*)$/.test(raw))__fieldFailure('InvalidInteger');return __int(BigInt(raw));}catch(_){__fieldFailure('InvalidInteger');}break;
  case'Bool':if(raw!=='true'&&raw!=='false')__fieldFailure('InvalidBoolean');return raw==='true';
  case'Id':if(!/^[-_A-Za-z0-9]{1,128}$/.test(raw))__fieldFailure('InvalidId');return{$:'id',name:codec.tag,value:raw};
  case'Choice':if(!Object.hasOwn(codec.choices,raw))__fieldFailure('InvalidChoice');return{$:'variant',name:codec.name,tag:codec.choices[raw],fields:[]};
  default:throw Error('input scalar schema');
 }
}
function __formatScalar(codec,value){
 if(codec.kind==='Optional')return value.$==='None'?'':__formatScalar(codec.inner,value.value);
 if(codec.kind==='Choice'){const name=Object.keys(codec.choices).find(key=>codec.choices[key]===value.tag);if(name===undefined)throw Error('input choice');return name;}
 const raw=__string(value);__decodeScalar(codec,raw);return raw;
}
function __encodeURL(raw){return encodeURIComponent(raw).replace(/[!'()*]/g,c=>'%'+c.charCodeAt(0).toString(16).toUpperCase());}
function __decodeURL(raw){if(raw.includes('+'))throw Error('noncanonical URL');const value=decodeURIComponent(raw);if(__encodeURL(value)!==raw)throw Error('noncanonical URL');return value;}
function __safeURLValue(raw,path){return(!path||raw!==''&&raw!=='.'&&raw!=='..')&&!/[\u0000-\u001f\u007f-\u009f/\\%#?]/u.test(raw);}
function __routeBuild(name,fields){
 const route=__program.schemas.routes[name];let result=route.segments.map(segment=>{if(Object.hasOwn(segment,'Literal'))return'/'+segment.Literal;const[name,field]=segment.Parameter,raw=__formatScalar(field.codec,fields[name]);if(!__safeURLValue(raw,true))throw Error('invalid path field');return'/'+__encodeURL(raw);}).join('');
 let separator='?';for(const[name,field]of Object.entries(route.query)){const value=fields[name];if(value?.$==='None')continue;const raw=__formatScalar(field.codec,value);if(!__safeURLValue(raw,false)||field.codec.kind==='Optional'&&raw==='')throw Error('invalid query field');if(field.default!==null&&raw===field.default)continue;result+=separator+name+'='+__encodeURL(raw);separator='&';}
 if(new TextEncoder().encode(result).length>2048)throw Error('typed URL budget');return result;
}
function __routeMatch(target){
 if(typeof target!=='string'||!target.startsWith('/')||new TextEncoder().encode(target).length>2048)return null;
 const mark=target.indexOf('?'),path=mark<0?target:target.slice(0,mark),query=mark<0?null:target.slice(mark+1),parts=path.slice(1).split('/');
 for(const[name,route]of Object.entries(__program.schemas.routes))try{
  if(parts.length!==route.segments.length)continue;const fields=__dict();
  route.segments.forEach((segment,i)=>{if(Object.hasOwn(segment,'Literal')){if(segment.Literal!==parts[i])throw Error('route literal');}else{const[key,field]=segment.Parameter,raw=__decodeURL(parts[i]);if(!__safeURLValue(raw,true))throw Error('route path');fields[key]=__decodeScalar(field.codec,raw);}});
  const supplied=__dict();if(query!==null)for(const pair of query.split('&')){const at=pair.indexOf('=');if(at<0)throw Error('query pair');const key=__decodeURL(pair.slice(0,at)),raw=__decodeURL(pair.slice(at+1));if(!Object.hasOwn(route.query,key)||Object.hasOwn(supplied,key))throw Error('query key');supplied[key]=raw;}
  for(const[key,field]of Object.entries(route.query)){let raw;if(Object.hasOwn(supplied,key)){raw=supplied[key];if(!__safeURLValue(raw,false)||field.codec.kind==='Optional'&&raw==='')throw Error('query value');}else if(field.default!==null)raw=field.default;else if(field.codec.kind==='Optional')raw='';else throw Error('required query');fields[key]=__decodeScalar(field.codec,raw);}
  if(__routeBuild(name,fields)!==target)throw Error('noncanonical route');return{name,value:{$:'record',name,fields}};
 }catch(_){}return null;
}
function __routeUrl(value){if(!value||value.$!=='record'||!Object.hasOwn(__program.schemas.routes,value.name))throw Error('typed route required');__validate(value,{Record:value.name});return __routeBuild(value.name,value.fields);}
function __locationURL(){return location.pathname+location.search;}
const __fieldMessages={Required:'Enter a value.',TooShort:'Value is too short.',TooLong:'Value is too long.',InvalidInteger:'Enter a canonical 64-bit integer.',InvalidBoolean:'Choose true or false.',InvalidId:'Enter a valid identifier.',InvalidChoice:'Choose an allowed value.'};
function __decodeForm(element,form){
 const schema=__program.schemas.forms[form.schema],fields=__dict(),errors=__dict(),seen=new Set();
 for(const control of Array.from(element.elements)){
  if(!control.name)continue;
  if(control.disabled||!Object.hasOwn(schema,control.name)||seen.has(control.name)||!['INPUT','TEXTAREA','SELECT'].includes(control.tagName))throw Error('form field shape');seen.add(control.name);
  try{const field=schema[control.name],raw=control.type==='checkbox'?String(control.checked):control.value;fields[control.name]=__decodeScalar(field.codec,raw);}catch(error){if(!error.diamondField)throw error;errors[control.name]=error.code;}
 }
 if(seen.size!==Object.keys(schema).length)throw Error('missing form control');
 if(Object.keys(errors).length)return{errors};const value={$:'record',name:form.schema,fields};__validate(value,{Record:form.schema});return{value,errors};
}
function __presentForm(element,v){
 if(!v.form)return;element.noValidate=true;const errors=v.frame.formErrors.get(v.key)||__dict();
 for(const control of element.querySelectorAll('[data-diamond-field]')){const name=control.getAttribute('data-diamond-field'),error=errors[name],span=Array.from(element.querySelectorAll('[data-diamond-field-error]')).find(el=>el.getAttribute('data-diamond-field-error')===name);if(!span)throw Error('form error ownership');
  const id=control.dataset.diamondManaged==='true'?span.id:'diamond-field-'+__encodeURL(v.key)+'-'+name;span.id=id;const message=error?__fieldMessages[error]:'';if(span.textContent!==message)span.textContent=message;span.dataset.code=error||'';
  const source=__meta.get(control)?.attrs['aria-describedby']||'';control.setAttribute('aria-describedby',[...new Set((source+' '+id).trim().split(/\s+/))].join(' '));control.setAttribute('aria-invalid',error?'true':'false');
 }
}
function __boundedDraft(raw){let bytes=0,result='';for(const scalar of raw){const point=scalar.codePointAt(0),size=point<128?1:point<2048?2:point<65536?3:4;if(bytes+size>16384)break;bytes+=size;result+=scalar;}return result;}
