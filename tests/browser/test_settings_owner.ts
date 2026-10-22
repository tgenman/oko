// Rule matrices use the real owner/SDK without a DOM, browser or HTTP server.
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {MenusOwner} from '../../frontend/src/settings/owner';
import {createReaderApi} from '../../frontend/src/api/reader';
import type {CommandContext,EffectiveSettingsSnapshot,EffectiveSettingsValues,RuntimeStatus,SettingsPatch} from '../../frontend/src/api/generated/types.gen';

function deferred(){let resolve!:()=>void;const promise=new Promise<void>(ready=>{resolve=ready;});return {promise,resolve};}
const status:RuntimeStatus={api_version:'3.0.0',runtime_state:'running',work_allowed:true,active_client_sessions:1,lifetime:{kind:'explicit'},initial_histories:[],sources:[],ready_sources:0,total_sources:0,work:[{phase:'idle'}],viewed_ids:[],telegram_reads:[],diagnostic_count:0,diagnostic_persistence_error:null,problems:{current:[],recent:[],count:0,updated_at:'2026-10-22T00:00:00Z'},youtube:{mode:'direct',address:'',has_password:false,has_username:false,admission:'ready',retry_at:null,strikes:0,reason:null}};
function harness(fail=false){
 const patches=new Map<string,SettingsPatch>(),requests:{path:string;body:SettingsPatch}[]=[],contexts:CommandContext[]=[],discarded:string[]=[];
 const sent=deferred(),release=deferred();
 function snapshot(path:string):EffectiveSettingsSnapshot{
  const patch=patches.get(path)||{},origin={scope:'default'} as const;
  const effective:EffectiveSettingsValues={originals:{value:patch.originals??true,origin},theses:{value:patch.theses??true,origin},processor:{value:patch.processor??'codex',origin},prompt_filters:{value:patch.prompt_filters??'',origin},prompt_theses:{value:patch.prompt_theses??'Saved prompt',origin}};
  const owner=path.split('/')[1]!;
  if(path.startsWith('oko/'))return {scope:'oko',owner:'global',effective,overrides:{}};
  if(path.startsWith('folder/'))return {scope:'folder',owner,effective,overrides:{}};
  return {scope:'feed',owner,effective,overrides:{},feed:{id:owner,title:owner,identity:owner,kind:'rss',transport:'rss',connection:'ready',folder_id:'folder',importance:'normal',initial_days:1,original_urls:['https://source.test/feed'],public_ai:false,history:{state:'absent',legacy_since:null}}};
 }
 const api=createReaderApi({token:'synthetic',origin:'http://oko.test',fetch:async(url,options)=>{
  const path=String(url),method=options?.method||'GET';let body:unknown;
  if(path==='/api/v3/status')body=status;
  else if(path==='/api/v3/settings/feed-refresh')body={value:patches.get('oko/global')?.feed_refresh_minutes??15,origin:{scope:'default'}};
  else if(path==='/api/v3/settings/telegram-http')body={value:patches.get('oko/global')?.telegram_http_rps??2,origin:{scope:'default'}};
  else if(path.startsWith('/api/v3/settings/')){
   const target=path.slice('/api/v3/settings/'.length);
   if(method==='PATCH'){
    const patch:SettingsPatch=JSON.parse(String(options?.body));requests.push({path,body:patch});sent.resolve();await release.promise;
    if(fail)return new Response(JSON.stringify({code:'action_invalid',message:'Synthetic failure',details:{reason:'invalid_state'},diagnostic_id:null,command_id:new Headers(options?.headers).get('X-Oko-Action')}),{status:400,headers:{'content-type':'application/json'}});
    patches.set(target,{...patches.get(target),...patch});
   }
   body=snapshot(target);
  }else throw new Error('Unexpected request '+method+' '+path);
  return new Response(JSON.stringify(body),{headers:{'content-type':'application/json'}});
 }});
 api.setActionTracker({beginCommand(context){contexts.push(context);return {id:'a'.repeat(32)};},succeeded(){},failed(){}});
 const owner=new MenusOwner(api,()=>{},async(path,field)=>{discarded.push(path+'/'+field);});
 return {owner,requests,contexts,sent:sent.promise,release:release.resolve,discarded};
}
const fields:{field:keyof SettingsPatch;value:string;body:SettingsPatch;global?:boolean}[]=[
 {field:'originals',value:'false',body:{originals:false}},
 {field:'theses',value:'false',body:{theses:false}},
 {field:'processor',value:'llamacpp',body:{processor:'llamacpp'}},
 {field:'prompt_filters',value:'',body:{prompt_filters:''}},
 {field:'prompt_theses',value:'New prompt',body:{prompt_theses:'New prompt'}},
 {field:'feed_refresh_minutes',value:'30',body:{feed_refresh_minutes:30},global:true},
 {field:'telegram_http_rps',value:'0.5',body:{telegram_http_rps:.5},global:true},
];
for(const row of fields)for(const path of row.global?['oko/global']:['oko/global','folder/folder','feed/feed'])for(const reset of [false,true])for(const edit of ['unchanged','new','ABA']){
 test(`${path} ${row.field} ${reset?'reset':'save'} preserves ${edit} and adjacent drafts`,async()=>{
  const h=harness(),{owner}=h;await owner.select(path);
  const key='setting-'+row.field,adjacent=row.field==='prompt_theses'?'setting-prompt_filters':'setting-prompt_theses';
  owner.edit(path,key,row.value);owner.edit(path,adjacent,'Adjacent draft');
  const body:SettingsPatch=reset?{[row.field]:null}:row.body;
  const saving=owner.update(row.field,body,row.value);await h.sent;
  if(edit!=='unchanged')owner.edit(path,key,'Newer input');
  if(edit==='ABA')owner.edit(path,key,row.value);
  h.release();await saving;
  assert.deepEqual(h.requests,[{path:'/api/v3/settings/'+path,body}]);
  const [scope,identity]=path.split('/');
  assert.deepEqual(h.contexts,[{operation:'settings_patch',target:{scope,owner:identity},keys:row.global?[]:[row.field]}]);
  assert.equal(Object.hasOwn(owner.draft(path),key),edit!=='unchanged');
  if(edit!=='unchanged')assert.equal(owner.draft(path)[key],edit==='ABA'?row.value:'Newer input');
  assert.equal(owner.draft(path)[adjacent],'Adjacent draft');
  assert.equal(owner.settingsMessage,'Setting saved');
 });
}
for(const fail of [false,true])for(const next of ['oko/global','folder/other'])test(`late ${fail?'failure':'success'} cannot report into a new context ${next}`,async()=>{
 const h=harness(fail),{owner}=h;await owner.select('oko/global');owner.edit('oko/global','setting-prompt_theses','Sent');
 const saving=owner.update('prompt_theses',{prompt_theses:'Sent'},'Sent');await h.sent;
 await owner.select(next);const currentMessage=owner.settingsMessage;h.release();await saving;
 assert.equal(owner.currentPath,next);assert.equal(owner.settingsMessage,currentMessage);
 assert.equal(Object.hasOwn(owner.draft('oko/global'),'setting-prompt_theses'),fail);
 assert.deepEqual(owner.draft('folder/other'),{});
});
for(const path of ['oko/global','folder/folder','feed/feed'])test(`failed save and discard keep adjacent drafts and command identity in ${path}`,async()=>{
 const h=harness(true),{owner}=h;await owner.select(path);
 owner.edit(path,'setting-prompt_theses','Sent');owner.edit(path,'setting-prompt_filters','Adjacent');
 const saving=owner.update('prompt_theses',{prompt_theses:'Sent'},'Sent');await h.sent;h.release();await saving;
 assert.equal(owner.settingsMessage,'Synthetic failure');assert.equal(owner.draft(path)['setting-prompt_theses'],'Sent');
 await owner.discardField(path,'prompt_theses');
 assert.deepEqual(h.discarded,['settings/'+path+'/prompt_theses']);
 assert.deepEqual(owner.draft(path),{'setting-prompt_filters':'Adjacent'});
});
