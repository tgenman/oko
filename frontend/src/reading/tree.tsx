import {useLayoutEffect} from 'react';
import {readingComposition} from '../api/generated/sdk.gen';
import type {ReadingComposition,SubscriptionFeed} from '../api/generated/types.gen';
import type {SourceStatus} from '../api/legacy-generated/types.gen';
export type Navigation={folders:ReadingComposition['folders'];feeds:(SubscriptionFeed&{count:number})[];count:number};
import type {ReaderApi} from '../api/reader';
import {mountSurface} from '../renderer';
import {createStore,useStore,saved,save,host} from './store';

export type Selection = {kind:'all'|'folder'|'feed';id:string|null;folder?:string|null};
export function SourceIcon({kind}:{kind:string}) {
  return <span className={'source-icon '+kind} aria-hidden="true">{kind==='folder'?<svg viewBox="0 0 512 512" fill="currentColor"><path d="M496 152a56 56 0 0 0-56-56H220.11a23.9 23.9 0 0 1-13.31-4L179 73.41A55.77 55.77 0 0 0 147.89 64H72a56 56 0 0 0-56 56v48a8 8 0 0 0 8 8h464a8 8 0 0 0 8-8ZM16 392a56 56 0 0 0 56 56h368a56 56 0 0 0 56-56V216a8 8 0 0 0-8-8H24a8 8 0 0 0-8 8Z"/></svg>:kind==='rss'?<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4"><circle cx="5" cy="19" r="1.5" fill="currentColor"/><path d="M4 11a9 9 0 0 1 9 9M4 4a16 16 0 0 1 16 16"/></svg>:kind==='youtube'?'▶':kind==='telegram'?'➤':'◷'}</span>;
}
function Title({title,kind,count}:{title:string;kind:string;count?:number}) {return <><SourceIcon kind={kind}/><span className="tree-title">{title}</span>{count!==undefined&&<span className="tree-count">{count||''}</span>}</>;}
const feedIcon=(feed:SubscriptionFeed)=>['youtube','telegram'].includes(feed.kind)?feed.kind:'rss';
export function createReadingTree({readerApi,onSelect,onFeedSettings,onWarning}:{readerApi:ReaderApi;onSelect:(selection:Selection)=>void;onFeedSettings:(id:string,trigger:HTMLElement)=>void;onWarning:()=>void}) {
  let collapsed:Record<string,boolean>={};
  try {const value:unknown=JSON.parse(saved('oko-reading-collapsed','{}'));if(value&&typeof value==='object'&&!Array.isArray(value))collapsed=value as Record<string,boolean>;}catch{}
  const store=createStore({catalog:{folders:[],feeds:[],count:0} as Navigation,sources:[] as SourceStatus[],selection:{kind:'all',id:null} as Selection,collapsed,width:Number(saved('oko-reading-width','280'))||280});
  function resize(value:number) {const state=store.get(),width=Math.max(220,Math.min(480,Math.max(220,window.innerWidth-700),value));save('oko-reading-width',width);store.set({...state,width});}
  function select(selection:Selection) {store.set({...store.get(),selection});onSelect(selection);}
  function toggle(id:string) {const state=store.get(),collapsed={...state.collapsed,[id]:!state.collapsed[id]};save('oko-reading-collapsed',JSON.stringify(collapsed));store.set({...state,collapsed});}
  function Tree() {
    const {catalog,sources,selection,collapsed,width}=useStore(store);
    useLayoutEffect(()=>{host('reading-nav').parentElement!.style.setProperty('--sidebar-width',width+'px');host('sidebar-resize').setAttribute('aria-valuenow',String(width));},[width]);
    useLayoutEffect(()=>{
      const divider=host('sidebar-resize');
      const change=()=>resize(store.get().width);
      const key=(event:KeyboardEvent)=>{if(['ArrowLeft','ArrowRight'].includes(event.key)){event.preventDefault();resize(store.get().width+(event.key==='ArrowRight'?10:-10));}};
      let moving=false;
      const down=(event:PointerEvent)=>{if(event.button!==0)return;moving=true;divider.setPointerCapture(event.pointerId);};
      const move=(event:PointerEvent)=>{if(moving)resize(event.clientX);};
      const up=()=>{moving=false;};
      window.addEventListener('resize',change);divider.addEventListener('keydown',key);divider.addEventListener('pointerdown',down);divider.addEventListener('pointermove',move);divider.addEventListener('pointerup',up);divider.addEventListener('pointercancel',up);
      return()=>{window.removeEventListener('resize',change);divider.removeEventListener('keydown',key);divider.removeEventListener('pointerdown',down);divider.removeEventListener('pointermove',move);divider.removeEventListener('pointerup',up);divider.removeEventListener('pointercancel',up);};
    },[]);
    return <><h2>Папки</h2><button id="all-folders" className={selection.kind==='all'?'active':''} aria-pressed={selection.kind==='all'} onClick={()=>select({kind:'all',id:null})}><Title title="Все материалы" kind="all" count={catalog.count}/></button><div id="folders">{catalog.folders.map(folder=><div className="reading-group" key={folder.id}><div className="tree-row folder-row"><button type="button" title={folder.title} aria-label={folder.title} className={'tree-select '+(selection.kind==='folder'&&selection.id===folder.id?'active':'')} data-folder={folder.id} data-tree-key={'folder/'+folder.id} aria-pressed={selection.kind==='folder'&&selection.id===folder.id} onClick={()=>select({kind:'folder',id:folder.id})}><Title title={folder.title} kind="folder" count={folder.count}/></button><button type="button" className="tree-collapse" title={(collapsed[folder.id]?'Раскрыть ':'Свернуть ')+folder.title} aria-label={(collapsed[folder.id]?'Раскрыть ':'Свернуть ')+folder.title} data-collapse={folder.id} data-tree-key={'collapse/'+folder.id} aria-expanded={!collapsed[folder.id]} aria-controls={'reading-folder-'+folder.id} onClick={()=>toggle(folder.id)}><span className="tree-chevron" aria-hidden="true"><svg viewBox="0 0 20 20"><path d="m7 4 6 6-6 6"/></svg></span></button></div><div id={'reading-folder-'+folder.id} hidden={!!collapsed[folder.id]}>{catalog.feeds.filter(feed=>feed.folder_id===folder.id).sort((a,b)=>a.title.toLocaleLowerCase().localeCompare(b.title.toLocaleLowerCase())||a.id.localeCompare(b.id)).map(feed=>{const source=sources.find(item=>item.feed_id===feed.id),warning=[source?.error,source?.gap].filter(Boolean).join(' · ');return <div className="tree-row feed-row" key={feed.id}><button type="button" className={'tree-select '+(selection.kind==='feed'&&selection.id===feed.id?'active':'')} title={feed.title} aria-label={feed.title} data-feed={feed.id} data-tree-key={'feed/'+feed.id} aria-pressed={selection.kind==='feed'&&selection.id===feed.id} onClick={()=>select({kind:'feed',id:feed.id,folder:folder.id})} onDoubleClick={event=>onFeedSettings(feed.id,event.currentTarget)}><Title title={feed.title} kind={feedIcon(feed)} count={feed.count}/></button>{warning&&<button type="button" title={warning} aria-label={warning} className="tree-warning" data-tree-key={'warning/'+feed.id} onClick={onWarning}>⚠</button>}</div>;})}</div></div>)}</div></>;
  }
  mountSurface(host('reading-nav'),<Tree/>);resize(store.get().width);
  return {
    get selection(){return store.get().selection;},
    async load():Promise<Navigation>{const value=(await readingComposition({client:readerApi.client})).data;return {...value,feeds:value.feeds.map(entry=>({...entry.feed,count:entry.count}))};},
    update(catalog:Navigation,sources:SourceStatus[]=[]) {
      const state=store.get();let selection=state.selection,changed=false,moved=false,collapsed=state.collapsed;
      if(selection.kind==='feed') {const feed=catalog.feeds.find(feed=>feed.id===selection.id);if(!feed){selection=catalog.folders.some(folder=>folder.id===selection.folder)?{kind:'folder',id:selection.folder!}:{kind:'all',id:null};changed=true;}else if(feed.folder_id!==selection.folder){selection={...selection,folder:feed.folder_id};collapsed={...collapsed,[feed.folder_id]:false};save('oko-reading-collapsed',JSON.stringify(collapsed));moved=true;}}
      else if(selection.kind==='folder'&&!catalog.folders.some(folder=>folder.id===selection.id)){selection={kind:'all',id:null};changed=true;}
      const focus=(document.activeElement as HTMLElement|null)?.dataset.treeKey,scroll=host('reading-nav').scrollTop;
      store.set({...state,catalog,sources,selection,collapsed});host('reading-nav').scrollTop=scroll;
      if(focus) [...host('reading-nav').querySelectorAll<HTMLElement>('[data-tree-key]')].find(node=>node.dataset.treeKey===focus)?.focus({preventScroll:true});
      if(moved)[...host('folders').querySelectorAll<HTMLElement>('[data-feed]')].find(node=>node.dataset.feed===selection.id)?.scrollIntoView({block:'nearest'});
      if(changed)onSelect(selection);return changed;
    },
    clear(){store.set({...store.get(),catalog:{folders:[],feeds:[],count:0},sources:[]});},
  };
}
