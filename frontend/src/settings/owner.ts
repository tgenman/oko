import {flushSync} from 'react-dom';
import type {ReaderApi} from '../api/reader';
import * as settings from '../api/generated/sdk.gen';
import type {EffectiveSettingsSnapshot,SettingsPatch,SettingsTarget,HistoryState,FeedHistoryState,YouTubeState,FeedPatchRequest as FeedPatch,RuntimeStatus,SourceState,RemoveFeedResult} from '../api/generated/types.gen';
import type {SubscriptionCatalog} from '../api/generated/types.gen';
import {createOperations,type Operation} from './operations';
type SettingsScope=SettingsTarget['scope'];
export type DialogName='add'|'feeds'|'settings';
export type Draft=Record<string,string|boolean>;
export type Discard=(path:string,field:string,method?:string)=>Promise<unknown>|undefined;
export const message=(error:unknown)=>error instanceof Error?error.message:'Action failed';
export class MenusOwner {
  private navigationRevision=0;
  private version=0; private listeners=new Set<()=>void>();
  subscribe=(listener:()=>void)=>{this.listeners.add(listener);return()=>{this.listeners.delete(listener);};};
  snapshot=()=>this.version;
  changed=()=>{this.version++;flushSync(()=>{for(const listener of this.listeners)listener();});};
  active:DialogName|null=null; page='home'; stack:{page:string;trigger:HTMLElement|null;target?:string}[]=[];
  opener:HTMLElement|null=null; stopped=false; selected:string|null=null; currentPath:string|null=null;
  youtube:YouTubeState|null=null;
  subscriptions:SubscriptionCatalog={folders:[],feeds:[]}; sources:SourceState[]=[]; histories:FeedHistoryState[]=[];
  collapsed=new Set<string>(); values=new Map<string,EffectiveSettingsSnapshot>(); drafts=new Map<string,Draft>();
  historyPending=new Map<string,Operation>();
  historyBusy(id:string){return this.historyPending.get('history/'+id)?.current===true;}
  deletions=new Set<string>();
  private removedFeeds=new Set<string>();
  private deletionRefresh:Promise<void>|null=null;
  saving=new Set<string>(); needsRefresh=new Set<string>(); settingsMessage=''; addMessage=''; tab='rss';
  operations=createOperations(); subscriptionOperations=createOperations(); menuOperations=createOperations();
  onPageChange:((page:string)=>void)[]=[]; onTabChange:((tab:string)=>void)[]=[]; onAddOpen:(()=>void)[]=[];
  constructor(readonly api:ReaderApi,readonly onChanged:()=>void,readonly discard:Discard=()=>undefined){}
  draft(path:string){let values=this.drafts.get(path);if(!values){values={};this.drafts.set(path,values);}return values;}
  edit(path:string,key:string,value:string|boolean){this.draft(path)[key]=value;this.changed();}
  describe(scope:string,owner:string|null){if(scope==='default')return 'Oko · Default Value';if(scope==='oko')return 'Oko';const list=scope==='folder'?this.subscriptions.folders:this.subscriptions.feeds;return(scope==='folder'?'Folder: ':'Feed: ')+(list.find(item=>item.id===owner)?.title||owner);}
  focusHeading(){queueMicrotask(()=>{const dialog=this.active&&document.getElementById(this.active+'-dialog');const heading=dialog?.querySelector<HTMLElement>('[data-settings-page]:not([hidden]) h2');heading?.focus({preventScroll:true});});}
  navigate(next:string,trigger:HTMLElement|null=null){if(!this.active||next===this.page)return;this.navigationRevision++;this.stack.push({page:this.page,trigger,target:trigger?.dataset.settingsTarget});this.operations.invalidate();this.settingsMessage='';this.page=next;this.changed();for(const callback of this.onPageChange)callback(next);if(this.currentPath&&this.needsRefresh.has(this.currentPath))void this.loadSettings();const dialog=document.getElementById(this.active+'-dialog');if(dialog)dialog.scrollTop=0;this.focusHeading();}
  back(){const previous=this.stack.pop();if(!previous)return;this.navigationRevision++;this.operations.invalidate();this.settingsMessage='';this.page=previous.page;this.changed();for(const callback of this.onPageChange)callback(this.page);if(this.currentPath&&this.needsRefresh.has(this.currentPath))void this.loadSettings();queueMicrotask(()=>{const fallback=this.active&&document.getElementById(this.active+'-dialog')?.querySelector<HTMLElement>('[data-settings-target="'+previous.target+'"]');const target=previous.trigger?.isConnected?previous.trigger:fallback;if(target&&!target.closest('[hidden]'))target.focus({preventScroll:true});else this.focusHeading();});}
  leave(){this.navigationRevision++;this.menuOperations.invalidate();this.operations.invalidate();this.currentPath=null;for(const callback of this.onPageChange)callback('');}
  close(name:DialogName){if(this.active!==name)return;this.leave();this.active=null;this.changed();if(this.opener?.isConnected)this.opener.focus({preventScroll:true});this.opener=null;}
  async open(name:DialogName,trigger:HTMLElement|null=null,preferred?:string|null){if(this.stopped)return;if(!this.active)this.opener=trigger||document.activeElement as HTMLElement;const previous=this.active;this.leave();this.active=name;this.page='home';this.stack=[];this.settingsMessage='';
    if(preferred!==undefined)this.selected=preferred;this.currentPath=name==='settings'?'oko/global':name==='feeds'?this.selected:null;this.changed();
    if(previous&&previous!==name)(document.getElementById(previous+'-dialog')as HTMLDialogElement).close();const dialog=document.getElementById(name+'-dialog')as HTMLDialogElement;if(!dialog.open)dialog.showModal();
    const action=this.menuOperations.begin('open');const revision=this.navigationRevision;const owner=this;try{await this.refresh();if(name==='add'){action.commit(()=>{for(const callback of this.onAddOpen)callback();});}else await action.commit(()=>this.loadSettings());if(name==='settings')action.commit(()=>{for(const callback of this.onPageChange)callback('settings-open');});}catch(error){action.commit(()=>{if(name==='add')this.addMessage=message(error);else this.settingsMessage=message(error);this.changed();});}return {get current(){return action.current&&revision===owner.navigationRevision;}};}
  async refresh():Promise<boolean>{
    const action=this.subscriptionOperations.begin('load',{abortable:true});
    try{const{data}=await settings.subscriptionList({client:this.api.client,signal:action.signal});if(!action.current)return false;
      this.subscriptions={...data,feeds:data.feeds.filter(feed=>!this.removedFeeds.has(feed.id))};
      for(const feed of this.subscriptions.feeds)if(feed.connection==='deleting')this.deletions.add(feed.id);
      if(this.selected&&!this.subscriptions.folders.some(f=>'folder/'+f.id===this.selected)&&!this.subscriptions.feeds.some(f=>'feed/'+f.id===this.selected))this.selected=null;
      this.changed();return true;
    }catch(error){if(!action.current)return false;throw error;}
  }
  async select(path:string){this.navigationRevision++;this.operations.invalidate();this.selected=path;this.currentPath=path;this.page='home';this.stack=[];for(const callback of this.onPageChange)callback('owner-change');this.changed();await this.loadSettings();}
  async loadSettings(){const path=this.currentPath;if(!path)return;const [level,owner]=path.split('/');if(!['oko','folder','feed'].includes(level||'')||!owner)return;const scope:SettingsScope=level==='folder'?'folder':level==='feed'?'feed':'oko';const action=this.operations.begin('load',{abortable:true});try{const{data}=await settings.effectiveSettings({client:this.api.client,path:{scope,owner},signal:action.signal});action.commit(()=>{this.values.set(path,data);this.needsRefresh.delete(path);this.changed();});}catch(error){action.commit(()=>{this.settingsMessage=message(error);this.changed();});}}
  async update(field:keyof SettingsPatch,body:SettingsPatch,submitted:string){const path=this.currentPath;if(!path)return;const key=path+'/'+field;if(this.saving.has(key))return;this.saving.add(key);this.settingsMessage='Saving…';this.changed();const action=this.operations.begin('field/'+field);const[level,owner]=path.split('/');const scope:SettingsScope=level==='folder'?'folder':level==='feed'?'feed':'oko';const target=scope==='oko'?{scope,owner:'global' as const}:{scope,owner:owner!};try{await this.api.command({operation:'settings_patch',target,keys:[field]},headers=>settings.settingsPatch({client:this.api.client,path:{scope,owner:owner!},body,headers}));const values=this.draft(path);if(values['setting-'+field]===submitted)delete values['setting-'+field];this.needsRefresh.add(path);if(this.currentPath===path)await this.loadSettings();action.commit(()=>{this.settingsMessage='Setting saved';this.changed();});}catch(error){action.commit(()=>{this.settingsMessage=message(error);this.changed();});}finally{this.saving.delete(key);this.changed();}}
  async discardField(path:string,field:string){const action=this.operations.begin('discard/'+field);delete this.draft(path)['setting-'+field];this.changed();try{await this.discard('settings/'+path,field);}catch(error){action.commit(()=>{this.settingsMessage=message(error);this.changed();});}}
  async saveFeed(feed:{id:string},patch:FeedPatch){const action=this.operations.begin('feed');const path='feed/'+feed.id,values=this.draft(path);const submitted={...values};try{await this.api.command({operation:'feed_patch',feed_id:feed.id,fields:Object.keys(patch).sort().filter((key):key is keyof FeedPatch=>['auto_read','folder_id','importance','initial_days','public_ai','read_history','transport'].includes(key))},headers=>settings.feedPatch({client:this.api.client,path:{identity:feed.id},body:patch,headers}));for(const[key,value]of Object.entries(submitted))if(key.startsWith('feed-')&&values[key]===value)delete values[key];this.onChanged();await this.refresh();this.needsRefresh.add(path);if(this.currentPath===path)await this.loadSettings();action.commit(()=>{this.settingsMessage='Feed saved';this.changed();});}catch(error){action.commit(()=>{this.settingsMessage=message(error);this.changed();});}}
  async history(feed:{id:string},method:'pause'|'start'|'stop',value?:boolean|string){const key='history/'+feed.id;if(this.historyBusy(feed.id))return;const action=this.operations.begin('history');this.historyPending.set(key,action);this.changed();try{const options={client:this.api.client,path:{identity:feed.id}};const {data}=await this.api.command({operation:method==='pause'?'history_pause':method==='stop'?'history_cancel':'history_start',feed_id:feed.id},headers=>method==='pause'?settings.historyPause({...options,headers,body:{paused:Boolean(value)}}):method==='stop'?settings.historyCancel({...options,headers}):settings.historyStart({...options,headers,body:{mode:'count',count:String(value)}}));if(method==='start'&&this.draft('feed/'+feed.id).youtubeHistoryCount===String(value))delete this.draft('feed/'+feed.id).youtubeHistoryCount;this.onChanged();await action.commit(async()=>{await this.refresh();await this.loadSettings();});action.commit(()=>{this.setHistory(feed.id,data.history);this.settingsMessage=method==='start'?'History download started':method==='stop'?'History download stopped':'';this.changed();});}catch(error){action.commit(()=>{this.settingsMessage=message(error);this.changed();});}finally{if(this.historyPending.get(key)===action)this.historyPending.delete(key);this.changed();}}
  private finishRemoval(id:string){
    this.removedFeeds.add(id);this.deletions.delete(id);
    this.subscriptions={...this.subscriptions,feeds:this.subscriptions.feeds.filter(feed=>feed.id!==id)};
    const path='feed/'+id;this.values.delete(path);this.drafts.delete(path);
    if(this.selected===path)this.selected=null;
    if(this.currentPath===path){this.navigationRevision++;this.operations.invalidate();this.currentPath=null;this.page='home';this.stack=[];for(const callback of this.onPageChange)callback('owner-change');}
  }
  async removeFeed(feed:{id:string},propagate=false){
    if(this.deletions.has(feed.id)||this.stopped)return;
    const context=this.operations.capture();this.deletions.add(feed.id);this.changed();
    let result:RemoveFeedResult;
    try{result=(await this.api.command({operation:'remove_feed',feed_id:feed.id},headers=>settings.removeFeed({headers,client:this.api.client,path:{identity:feed.id}}))).data;}
    catch(error){this.deletions.delete(feed.id);context.commit(()=>{this.settingsMessage=message(error);});this.changed();if(propagate)throw error;return;}
    if(result.state==='deleted'){context.commit(()=>{this.settingsMessage='Feed deleted';});this.finishRemoval(feed.id);}
    else context.commit(()=>{this.settingsMessage='Feed is being deleted. Local cleanup will continue after restart.';});
    this.onChanged();this.changed();
    try{await this.refresh();}catch(error){context.commit(()=>{this.settingsMessage='Deletion accepted; could not refresh catalog: '+message(error);this.changed();});}
    return result;
  }
  setHistory(id:string,history:HistoryState){this.invalidateStatus();this.histories=[...(this.histories||[]).filter(h=>h.feed_id!==id),{history,feed_id:id}];}
  getHistory(feed:{id:string}){const current=this.histories.find(h=>h.feed_id===feed.id)?.history;const saved=this.values.get('feed/'+feed.id);return current||(saved?.scope==='feed'?saved.feed.history:undefined);}
  status(state:RuntimeStatus,revision=this.statusGeneration){
    if(revision!==this.statusGeneration||this.stopped)return;
    this.histories=state.initial_histories;this.youtube=state.youtube;this.changed();
    if(this.deletions.size&&!this.deletionRefresh)this.deletionRefresh=this.refresh().then(applied=>{
      if(!applied)return;for(const id of this.deletions)if(!this.subscriptions.feeds.some(feed=>feed.id===id))this.finishRemoval(id);this.changed();
    }).catch(()=>{}).finally(()=>{this.deletionRefresh=null;});
  }
  private statusGeneration=0;
  get statusRevision(){return this.statusGeneration;}
  invalidateStatus(){this.statusGeneration++;}
  stop(){this.stopped=true;this.leave();this.active=null;this.subscriptionOperations.invalidate();this.changed();for(const name of ['add','feeds','settings']){(document.getElementById(name+'-dialog')as HTMLDialogElement).close();(document.getElementById(name+'-open')as HTMLButtonElement).disabled=true;}}
}
