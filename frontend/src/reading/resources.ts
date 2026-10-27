import {checkReading} from '../api/generated/sdk.gen';
import type {CompactReadingCard, SubscriptionFeed, ReadingCheck, ReadingCheckResponse, ReadingListOptions, ReadingListData, ReadingIndex, RuntimeStatus} from '../api/generated/types.gen';
import type {ReaderApi} from '../api/reader';
import type {Navigation} from './tree';

export type CardView = CompactReadingCard & Pick<SubscriptionFeed,'folder_id'|'importance'> & {feed_title:string};
const portionSize=40;
type Value = {version:string; data:unknown};
const key = (kind:string,id:string) => kind+'/'+id;
export const listIdentity = (options:ReadingListOptions) => JSON.stringify([options.target.scope,options.target.scope==='feed'?options.target.feed_id:options.target.scope==='folder'?options.target.folder_id:'',options.sort,options.show_hidden]);

// Versions travel with the accepted entity, never with an attempted request.
// Opened bodies belong to the opened session and are deliberately absent here.
export function createReadingResources(api:ReaderApi) {
  let storageId:string|null=null;
  const values=new Map<string,Value>();
  const limits=new Map<string,number>();
  const limit=(options:ReadingListOptions)=>limits.get(listIdentity(options))??portionSize;
  const version=(kind:string,id:string) => values.get(key(kind,id))?.version??null;
  function data<T>(kind:string,id:string):T|undefined {return values.get(key(kind,id))?.data as T|undefined;}
  function forget(ids:string[]) {for(const id of ids)values.delete(key('card',id));}
  function plan(options:ReadingListOptions, extra:ReadingCheck[]=[],listOnly=false):ReadingCheck[] {
    const known:Record<string,string|null>={};
    const previous=data<ReadingListData>('list',listIdentity(options));
    for(const id of previous?.ids.slice(0,limit(options))??[])known[id]=version('card',id);
    // Cached cards from another area can be reused when the composition changes.
    if(!previous)for(const [identity,value] of values)if(identity.startsWith('card/'))known[identity.slice(5)]=value.version;
    const listing:ReadingCheck={kind:'list',options,known_version:version('list',listIdentity(options)),known_cards:known,card_limit:limit(options)};
    if(listOnly)return [listing];
    const index=data<ReadingIndex>('navigation','navigation');
    return [{kind:'status',known_version:version('status','runtime')},{kind:'navigation',known_version:version('navigation','navigation')},
      ...(index?.feeds??[]).map(feed=>({kind:'feed' as const,id:feed.id,known_version:version('feed',feed.id)})),
      listing,...extra];
  }
  async function check(resources:ReadingCheck[],signal:AbortSignal) {return (await checkReading({client:api.client,body:{storage_id:storageId,resources},signal})).data;}
  function accept(frame:ReadingCheckResponse) {
    const changed=new Set<string>();
    const reset=frame.state==='reset'||storageId!==null&&storageId!==frame.storage_id;
    if(reset){values.clear();limits.clear();}
    storageId=frame.storage_id;
    if(frame.state==='reset')return {reset:true,changed};
    for(const resource of frame.resources) {
      if(!['status','navigation','feed','card','list'].includes(resource.kind))continue;
      const identity=key(resource.kind,resource.id),previous=values.get(identity);
      if(resource.state==='changed'&&(!previous||BigInt(resource.version)>BigInt(previous.version))) {
        if(resource.kind==='list'&&previous){
          const before=previous.data as ReadingListData,after=resource.data;
          const retained=new Set(before.ids.slice(0,limit(before.options)));
          let end=limit(after.options);
          after.ids.forEach((id,index)=>{if(retained.has(id))end=Math.max(end,index+1);});
          limits.set(listIdentity(after.options),end);
        }
        values.set(identity,{version:resource.version,data:resource.data});changed.add(resource.kind);
      } else if(resource.state==='unavailable') {if(values.delete(identity))changed.add(resource.kind);}
    }
    const index=data<ReadingIndex>('navigation','navigation');
    if(index)for(const [identity,value] of values)if(identity.startsWith('feed/')&&!index.feeds.some(feed=>feed.id===identity.slice(5))) {
      values.delete(identity);const id=(value.data as SubscriptionFeed).id;
      for(const [cardKey,card] of values)if(cardKey.startsWith('card/')&&(card.data as CompactReadingCard).feed_id===id)values.delete(cardKey);
    }
    const status=data<RuntimeStatus>('status','runtime');if(status&&changed.has('status'))forget(status.viewed_ids);
    return {reset,changed};
  }
  function navigation():Navigation|undefined {
    const index=data<ReadingIndex>('navigation','navigation');if(!index)return;
    const feeds=index.feeds.flatMap(ref=>{const feed=data<SubscriptionFeed>('feed',ref.id);return feed?[{...feed,...ref}]:[];});
    return {...index,feeds};
  }
  function cards(options:ReadingListOptions):CardView[]|undefined {
    const list=data<ReadingListData>('list',listIdentity(options));if(!list)return;
    return list.ids.slice(0,limit(options)).flatMap(id=>{
      const card=data<CompactReadingCard>('card',id),feed=card&&data<SubscriptionFeed>('feed',card.feed_id);
      return card&&feed?[{...card,feed_title:feed.title,folder_id:feed.folder_id,importance:feed.importance}]:[];
    });
  }
  function ids(options:ReadingListOptions):string[] {return data<ReadingListData>('list',listIdentity(options))?.ids??[];}
  function more(options:ReadingListOptions) {return ids(options).length>limit(options);}
  function extend(options:ReadingListOptions) {
    if(!more(options))return false;
    limits.set(listIdentity(options),limit(options)+portionSize);return true;
  }
  async function titles(ids:string[],signal:AbortSignal):Promise<Pick<CardView,'id'|'title'>[]> {
    const identity=storageId;
    const missing=ids.filter(id=>!data<CompactReadingCard>('card',id));
    for(let offset=0;offset<missing.length;offset+=portionSize){
      signal.throwIfAborted();
      const frame=await check(missing.slice(offset,offset+portionSize).map(id=>({kind:'card',id,known_version:null})),signal);
      signal.throwIfAborted();
      if(storageId!==identity||frame.storage_id!==identity||frame.state==='reset')throw new Error('Reading list changed; reopen the action');
      accept(frame);
    }
    return ids.map(id=>{const card=data<CompactReadingCard>('card',id);if(!card)throw new Error('Could not load all selected items; reopen the action');return {id,title:card.title};});
  }
  return {plan,check,accept,forget,navigation,cards,ids,more,extend,titles,get storageId(){return storageId;},get status(){return data<RuntimeStatus>('status','runtime');}};
}
