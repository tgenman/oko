import {useState} from 'react';
import * as sdk from '../api/generated/sdk.gen';
import type {TelegramAccess,TelegramProfiles as Profiles,TelegramRole,ExistingTelegramFeed,SubscriptionFeed} from '../api/generated/types.gen';
import {ReaderError} from '../api/errors';
import {MenusOwner,message} from './owner';
import {useOwner,Page} from './editor';
import {initial,selection,HistoryFields,FolderPicker,validFolder} from './connections';

export const roleLabel=(role:TelegramRole)=>role==='personal'?'Personal Account':'Collection Account';
export function accessLabel(access:TelegramAccess):string {
 switch(access.kind){
 case 'web':return 'Web Access · @'+access.username;
 case 'client':return roleLabel(access.role)+' · '+access.account_user_id;
 case 'unconfigured':return 'Connection required · '+access.source_locator;
 }
}
function duplicateOwners(error:unknown):ExistingTelegramFeed[] {
 if(!(error instanceof ReaderError)||error.code!=='duplicate_source')return [];
 const details=error.detail;
 if(!details||typeof details!=='object'||!('existing_feeds'in details)||!Array.isArray(details.existing_feeds))return [];
 const result:ExistingTelegramFeed[]=[];
 for(const candidate of details.existing_feeds){
  const row:unknown=candidate;
  if(!row||typeof row!=='object'||!('feed_id'in row)||typeof row.feed_id!=='string'||!('title'in row)||typeof row.title!=='string'||!('access'in row))return [];
  const a:unknown=row.access;
  if(!a||typeof a!=='object'||!('kind'in a))return [];
  let access:TelegramAccess;
  if(a.kind==='web'&&'username'in a&&typeof a.username==='string'&&'preview_url'in a&&typeof a.preview_url==='string')access={kind:'web',username:a.username,preview_url:a.preview_url};
  else if(a.kind==='client'&&'role'in a&&(a.role==='personal'||a.role==='collection')&&'account_user_id'in a&&typeof a.account_user_id==='string'&&'peer_id'in a&&typeof a.peer_id==='string')access={kind:'client',role:a.role,account_user_id:a.account_user_id,peer_id:a.peer_id};
  else if(a.kind==='unconfigured'&&'source_locator'in a&&typeof a.source_locator==='string')access={kind:'unconfigured',source_locator:a.source_locator};
  else return [];
  result.push({feed_id:row.feed_id,title:row.title,access});
 }
 return result;
}
class TelegramOwner {
 access:'web'|TelegramRole='web';peer='';folder='';newFolder='';auto=false;history=initial();status='';busy=false;profiles:Profiles|null=null;duplicates:ExistingTelegramFeed[]=[];
 constructor(readonly owner:MenusOwner){owner.onAddOpen.push(()=>{if(owner.tab==='telegram')void this.load();});owner.onTabChange.push(tab=>{if(tab==='telegram')void this.load();});}
 change(){this.owner.changed();}
 async load(){const context=this.owner.menuOperations.capture();try{const {data}=await sdk.telegramProfiles({client:this.owner.api.client});context.commit(()=>{this.profiles=data;this.change();});}catch(error){context.commit(()=>{if(this.access!=='web')this.status=message(error);this.change();});}}
 async connect(){if(this.busy||!this.owner.workAllowed||this.owner.stopped)return;const history=selection(this.history,'telegram');if(!history||!validFolder('telegram',this.folder,this.newFolder))return;
  const context=this.owner.menuOperations.capture(),sent={access:this.access,peer:this.peer,folder:this.folder,newFolder:this.newFolder,auto:this.auto,history:JSON.stringify(this.history)};
  this.busy=true;this.status='Connecting…';this.change();
  try{const body={source_locator:this.peer.trim(),folder:(this.folder||this.newFolder).trim(),initial_history:history};const {data}=await this.owner.api.command({operation:'telegram_connect'},headers=>sdk.telegramConnect({headers,client:this.owner.api.client,body:this.access==='web'?{...body,access:{kind:'web'}}:{...body,access:{kind:'client',profile:this.access},auto_read:this.auto}}));
   if(this.peer===sent.peer)this.peer='';if(this.newFolder===sent.newFolder)this.newFolder='';if(this.folder===sent.folder)this.folder='';if(JSON.stringify(this.history)===sent.history)this.history=initial();if(this.auto===sent.auto)this.auto=false;
   if(!this.owner.workAllowed||this.owner.stopped)return;this.owner.onChanged();await this.owner.refresh();
   if(context.current){this.status='Source connected';await context.commit(()=>this.owner.open('feeds',null,'feed/'+data.feed_id));}
  }catch(error){context.commit(()=>{this.status=message(error);this.duplicates=duplicateOwners(error);});}finally{this.busy=false;if(!context.current&&this.status==='Connecting…')this.status='';this.change();}
 }
}
export function Telegram({owner}:{owner:MenusOwner}){
 useOwner(owner);const[state]=useState(()=>new TelegramOwner(owner));const edit=(effect:()=>void)=>{effect();state.status='';state.change();};const disabled=owner.stopped||state.busy;
 const available=state.access==='web'||state.profiles?.[state.access].state==='ready';
 return <div role="tabpanel" aria-labelledby="add-telegram-tab" id="telegram-connection" hidden={owner.tab!=='telegram'}>
 <p>The entire group is collected. Channel access can be changed explicitly in feed settings. Web access does not mark Telegram messages as read.</p>
 <label>Access Method<select id="telegram-access" disabled={disabled} value={state.access} onChange={e=>edit(()=>{const value=e.target.value;state.access=value==='personal'||value==='collection'?value:'web';state.auto=false;})}><option value="web">Web Access</option><option value="personal">Personal Account</option><option value="collection">Collection Account</option></select></label>
 {state.access!=='web'&&<p>{available?'Profile verified':'First configure and verify this profile in Oko Settings → Telegram.'}</p>}
 <form onSubmit={e=>{e.preventDefault();void state.connect();}}>
 <label>Source Address<input id="telegram-peer" required maxLength={4000} placeholder="@channel or https://t.me/channel" disabled={disabled} value={state.peer} onChange={e=>edit(()=>{state.peer=e.target.value;})}/></label>
 <FolderPicker owner={owner} prefix="telegram" value={state.folder} newValue={state.newFolder} disabled={disabled} onChange={value=>edit(()=>{state.folder=value;})} onNew={value=>edit(()=>{state.newFolder=value;})}/>
 <HistoryFields prefix="telegram" value={state.history} disabled={disabled} onChange={value=>edit(()=>{state.history=value;})}/>
 <label hidden={state.access==='web'}><input id="telegram-auto" type="checkbox" disabled={disabled||state.access==='web'} checked={state.auto} onChange={e=>edit(()=>{state.auto=e.target.checked;})}/>Mark as read after fully saving</label>
 <button id="telegram-add" disabled={disabled||!available||!state.peer.trim()||!(state.folder||state.newFolder).trim()}>{state.busy?'Connecting…':'Connect Source'}</button>
 <p id="telegram-message" role="status">{state.status}</p></form>
 {state.duplicates.length>0&&<dialog id="telegram-duplicate" aria-label="Source already added" ref={node=>{if(node&&!node.open)node.showModal();}} onCancel={()=>{state.duplicates=[];state.change();}}><h2>Source already added</h2>{state.duplicates.map(feed=><p key={feed.feed_id}>{feed.title}: {accessLabel(feed.access)}</p>)}<p>Open feed settings to change channel access while keeping saved publications and settings.</p><button id="telegram-duplicate-close" onClick={()=>{state.duplicates=[];state.change();}}>Close</button></dialog>}
 </div>;
}
export function TelegramAccessEditor({owner,feed}:{owner:MenusOwner;feed:SubscriptionFeed}){
 const current=feed.kind==='telegram'&&feed.access.kind==='client'?feed.access.role:'web';
 const original=feed.kind==='telegram'&&feed.access.kind==='web'?'@'+feed.access.username:feed.original_urls[0]||'';
 const [access,setAccess]=useState<'web'|TelegramRole>(current),[source,setSource]=useState(original),[busy,setBusy]=useState(false),[status,setStatus]=useState('');
 const change=async()=>{
  if(busy||!owner.workAllowed||owner.stopped)return;
  const context=owner.operations.capture();setBusy(true);setStatus('Checking source identity…');
  try{
   const body={feed_id:feed.id,source_locator:source.trim(),folder:owner.subscriptions.folders.find(f=>f.id===feed.folder_id)?.title||'Inbox'};
   await owner.api.command({operation:'telegram_connect'},headers=>sdk.telegramConnect({headers,client:owner.api.client,body:access==='web'?{...body,access:{kind:'web'}}:{...body,access:{kind:'client',profile:access}}}));
   owner.needsRefresh.add('feed/'+feed.id);await owner.refresh();
   await context.commit(()=>owner.loadSettings());context.commit(()=>setStatus('Access changed. Saved publications and settings kept.'));
  }catch(error){context.commit(()=>setStatus(message(error)));}finally{setBusy(false);}
 };
 return <fieldset disabled={busy||!owner.workAllowed||owner.stopped}><legend>Change Channel Access</legend><p>Oko verifies the same source before switching. Saved publications and settings are kept.</p>
 <label>Access Method<select id="feed-telegram-access" value={access} onChange={e=>{setAccess(e.target.value==='personal'||e.target.value==='collection'?e.target.value:'web');setStatus('');}}><option value="web">Web Access</option><option value="personal">Personal Account</option><option value="collection">Collection Account</option></select></label>
 <label>Source Address<input id="feed-telegram-source" value={source} onChange={e=>{setSource(e.target.value);setStatus('');}} required maxLength={4000}/></label>
 <button id="feed-telegram-change" disabled={!source.trim()||access===current&&source===original} onClick={()=>void change()}>Change Access</button><p id="feed-telegram-status" role="status">{status}</p></fieldset>;
}
class ProfilesOwner {
 data:Profiles|null=null;choices:Partial<Record<TelegramRole,string>>={};busy=new Set<TelegramRole>();status='';
 constructor(readonly owner:MenusOwner){owner.onPageChange.push(page=>{if(page==='telegram-profiles')void this.load();});}
 change(){this.owner.changed();}
 async load(){const action=this.owner.operations.begin('telegram-profiles',{abortable:true});try{const{data}=await sdk.telegramProfiles({client:this.owner.api.client,signal:action.signal});action.commit(()=>{this.data=data;this.change();});}catch(error){action.commit(()=>{this.status=message(error);this.change();});}}
 async save(role:TelegramRole){const profile=this.data?.[role];const choice=this.choices[role]??(profile&&'configuration_ref'in profile?profile.configuration_ref:'');if(!choice||this.busy.has(role)||!this.owner.workAllowed||this.owner.stopped)return;const context=this.owner.operations.capture();this.busy.add(role);this.status='Checking account…';this.change();try{await this.owner.api.command({operation:'telegram_profile_save',role},headers=>sdk.telegramProfileSave({headers,client:this.owner.api.client,path:{role},body:{configuration_ref:choice}}));await this.owner.refresh();context.commit(()=>{this.status='Profile verified';void this.load();});}catch(error){context.commit(()=>{this.status=message(error);});}finally{this.busy.delete(role);this.change();}}
}
export function TelegramProfiles({owner}:{owner:MenusOwner}){
 useOwner(owner);const[state]=useState(()=>new ProfilesOwner(owner));return <Page owner={owner} name="telegram-profiles" title="Telegram"><p>Select existing client configurations. Checking again restores access to the same account. The two roles must use different accounts. Previous bindings are kept until the profile is confirmed.</p>{(['personal','collection']as const).map(role=>{const profile=state.data?.[role],configuration=profile&&'configuration_ref'in profile?profile.configuration_ref:'';return <fieldset key={role} disabled={!owner.workAllowed||owner.stopped||state.busy.has(role)}><legend>{roleLabel(role)}</legend><p id={'telegram-profile-'+role+'-state'}>{profile?.state==='ready'?'Verified · '+profile.verified_user_id:profile?.state==='unconfigured'?'Not Configured':profile&&'message'in profile?profile.message:'Loading…'}</p><label>Configuration<select id={'telegram-profile-'+role} value={state.choices[role]??configuration} onChange={e=>{state.choices[role]=e.target.value;state.change();}}><option value="">Choose Configuration</option>{state.data?.configurations.map(c=><option key={c.configuration_ref} value={c.configuration_ref}>{c.label}</option>)}</select></label><button id={'telegram-profile-'+role+'-save'} disabled={!(state.choices[role]??configuration)} onClick={()=>void state.save(role)}>Save and Verify</button></fieldset>;})}<p id="telegram-profiles-message" role="status">{state.status}</p></Page>;
}
