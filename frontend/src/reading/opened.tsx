import {newsTitle} from './title';
import {copyText} from '../browser-platform';
import {serviceText} from '../service-text';
import {useEffect,useLayoutEffect,useRef,useState} from 'react';
import {readingMaterial,previousVersion,regenerate,retryMaterial,retryTheses,retryFilter,restoreFilter,viewed} from '../api/generated/sdk.gen';
import type {PublicationVersion as Version,PreviousVersionReference as Previous,ThesesPoint as Point,Attachment,AvailablePublicationContent} from '../api/generated/types.gen';
import type {ReadingCheck,ReadingResource,ReadingEntityMaterial,ReadingFilterData,ReadingThesesData,ReadingMediaData} from '../api/generated/types.gen';
import type {CardView as Card} from './resources';
import type {ReaderApi} from '../api/reader';
import {mountSurface} from '../renderer';
import {reportError} from '../status/debug';
import {createStore,useStore,host,errorMessage} from './store';

type MediaFetch=(path:string,signal?:AbortSignal,cache?:RequestCache)=>Promise<Response>;
type Material = (ReadingEntityMaterial & {filter:ReadingFilterData['filter'];theses:ReadingThesesData['theses']}) & {content:AvailablePublicationContent|{availability:'pending'}};
type Part='material'|'filter'|'theses'|'media';
type OpenCard=Pick<Card,'id'>&Partial<Card>;
type Session={id:string;folder_id:string|undefined;parts:Map<Part,{version:string;data:unknown}>;loading:boolean;controller:AbortController};
type Resource={url:string;kind:Attachment['kind'];name:string};
function Media({material,fetchMedia,session,id,className}:{material:Pick<AvailablePublicationContent,'images'|'attachments'>;fetchMedia:MediaFetch;session:Session;id?:string;className?:string}) {
  const ref=useRef<HTMLDivElement>(null),[resources,setResources]=useState<Resource[]>([]);
  useLayoutEffect(()=>()=>{for(const media of ref.current?.querySelectorAll<HTMLMediaElement>('video,audio')||[]){media.pause();media.removeAttribute('src');media.load();}},[]);
  const resourcesKey=JSON.stringify([material.images,material.attachments]);
  useEffect(()=>{
    let active=true;const urls:string[]=[];setResources([]);
    void(async()=>{try{
      const inputs=[...(material.images||[]).map(url=>({url,kind:'image' as const})),...(material.attachments||[])];
      for(const input of inputs){if(!active||session.controller.signal.aborted)return;const response=await fetchMedia(input.url,session.controller.signal);if(!response.ok)continue;const blob=await response.blob();if(!active||session.controller.signal.aborted)return;const url=URL.createObjectURL(blob);urls.push(url);setResources(previous=>[...previous,{url,kind:input.kind,name:input.url.split('/').pop()||''}]);}
    }catch(error){if(active&&!(error instanceof Error&&error.name==='AbortError'))reportError(error,'Opened item');}})();
    return()=>{active=false;for(const url of urls)URL.revokeObjectURL(url);};
  },[resourcesKey,session,fetchMedia]);
  return <div ref={ref} {...(id?{id}:{})} {...(className?{className}:{})}>{resources.map(resource=>resource.kind==='image'?<img key={resource.url} src={resource.url} alt="Publication image"/>:resource.kind==='video'||resource.kind==='circle'?<video key={resource.url} src={resource.url} controls preload="metadata" className={resource.kind==='circle'?'circle':undefined} aria-label={resource.kind==='circle'?'Telegram video message':'Publication video'}/>:resource.kind==='audio'?<audio key={resource.url} src={resource.url} controls preload="metadata" aria-label="Publication audio"/>:<a key={resource.url} href={resource.url} download={resource.name}>Download Attachment</a>)}</div>;
}
function PreviousVersion({previous,session,readerApi,fetchMedia}:{previous:Previous;session:Session;readerApi:ReaderApi;fetchMedia:MediaFetch}) {
  const [version,setVersion]=useState<Version|null>(null),[error,setError]=useState(''),active=useRef(true),loading=useRef(false);
  useEffect(()=>{active.current=true;return()=>{active.current=false;};},[]);
  async function load() {
    if(loading.current||version)return;loading.current=true;setError('');
    try {
      const number=previous.number;
      const reply=await previousVersion({client:readerApi.client,path:{item:session.id,number},signal:session.controller.signal});
      if(active.current&&!session.controller.signal.aborted)setVersion(reply.data);
    }catch(error){reportError(error,'Previous Version');if(active.current&&!session.controller.signal.aborted){setError(errorMessage(error));loading.current=false;}}
  }
  return <details onToggle={event=>{if(event.currentTarget.open)void load();}}><summary>Previous Version</summary><div>{version?<><h3>{newsTitle(version.title)}</h3><p>{version.text}</p><Media material={version} fetchMedia={fetchMedia} session={session}/></>:error}</div></details>;
}
function Versions({material,session,readerApi,fetchMedia}:{material:Pick<AvailablePublicationContent,'previous_versions'>;session:Session;readerApi:ReaderApi;fetchMedia:MediaFetch}) {return <>{(material.previous_versions).map(previous=><PreviousVersion key={previous.url} previous={previous} session={session} readerApi={readerApi} fetchMedia={fetchMedia}/>)}</>;}
function Points({points}:{points:Point[]}) {return <>{points.map((point,index)=><li key={index}>{point.text}{point.references.map((ref,index)=><a key={index} href={ref.source_url} target="_blank" rel="noopener noreferrer"> Source</a>)}</li>)}</>;}

function availableContent(material:Material):AvailablePublicationContent|null {
  switch(material.availability) {
    case 'available': return material.content;
    case 'pending': return null;
    default: {const unreachable:never=material;return unreachable;}
  }
}

export function createOpenedMaterial({readerApi,fetchMedia,onViewed,onChanged,acceptStorage}:{readerApi:ReaderApi;fetchMedia:MediaFetch;onViewed:(id:string)=>void;onChanged:(changed:boolean)=>void;acceptStorage:(identity:string)=>boolean}) {
  const store=createStore({session:null as Session|null,material:null as Material|null,empty:'Select an item',message:'',originalOpen:true,processingOpen:false,workAllowed:true});
  const active=(session:Session)=>store.get().session===session&&!session.controller.signal.aborted;
  function clear(empty='Select an item') {store.get().session?.controller.abort();store.set({session:null,material:null,empty,message:'',originalOpen:true,processingOpen:false,workAllowed:store.get().workAllowed});}
  function apply(resources:ReadingResource[],session:Session,initial=false) {
    if(!active(session))return;
    let changed=false;
    for(const resource of resources) {
      if(resource.id!==session.id||!['material','filter','theses','media'].includes(resource.kind))continue;
      if(resource.state==='unavailable'){clear();return;}
      if(resource.state==='failed'){store.set({...store.get(),message:resource.message});continue;}
      if(resource.state!=='changed')continue;
      const kind=resource.kind as Part,previous=session.parts.get(kind);
      if(previous&&BigInt(previous.version)>=BigInt(resource.version))continue;
      session.parts.set(kind,{version:resource.version,data:resource.data});changed=true;
    }
    if(!changed)return;
    const text=session.parts.get('material')?.data as ReadingEntityMaterial|undefined;
    const filter=session.parts.get('filter')?.data as ReadingFilterData|undefined;
    const theses=session.parts.get('theses')?.data as ReadingThesesData|undefined;
    const media=session.parts.get('media')?.data as ReadingMediaData|undefined;
    if(!text||!filter||!theses||!media)return;
    const revision=text.availability==='available'?text.content.revision_id:null;
    if([filter,theses,media].some(part=>part.revision_id!==revision))return;
    let material:Material;
    if(text.availability==='available') {
      let display=text.content.text;
      if(filter.filter.result&&!filter.filter.result.restored)for(const fragment of filter.filter.result.removed)display=display.split(fragment).join('');
      material={...text,filter:filter.filter,theses:theses.theses,content:{...text.content,...media,display_text:display,revision_id:text.content.revision_id}};
    } else material={...text,filter:filter.filter,theses:theses.theses};
    const result=material.filter.result;
    store.set({...store.get(),material,message:'',...(initial?{originalOpen:material.originals&&(!result||result.restored||!result.removed.length&&!result.hidden)}:{})});
  }
  async function open(card:OpenCard) {
    clear('Loading item…');
    const session:Session={id:card.id,folder_id:card.folder_id,parts:new Map(),loading:true,controller:new AbortController()};
    store.set({...store.get(),session});
    try {
      const frame=(await readingMaterial({client:readerApi.client,path:{item:card.id},signal:session.controller.signal})).data;
      if(active(session)&&frame.state==='current'){if(acceptStorage(frame.storage_id))apply(frame.resources,session,true);else{clear('Storage changed · refreshing items');onChanged(false);}}
    }catch(error){if(active(session)){reportError(error,'Opened item');clear(errorMessage(error));}}
    finally{session.loading=false;}
  }
  function poll():{session:Session|null;resources:ReadingCheck[]} {
    const session=store.get().session;
    return {session,resources:!session||session.loading?[]:(['material','filter','theses','media'] as const).map(kind=>({kind,id:session.id,known_version:session.parts.get(kind)?.version??null}))};
  }
  const operations={regenerate,retryMaterial,retryTheses,retryFilter,restoreFilter};
  const intents={regenerate:'regenerate',retryMaterial:'retry_material',retryTheses:'retry_theses',retryFilter:'retry_filter',restoreFilter:'restore_filter'} as const;
  async function action(kind:keyof typeof operations,changed=false) {if(kind!=='restoreFilter'&&!store.get().workAllowed)return;const session=store.get().session;if(!session||!store.get().material)return;try{await readerApi.command({operation:intents[kind],material_id:session.id},headers=>operations[kind]({client:readerApi.client,headers,path:{item:session.id}}));if(active(session))onChanged(changed);}catch(error){reportError(error,'Opened item');if(active(session))store.set({...store.get(),message:errorMessage(error)});}}
  async function copy(url:string) {const session=store.get().session;if(!session)return;try{await copyText(url);if(active(session))store.set({...store.get(),message:'Link copied'});}catch(error){reportError(error,'Opened item');if(active(session))store.set({...store.get(),message:'Clipboard access denied by browser'});}}
  async function markViewed() {const session=store.get().session;if(!session||!store.get().material)return;try{await readerApi.command({operation:'viewed',material_id:session.id},headers=>viewed({client:readerApi.client,headers,path:{item:session.id}}));if(active(session))clear();onViewed(session.id);}catch(error){reportError(error,'Opened item');if(active(session))store.set({...store.get(),message:errorMessage(error)});}}
  function Surface() {
    const state=useStore(store),material=state.material,session=state.session;
    const content=material?availableContent(material):null,filter=material?.filter.result,theses=material?.theses.result,hasResult=!!theses,origin=theses?.provenance;
    const provenance=!origin||origin.kind==='unknown'?'Unknown model':origin.kind==='codex'?[origin.kind,origin.model,origin.reasoning_effort,origin.fast?'Fast enabled':'Fast disabled'].join(' · '):[origin.processor_id,origin.model.state==='known'?origin.model.name:'Unknown model'].join(' · ');
    const captions=material?.source.kind==='youtube';
    const completeness=!material?'':!content?'Loading item':content.loading?(captions?'Fetching subtitles and preview':'Text saved · loading attachments'):content.completeness.state==='incomplete'?'Incomplete item · '+serviceText(content.completeness.message):captions?'Subtitles and preview saved':'Fully saved';
    const filterStatus=filter?(filter.restored?'Restored by user · ':filter.hidden?'Hidden · ':filter.removed.length?'Promotional content removed · ':'Filtering complete')+filter.reason:material?.filter.diagnostic_message&&serviceText(material.filter.diagnostic_message)||'Waiting for filtering';
    return <><div id="empty" hidden={!!material}>{state.empty}</div><div id="material" hidden={!material}><p className="meta" id="completeness">{completeness}{content?.changed?' · Updated':''}{content?.source_deleted?' · Unavailable at source; local copy kept':''}</p><h1 id="title">{material?.source_url?<a href={material.source_url} target="_blank" rel="noopener noreferrer">{newsTitle(material.title)}</a>:newsTitle(material?.title||'')}</h1><p id="date">{material?(material.published_at===null?'Unknown date':new Date(material.published_at!).toLocaleString('en')):''}</p><div id="theses-panel" hidden={!theses&&material?.theses.attempt.state==='disabled'}><h2>Publication Summary</h2><p id="ai-status" role="status">{!material?'':theses?'Summary ready':material.theses.diagnostic_message&&serviceText(material.theses.diagnostic_message)||(material.theses.attempt.state==='running'?'Generating summary':'Waiting for processor')}</p><ul id="theses"><Points points={theses?.points||[]}/></ul><button id="theses-retry" disabled={!state.workAllowed} hidden={!material?.theses.diagnostic_message} onClick={()=>void action('retryTheses')}>Retry Summary</button></div><div id="filter-panel" hidden={material?.filter.attempt.state==='disabled'}><p id="filter-status" role="status">{filterStatus}</p><button id="filter-restore" hidden={!filter||filter.restored||!(filter.hidden||filter.removed.length)} onClick={()=>void action('restoreFilter',true)}>Restore Item</button><button id="filter-retry" disabled={!state.workAllowed} hidden={!material?.filter.diagnostic_message} onClick={()=>void action('retryFilter')}>Retry Filtering</button><div id="filtered-text">{filter&&!filter.hidden&&!filter.restored&&filter.removed.length?content?.display_text:''}</div></div><details id="original-panel" open={state.originalOpen} onToggle={event=>{if(event.currentTarget.open!==store.get().originalOpen)store.set({...store.get(),originalOpen:event.currentTarget.open});}}><summary>Original Publication</summary><div id="description-panel" hidden={!content?.description}><h3>Video Description</h3><div id="description">{content?.description||''}</div></div><div id="text">{content?.text||''}</div>{content&&session?<Media key={session.id+'/'+content.revision_id} id="images" material={content} session={session} fetchMedia={fetchMedia}/>:<div id="images"/>}</details><div id="versions">{content&&session&&<Versions key={session.id+'/'+content.revision_id} material={content} session={session} readerApi={readerApi} fetchMedia={fetchMedia}/>}</div><details id="processing-details" hidden={!hasResult} open={state.processingOpen} onToggle={event=>{if(event.currentTarget.open!==store.get().processingOpen)store.set({...store.get(),processingOpen:event.currentTarget.open});}}><summary>Processing Details</summary><p id="processing-provenance">{hasResult?provenance:''}</p></details><button id="regenerate" disabled={!state.workAllowed} hidden={!hasResult} onClick={()=>void action('regenerate')}>Regenerate</button><div className="actions"><button id="retry" disabled={!state.workAllowed} hidden={!content||content.completeness.state!=='incomplete'||content.loading} onClick={()=>void action('retryMaterial')}>Retry Download</button><button id="copy" onClick={()=>material&&void copy(material.source_url)}>Copy Original Link</button><button id="viewed" onClick={()=>void markViewed()}>Mark as Read</button></div><p id="message" role="status">{state.message}</p></div></>;
  }
  mountSurface(host('material').parentElement!,<Surface/>);
  return {
    workAllowed(allowed:boolean){if(store.get().workAllowed!==allowed)store.set({...store.get(),workAllowed:allowed});},
    get selection(){const session=store.get().session;return session?{id:session.id,folder_id:session.folder_id}:null;},open,clear,poll,apply,
    reconcile(cards:Card[]){const session=store.get().session;if(!session)return;const card=cards.find(card=>card.id===session.id);if(!card)clear();},
    viewed(id:string){if(store.get().session?.id===id)clear();},
  };
}
