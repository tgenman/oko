import {useState} from 'react';
import * as sdk from '../api/generated/sdk.gen';
import type {TelegramAccess,ExistingTelegramFeed} from '../api/generated/types.gen';
import {ReaderError} from '../api/errors';
import {MenusOwner,message} from './owner';
import {useOwner} from './editor';
import {initial,selection,HistoryFields,FolderPicker,validFolder} from './connections';

export function accessLabel(access:TelegramAccess):string {
 switch(access.kind){
 case 'web':return 'Web Access · @'+access.username;
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
  else if(a.kind==='unconfigured'&&'source_locator'in a&&typeof a.source_locator==='string')access={kind:'unconfigured',source_locator:a.source_locator};
  else return [];
  result.push({feed_id:row.feed_id,title:row.title,access});
 }
 return result;
}
class TelegramOwner {
 peer='';folder='';newFolder='';history=initial();status='';busy=false;duplicates:ExistingTelegramFeed[]=[];
 constructor(readonly owner:MenusOwner){}
 change(){this.owner.changed();}
 async connect(){if(this.busy||!this.owner.workAllowed||this.owner.stopped)return;const history=selection(this.history,'telegram');if(!history||!validFolder('telegram',this.folder,this.newFolder))return;
  const context=this.owner.menuOperations.capture(),sent={peer:this.peer,folder:this.folder,newFolder:this.newFolder,history:JSON.stringify(this.history)};
  this.busy=true;this.status='Connecting…';this.change();
  try{const body={source_locator:this.peer.trim(),folder:(this.folder||this.newFolder).trim(),initial_history:history};const {data}=await this.owner.api.command({operation:'telegram_connect'},headers=>sdk.telegramConnect({headers,client:this.owner.api.client,body:{...body,access:{kind:'web'}}}));
   if(this.peer===sent.peer)this.peer='';if(this.newFolder===sent.newFolder)this.newFolder='';if(this.folder===sent.folder)this.folder='';if(JSON.stringify(this.history)===sent.history)this.history=initial();
   if(!this.owner.workAllowed||this.owner.stopped)return;this.owner.onChanged();await this.owner.refresh();
   if(context.current){this.status='Source connected';await context.commit(()=>this.owner.open('feeds',null,'feed/'+data.feed_id));}
  }catch(error){context.commit(()=>{this.status=message(error);this.duplicates=duplicateOwners(error);});}finally{this.busy=false;if(!context.current&&this.status==='Connecting…')this.status='';this.change();}
 }
}
export function Telegram({owner}:{owner:MenusOwner}){
 useOwner(owner);const[state]=useState(()=>new TelegramOwner(owner));const edit=(effect:()=>void)=>{effect();state.status='';state.change();};const disabled=owner.stopped||state.busy;
 return <div role="tabpanel" aria-labelledby="add-telegram-tab" id="telegram-connection" hidden={owner.tab!=='telegram'}>
 <p>Connect a public Telegram channel through its web page.</p>
 <form onSubmit={e=>{e.preventDefault();void state.connect();}}>
 <label>Source Address<input id="telegram-peer" required maxLength={4000} placeholder="@channel or https://t.me/channel" disabled={disabled} value={state.peer} onChange={e=>edit(()=>{state.peer=e.target.value;})}/></label>
 <FolderPicker owner={owner} prefix="telegram" value={state.folder} newValue={state.newFolder} disabled={disabled} onChange={value=>edit(()=>{state.folder=value;})} onNew={value=>edit(()=>{state.newFolder=value;})}/>
 <HistoryFields prefix="telegram" value={state.history} disabled={disabled} onChange={value=>edit(()=>{state.history=value;})}/>
 <button id="telegram-add" disabled={disabled||!state.peer.trim()||!(state.folder||state.newFolder).trim()}>{state.busy?'Connecting…':'Connect Source'}</button>
 <p id="telegram-message" role="status">{state.status}</p></form>
 {state.duplicates.length>0&&<dialog id="telegram-duplicate" aria-label="Source already added" ref={node=>{if(node&&!node.open)node.showModal();}} onCancel={()=>{state.duplicates=[];state.change();}}><h2>Source already added</h2>{state.duplicates.map(feed=><p key={feed.feed_id}>{feed.title}: {accessLabel(feed.access)}</p>)}<p>This channel is already connected.</p><button id="telegram-duplicate-close" onClick={()=>{state.duplicates=[];state.change();}}>Close</button></dialog>}
 </div>;
}
