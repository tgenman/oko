import type {Navigation} from './tree';
import './opened.css';
import './linked-content.css';
import {Markdown} from './markdown';
import {readingPosition} from './position';
import {newsTitle} from './title';
import {copyText} from '../browser-platform';
import {useEffect,useLayoutEffect,useRef,useState} from 'react';
import {publication,regenerate,retryMaterial,retryTheses,retryFilter,viewed} from '../api/generated/sdk.gen';
import type {PublicationResponse} from '../api/generated/types.gen';
import type {CardView as Card} from './resources';
import {ReaderError,type ReaderApi} from '../api/reader';
import {mountSurface} from '../renderer';
import {reportError} from '../status/debug';
import {createStore,useStore,host,errorMessage} from './store';

type MediaFetch=(path:string,signal?:AbortSignal,cache?:RequestCache)=>Promise<Response>;
type Attachment={kind:PublicationResponse['media'][number]['kind'];url:string;mime:string};
type QuoteContext={author:string|null;text:string;source_url:string|null};
type Publication = PublicationResponse;
type DisplayContent = PublicationResponse & {images: string[]; attachments: Attachment[]};
type OpenCard=Pick<Card,'id'>&Partial<Card>;
type Session={id:string;sourceTitle?:string;sourceFeed?:Navigation['feeds'][number];folder_id:string|undefined;etag:string|null;loading:boolean;detached:boolean;reset:boolean;assetsPaused:boolean;assetEpoch:number;assets:AbortController;controller:AbortController};
type Resource={url:string;kind:Attachment['kind'];name:string;failed?:boolean};
function Media({material,fetchMedia,session,id,className,imageAlt='Publication image',optional=false}:{material:Pick<DisplayContent,'images'|'attachments'>;fetchMedia:MediaFetch;session:Session;id?:string;className?:string;imageAlt?:string;optional?:boolean}) {
  const ref=useRef<HTMLDivElement>(null),[resources,setResources]=useState<Resource[]>([]);
  useLayoutEffect(()=>()=>{for(const media of ref.current?.querySelectorAll<HTMLMediaElement>('video,audio')||[]){media.pause();media.removeAttribute('src');media.load();}},[]);
  const resourcesKey=JSON.stringify([material.images,material.attachments]);
  const loaded=useRef(new Map<string,Resource>());
  useEffect(()=>{
    let active=true;const controller=new AbortController(),signal=AbortSignal.any([controller.signal,session.controller.signal,session.assets.signal]),epoch=session.assetEpoch;
    const inputs=[...(material.images||[]).map(url=>({url,kind:'image' as const})),...(material.attachments||[])];
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
  return <div ref={ref} {...(id?{id}:{})} {...(className?{className}:{})}>{resources.map(resource=>resource.kind==='image'?<img key={resource.url} src={resource.url} alt={imageAlt} onError={optional?()=>{resource.failed=true;setResources(current=>current.filter(item=>item!==resource));}:undefined}/>:resource.kind==='video'||resource.kind==='circle'?<video key={resource.url} src={resource.url} controls preload="metadata" className={resource.kind==='circle'?'circle':undefined} aria-label={resource.kind==='circle'?'Video message':'Publication video'}/>:resource.kind==='audio'?<audio key={resource.url} src={resource.url} controls preload="metadata" aria-label="Publication audio"/>:<a key={resource.url} href={resource.url} download={resource.name}>Download Attachment</a>)}</div>;
}
type PublicationBody = Pick<DisplayContent,'content'|'linked_publications'|'media'|'jobs'>;
function SubtitleText({url,session,fetchMedia}:{url:string|null;session:Session;fetchMedia:MediaFetch}) {
  const [text,setText]=useState('');
  useEffect(()=>{
    if(!url||session.detached||session.assetsPaused)return;
    const controller=new AbortController(),epoch=session.assetEpoch;
    const signal=AbortSignal.any([controller.signal,session.controller.signal,session.assets.signal]);
    void fetchMedia(url,signal).then(response=>response.ok?response.text():'').then(value=>{
      if(!signal.aborted&&!session.detached&&epoch===session.assetEpoch)setText(value);
    }).catch(error=>{if(!signal.aborted)reportError(error,'Opened item');});
    return()=>controller.abort();
  },[url,session,fetchMedia,session.assetsPaused,session.assetEpoch]);
  return <Markdown text={text}/>;
}
function postMedia<T extends Pick<DisplayContent,'images'|'attachments'|'linked_publications'|'media'>>(material:T):T {
  const articleIds=new Set(material.linked_publications.flatMap(article=>(article.content.blocks||[]).filter(block=>block.type==='media').map(block=>block.media_id)));
  const articleUrls=new Set(material.media.filter(media=>articleIds.has(media.id)&&media.url).map(media=>media.url!));
  return {...material,images:material.images.filter(url=>!articleUrls.has(url)),attachments:material.attachments.filter(media=>!articleUrls.has(media.url))};
}
function PublicationText({material,session,fetchMedia,sourceUrl,revision,inlineMedia=true}:{material:PublicationBody;session:Session;fetchMedia:MediaFetch;sourceUrl:string;revision:number;inlineMedia?:boolean}) {
  const blocks=material.content.blocks||[];
  const hasMedia=blocks.some(block=>block.type==='media')||material.linked_publications.some(article=>(article.content.blocks||[]).some(block=>block.type==='media'));
  if(blocks.some(block=>block.type==='text'||block.type==='quote'||block.type==='media'||block.type==='publication'||block.type==='external_media')){
    const render=(values:typeof blocks,base:string,linked:boolean)=>values.map((block,index)=>{
      if(block.type==='text'&&block.role==='body')return <Markdown key={index} text={block.text} sourceUrl={base}/>;
      if(block.type==='text'&&block.role==='description')return <section key={index} id={!linked?'description-panel':undefined}><h3>Description</h3><div id={!linked?'description':undefined}><Markdown text={block.text} sourceUrl={base}/></div></section>;
      if(block.type==='quote')return <QuoteBlock key={session.id+'/'+revision+'/'+index} id={!linked&&index===values.findIndex(value=>value.type==='quote')?'reply-preview':undefined} preview={block}/>;
      if(block.type==='media'){
        const file=material.media.find(value=>value.id===block.media_id);
        const occurrence=values.slice(0,index).filter(previous=>previous.type==='media'&&previous.media_id===block.media_id).length;
        return <div key={'media/'+block.media_id+'/'+occurrence}>{block.source_url&&<a href={block.source_url} target="_blank" rel="noopener noreferrer">Original file</a>}{block.source_url&&block.publication_url&&' · '}{block.publication_url&&<a href={block.publication_url} target="_blank" rel="noopener noreferrer">Original publication</a>}{block.role==='subtitles'?<SubtitleText key={session.id+'/'+block.media_id+'/'+(file?.url||'')} url={file?.url||null} session={session} fetchMedia={fetchMedia}/>:inlineMedia&&file?.url&&<Media material={{images:file.kind==='image'?[file.url]:[],attachments:file.kind==='image'?[]:[{url:file.url,kind:file.kind,mime:file.mime||'application/octet-stream'}]}} session={session} fetchMedia={fetchMedia}/>}</div>;
      }
      if(block.type==='external_media'){
        const poster=material.media.find(value=>value.id===block.poster_media_id);
        return <section key={index} className="external-attachment" aria-label="External media">{inlineMedia&&poster?.url&&<Media material={{images:[poster.url],attachments:[]}} session={session} fetchMedia={fetchMedia} className="external-attachment-art" optional/>}<div className="external-attachment-copy"><strong>{block.title||'External media'}</strong>{block.author&&<p>{block.author}</p>}{block.duration&&<p>{block.duration}</p>}{block.size_label&&<p>{block.size_label}</p>}{block.restriction==='size_limit'?<p>File exceeds the available size limit.</p>:block.reason==='public_preview_unavailable'&&<p>Available at the source; no local copy.</p>}<a href={block.url} target="_blank" rel="noopener noreferrer">Open original ↗</a></div></section>;
      }
      if(block.type!=='publication'||linked)return null;
      const article=material.linked_publications.find(value=>value.id===block.publication_id);
      if(!article)return <a key={index} href={block.source_url} target="_blank" rel="noopener noreferrer">Original publication</a>;
      const problem=material.jobs.find(job=>job.operation==='fetch'&&job.object_id===article.id&&(job.status==='failed'||job.status==='unavailable'));
      return <section key={article.id} className="linked-publication" aria-label="Linked publication"><header><a href={article.source_url} target="_blank" rel="noopener noreferrer">Original publication ↗</a></header><div className="linked-publication-body"><h2>{article.title}</h2>{problem&&<p role="status">{problem.reason_message||'Publication unavailable'}</p>}{render(article.content.blocks||[],article.source_url,true)}</div></section>;
    });
    return <div id={inlineMedia&&hasMedia?'images':undefined}>{render(blocks,sourceUrl,false)}</div>;
  }
  return null;
}
function QuoteBlock({preview,id}:{preview:QuoteContext;id?:string}) {
  const [expanded,setExpanded]=useState(false);
  let href:string|undefined;
  try {const url=new URL(preview.source_url||'');if(['http:','https:'].includes(url.protocol)&&!url.username&&!url.password)href=url.href;}catch{/* Missing/unsafe source remains readable. */}
  const body=<><strong>{preview.author||'Цитата'}</strong><span className="reply-preview-text" data-reading-paragraph>{preview.text}</span></>;
  return <section id={id} className={'reply-preview'+(expanded?' expanded':'')} aria-label="Цитата" data-reading-region>
    {href?<a className="reply-preview-link" href={href} target="_blank" rel="noopener noreferrer">{body}</a>:<div className="reply-preview-link">{body}</div>}
    {preview.text&&<button type="button" className="reply-preview-expand" aria-expanded={expanded} onClick={()=>setExpanded(value=>!value)}>{expanded?'Свернуть':'Весь фрагмент'}</button>}
  </section>;
}
type SummaryReference={material_id:string;source_url:string|null};
function Points({points,references}:{points:string[];references:SummaryReference[][]}) {return <>{points.map((point,index)=><li key={index}>{point}{(references[index]||[]).map((ref,i)=>ref.source_url?<a key={i} href={ref.source_url} target="_blank" rel="noopener noreferrer"> Source</a>:<span key={i}> Source unavailable ({ref.material_id})</span>)}</li>)}</>;}

function summaryResult(material:Publication):{points:string[];references:SummaryReference[][]}|null {
  const raw=material.summarized_content.points;
  if(!raw)return null;
  const sources=new Map([[material.id,material.source_url],...material.linked_publications.map(article=>[article.id,article.source_url] as const)]);
  const points:string[]=[],references:SummaryReference[][]=[];
  for(const value of raw){
    points.push(value.text);
    references.push(value.source_publication_ids.map(id=>({material_id:id,source_url:sources.get(id)||null})));
  }
  return {points,references};
}

function resultStale(material:Publication|null, section:'filter'|'summary'):boolean {
  const value=material?.processing_metadata[section];
  return !!value&&typeof value==='object'&&'stale'in value&&value.stale===true;
}

function summaryProvenance(material:Publication):string {
  const value=material.processing_metadata.summary;
  if(!value||typeof value!=='object')return 'Unknown model';
  const processor='processor'in value&&typeof value.processor==='string'?value.processor:null;
  const model='model'in value&&typeof value.model==='string'?value.model:null;
  if(!processor&&!model)return 'Unknown model';
  const parameters='parameters'in value&&value.parameters&&typeof value.parameters==='object'?value.parameters:value;
  const effort=parameters&&'reasoning_effort'in parameters&&typeof parameters.reasoning_effort==='string'?parameters.reasoning_effort:null;
  const fast=parameters&&'fast'in parameters&&typeof parameters.fast==='boolean'?(parameters.fast?'Fast enabled':'Fast disabled'):null;
  return [processor,model,effort,fast].filter(Boolean).join(' · ');
}

function availableContent(material:Publication):DisplayContent|null {
  if(material.status==='pending_collection'&&!material.content.blocks?.length)return null;
  const subtitleIds=new Set([...(material.content.blocks||[]),...material.linked_publications.flatMap(article=>article.content.blocks||[])].filter(block=>block.type==='media'&&block.role==='subtitles').map(block=>block.media_id));
  return {...material,
    images:material.media.filter(m=>m.role==='image'&&m.url!==null).map(m=>m.url!),
    attachments:material.media.filter(m=>m.role==='attachment'&&m.url!==null&&!subtitleIds.has(m.id)).map(m=>({kind:m.kind,url:m.url!,mime:m.mime||'application/octet-stream'}))};
}

export function createOpenedMaterial({readerApi,fetchMedia,onViewed,onChanged,getFeed,getStorage}:{getStorage:()=>string|null;getFeed:(id:string)=>Navigation['feeds'][number]|undefined;readerApi:ReaderApi;fetchMedia:MediaFetch;onViewed:(id:string)=>void;onChanged:(changed:boolean)=>void}) {
  const store=createStore({session:null as Session|null,material:null as Publication|null,empty:'Select an item',message:'',processingOpen:false,workAllowed:true,pending:null as Publication|null,retained:false,generation:0});
  const active=(session:Session)=>store.get().session===session&&!session.controller.signal.aborted;
  function clear(empty='Select an item') {store.get().session?.controller.abort();store.set({session:null,material:null,empty,message:'',processingOpen:false,workAllowed:store.get().workAllowed,pending:null,retained:false,generation:0});}
  function update(next:ReturnType<typeof store.get>) {const restore=readingPosition();store.set(next);restore();}
  function retain(message='Item removed · only content already loaded in this tab is available') {
    const state=store.get(),session=state.session;
    if(!session)return;
    if(!state.material){clear();return;}
    session.detached=true;session.controller.abort();
    update({...state,retained:true,pending:null,message});
  }
  function showLatest(){const state=store.get();if(state.pending&&!state.retained){state.session!.reset=false;state.session!.assetsPaused=false;update({...state,material:state.pending,pending:null,message:'',generation:state.generation+1});}}
  function acceptPublication(value:PublicationResponse,session:Session) {
    const next=value,state=store.get(),shown=state.material;
    const feed=getFeed(next.feed_id);if(feed){session.sourceFeed=feed;session.sourceTitle=feed.title;}
    const linked=(value:Publication)=>value.linked_publications.map(article=>[article.id,article.title,article.source_url,article.content.blocks]);
    const textMedia=(value:Publication)=>{const ids=new Set((value.content.blocks||[]).filter(block=>block.type==='media'&&block.role==='subtitles').map(block=>block.media_id));return value.media.filter(file=>ids.has(file.id)&&file.status==='ready').map(file=>[file.id,file.url]);};
    const changedTextMedia=!!shown&&!!shown.summarized_content.points&&JSON.stringify(textMedia(shown))!==JSON.stringify(textMedia(next));
    const changed=!!shown&&!!availableContent(shown)&&(changedTextMedia||session.reset||!availableContent(next)||JSON.stringify(next.content.blocks)!==JSON.stringify(shown.content.blocks)||JSON.stringify(linked(next))!==JSON.stringify(linked(shown))||next.title!==shown.title);
    if(changed)update({...state,pending:next,message:'A new version is available. Updates for the displayed version may be unavailable.'});
    else update({...state,material:next,pending:null,message:''});
  }
  async function load(session:Session) {
    if(session.loading||session.detached||!active(session))return;
    const storage=getStorage();if(!storage)return;
    session.loading=true;
    const epoch=session.assetEpoch;
    try {
      const reply=await publication({client:readerApi.client,path:{item:session.id},signal:session.controller.signal,throwOnError:false,headers:{'X-Oko-Storage-Id':storage,...(session.etag?{'If-None-Match':session.etag}:{})}});
      if(!active(session)||epoch!==session.assetEpoch||storage!==getStorage())return;
      if(reply.response?.status===304)return;
      if(reply.response?.status===404){retain();return;}
      if(reply.error)throw reply.error;
      if(!reply.data)throw new Error('Publication temporarily unavailable');
      acceptPublication(reply.data,session);
      session.etag=reply.response?.headers.get('etag')??null;
    }catch(error){if(active(session)&&epoch===session.assetEpoch&&storage===getStorage()){if(error instanceof ReaderError&&error.code==='storage_changed')return;if(error instanceof ReaderError&&error.code==='resource_not_found'){retain();return;}reportError(error,'Opened item');if(store.get().material)update({...store.get(),message:errorMessage(error)});else clear(errorMessage(error));}}
    finally{session.loading=false;}
  }
  async function open(card:OpenCard) {
    clear('Loading item…');
    const session:Session={id:card.id,sourceTitle:card.feed_title,folder_id:card.folder_id,etag:null,loading:false,detached:false,reset:false,assetsPaused:false,assetEpoch:0,assets:new AbortController(),controller:new AbortController()};
    store.set({...store.get(),session});
    await load(session);
  }
  function refresh() {
    const session=store.get().session;
    if(session)void load(session);
  }
  const operations={regenerate,retryMaterial,retryTheses,retryFilter};
  const intents={regenerate:'regenerate',retryMaterial:'retry_material',retryTheses:'retry_theses',retryFilter:'retry_filter'} as const;
  async function action(kind:keyof typeof operations,changed=false) {if(store.get().pending||!store.get().workAllowed)return;const session=store.get().session;if(!session||session.detached||!store.get().material)return;try{await readerApi.command({operation:intents[kind],material_id:session.id},headers=>operations[kind]({client:readerApi.client,headers,path:{item:session.id}}));if(active(session))onChanged(changed);}catch(error){reportError(error,'Opened item');if(active(session))store.set({...store.get(),message:errorMessage(error)});}}
  async function copy(url:string) {const session=store.get().session;if(!session)return;try{await copyText(url);if(active(session))store.set({...store.get(),message:'Link copied'});}catch(error){reportError(error,'Opened item');if(active(session))store.set({...store.get(),message:'Clipboard access denied by browser'});}}
  async function markViewed() {const session=store.get().session;if(!session||session.detached||!store.get().material)return;try{await readerApi.command({operation:'viewed',material_id:session.id},headers=>viewed({client:readerApi.client,headers,path:{item:session.id}}));onViewed(session.id);}catch(error){reportError(error,'Opened item');if(active(session))store.set({...store.get(),message:errorMessage(error)});}}
  function Surface() {
    const state=useStore(store),material=state.material,session=state.session;
    const content=material?availableContent(material):null,filter=material?.filtered_content.hidden===undefined?null:material.filtered_content,theses=material?summaryResult(material):null,hasResult=!!theses;
    const filterJob=material?.jobs.find(job=>job.operation==='filter'&&job.object_id===material.id&&job.status!=='cancelled');
    const filterError=filterJob?.reason_message||((filterJob?.status==='failed'||filterJob?.status==='unavailable')?(filterJob.reason_code||'Filtering unavailable'):null);
    const thesisJob=material?.jobs.find(job=>job.operation==='theses'&&job.object_id===material.id&&job.status!=='cancelled');
    const thesisError=thesisJob?.reason_message||((thesisJob?.status==='failed'||thesisJob?.status==='unavailable')?(thesisJob.reason_code||'Summary unavailable'):null);
    const provenance=material?summaryProvenance(material):'Unknown model';
    const shortened=!!filter&&!filter.hidden&&(filter.removed?.length||0)>0;
    const filteredText=(filter?.blocks||[]).filter(block=>block.type==='text'&&block.role==='body').map(block=>block.text).join('\n\n');
    const commonMedia=!shortened&&!!content&&((content.content.blocks||[]).some(block=>block.type==='media')||content.linked_publications.some(article=>(article.content.blocks||[]).some(block=>block.type==='media')));
    const originalText=<div id="text" data-reading-region>{content&&session?<PublicationText revision={state.generation} material={content} sourceUrl={material?.source_url||''} session={session} fetchMedia={fetchMedia} inlineMedia={!shortened}/>:null}</div>;
    const feed=material?getFeed(material.feed_id)||session?.sourceFeed:undefined;
    const avatar=feed?.avatar_url??undefined;
    const captions=!!content&&(content.content.blocks||[]).some(block=>block.type==='media'&&block.role==='subtitles');
    const fetchProblem=material?.jobs.find(job=>job.operation==='fetch'&&job.object_id===material.id&&(job.status==='unavailable'||job.status==='failed'));
    const collectionJobs=material?.jobs.filter(job=>job.operation==='fetch'||job.operation==='download_media')||[];
    const loading=collectionJobs.some(job=>job.status==='running'||job.status==='pending'&&!job.retry_at);
    const problem=collectionJobs.find(job=>job.reason_message&&(job.status!=='done'||job.reason_code==='subtitles_absent'));
    const externalBlocks=content?[...(content.content.blocks||[]),...content.linked_publications.flatMap(article=>article.content.blocks||[])].filter(block=>block.type==='external_media'):[];
    const external=externalBlocks.some(block=>block.kind==='web'||block.reason==='public_preview_unavailable');
    const externalReason=externalBlocks.some(block=>block.kind==='web')?'External embed not saved locally':'';
    const incomplete=!!material&&(material.status==='pending_collection'||material.linked_publications.some(article=>article.status==='pending_collection')||material.media.some(file=>file.status!=='ready'||!file.url)||external);
    const completeness=!material?'':!content?(fetchProblem?'Incomplete item · '+(fetchProblem.reason_message||'Publication unavailable'):'Loading item'):loading?('Loading publication'+(collectionJobs.some(job=>job.operation==='download_media'&&job.status==='running')?' · loading attachments':'')+(problem?.reason_message?' · '+problem.reason_message:'')):incomplete?('Incomplete item'+(problem?.reason_message?' · '+problem.reason_message:externalReason?' · '+externalReason:'')):'Fully saved';
    const canRetry=collectionJobs.some(job=>job.status==='failed'||job.status==='unavailable'||job.status==='pending'&&!!job.reason_message||job.reason_code==='subtitles_absent');
    const filterStatus=resultStale(material,'filter')?'Filter out of date':filterJob?.status==='running'?'Filtering publication':filterError||(filter?(filter.hidden?'Hidden · ':filter.removed?.length?'Promotional content removed · ':'Filtering complete')+(filter.reason||''):'Waiting for filtering');
    return <><div id="empty" hidden={!!material}>{state.empty}</div><div id="material" hidden={!material}><div id="material-update" hidden={!state.pending}><span>A new version is available </span><button id="material-update-button" onClick={showLatest}>Show new version</button></div><p id="material-retained" hidden={!state.retained}>Local content kept in this tab · missing parts are unavailable</p><p className="meta" id="completeness">{completeness}</p><h1 id="title">{material?.source_url?<a href={material.source_url} target="_blank" rel="noopener noreferrer">{newsTitle(material.title)}</a>:newsTitle(material?.title||'')}</h1><div id="material-byline">{material&&<><div className="material-feed-avatar"><span className="source-icon" aria-hidden="true">◉</span>{avatar&&session&&<Media material={{images:[avatar],attachments:[]}} session={session} fetchMedia={fetchMedia} className="material-feed-image" imageAlt="" optional/>}</div><div className="material-source-details"><strong id="material-feed-title">{feed?.title||session?.sourceTitle||'Unknown source'}</strong><p id="date">{material.published_at===null?'Unknown date':new Date(material.published_at).toLocaleString('en')}</p></div></>}</div><div id="theses-panel" hidden={!theses&&!thesisJob}><h2>Publication Summary</h2><p id="ai-status" role="status">{!material?'':resultStale(material,'summary')?'Summary out of date':thesisJob?.status==='running'?'Generating summary':thesisError|| (theses?'Summary ready':'Waiting for processor')}</p><ul id="theses"><Points points={theses?.points||[]} references={theses?.references||[]}/></ul><button id="theses-retry" disabled={!state.workAllowed||state.retained||!!state.pending} hidden={!thesisError} onClick={()=>void action('retryTheses')}>Retry Summary</button></div><div id="filter-panel" hidden={!filter&&!filterJob}><p id="filter-status" role="status">{filterStatus}</p><button id="filter-retry" disabled={!state.workAllowed||state.retained||!!state.pending} hidden={!filterError} onClick={()=>void action('retryFilter')}>Retry Filtering</button><div id="filtered-text" data-reading-region><Markdown sourceUrl={material?.source_url} text={shortened?filteredText:''}/></div></div>{fetchProblem&&material&&<section id="source-unavailable" aria-label="Publication unavailable"><p>{fetchProblem.reason_message||'Publication unavailable'}</p><a href={material.source_url} target="_blank" rel="noopener noreferrer">Open original ↗</a></section>}{shortened?<details id="original-panel" key={state.generation}><summary>Original Publication</summary>{originalText}</details>:originalText}{!commonMedia&&(content&&session?<Media key={session.id+'/'+state.generation} id="images" material={shortened?content:postMedia(content)} session={session} fetchMedia={fetchMedia}/>:<div id="images"/>)}<details id="processing-details" hidden={!hasResult} open={state.processingOpen} onToggle={event=>{if(event.currentTarget.open!==store.get().processingOpen)store.set({...store.get(),processingOpen:event.currentTarget.open});}}><summary>Processing Details</summary><p id="processing-provenance">{hasResult?provenance:''}</p></details><button id="regenerate" disabled={!state.workAllowed||state.retained||!!state.pending} hidden={!hasResult} onClick={()=>void action('regenerate')}>Regenerate</button><div className="actions"><button id="retry" disabled={!state.workAllowed||state.retained||!!state.pending} hidden={!material||!canRetry||loading&&!captions} onClick={()=>void action('retryMaterial')}>Retry Download</button><button id="copy" onClick={()=>material&&void copy(material.source_url)}>Copy Original Link</button><button id="viewed" disabled={state.retained} onClick={()=>void markViewed()}>Mark as Read</button></div><p id="message" role="status">{state.message}</p></div></>;
  }
  mountSurface(host('material').parentElement!,<Surface/>);
  return {
    workAllowed(allowed:boolean){if(store.get().workAllowed!==allowed)store.set({...store.get(),workAllowed:allowed});},
    get selection(){const session=store.get().session;return session?{id:session.id,folder_id:session.folder_id}:null;},open,clear,refresh,
    reconcile(_cards:Card[]){/* Feed presentation may change without closing the material. */const state=store.get();if(state.material){const feed=getFeed(state.material.feed_id);if(feed&&state.session){state.session.sourceFeed=feed;state.session.sourceTitle=feed.title;}store.set({...state});}},
    disconnected(){const state=store.get();if(!state.material){clear();return;}const session=state.session!;session.assetEpoch++;session.assets.abort();session.assets=new AbortController();session.assetsPaused=true;},
    connected(){const state=store.get(),session=state.session;if(session?.assetsPaused&&!session.reset&&!session.detached){session.assetsPaused=false;store.set({...state});}},
    reset(){const state=store.get();if(!state.material){clear();return;}const session=state.session!;session.assetEpoch++;session.assets.abort();session.assets=new AbortController();session.assetsPaused=true;session.reset=true;session.etag=null;update({...state,pending:null});},
  };
}
