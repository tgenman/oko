import {useState} from 'react';
import * as sdk from '../api/generated/sdk.gen';
import type {TelegramAccess,TelegramProfiles as Profiles,TelegramRole,ExistingTelegramFeed} from '../api/generated/types.gen';
import {ReaderError} from '../api/errors';
import {MenusOwner,message} from './owner';
import {useOwner,Page} from './editor';
import {initial,selection,HistoryFields,FolderPicker,validFolder} from './connections';

export const roleLabel=(role:TelegramRole)=>role==='personal'?'Личный аккаунт':'Аккаунт для сбора';
export function accessLabel(access:TelegramAccess):string {
 switch(access.kind){
 case 'web':return 'Веб-доступ · @'+access.username;
 case 'client':return roleLabel(access.role)+' · '+access.account_user_id;
 case 'legacy_client':return 'Прежний аккаунт · '+access.alias+' · требуется сопоставление профиля';
 case 'unconfigured':return 'Требуется подключение · '+access.source_locator;
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
  else if(a.kind==='legacy_client'&&'alias'in a&&typeof a.alias==='string'&&'source_locator'in a&&typeof a.source_locator==='string'&&'peer_id'in a&&(a.peer_id===null||typeof a.peer_id==='string'))access={kind:'legacy_client',alias:a.alias,source_locator:a.source_locator,peer_id:a.peer_id};
  else if(a.kind==='unconfigured'&&'source_locator'in a&&typeof a.source_locator==='string')access={kind:'unconfigured',source_locator:a.source_locator};
  else return [];
  result.push({feed_id:row.feed_id,title:row.title,access});
 }
 return result;
}
class TelegramOwner {
 access:'web'|TelegramRole='web';peer='';folder='Telegram';newFolder='';auto=false;history=initial();status='';busy=false;profiles:Profiles|null=null;duplicates:ExistingTelegramFeed[]=[];
 constructor(readonly owner:MenusOwner){owner.onAddOpen.push(()=>{if(owner.tab==='telegram')void this.load();});owner.onTabChange.push(tab=>{if(tab==='telegram')void this.load();});}
 change(){this.owner.changed();}
 async load(){const context=this.owner.menuOperations.capture();try{const {data}=await sdk.telegramProfiles({client:this.owner.api.client});context.commit(()=>{this.profiles=data;this.change();});}catch(error){context.commit(()=>{if(this.access!=='web')this.status=message(error);this.change();});}}
 async connect(){if(this.busy||this.owner.stopped)return;const history=selection(this.history,'telegram');if(!history||!validFolder('telegram',this.folder,this.newFolder))return;
  const context=this.owner.menuOperations.capture(),sent={access:this.access,peer:this.peer,folder:this.folder,newFolder:this.newFolder,auto:this.auto,history:JSON.stringify(this.history)};
  this.busy=true;this.status='Подключение…';this.change();
  try{const body={source_locator:this.peer.trim(),folder:(this.folder||this.newFolder).trim(),initial_history:history};const {data}=await this.owner.api.command({operation:'telegram_connect'},headers=>sdk.telegramConnect({headers,client:this.owner.api.client,body:this.access==='web'?{...body,access:{kind:'web'}}:{...body,access:{kind:'client',profile:this.access},auto_read:this.auto}}));
   if(this.peer===sent.peer)this.peer='';if(this.newFolder===sent.newFolder)this.newFolder='';if(this.folder===sent.folder)this.folder='Telegram';if(JSON.stringify(this.history)===sent.history)this.history=initial();if(this.auto===sent.auto)this.auto=false;
   if(this.owner.stopped)return;this.owner.onChanged();await this.owner.refresh();
   if(context.current){this.status='Источник подключён';await context.commit(()=>this.owner.open('feeds',null,'feed/'+data.feed_id));}
  }catch(error){context.commit(()=>{this.status=message(error);this.duplicates=duplicateOwners(error);});}finally{this.busy=false;if(!context.current&&this.status==='Подключение…')this.status='';this.change();}
 }
}
export function Telegram({owner}:{owner:MenusOwner}){
 useOwner(owner);const[state]=useState(()=>new TelegramOwner(owner));const edit=(effect:()=>void)=>{effect();state.status='';state.change();};const disabled=owner.stopped||state.busy;
 const available=state.access==='web'||state.profiles?.[state.access].state==='ready';
 return <div role="tabpanel" aria-labelledby="add-telegram-tab" id="telegram-connection" hidden={owner.tab!=='telegram'}>
 <p>Группа собирается целиком. Способ доступа закрепляется за фидом. Веб-доступ не отмечает сообщения прочитанными в Telegram.</p>
 <label>Способ доступа<select id="telegram-access" disabled={disabled} value={state.access} onChange={e=>edit(()=>{const value=e.target.value;state.access=value==='personal'||value==='collection'?value:'web';state.auto=false;})}><option value="web">Веб-доступ</option><option value="personal">Личный аккаунт</option><option value="collection">Аккаунт для сбора</option></select></label>
 {state.access!=='web'&&<p>{available?'Профиль проверен':'Сначала настройте и проверьте этот профиль в настройках Око → Telegram.'}</p>}
 <form onSubmit={e=>{e.preventDefault();void state.connect();}}>
 <label>Адрес источника<input id="telegram-peer" required maxLength={4000} placeholder="@channel или https://t.me/channel" disabled={disabled} value={state.peer} onChange={e=>edit(()=>{state.peer=e.target.value;})}/></label>
 <FolderPicker owner={owner} prefix="telegram" value={state.folder} newValue={state.newFolder} disabled={disabled} onChange={value=>edit(()=>{state.folder=value;})} onNew={value=>edit(()=>{state.newFolder=value;})}/>
 <HistoryFields prefix="telegram" value={state.history} disabled={disabled} onChange={value=>edit(()=>{state.history=value;})}/>
 <label hidden={state.access==='web'}><input id="telegram-auto" type="checkbox" disabled={disabled||state.access==='web'} checked={state.auto} onChange={e=>edit(()=>{state.auto=e.target.checked;})}/>Автопрочитывать после полного сохранения</label>
 <button id="telegram-add" disabled={disabled||!available||!state.peer.trim()||!(state.folder||state.newFolder).trim()}>{state.busy?'Подключение…':'Подключить источник'}</button>
 <p id="telegram-message" role="status">{state.status}</p></form>
 {state.duplicates.length>0&&<dialog id="telegram-duplicate" aria-label="Источник уже добавлен" ref={node=>{if(node&&!node.open)node.showModal();}} onCancel={()=>{state.duplicates=[];state.change();}}><h2>Источник уже добавлен</h2>{state.duplicates.map(feed=><p key={feed.feed_id}>{feed.title}: {accessLabel(feed.access)}</p>)}<p>Чтобы выбрать другой способ доступа, удалите существующий фид и добавьте источник заново. Удаление очистит локальные материалы и настройки.</p><button id="telegram-duplicate-close" onClick={()=>{state.duplicates=[];state.change();}}>Закрыть</button></dialog>}
 </div>;
}
class ProfilesOwner {
 data:Profiles|null=null;choices:Partial<Record<TelegramRole,string>>={};busy=new Set<TelegramRole>();status='';
 constructor(readonly owner:MenusOwner){owner.onPageChange.push(page=>{if(page==='telegram-profiles')void this.load();});}
 change(){this.owner.changed();}
 async load(){const action=this.owner.operations.begin('telegram-profiles',{abortable:true});try{const{data}=await sdk.telegramProfiles({client:this.owner.api.client,signal:action.signal});action.commit(()=>{this.data=data;this.change();});}catch(error){action.commit(()=>{this.status=message(error);this.change();});}}
 async save(role:TelegramRole){const profile=this.data?.[role];const choice=this.choices[role]??(profile&&'configuration_ref'in profile?profile.configuration_ref:'');if(!choice||this.busy.has(role)||this.owner.stopped)return;const context=this.owner.operations.capture();this.busy.add(role);this.status='Проверка аккаунта…';this.change();try{await this.owner.api.command({operation:'telegram_profile_save',role},headers=>sdk.telegramProfileSave({headers,client:this.owner.api.client,path:{role},body:{configuration_ref:choice}}));await this.owner.refresh();context.commit(()=>{this.status='Профиль проверен';void this.load();});}catch(error){context.commit(()=>{this.status=message(error);});}finally{this.busy.delete(role);this.change();}}
}
export function TelegramProfiles({owner}:{owner:MenusOwner}){
 useOwner(owner);const[state]=useState(()=>new ProfilesOwner(owner));return <Page owner={owner} name="telegram-profiles" title="Telegram"><p>Выберите существующие конфигурации клиента. Повторная проверка восстанавливает вход в тот же аккаунт. Аккаунты двух ролей должны различаться. Старые привязки сохраняются до подтверждения профиля.</p>{(['personal','collection']as const).map(role=>{const profile=state.data?.[role],configuration=profile&&'configuration_ref'in profile?profile.configuration_ref:'';return <fieldset key={role} disabled={owner.stopped||state.busy.has(role)}><legend>{roleLabel(role)}</legend><p id={'telegram-profile-'+role+'-state'}>{profile?.state==='ready'?'Проверен · '+profile.verified_user_id:profile?.state==='unconfigured'?'Не настроен':profile&&'message'in profile?profile.message:'Загрузка…'}</p><label>Конфигурация<select id={'telegram-profile-'+role} value={state.choices[role]??configuration} onChange={e=>{state.choices[role]=e.target.value;state.change();}}><option value="">Выберите конфигурацию</option>{state.data?.configurations.map(c=><option key={c.configuration_ref} value={c.configuration_ref}>{c.label}</option>)}</select></label><button id={'telegram-profile-'+role+'-save'} disabled={!(state.choices[role]??configuration)} onClick={()=>void state.save(role)}>Сохранить и проверить</button></fieldset>;})}<p id="telegram-profiles-message" role="status">{state.status}</p></Page>;
}
