import {SourceIcon} from './tree';
import type {SubscriptionFeed} from '../api/generated/types.gen';
import {LinkedPublication} from './linked-content';
import {PostWebUnavailable} from './post-web-unavailable';
import {ExternalAttachments} from './external-attachments';
import './opened.css';
import {Markdown} from './markdown';
import {readingPosition} from './position';
import {newsTitle} from './title';
import {copyText} from '../browser-platform';
import {useEffect,useLayoutEffect,useRef,useState} from 'react';
import {readingMaterial,regenerate,retryMaterial,retryTheses,retryFilter,viewed} from '../api/generated/sdk.gen';
import type {ThesesPoint as Point,Attachment,AvailablePublicationContent,ReplyPreview as ReplyContext} from '../api/generated/types.gen';
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
type Session={id:string;sourceTitle?:string;sourceFeed?:SubscriptionFeed;folder_id:string|undefined;parts:Map<Part,{version:string;data:unknown}>;loading:boolean;detached:boolean;reset:boolean;assetsPaused:boolean;assetEpoch:number;assets:AbortController;controller:AbortController};
type Resource={url:string;kind:Attachment['kind'];name:string;failed?:boolean};
function Media({material,fetchMedia,session,id,className,imageAlt='Publication image',optional=false}:{material:Pick<AvailablePublicationContent,'images'|'attachments'|'external_attachments'>;fetchMedia:MediaFetch;session:Session;id?:string;className?:string;imageAlt?:string;optional?:boolean}) {
  const ref=useRef<HTMLDivElement>(null),[resources,setResources]=useState<Resource[]>([]);
  useLayoutEffect(()=>()=>{for(const media of ref.current?.querySelectorAll<HTMLMediaElement>('video,audio')||[]){media.pause();media.removeAttribute('src');media.load();}},[]);
  const resourcesKey=JSON.stringify([material.images,material.attachments,material.external_attachments]);
  const loaded=useRef(new Map<string,Resource>());
  useEffect(()=>{
    let active=true;const controller=new AbortController(),signal=AbortSignal.any([controller.signal,session.controller.signal,session.assets.signal]),epoch=session.assetEpoch;
    const posters=new Set((material.external_attachments||[]).map(media=>media.poster_url));
    const inputs=[...(material.images||[]).map(url=>({url,kind:'image' as const})),...(material.attachments||[])].filter(media=>!posters.has(media.url));
    const keys=new Set(inputs.map(input=>input.kind+'/'+input.url));
    for(const [key,value] of loaded.current)if(!keys.has(key)){URL.revokeObjectURL(value.url);loaded.current.delete(key);}
    const publish=()=>{if(active){setResources(inputs.flatMap(input=>{const resource=loaded.current.get(input.kind+'/'+input.url);return resource&&!resource.failed?[resource]:[];}));}};
    publish();
    for(const input of inputs){
      const key=input.kind+'/'+input.url;
      if(loaded.current.has(key))continue;
      void(async()=>{try{
        if(session.detached||session.assetsPaused||session.controller.signal.aborted)return;
        const response=await fetchMedia(input.url,signal);if(!response.ok)return;
        const blob=await response.blob();if(!active||epoch!==session.assetEpoch||session.detached||session.assetsPaused||session.controller.signal.aborted)return;
        loaded.current.set(key,{url:URL.createObjectURL(blob),kind:input.kind,name:input.url.split('/').pop()||''});publish();
      }catch(error){if(active&&!optional&&!(error instanceof Error&&error.name==='AbortError'))reportError(error,'Opened item');}})();
    }
    return()=>{active=false;controller.abort();};
  },[resourcesKey,session,fetchMedia,session.assetsPaused,session.assetEpoch,optional]);
  useEffect(()=>{const owned=loaded.current;return()=>{for(const resource of owned.values())URL.revokeObjectURL(resource.url);owned.clear();};},[]);
  return <div ref={ref} {...(id?{id}:{})} {...(className?{className}:{})}>{resources.map(resource=>resource.kind==='image'?<img key={resource.url} src={resource.url} alt={imageAlt} onError={optional?()=>{resource.failed=true;setResources(current=>current.filter(item=>item!==resource));}:undefined}/>:resource.kind==='video'||resource.kind==='circle'?<video key={resource.url} src={resource.url} controls preload="metadata" className={resource.kind==='circle'?'circle':undefined} aria-label={resource.kind==='circle'?'Telegram video message':'Publication video'}/>:resource.kind==='audio'?<audio key={resource.url} src={resource.url} controls preload="metadata" aria-label="Publication audio"/>:<a key={resource.url} href={resource.url} download={resource.name}>Download Attachment</a>)}</div>;
}
type PublicationBody = Pick<AvailablePublicationContent,'text'|'images'|'attachments'|'linked_content'>;
function postMedia<T extends Pick<AvailablePublicationContent,'images'|'attachments'|'linked_content'>>(material:T):T {
  const articleUrls=new Set(material.linked_content?.articles.flatMap(article=>article.media_urls)||[]);
  return {...material,attachments:material.attachments.filter(media=>!articleUrls.has(media.url))};
}
function PublicationText({material,session,fetchMedia,sourceUrl,inlineMedia=true}:{material:PublicationBody;session:Session;fetchMedia:MediaFetch;sourceUrl:string;inlineMedia?:boolean}) {
  return material.linked_content?<LinkedPublication content={material.linked_content} renderMedia={urls=>inlineMedia&&<Media material={{images:[],attachments:material.attachments.filter(media=>urls.includes(media.url))}} session={session} fetchMedia={fetchMedia}/>}/>:<Markdown text={material.text} sourceUrl={sourceUrl}/>;
}
function ReplyPreview({preview,id}:{preview:ReplyContext;id?:string}) {
  const [expanded,setExpanded]=useState(false);
  let href:string|undefined;
  try {const url=new URL(preview.source_url||'');if(['http:','https:'].includes(url.protocol)&&!url.username&&!url.password)href=url.href;}catch{/* Missing/unsafe source remains readable. */}
  const body=<><strong>{preview.author||'Ответ на сообщение'}</strong><span className="reply-preview-text" data-reading-paragraph>{preview.text}</span></>;
  return <section id={id} className={'reply-preview'+(expanded?' expanded':'')} aria-label="Ответ на сообщение" data-reading-region>
    {href?<a className="reply-preview-link" href={href} target="_blank" rel="noopener noreferrer">{body}</a>:<div className="reply-preview-link">{body}</div>}
    {preview.text&&<button type="button" className="reply-preview-expand" aria-expanded={expanded} onClick={()=>setExpanded(value=>!value)}>{expanded?'Свернуть':'Весь фрагмент'}</button>}
  </section>;
}
function Points({points}:{points:Point[]}) {return <>{points.map((point,index)=><li key={index}>{point.text}{point.references.map((ref,index)=><a key={index} href={ref.source_url} target="_blank" rel="noopener noreferrer"> Source</a>)}</li>)}</>;}

function availableContent(material:Material):AvailablePublicationContent|null {
  switch(material.availability) {
    case 'available': return material.content;
    case 'pending': return null;
    default: {const unreachable:never=material;return unreachable;}
  }
}

export function createOpenedMaterial({readerApi,fetchMedia,onViewed,onChanged,acceptStorage,getFeed}:{getFeed:(id:string)=>SubscriptionFeed|undefined;readerApi:ReaderApi;fetchMedia:MediaFetch;onViewed:(id:string)=>void;onChanged:(changed:boolean)=>void;acceptStorage:(identity:string)=>boolean}) {
  const store=createStore({session:null as Session|null,material:null as Material|null,empty:'Select an item',message:'',processingOpen:false,workAllowed:true,pending:null as Material|null,retained:false,generation:0});
  const active=(session:Session)=>store.get().session===session&&!session.controller.signal.aborted;
  function clear(empty='Select an item') {store.get().session?.controller.abort();store.set({session:null,material:null,empty,message:'',processingOpen:false,workAllowed:store.get().workAllowed,pending:null,retained:false,generation:0});}
  function update(next:ReturnType<typeof store.get>) {const restore=readingPosition();store.set(next);restore();}
  function retain(message='Item removed · only content already loaded in this tab is available') {
    const state=store.get(),session=state.session;
    if(!session)return;
    if(!state.material){clear();return;}
    session.detached=true;session.controller.abort();session.parts.clear();
    update({...state,retained:true,pending:null,message});
  }
  function apply(resources:ReadingResource[],session:Session) {
    if(!active(session))return;
    let changed=false;
    for(const resource of resources) {
      if(resource.id!==session.id||!['material','filter','theses','media'].includes(resource.kind))continue;
      if(resource.state==='unavailable'){retain();return;}
      if(resource.state==='failed'){update({...store.get(),message:resource.message});continue;}
      if(resource.state!=='changed')continue;
      const kind=resource.kind as Part,previous=session.parts.get(kind);
      if(previous&&BigInt(previous.version)>=BigInt(resource.version))continue;
      session.parts.set(kind,{version:resource.version,data:resource.data});changed=true;
    }
    if(!changed)return;
    const text=session.parts.get('material')?.data as ReadingEntityMaterial|undefined;
    if(!text)return;
    const sourceFeed=getFeed(text.feed_id);if(sourceFeed){session.sourceFeed=sourceFeed;session.sourceTitle=sourceFeed.title;}
    const revision=text.availability==='available'?text.content.revision_id:null;
    const filter=session.parts.get('filter')?.data as ReadingFilterData|undefined;
    const theses=session.parts.get('theses')?.data as ReadingThesesData|undefined;
    const media=session.parts.get('media')?.data as ReadingMediaData|undefined;
    const state=store.get(),shown=state.material;
    const shownRevision=shown?.availability==='available'?shown.content.revision_id:null;
    const newText=shown?.availability==='available'&&(session.reset||text.availability!=='available'||revision!==shownRevision||text.content.text!==shown.content.text||text.content.description!==shown.content.description||text.title!==shown.title);
    // A new source revision remains staged. Each visible part can otherwise
    // update independently; mismatched replies never borrow another revision.
    const base=newText?shown:text;
    if(!base)return;
    const baseRevision=base.availability==='available'?base.content.revision_id:null;
    const shownFilter=newText?shown.filter:filter?.revision_id===baseRevision?filter.filter:shownRevision===baseRevision?shown?.filter:undefined;
    const shownTheses=newText?shown.theses:theses?.revision_id===baseRevision?theses.theses:shownRevision===baseRevision?shown?.theses:undefined;
    const shownMedia=newText&&shown.availability==='available'?shown.content:media?.revision_id===baseRevision?media:shown?.availability==='available'&&shownRevision===baseRevision?shown.content:undefined;
    function compose(source:ReadingEntityMaterial,f:ReadingFilterData['filter'],t:ReadingThesesData['theses'],m:ReadingMediaData|AvailablePublicationContent):Material {
      if(source.availability!=='available')return {...source,filter:f,theses:t};
      let display=source.content.text;
      if(f.result)for(const fragment of f.result.removed)display=display.split(fragment).join('');
      return {...source,filter:f,theses:t,content:{...source.content,images:m.images,attachments:m.attachments,external_attachments:m.external_attachments,display_text:display}};
    }
    if(!shownFilter||!shownTheses||!shownMedia)return;
    const material=compose(base,shownFilter,shownTheses,shownMedia);
    const pending=newText&&filter?.revision_id===revision&&theses?.revision_id===revision&&media?.revision_id===revision?compose(text,filter.filter,theses.theses,media):null;
    update({...state,material,pending,message:newText?'A new version is available. Updates for the displayed version may be unavailable.':''});
  }
  function showLatest(){const state=store.get();if(state.pending&&!state.retained){state.session!.reset=false;state.session!.assetsPaused=false;update({...state,material:state.pending,pending:null,message:'',generation:state.generation+1});}}
  async function open(card:OpenCard) {
    clear('Loading item…');
    const session:Session={id:card.id,sourceTitle:card.feed_title,folder_id:card.folder_id,parts:new Map(),loading:true,detached:false,reset:false,assetsPaused:false,assetEpoch:0,assets:new AbortController(),controller:new AbortController()};
    store.set({...store.get(),session});
    try {
      const frame=(await readingMaterial({client:readerApi.client,path:{item:card.id},signal:session.controller.signal})).data;
      if(active(session)&&frame.state==='current'){if(acceptStorage(frame.storage_id))apply(frame.resources,session);else{clear('Storage changed · refreshing items');onChanged(false);}}
    }catch(error){if(active(session)){reportError(error,'Opened item');clear(errorMessage(error));}}
    finally{session.loading=false;}
  }
  function poll():{session:Session|null;resources:ReadingCheck[]} {
    const session=store.get().session;
    return {session,resources:!session||session.loading||session.detached?[]:(['material','filter','theses','media'] as const).map(kind=>({kind,id:session.id,known_version:session.parts.get(kind)?.version??null}))};
  }
  const operations={regenerate,retryMaterial,retryTheses,retryFilter};
  const intents={regenerate:'regenerate',retryMaterial:'retry_material',retryTheses:'retry_theses',retryFilter:'retry_filter'} as const;
  async function action(kind:keyof typeof operations,changed=false) {if(store.get().pending||!store.get().workAllowed)return;const session=store.get().session;if(!session||session.detached||!store.get().material)return;try{await readerApi.command({operation:intents[kind],material_id:session.id},headers=>operations[kind]({client:readerApi.client,headers,path:{item:session.id}}));if(active(session))onChanged(changed);}catch(error){reportError(error,'Opened item');if(active(session))store.set({...store.get(),message:errorMessage(error)});}}
  async function copy(url:string) {const session=store.get().session;if(!session)return;try{await copyText(url);if(active(session))store.set({...store.get(),message:'Link copied'});}catch(error){reportError(error,'Opened item');if(active(session))store.set({...store.get(),message:'Clipboard access denied by browser'});}}
  async function markViewed() {const session=store.get().session;if(!session||session.detached||!store.get().material)return;try{await readerApi.command({operation:'viewed',material_id:session.id},headers=>viewed({client:readerApi.client,headers,path:{item:session.id}}));if(active(session))retain();onViewed(session.id);}catch(error){reportError(error,'Opened item');if(active(session))store.set({...store.get(),message:errorMessage(error)});}}
  function Surface() {
    const state=useStore(store),material=state.material,session=state.session;
    const content=material?availableContent(material):null,filter=material?.filter.result,theses=material?.theses.result,hasResult=!!theses,origin=theses?.provenance;
    const provenance=!origin||origin.kind==='unknown'?'Unknown model':origin.kind==='codex'?[origin.kind,origin.model,origin.reasoning_effort,origin.fast?'Fast enabled':'Fast disabled'].join(' · '):[origin.processor_id,origin.model.state==='known'?origin.model.name:'Unknown model'].join(' · ');
    const shortened=!!filter&&!filter.hidden&&filter.removed.length>0;
    const originalText=<div id="text" data-reading-region>{content&&session?<PublicationText material={content} sourceUrl={material?.source_url||''} session={session} fetchMedia={fetchMedia} inlineMedia={!shortened}/>:null}</div>;
    const feed=material?getFeed(material.feed_id)||session?.sourceFeed:undefined;
    const avatar=feed&&'avatar_url' in feed?feed.avatar_url:undefined;
    const sourceKind=material?.source.kind||'rss';
    const captions=material?.source.kind==='youtube';
    const completeness=!material?'':!content?'Loading item':content.loading?(captions?'Fetching subtitles and preview'+(content.completeness.state==='incomplete'?' · '+content.completeness.message:''):'Text saved · loading attachments'):content.completeness.state==='incomplete'?(content.completeness.reason==='post_web_unavailable'?'Пост недоступен для веб-просмотра':content.completeness.reason==='external_attachment'?'Текст сохранён · вложения доступны в Telegram':'Incomplete item · '+content.completeness.message):captions?'Subtitles and preview saved':'Fully saved';
    const filterStatus=filter?(filter.hidden?'Hidden · ':filter.removed.length?'Promotional content removed · ':'Filtering complete')+filter.reason:material?.filter.diagnostic_message&&material.filter.diagnostic_message||'Waiting for filtering';
    return <><div id="empty" hidden={!!material}>{state.empty}</div><div id="material" hidden={!material}><div id="material-update" hidden={!state.pending}><span>A new version is available </span><button id="material-update-button" onClick={showLatest}>Show new version</button></div><p id="material-retained" hidden={!state.retained}>Local content kept in this tab · missing parts are unavailable</p><p className="meta" id="completeness">{completeness}{content?.changed?' · Updated':''}{content?.source_deleted?' · Unavailable at source; local copy kept':''}</p><h1 id="title">{material?.source_url?<a href={material.source_url} target="_blank" rel="noopener noreferrer">{content?.post_web_unavailable&&!content.text?'Публикация в Telegram':newsTitle(material.title)}</a>:newsTitle(material?.title||'')}</h1><div id="material-byline">{material&&<><div className="material-feed-avatar"><SourceIcon kind={sourceKind}/>{avatar&&session&&<Media material={{images:[avatar],attachments:[],external_attachments:[]}} session={session} fetchMedia={fetchMedia} className="material-feed-image" imageAlt="" optional/>}</div><div className="material-source-details"><strong id="material-feed-title">{feed?.title||session?.sourceTitle||'Unknown source'}</strong><p id="date">{material.published_at===null?'Unknown date':new Date(material.published_at).toLocaleString('en')}</p></div></>}</div>{content?.reply_preview&&session&&<ReplyPreview id="reply-preview" key={'reply/'+session.id+'/'+content.revision_id+'/'+state.generation} preview={content.reply_preview}/>}<div id="theses-panel" hidden={!theses&&material?.theses.attempt.state==='disabled'}><h2>Publication Summary</h2><p id="ai-status" role="status">{!material?'':theses?'Summary ready':material.theses.diagnostic_message&&material.theses.diagnostic_message||(material.theses.attempt.state==='running'?'Generating summary':'Waiting for processor')}</p><ul id="theses"><Points points={theses?.points||[]}/></ul><button id="theses-retry" disabled={!state.workAllowed||state.retained||!!state.pending} hidden={!material?.theses.diagnostic_message} onClick={()=>void action('retryTheses')}>Retry Summary</button></div><div id="filter-panel" hidden={material?.filter.attempt.state==='disabled'}><p id="filter-status" role="status">{filterStatus}</p><button id="filter-retry" disabled={!state.workAllowed||state.retained||!!state.pending} hidden={!material?.filter.diagnostic_message} onClick={()=>void action('retryFilter')}>Retry Filtering</button><div id="filtered-text" data-reading-region><Markdown sourceUrl={material?.source_url} text={filter&&!filter.hidden&&filter.removed.length?content?.display_text||'':''}/></div></div><div id="description-panel" hidden={!content?.description}><h3>Video Description</h3><div id="description" data-reading-region><Markdown sourceUrl={material?.source_url} text={content?.description||''}/></div></div>{content?.post_web_unavailable&&<PostWebUnavailable id="post-web-unavailable" sourceUrl={material?.source_url||''} sourceTitle={session?.sourceTitle} retained={!!content.text||!!content.attachments.length||!!content.images.length}/>}<div id="external-attachments">{content&&session&&<ExternalAttachments attachments={content.external_attachments} poster={url=><Media material={{images:[],attachments:[{url,kind:'image',mime:'image/jpeg'}]}} fetchMedia={fetchMedia} session={session}/>}/>}</div>{shortened?<details id="original-panel" key={content?.revision_id+'/'+state.generation}><summary>Original Publication</summary>{originalText}</details>:originalText}{content&&session?<Media key={session.id+'/'+content.revision_id+'/'+state.generation} id="images" material={shortened?content:postMedia(content)} session={session} fetchMedia={fetchMedia}/>:<div id="images"/>}<details id="processing-details" hidden={!hasResult} open={state.processingOpen} onToggle={event=>{if(event.currentTarget.open!==store.get().processingOpen)store.set({...store.get(),processingOpen:event.currentTarget.open});}}><summary>Processing Details</summary><p id="processing-provenance">{hasResult?provenance:''}</p></details><button id="regenerate" disabled={!state.workAllowed||state.retained||!!state.pending} hidden={!hasResult} onClick={()=>void action('regenerate')}>Regenerate</button><div className="actions"><button id="retry" disabled={!state.workAllowed||state.retained||!!state.pending} hidden={!content||content.completeness.state!=='incomplete'||['external_attachment','post_web_unavailable'].includes(content.completeness.reason)||content.loading&&!captions} onClick={()=>void action('retryMaterial')}>Retry Download</button><button id="copy" onClick={()=>material&&void copy(material.source_url)}>Copy Original Link</button><button id="viewed" disabled={state.retained} onClick={()=>void markViewed()}>Mark as Read</button></div><p id="message" role="status">{state.message}</p></div></>;
  }
  mountSurface(host('material').parentElement!,<Surface/>);
  return {
    workAllowed(allowed:boolean){if(store.get().workAllowed!==allowed)store.set({...store.get(),workAllowed:allowed});},
    get selection(){const session=store.get().session;return session?{id:session.id,folder_id:session.folder_id}:null;},open,clear,poll,apply,
    reconcile(_cards:Card[]){/* Feed presentation may change without closing the material. */const state=store.get();if(state.material){const feed=getFeed(state.material.feed_id);if(feed&&state.session){state.session.sourceFeed=feed;state.session.sourceTitle=feed.title;}store.set({...state});}},
    viewed(id:string){if(store.get().session?.id===id)retain();},
    disconnected(){const state=store.get();if(!state.material){clear();return;}const session=state.session!;session.assetEpoch++;session.assets.abort();session.assets=new AbortController();session.assetsPaused=true;},
    connected(){const state=store.get(),session=state.session;if(session?.assetsPaused&&!session.reset&&!session.detached){session.assetsPaused=false;store.set({...state});}},
    reset(){const state=store.get();if(!state.material){clear();return;}const session=state.session!;session.assetEpoch++;session.assets.abort();session.assets=new AbortController();session.assetsPaused=true;session.reset=true;session.parts.clear();update({...state,pending:null});},
  };
}
