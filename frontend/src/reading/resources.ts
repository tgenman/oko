import {status as runtimeStatus,statusDetails,navigation as getNavigation} from '../api/generated/sdk.gen';
import type {CardListItem, CollectionTarget, NavigationResponse, RuntimeStatus, StatusSummary} from '../api/generated/types.gen';
import type {ReaderApi} from '../api/reader';
import type {Navigation} from './tree';

export type ReadingListOptions = {target:CollectionTarget;sort:'newest'|'oldest';show_hidden:boolean};
export type CardView = CardListItem & {folder_id:string;feed_title:string};
export const listIdentity = (options:ReadingListOptions) => JSON.stringify([options.target.scope,options.target.scope==='feed'?options.target.feed_id:options.target.scope==='folder'?options.target.folder_id:'',options.sort,options.show_hidden]);

type Reply<T>={data?:T;error?:unknown;response?:Response};
function accepted<T>(reply:Reply<T>):T|undefined {
  if(reply.response?.status===304)return;
  if(reply.error)throw reply.error;
  if(!reply.data)throw new Error('Reading response temporarily unavailable');
  return reply.data;
}

// Each subject keeps its own accepted response and HTTP validator. No entity plan.
export function createReadingResources(api:ReaderApi) {
  let storageId:string|null=null;
  let nav:NavigationResponse|undefined,state:StatusSummary|undefined,details:RuntimeStatus|undefined;
  let navTag:string|undefined,statusTag:string|undefined,detailsTag:string|undefined;
  const headers=(tag?:string)=>tag?{'If-None-Match':tag}:{};
  async function refresh(signal:AbortSignal,includeDetails:boolean) {
    const [status,navigation,detail]=await Promise.all([
      runtimeStatus({client:api.client,signal,throwOnError:false,headers:headers(statusTag)}),
      getNavigation({client:api.client,signal,throwOnError:false,headers:headers(navTag)}),
      includeDetails?statusDetails({client:api.client,signal,throwOnError:false,headers:headers(detailsTag)}):Promise.resolve(undefined),
    ]);
    signal.throwIfAborted();
    return {status,navigation,detail};
  }
  function accept(reply:Awaited<ReturnType<typeof refresh>>) {
    const next=accepted(reply.status);
    const inactive=['stopped','unavailable'].includes((next??state)?.runtime_state??'');
    const navigation=inactive?undefined:accepted(reply.navigation);
    const identity=reply.status.response?.headers.get('X-Oko-Storage-Id')||null;
    const navIdentity=reply.navigation.response?.headers.get('X-Oko-Storage-Id')||identity;
    const reset=!!identity&&(storageId!==null&&storageId!==identity||identity!==navIdentity);
    if(reset){nav=undefined;state=undefined;details=undefined;navTag=statusTag=detailsTag=undefined;storageId=identity;return {reset:true,changed:new Set<string>(),detailsError:null};}
    if(identity)storageId=identity;
    const changed=new Set<string>();
    if(next){state=next;statusTag=reply.status.response?.headers.get('etag')??undefined;changed.add('status');}
    if(navigation){nav=navigation;navTag=reply.navigation.response?.headers.get('etag')??undefined;changed.add('navigation');}
    let detailsError:unknown=null;
    if(reply.detail){
      try {
        const detailIdentity=reply.detail.response?.headers.get('X-Oko-Storage-Id')||identity;
        if(detailIdentity!==identity)throw new Error('Storage changed while reading status details');
        const value=accepted(reply.detail);
        if(value){details=value;detailsTag=reply.detail.response?.headers.get('etag')??undefined;changed.add('details');}
      } catch(error){detailsError=error;}
    }
    return {reset:false,changed,detailsError};
  }
  function navigation():Navigation|undefined {
    if(!nav)return;
    return {count:nav.total,folders:nav.folders.map(({id,title,count})=>({id,title,count})),
      feeds:nav.folders.flatMap(folder=>folder.feeds.map(feed=>({...feed,folder_id:folder.id})))};
  }
  return {refresh,accept,navigation,get storageId(){return storageId;},get status(){return state;},get details(){return details;}};
}
