import {useEffect,useLayoutEffect,useRef,useState} from 'react';
import {cards as readCards} from '../api/generated/sdk.gen';
import type {Card,Navigation} from '../api/generated/types.gen';
import type {ReaderApi} from '../api/reader';
import {mountSurface} from '../renderer';
import {reportError} from '../status/debug';
import type {Selection} from './tree';
import {createStore,useStore,saved,save,host} from './store';

type MediaFetch=(path:string,signal?:AbortSignal)=>Promise<Response>;
type Anchor={id?:string;offset:number;top:number};
const previewPath=(card:Card)=>card.digest||card.event?null:card.images?.[0]||card.attachments?.find(item=>item.kind==='image')?.url;
function date(card:Card) {return card.published_known===false?'Дата неизвестна':new Date(card.published*1000).toLocaleString('ru',{year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'});}
function group(card:Card) {if(card.published_known===false)return 'Дата неизвестна';const stamp=new Date(card.published*1000),today=new Date(),yesterday=new Date();yesterday.setDate(today.getDate()-1);return stamp.toDateString()===today.toDateString()?'Сегодня':stamp.toDateString()===yesterday.toDateString()?'Вчера':stamp.toLocaleDateString('ru',{year:'numeric',month:'long',day:'numeric'});}
function Preview({card,fetchMedia}:{card:Card;fetchMedia:MediaFetch}) {
  const ref=useRef<HTMLSpanElement>(null),[url,setUrl]=useState<string|null>(null);
  const symbol=card.digest?'▤':card.event?'▧':'▥',path=previewPath(card);
  useEffect(()=>{
    if(!path||!ref.current)return;
    const controller=new AbortController();let current=true,started=false,objectUrl:string|null=null;
    const observer=new IntersectionObserver(entries=>{if(started||!entries.some(entry=>entry.isIntersecting))return;started=true;void(async()=>{try{const response=await fetchMedia(path,controller.signal);if(!current)return;if(!response.ok)throw new Error('Не удалось загрузить превью: HTTP '+response.status);const blob=await response.blob();if(!current)return;objectUrl=URL.createObjectURL(blob);setUrl(objectUrl);}catch(error){if(!(error instanceof Error&&error.name==='AbortError')&&current)reportError(error,'Превью списка');}})();},{root:host('card-scroll'),rootMargin:'150px'});
    observer.observe(ref.current);
    return()=>{current=false;controller.abort();observer.disconnect();if(objectUrl)URL.revokeObjectURL(objectUrl);};
  },[path,card.version,fetchMedia]);
  return <span ref={ref} className="card-preview" aria-hidden="true">{url?<img src={url} alt="" onError={()=>{URL.revokeObjectURL(url);setUrl(null);reportError(new Error('Не удалось показать превью'),'Список материалов');}}/>:<span>{symbol}</span>}</span>;
}
function Row({card,selected,onOpen,onArrow,fetchMedia}:{card:Card;selected:boolean;onOpen:(card:Card)=>void;onArrow:(id:string,offset:number)=>void;fetchMedia:MediaFetch}) {
  const kind=card.digest?'Обзор':card.event?'Событие':card.filter?.hidden?'Скрыто':'';
  const status=[kind,card.changed?'Изменено':'',card.loading||card.complete===false?'Загружается':''].filter(Boolean).join(' · ');
  const sources=card.sources?.length?[...new Set(card.sources.map(source=>source.title||source.feed_id).filter(Boolean))].join(', '):card.feed_title||card.feed_id||'';
  return <button type="button" className={'material-row '+(selected?'active':'')} data-card={card.id} aria-pressed={selected} aria-label={(kind?kind+' · ':'')+card.title+' · '+sources+' · '+date(card)} onClick={()=>onOpen(card)} onKeyDown={event=>{if(['ArrowUp','ArrowDown'].includes(event.key)){event.preventDefault();onArrow(card.id,event.key==='ArrowDown'?1:-1);}}}><Preview card={card} fetchMedia={fetchMedia}/><span className="card-body"><span className="card-title" title={card.title}>{card.title}</span>{status&&<span className="card-kind">{status}</span>}{card.snippet&&<span className="card-snippet">{card.snippet}</span>}<span className="card-meta"><span className="card-source" title={sources}>{sources}</span><time className="card-date">{date(card)}</time></span></span></button>;
}
export function createMaterialList({readerApi,onOpen,onRetry,fetchMedia}:{readerApi:ReaderApi;onOpen:(card:Card)=>void;onRetry:()=>void;fetchMedia:MediaFetch}) {
  const stored=saved('oko-material-sort','newest');
  const sort=['newest','oldest','importance'].includes(stored)?stored:'newest';
  const store=createStore({area:'all',cards:[] as Card[],selected:null as string|null,title:'Все материалы',folder:false,status:'Загрузка материалов…',retry:false,menu:false,sort,showHidden:false,width:Math.max(320,Math.min(640,Number(saved('oko-material-width','440'))||440))});
  let commands:{openDigest:(period:boolean)=>void;openCompletion:()=>void}|null=null;
  const positions=new Map<string,Anchor>();let restore:Anchor|null=null;
  const row=(id:string)=>[...host('cards').querySelectorAll<HTMLElement>('[data-card]')].find(element=>element.dataset.card===id);
  function anchor():Anchor {const scroll=host('card-scroll'),top=scroll.getBoundingClientRect().top,node=[...host('cards').querySelectorAll<HTMLElement>('[data-card]')].find(node=>node.getBoundingClientRect().bottom>top);return {top:scroll.scrollTop,offset:node?node.getBoundingClientRect().top-top:0,...(node?.dataset.card?{id:node.dataset.card}:{})};}
  function apply(position:Anchor) {const scroll=host('card-scroll'),node=position.id?row(position.id):undefined;if(node)scroll.scrollTop+=node.getBoundingClientRect().top-scroll.getBoundingClientRect().top-position.offset;else scroll.scrollTop=position.top;}
  function closeMenu(focus=false) {store.set({...store.get(),menu:false});if(focus)host('list-menu-toggle').focus();}
  function resize() {const state=store.get(),panel=host('material-list'),main=panel.parentElement!,available=Math.max(320,main.clientWidth-host('reading-nav').getBoundingClientRect().width-12-360),effective=Math.min(state.width,available);main.style.setProperty('--material-width',effective+'px');host('list-resize').setAttribute('aria-valuenow',String(Math.round(effective)));host('list-resize').setAttribute('aria-valuemax',String(Math.min(640,available)));}
  function width(value:number) {const width=Math.max(320,Math.min(640,value));save('oko-material-width',width);store.set({...store.get(),width});}
  function Surface() {
    const state=useStore(store),n=state.cards.length,word=n%10===1&&n%100!==11?'карточка':n%10>=2&&n%10<=4&&(n%100<12||n%100>14)?'карточки':'карточек';
    useLayoutEffect(resize,[state.width]);
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
    return <><div className="list-heading"><div><h2 id="list-title">{state.title}</h2><p id="count-label"><span id="count">{n}</span> <span id="count-word">{word}</span></p></div><button id="folder-viewed" aria-label="Всё просмотрено" title="Всё просмотрено" hidden={!state.folder} disabled={!state.folder} onClick={()=>commands?.openCompletion()}>✓</button><button id="list-menu-toggle" aria-label="Действия списка" aria-expanded={state.menu} aria-controls="list-menu" onClick={()=>store.set({...store.get(),menu:!store.get().menu})}>⋯</button><div id="list-menu" hidden={!state.menu} onKeyDown={event=>{if(event.key==='Escape'){event.preventDefault();closeMenu(true);}}}><label>Порядок <select id="sort" value={state.sort} onChange={event=>{save('oko-material-sort',event.currentTarget.value);store.set({...store.get(),sort:event.currentTarget.value});}}><option value="newest">Новые сверху</option><option value="oldest">Старые сверху</option><option value="importance">По важности</option></select></label><label><input id="show-hidden" type="checkbox" checked={state.showHidden} onChange={event=>store.set({...store.get(),showHidden:event.currentTarget.checked})}/> Показать скрытое</label><button id="digest-now" hidden={!state.folder} disabled={!state.folder} onClick={()=>commands?.openDigest(true)}>Обновить обзор сейчас</button><button id="compress" hidden={!state.folder} disabled={!state.folder} onClick={()=>commands?.openDigest(false)}>Сжать историю</button></div></div><div id="card-scroll"><p id="list-status" role="status">{state.status}</p><button id="list-retry" hidden={!state.retry} onClick={onRetry}>Повторить</button><div id="cards">{state.cards.flatMap(card=>{const label=state.sort==='importance'?null:group(card),nodes=[];if(label&&label!==previous){nodes.push(<div className="date-group" key={'date/'+card.id}>{label}</div>);previous=label;}nodes.push(<Row key={state.area+'/'+card.id+'/'+card.version+'/'+previewPath(card)} card={card} selected={state.selected===card.id} fetchMedia={fetchMedia} onOpen={card=>{select(card.id);onOpen(card);}} onArrow={(id,offset)=>{const cards=store.get().cards,index=cards.findIndex(card=>card.id===id),next=cards[Math.max(0,Math.min(cards.length-1,index+offset))];if(next)row(next.id)?.focus();}}/>);return nodes;})}</div></div></>;
  }
  function select(selected:string|null) {store.set({...store.get(),selected});}
  mountSurface(host('material-list'),<Surface/>);
  function update(cards:Card[],selected:string|null=null) {const position=restore||anchor(),focused=(document.activeElement as HTMLElement|null)?.dataset.card;restore=null;store.set({...store.get(),cards,selected,status:cards.length?'':'Нет материалов',retry:false});if(focused)row(focused)?.focus({preventScroll:true});apply(position);}
  return {
    select,update,
    bindCommands(value:NonNullable<typeof commands>){commands=value;},
    async load(params:URLSearchParams) {const sort=params.get('sort');return (await readCards({client:readerApi.client,query:{sort:sort==='oldest'||sort==='importance'?sort:'newest',...(params.get('show_hidden')==='true'?{show_hidden:true}:{}),...(params.has('folder')?{folder:params.get('folder')!}:{}),...(params.has('feed')?{feed:params.get('feed')!}:{})}})).data;},
    setArea(selection:Selection,catalog:Navigation) {const state=store.get(),area=selection.kind+'/'+(selection.id||''),changed=area!==state.area;if(changed){positions.set(state.area,anchor());restore=positions.get(area)||{top:0,offset:0};}store.set({...state,area,title:selection.kind==='all'?'Все материалы':catalog[selection.kind==='folder'?'folders':'feeds'].find(item=>item.id===selection.id)?.title||'Материалы',folder:selection.kind==='folder',...(changed?{cards:[],selected:null,status:'Загрузка материалов…',retry:false,menu:false}:{})});if(changed)host('card-scroll').scrollTop=0;},
    removed(identity:string) {const state=store.get(),index=state.cards.findIndex(card=>card.id===identity||card.sources?.some(source=>source.id===identity)),affected=state.cards[index];if(!affected)return;const next=state.cards.filter(card=>card.id!==identity&&!card.sources?.some(source=>source.id===identity)),focus=affected.id===state.selected||(document.activeElement as HTMLElement|null)?.dataset.card===affected.id;update(next,focus?null:state.selected);if(focus){const target=next[Math.min(index,next.length-1)];if(target)row(target.id)?.focus({preventScroll:true});else host('list-menu-toggle').focus({preventScroll:true});}},
    error(message:string) {store.set({...store.get(),status:'Не удалось обновить материалы: '+message,retry:true});},
    clear(){store.set({...store.get(),cards:[],selected:null,status:'',menu:false});},
  };
}
