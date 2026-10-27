import {PostWebBadge,postWebUnavailableLabel} from './post-web-unavailable';
import {ExternalBadges,externalMediaLabel} from './external-attachments';
import {newsTitle} from './title';
import {useEffect,useLayoutEffect,useRef,useState} from 'react';
import type {CardView as Card} from './resources';
import type {Navigation} from './tree';
import type {ReaderApi} from '../api/reader';
import {mountSurface} from '../renderer';
import {reportError} from '../status/debug';
import type {NewsContext} from './news-actions';
import type {Selection} from './tree';
import {createStore,useStore,saved,save,host} from './store';

type MediaFetch=(path:string,signal?:AbortSignal)=>Promise<Response>;
type Anchor={id?:string;offset:number;top:number};
const previewPath=(card:Card)=>card.preview_url??undefined;
function date(card:Card) {return card.published_at===null?'Unknown date':new Date(card.published_at!).toLocaleString('en',{year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'});}
function group(card:Card) {if(card.published_at===null)return 'Unknown date';const stamp=new Date(card.published_at!),today=new Date(),yesterday=new Date();yesterday.setDate(today.getDate()-1);return stamp.toDateString()===today.toDateString()?'Today':stamp.toDateString()===yesterday.toDateString()?'Yesterday':stamp.toLocaleDateString('en',{year:'numeric',month:'long',day:'numeric'});}
function Preview({card,fetchMedia}:{card:Card;fetchMedia:MediaFetch}) {
  const ref=useRef<HTMLSpanElement>(null),[url,setUrl]=useState<string|null>(null);
  const symbol='▥',path=previewPath(card);
  useEffect(()=>{
    if(!path||!ref.current)return;
    const controller=new AbortController();let current=true,started=false,objectUrl:string|null=null;
    const observer=new IntersectionObserver(entries=>{if(started||!entries.some(entry=>entry.isIntersecting))return;started=true;void(async()=>{try{const response=await fetchMedia(path,controller.signal);if(!current)return;if(!response.ok)throw new Error('Could not load preview: HTTP '+response.status);const blob=await response.blob();if(!current)return;objectUrl=URL.createObjectURL(blob);setUrl(objectUrl);}catch(error){if(!(error instanceof Error&&error.name==='AbortError')&&current)reportError(error,'List preview');}})();},{root:host('card-scroll'),rootMargin:'150px'});
    observer.observe(ref.current);
    return()=>{current=false;controller.abort();observer.disconnect();if(objectUrl)URL.revokeObjectURL(objectUrl);};
  },[path,(card.availability==='available'?card.revision_id:'pending'),fetchMedia]);
  return <span className="card-preview-wrap"><span ref={ref} className="card-preview" aria-hidden="true">{url?<img src={url} alt="" onError={()=>{URL.revokeObjectURL(url);setUrl(null);reportError(new Error('Could not display preview'),'Item list');}}/>:<span>{symbol}</span>}</span>{card.post_web_unavailable&&<PostWebBadge/>}<ExternalBadges kinds={card.external_attachment_kinds}/></span>;
}
function Row({card,selected,onOpen,onArrow,onContext,preview,fetchMedia,showSource,showDate}:{showSource:boolean;showDate:boolean;card:Card;selected:boolean;preview:boolean;onContext:(card:Card,x:number,y:number,trigger:HTMLElement)=>void;onOpen:(card:Card)=>void;onArrow:(id:string,offset:number)=>void;fetchMedia:MediaFetch}) {
  const title=newsTitle(card.title);
  const kind=card.hidden?'Hidden':'';
  const status=[kind,card.availability==='available'&&card.changed?'Updated':'',card.loading||card.availability==='pending'?'Loading':''].filter(Boolean).join(' · ');
  const sources=card.feed_title||card.feed_id||'';
  return <button type="button" className={'material-row '+(selected?'active ':'')+(preview?'news-range-preview':'')} data-card={card.id} aria-pressed={selected} aria-label={(kind?kind+' · ':'')+title+' · '+sources+' · '+date(card)+(card.post_web_unavailable?' · '+postWebUnavailableLabel:'')+(card.external_attachment_kinds?.length?' · '+card.external_attachment_kinds.map(externalMediaLabel).join(' · '):'')} onClick={()=>onOpen(card)} onContextMenu={event=>{event.preventDefault();onContext(card,event.clientX,event.clientY,event.currentTarget);}} onKeyDown={event=>{if(event.key==='ContextMenu'||event.shiftKey&&event.key==='F10'){event.preventDefault();const rect=event.currentTarget.getBoundingClientRect();onContext(card,rect.left,rect.bottom,event.currentTarget);return;}if(['ArrowUp','ArrowDown'].includes(event.key)){event.preventDefault();onArrow(card.id,event.key==='ArrowDown'?1:-1);}}}><Preview card={card} fetchMedia={fetchMedia}/><span className="card-body"><span className="card-title" title={title}>{title}</span>{status&&<span className="card-kind">{status}</span>}{(showSource||showDate)&&<span className="card-meta">{showSource&&<span className="card-source" title={sources}>{sources}</span>}{showDate&&<time className="card-date">{date(card)}</time>}</span>}</span></button>;
}
export function createMaterialList({readerApi,onOpen,onRetry,onContext,onMore,fetchMedia}:{readerApi:ReaderApi;onOpen:(card:Card)=>void;onRetry:()=>void;onMore:()=>Promise<void>;onContext:(card:Card,context:NewsContext,x:number,y:number,trigger:HTMLElement)=>void;fetchMedia:MediaFetch}) {
  const stored=saved('oko-material-sort','newest');
  const sort=['newest','oldest','importance'].includes(stored)?stored:'newest';
  const store=createStore({area:'all',cards:[] as Card[],ids:[] as string[],more:false,preview:[] as string[],selected:null as string|null,title:'All Items',folder:false,status:'Loading items…',retry:false,menu:false,sort,showHidden:false,width:Math.max(320,Math.min(640,Number(saved('oko-material-width','440'))||440))});
  let commands:{openCompletion:()=>void}|null=null;
  const positions=new Map<string,Anchor>();let restore:Anchor|null=null;
  const row=(id:string)=>[...host('cards').querySelectorAll<HTMLElement>('[data-card]')].find(element=>element.dataset.card===id);
  function anchor():Anchor {const scroll=host('card-scroll'),top=scroll.getBoundingClientRect().top,node=[...host('cards').querySelectorAll<HTMLElement>('[data-card]')].find(node=>node.getBoundingClientRect().bottom>top);return {top:scroll.scrollTop,offset:node?node.getBoundingClientRect().top-top:0,...(node?.dataset.card?{id:node.dataset.card}:{})};}
  function apply(position:Anchor) {const scroll=host('card-scroll'),node=position.id?row(position.id):undefined;if(node)scroll.scrollTop+=node.getBoundingClientRect().top-scroll.getBoundingClientRect().top-position.offset;else scroll.scrollTop=position.top;}
  function closeMenu(focus=false) {store.set({...store.get(),menu:false});if(focus)host('list-menu-toggle').focus();}
  function resize() {const state=store.get(),panel=host('material-list'),main=panel.parentElement!,available=Math.max(320,main.clientWidth-host('reading-nav').getBoundingClientRect().width-12-360),effective=Math.min(state.width,available);main.style.setProperty('--material-width',effective+'px');host('list-resize').setAttribute('aria-valuenow',String(Math.round(effective)));host('list-resize').setAttribute('aria-valuemax',String(Math.min(640,available)));}
  function width(value:number) {const width=Math.max(320,Math.min(640,value));save('oko-material-width',width);store.set({...store.get(),width});}
  function Surface() {
    const state=useStore(store),n=state.ids.length,word=n===1?'item':'items';
    useLayoutEffect(resize,[state.width]);
    useEffect(()=>{
      if(!state.more||state.retry)return;
      const observer=new IntersectionObserver(entries=>{if(entries.some(entry=>entry.isIntersecting))void onMore();},{root:host('card-scroll'),rootMargin:'300px'});
      observer.observe(host('list-more'));return()=>observer.disconnect();
    },[state.area,state.cards.length,state.more,state.retry]);
    useEffect(()=>{
      const divider=host('list-resize'),observer=new ResizeObserver(resize);observer.observe(host('reading-nav'));window.addEventListener('resize',resize);
      const key=(event:KeyboardEvent)=>{if(['ArrowLeft','ArrowRight'].includes(event.key)){event.preventDefault();width(Number(divider.getAttribute('aria-valuenow'))+(event.key==='ArrowRight'?10:-10));}};
      let initial:number|null=null,start=0;
      const down=(event:PointerEvent)=>{if(event.button!==0)return;divider.setPointerCapture(event.pointerId);initial=Number(divider.getAttribute('aria-valuenow'));start=event.clientX;};
      const move=(event:PointerEvent)=>{if(initial!==null)width(initial+event.clientX-start);};const up=()=>{initial=null;};
      const outside=(event:PointerEvent)=>{const target=event.target;if(target instanceof Node&&store.get().menu&&!host('list-menu').contains(target)&&!host('list-menu-toggle').contains(target))closeMenu();};
      divider.addEventListener('keydown',key);divider.addEventListener('pointerdown',down);divider.addEventListener('pointermove',move);divider.addEventListener('pointerup',up);divider.addEventListener('pointercancel',up);document.addEventListener('pointerdown',outside);
      return()=>{observer.disconnect();window.removeEventListener('resize',resize);divider.removeEventListener('keydown',key);divider.removeEventListener('pointerdown',down);divider.removeEventListener('pointermove',move);divider.removeEventListener('pointerup',up);divider.removeEventListener('pointercancel',up);document.removeEventListener('pointerdown',outside);};
    },[]);
    let previous:string|null=null;
    return <><div className="list-heading"><div className="list-title-count"><h2 id="list-title">{state.title}</h2><span id="count-label" aria-label={`${n} ${word}`}><span id="count">{n}</span></span></div><button id="folder-viewed" aria-label="Mark All as Read" title="Mark All as Read" hidden={!state.folder} disabled={!state.folder} onClick={()=>commands?.openCompletion()}>✓</button><button id="list-menu-toggle" aria-label="List Actions" aria-expanded={state.menu} aria-controls="list-menu" onClick={()=>store.set({...store.get(),menu:!store.get().menu})}>⋯</button><div id="list-menu" hidden={!state.menu} onKeyDown={event=>{if(event.key==='Escape'){event.preventDefault();closeMenu(true);}}}><label>Sort Order <select id="sort" value={state.sort} onChange={event=>{save('oko-material-sort',event.currentTarget.value);store.set({...store.get(),sort:event.currentTarget.value});}}><option value="newest">Newest First</option><option value="oldest">Oldest First</option><option value="importance">By Importance</option></select></label><label><input id="show-hidden" type="checkbox" checked={state.showHidden} onChange={event=>store.set({...store.get(),showHidden:event.currentTarget.checked})}/> Show Hidden</label></div></div><div id="card-scroll"><p id="list-status" role="status">{state.status}</p><button id="list-retry" hidden={!state.retry} onClick={onRetry}>Retry</button><div id="cards">{state.cards.flatMap(card=>{const label=state.sort==='importance'?null:group(card),nodes=[];if(label&&label!==previous){nodes.push(<div className="date-group" key={'date/'+card.id}>{label}</div>);previous=label;}nodes.push(<Row key={state.area+'/'+card.id+'/'+(card.availability==='available'?card.revision_id:'pending')+'/'+previewPath(card)} card={card} showSource={!state.area.startsWith('feed/')} showDate={state.sort==='importance'} preview={state.preview.includes(card.id)} onContext={(card,x,y,trigger)=>onContext(card,context(),x,y,trigger)} selected={state.selected===card.id} fetchMedia={fetchMedia} onOpen={card=>{select(card.id);onOpen(card);}} onArrow={(id,offset)=>{void arrow(id,offset);}}/>);return nodes;})}</div><div id="list-more" hidden={!state.more} role="status">More items load as you scroll</div></div></>;
  }
  async function arrow(id:string,offset:number) {
    const state=store.get(),key=context().key,index=state.cards.findIndex(card=>card.id===id);
    if(offset>0&&index===state.cards.length-1&&state.more)await onMore();
    if(context().key!==key||(document.activeElement as HTMLElement|null)?.dataset.card!==id)return;
    const cards=store.get().cards,next=cards[Math.max(0,Math.min(cards.length-1,cards.findIndex(card=>card.id===id)+offset))];
    if(next)row(next.id)?.focus();
  }
  function select(selected:string|null) {store.set({...store.get(),selected});}
  mountSurface(host('material-list'),<Surface/>);
  function update(cards:Card[],selected:string|null=null,ids:string[]=cards.map(card=>card.id),more=false) {const position=restore||anchor(),focused=(document.activeElement as HTMLElement|null)?.dataset.card;restore=null;store.set({...store.get(),cards,ids,more,selected,status:ids.length?'':'No items',retry:false});if(focused)row(focused)?.focus({preventScroll:true});apply(position);}
  function context():NewsContext{const state=store.get();return {key:JSON.stringify([state.area,state.sort,state.showHidden]),ids:state.ids};}
  return {
    context,preview(ids:string[]){store.set({...store.get(),preview:ids});},
    title(id:string){return store.get().cards.find(card=>card.id===id)?.title;},
    select,update,
    bindCommands(value:NonNullable<typeof commands>){commands=value;},
    setArea(selection:Selection,catalog:Navigation) {const state=store.get(),area=selection.kind+'/'+(selection.id||''),changed=area!==state.area;if(changed){positions.set(state.area,anchor());restore=positions.get(area)||{top:0,offset:0};}store.set({...state,area,title:selection.kind==='all'?'All Items':catalog[selection.kind==='folder'?'folders':'feeds'].find(item=>item.id===selection.id)?.title||'Items',folder:selection.kind==='folder',...(changed?{cards:[],ids:[],more:false,selected:null,status:'Loading items…',retry:false,menu:false}:{})});if(changed)host('card-scroll').scrollTop=0;},
    removed(identity:string) {const state=store.get(),index=state.cards.findIndex(card=>card.id===identity),affected=state.cards[index];if(!affected)return;const next=state.cards.filter(card=>card.id!==identity),focus=affected.id===state.selected||(document.activeElement as HTMLElement|null)?.dataset.card===affected.id;update(next,focus?null:state.selected,state.ids.filter(id=>id!==identity),state.more);if(focus){const target=next[Math.min(index,next.length-1)];if(target)row(target.id)?.focus({preventScroll:true});else host('list-menu-toggle').focus({preventScroll:true});}},
    error(message:string) {store.set({...store.get(),status:'Could not refresh items: '+message,retry:true});},
    clear(){store.set({...store.get(),cards:[],ids:[],more:false,selected:null,status:'',menu:false});},
  };
}
