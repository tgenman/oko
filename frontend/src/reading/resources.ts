import {checkReading} from '../api/generated/sdk.gen';
import type {CardListItem, SubscriptionFeed, ReadingCheck, ReadingCheckResponse, CollectionTarget, ReadingIndex, RuntimeStatus} from '../api/generated/types.gen';
import type {ReaderApi} from '../api/reader';
import type {Navigation} from './tree';

export type ReadingListOptions = {target:CollectionTarget;sort:'newest'|'oldest';show_hidden:boolean};
export type CardView = CardListItem & Pick<SubscriptionFeed,'folder_id'> & {feed_title:string};
type Value = {version:string; data:unknown};
const key = (kind:string,id:string) => kind+'/'+id;
export const listIdentity = (options:ReadingListOptions) => JSON.stringify([options.target.scope,options.target.scope==='feed'?options.target.feed_id:options.target.scope==='folder'?options.target.folder_id:'',options.sort,options.show_hidden]);

// Versions travel with the accepted entity, never with an attempted request.
// Opened bodies belong to the opened session and are deliberately absent here.
export function createReadingResources(api:ReaderApi) {
  let storageId:string|null=null;
  const values=new Map<string,Value>();
  const version=(kind:string,id:string) => values.get(key(kind,id))?.version??null;
  function data<T>(kind:string,id:string):T|undefined {return values.get(key(kind,id))?.data as T|undefined;}
  function plan(extra:ReadingCheck[]=[]):ReadingCheck[] {
    const index=data<ReadingIndex>('navigation','navigation');

    return [{kind:'status',known_version:version('status','runtime')},{kind:'navigation',known_version:version('navigation','navigation')},
      ...(index?.feeds??[]).map(feed=>({kind:'feed' as const,id:feed.id,known_version:version('feed',feed.id)})),
      ...extra];
  }
  async function check(resources:ReadingCheck[],signal:AbortSignal) {return (await checkReading({client:api.client,body:{storage_id:storageId,resources},signal})).data;}
  function accept(frame:ReadingCheckResponse) {
    const changed=new Set<string>();
    const reset=frame.state==='reset'||storageId!==null&&storageId!==frame.storage_id;
    if(reset)values.clear();
    storageId=frame.storage_id;
    if(frame.state==='reset')return {reset:true,changed};
    for(const resource of frame.resources) {
      if(!['status','navigation','feed'].includes(resource.kind))continue;
      const identity=key(resource.kind,resource.id),previous=values.get(identity);
      if(resource.state==='changed'&&(!previous||BigInt(resource.version)>BigInt(previous.version))) {
        values.set(identity,{version:resource.version,data:resource.data});changed.add(resource.kind);
      } else if(resource.state==='unavailable') {if(values.delete(identity))changed.add(resource.kind);}
    }

    return {reset,changed};
  }
  function navigation():Navigation|undefined {
    const index=data<ReadingIndex>('navigation','navigation');if(!index)return;
    const feeds=index.feeds.flatMap(ref=>{const feed=data<SubscriptionFeed>('feed',ref.id);return feed?[{...feed,...ref}]:[];});
    return {...index,feeds};
  }
  return {plan,check,accept,navigation,get storageId(){return storageId;},get status(){return data<RuntimeStatus>('status','runtime');}};
}
