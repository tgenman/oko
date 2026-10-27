import {cards as getCards} from '../api/generated/sdk.gen';
import type {CardListItem, CardPage, CardsData} from '../api/generated/types.gen';
import {ReaderError,type ReaderApi} from '../api/reader';
import {listIdentity,type ReadingListOptions} from './resources';

type PageState={items:CardListItem[];total:number;next:string|null;etags:Map<string,string>;head:string[];newItems:boolean;changed:boolean};
type Window={cursor:string;limit:number}|null;

// The list owns accepted pages and request validators; it does not cache the library.
export function createCardPages(api:ReaderApi) {
 const states=new Map<string,PageState>();
 function get(options:ReadingListOptions){const key=listIdentity(options);let state=states.get(key);if(!state){state={items:[],total:0,next:null,etags:new Map(),head:[],newItems:false,changed:false};states.set(key,state);}return state;}
 async function request(options:ReadingListOptions,cursor:string|null,limit:number,signal:AbortSignal):Promise<CardPage|null>{
  const state=get(options),key=JSON.stringify([cursor,limit]);
  const query:NonNullable<CardsData['query']>={sort:options.sort==='oldest'?'oldest':'newest',show_hidden:options.show_hidden,limit,...(cursor?{cursor}:{}),...(options.target.scope==='folder'?{folder:options.target.folder_id}:options.target.scope==='feed'?{feed:options.target.feed_id}:{})};
  const etag=state.etags.get(key);
  const reply=await getCards({client:api.client,query,signal,throwOnError:false,...(etag?{headers:{'If-None-Match':etag}}:{})});
  if(reply.response?.status===304)return null;
  if(reply.response?.status===409||(reply.error instanceof ReaderError&&reply.error.code==='cursor_expired')){states.delete(listIdentity(options));throw new Error('cursor_expired');}
  if(reply.error)throw reply.error;
  if(!reply.data)throw new Error('Reading page unavailable');
  const next=reply.response?.headers.get('etag');if(next)state.etags.set(key,next);
  return reply.data;
 }
 async function poll(options:ReadingListOptions,visible:Window,atTop:boolean,signal:AbortSignal){
  const state=get(options),initial=!state.head.length&&!state.items.length;
  const head=await request(options,null,40,signal);signal.throwIfAborted();
  if(head){
   const ids=head.items.map(card=>card.id),known=new Set(state.items.map(card=>card.id));
   const lastShared=ids.reduce((last,id,index)=>state.head.includes(id)?index:last,-1);
   const newItems=!initial&&(head.total>state.total||ids.some((id,index)=>!known.has(id)&&(lastShared<0||index<lastShared)));
   state.total=head.total;state.changed=true;
   if(initial||atTop){
    const nextIds=new Set(ids);
    const boundary=head.next_cursor?state.items.findIndex(card=>card.id===head.next_cursor):-1;
    const suffix=boundary>=0?state.items.slice(boundary).filter(card=>!nextIds.has(card.id)):[];
    state.items=[...head.items,...suffix];
    if(!suffix.length)state.next=head.next_cursor;
    state.newItems=options.sort==='oldest'&&(state.newItems||newItems);state.changed=true;
   } else if(newItems)state.newItems=true;
   state.head=ids;
  }
  if(visible&&!atTop&&!initial){
   const page=await request(options,visible.cursor,visible.limit,signal);signal.throwIfAborted();
   if(page){
    const start=state.items.findIndex(card=>card.id===visible.cursor);
    if(start>=0){const end=page.next_cursor?state.items.findIndex((card,index)=>index>start&&card.id===page.next_cursor):-1;
     const cut=end>=0?end:Math.min(start+visible.limit,state.items.length),replacement=new Set(page.items.map(card=>card.id));
     state.items=[...state.items.slice(0,start).filter(card=>!replacement.has(card.id)),...page.items,...state.items.slice(cut).filter(card=>!replacement.has(card.id))];
     state.total=page.total;state.changed=true;
    }
   }
  }
  return state;
 }
 async function more(options:ReadingListOptions,signal:AbortSignal){
  const state=get(options),cursor=state.next;if(!cursor)return;
  const page=await request(options,cursor,40,signal);signal.throwIfAborted();
  if(page){const known=new Set(state.items.map(card=>card.id));state.items.push(...page.items.filter(card=>!known.has(card.id)));state.total=page.total;state.next=page.next_cursor;state.changed=true;}
 }
 function forget(ids:string[]){const removed=new Set(ids);for(const state of states.values()){const before=state.items.length;state.items=state.items.filter(card=>!removed.has(card.id));state.total=Math.max(0,state.total-(before-state.items.length));state.changed=true;state.etags.clear();}}
 function reset(options:ReadingListOptions){states.delete(listIdentity(options));}
 return {get,poll,more,forget,reset,clear(){states.clear();}};
}
