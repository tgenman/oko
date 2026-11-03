import {useEffect,useRef,useState} from 'react';
import type {ReaderApi} from '../api/reader';
import {collectionStatus,collectionSourceStatus} from '../api/generated/sdk.gen';
import type {CollectionSnapshot,CollectionSource,CollectionDetails,CollectionFailure,CollectionOperation} from '../api/generated/types.gen';
import {mountSurface} from '../renderer';
import './collection.css';

const stateNames:Record<CollectionSource['state'],string>={collecting:'Collecting',queued:'Queued',waiting:'Waiting',error:'Error',paused:'Paused',complete:'Completed',never:'Never refreshed',deleting:'Deleting',unavailable:'Source not connected'};
const timeNames={attempt:'attempt',waiting:'waiting',queue:'queued'};
function duration(value:number|null){if(value===null)return 'not available';const seconds=Math.max(0,Math.floor(value));return seconds<60?`${seconds } s`:`${Math.floor(seconds/60)} min ${seconds%60 } s`;}
function date(value:string|null){return value?new Date(value).toLocaleString(): 'not available';}
function retry(value:string|null,observed:string){if(!value)return '';const seconds=Math.max(0,(Date.parse(value)-Date.parse(observed))/1000);return seconds>0?`Retry in ${duration(seconds)}`:'Waiting to retry';}
function isUnfinished(row:CollectionSource){return ['collecting','queued','waiting','error','paused'].includes(row.state);}
function errorText(error:CollectionFailure){return `${error.error_type||'Original technical details are unavailable'}: ${error.message}${error.message_truncated?'\n[Message truncated]':''}\n${error.traceback}${error.traceback_truncated?'\n[Traceback truncated]':''}`;}
function detailsText(value:CollectionDetails){return [value.source.title,...value.operations.map(operation=>`${operation.operation_id} · ${operation.attempt_id||'not available'} · ${operation.stage}`),...failures(value).map(error=>`${date(error.occurred_at)} · ${error.resolved?'Resolved':'Latest error'}\n${errorText(error)}\n${error.requests.map(row=>`${row.method} ${row.url}${row.original_url?'\nOriginal URL: '+row.original_url:''}\n${row.http_status??'No HTTP response'} · ${row.error_type}: ${row.message}${row.message_truncated?' [Message truncated]':''}`).join('\n')}\n${error.request_count>error.requests.length?`Showing latest ${error.requests.length} of ${error.request_count}`:''}`)].join('\n\n');}
function failures(value:CollectionDetails){const result:CollectionFailure[]=[];for(const error of [...value.operations.map(op=>op.last_error),value.last_error])if(error&&!result.some(old=>old.attempt_id===error.attempt_id))result.push(error);return result;}
function safeLink(url:string,redacted=false){if(redacted||/\[hidden\]|\[session\]/i.test(url))return false;try{const parsed=new URL(url);return ['http:','https:'].includes(parsed.protocol)&&!parsed.username&&!parsed.password;}catch{return false;}}

export function createCollectionPanel(api:ReaderApi,host:HTMLElement){
 let snapshot:CollectionSnapshot|undefined,received=0,open=false,connection='',stopped=false,viewEpoch=0,sequence=0,main:AbortController|undefined;
 const details=new Map<string,CollectionDetails>(),detailErrors=new Map<string,string>(),expanded=new Set<string>(),pending=new Map<string,{controller:AbortController;epoch:number}>();
 let returnFocus:HTMLElement|undefined;
 function render(){mountSurface(host,<Panel/>);}
 function hide(){if(!open)return;open=false;viewEpoch++;for(const task of pending.values())task.controller.abort();pending.clear();render();returnFocus?.focus();}
 async function loadDetails(id:string){
  if(!open||!expanded.has(id)||pending.has(id)||!snapshot?.sources.some(row=>row.feed_id===id))return;
  const epoch=viewEpoch,instance=snapshot.instance_id,controller=new AbortController();pending.set(id,{controller,epoch});
  try{
   const {data}=await collectionSourceStatus({client:api.client,path:{feed_id:id},signal:controller.signal});
   if(!open||epoch!==viewEpoch||snapshot?.instance_id!==instance||data.instance_id!==instance||data.revision<snapshot.revision||!snapshot.sources.some(row=>row.feed_id===id))return;
   details.set(id,data);detailErrors.delete(id);
  }catch(error){if(!controller.signal.aborted&&epoch===viewEpoch)detailErrors.set(id,error instanceof Error?error.message:'Could not load details');}
  finally{if(pending.get(id)?.controller===controller){pending.delete(id);render();}}
 }
 async function refresh(){
  if(stopped)return;
  const stamp=++sequence;main?.abort();const controller=new AbortController();main=controller;
  try{
   const {data}=await collectionStatus({client:api.client,signal:controller.signal});
   if(stamp!==sequence)return;
   if(snapshot&&data.instance_id===snapshot.instance_id&&data.revision<snapshot.revision)return;
   if(snapshot&&snapshot.instance_id!==data.instance_id){viewEpoch++;details.clear();detailErrors.clear();for(const task of pending.values())task.controller.abort();pending.clear();}
   snapshot=data;received=performance.now();connection='';
   for(const row of data.sources){const cached=details.get(row.feed_id);if(cached&&cached.source.operation?.attempt_id!==row.operation?.attempt_id)details.delete(row.feed_id);}
   for(const id of details.keys())if(!data.sources.some(row=>row.feed_id===id)){details.delete(id);expanded.delete(id);}
   render();for(const id of expanded)void loadDetails(id);
  }catch(error){if(stamp===sequence&&!controller.signal.aborted){connection='No connection to Oko';render();}}
 }
 function show(event:React.MouseEvent<HTMLButtonElement>){returnFocus=event.currentTarget;open=true;viewEpoch++;render();void refresh();}
 function toggle(id:string){if(expanded.has(id))expanded.delete(id);else{expanded.add(id);void loadDetails(id);}render();}
 function Panel(){
  const dialog=useRef<HTMLDialogElement>(null);const [all,setAll]=useState(false),[search,setSearch]=useState(''),[tick,setTick]=useState(0),[copyStatus,setCopyStatus]=useState('');
  useEffect(()=>{if(open&&!dialog.current?.open)dialog.current?.showModal();if(!open&&dialog.current?.open)dialog.current?.close();},[open]);
  useEffect(()=>{if(!open)return;const timer=setInterval(()=>setTick(value=>value+1),1000);return()=>clearInterval(timer);},[open]);
  void tick;
  async function copy(value:string){try{await navigator.clipboard.writeText(value);setCopyStatus('Copied');}catch{setCopyStatus('Could not copy');}}
  const rows=snapshot?.sources||[],unfinished=rows.filter(isUnfinished).length;
  const visible=rows.filter(row=>(all||isUnfinished(row))&&`${row.title} ${row.source_url}`.toLocaleLowerCase().includes(search.toLocaleLowerCase()));
  const observed=snapshot?.observed_at||new Date().toISOString(),currentObserved=connection?observed:new Date(Date.parse(observed)+Math.max(0,performance.now()-received)).toISOString();
  const first=rows.find(row=>row.state==='collecting')||rows.find(row=>row.state==='waiting')||rows.find(isUnfinished);
  const title=first?`${first.state==='waiting'?first.title+' waiting to retry':stateNames[first.state]+': '+first.title}${first.operation?' · '+(first.state==='waiting'?retry(first.operation.retry_at,currentObserved):duration(first.operation.elapsed_seconds)):''}${unfinished>1?' · more '+(unfinished-1):''}`:'No sources are being refreshed';
  function elapsed(operation:CollectionOperation){return duration(operation.elapsed_seconds===null?null:operation.elapsed_seconds+(!connection&&['collecting','waiting','queued'].includes(operation.state)?Math.max(0,performance.now()-received)/1000:0));}
  function failure(error:CollectionFailure){return <section className="collection-failure"><strong>{error.resolved?'Previous error · resolved':'Latest error'} · {date(error.occurred_at)}</strong><small>Attempt {error.attempt_id} · {error.source==='worker'?'worker':'server'}</small><h4>Original error</h4><pre>{error.error_type?error.error_type+': ':''}{error.message||'Original technical details are unavailable'}{error.message_truncated?'\n[Message truncated]':''}</pre><button onClick={()=>void copy(errorText(error))}>Copy error</button>{error.traceback&&<details><summary>Traceback</summary><pre>{error.traceback}{error.traceback_truncated?'\n[Traceback truncated]':''}</pre></details>}<h4>Failed downloads</h4>{!error.requests.length&&<p>Failed request URL was not saved</p>}{error.requests.map((request,index)=><div className="collection-request" key={index}><small>{request.purpose} · {request.method} · {date(new Date(request.occurred_at*1000).toISOString())} · occurrences {request.count}</small>{safeLink(request.url,request.redacted)?<a href={request.url} target="_blank" rel="noopener noreferrer">{request.url}</a>:<span>{request.url} {request.redacted?'(secrets hidden)':''}</span>}{request.original_url&&<p>Original URL: {request.original_url}</p>}<p>{request.http_status===null?'No HTTP response':`HTTP ${request.http_status}`} · {request.error_type}: {request.message}{request.message_truncated?' [Message truncated]':''}</p><button onClick={()=>void copy(request.url)}>Copy URL</button></div>)}{error.request_count>error.requests.length&&<p>Showing latest {error.requests.length} of {error.request_count}</p>}</section>;}
  return <><button id="collection-progress" className="collection-trigger" onClick={show}>{connection?connection+(snapshot?' · as of '+date(observed):''):snapshot?title:'Source collection status'} <b>Details ›</b></button><dialog ref={dialog} className="collection-dialog" aria-labelledby="collection-title" onCancel={event=>{event.preventDefault();hide();}} onClick={event=>{if(event.target===dialog.current){const rect=dialog.current.getBoundingClientRect();if(event.clientX<rect.left||event.clientX>rect.right||event.clientY<rect.top||event.clientY>rect.bottom)hide();}}}><div className="collection-heading"><h2 id="collection-title">Source collection</h2><button className="collection-close" aria-label="Close" onClick={hide}>×</button></div>{connection&&<p role="status">{connection} · as of {date(observed)}</p>}{!snapshot?<p>Loading status…</p>:<><p className="collection-summary">Total {rows.length} · collecting {snapshot.counts.collecting} · waiting {snapshot.counts.waiting} · queued {snapshot.counts.queued} · errors {snapshot.counts.error}</p>{!unfinished&&<p>No sources are being refreshed</p>}{!snapshot.work_allowed&&<p>Work is paused</p>}<div className="collection-tools"><div className="collection-tabs"><button aria-pressed={!all} onClick={()=>setAll(false)}>Unfinished {unfinished}</button><button aria-pressed={all} onClick={()=>setAll(true)}>All {rows.length}</button></div><input type="search" aria-label="Find a source" placeholder="Find a source" value={search} onChange={event=>setSearch(event.target.value)}/></div><div className="collection-rows">{visible.map(row=><section className="collection-row" key={row.feed_id}><div className="collection-row-heading"><div><strong>{row.title}</strong><small>{{rss:'RSS',youtube:'YouTube',telegram:'Telegram',demo:'Demo'}[row.source_kind]||row.source_kind}{row.access!==row.source_kind?' · '+row.access:''}</small></div><div className="collection-time"><strong>{row.operation?elapsed(row.operation):duration(row.last_success_seconds)}</strong><small>{row.operation?timeNames[row.operation.time_kind]:row.last_success_seconds===null?'':'last collection'}</small></div></div><p><span className={'collection-state collection-'+row.state}>{stateNames[row.state]}</span>{row.operation?' · '+row.operation.stage:''}</p>{row.operation?.reason&&<p>{row.operation.reason}</p>}{row.operation?.retry_at&&<p>{retry(row.operation.retry_at,currentObserved)}</p>}{row.history&&<p>{row.history}</p>}{row.operation_count>1&&<p>Other operations: {row.operation_count-1} · errors {row.error_count} · waiting {row.waiting_count}</p>}<small>Last successful collection: {date(row.last_success_at)}</small><button className="collection-expand" aria-expanded={expanded.has(row.feed_id)} onClick={()=>toggle(row.feed_id)}>Details</button>{expanded.has(row.feed_id)&&<div className="collection-details"><p>Source URL: {row.source_url||'not available'}</p>{detailErrors.get(row.feed_id)&&<p role="status">Details unavailable: {detailErrors.get(row.feed_id)}</p>}{details.get(row.feed_id)?<><button onClick={()=>void copy(detailsText(details.get(row.feed_id)!))}>Copy details</button>{details.get(row.feed_id)!.operations.map(operation=><p key={operation.operation_id}>{operation.stage} · {stateNames[operation.state]} · {elapsed(operation)} ({timeNames[operation.time_kind]})<small>Operation {operation.operation_id} · attempt {operation.attempt_id||'not available'}<br/>Queued: {date(operation.queued_at)} · started: {date(operation.started_at)} · finished: {date(operation.finished_at)}</small></p>)}{failures(details.get(row.feed_id)!).map(error=><div key={error.attempt_id}>{failure(error)}</div>)}{!failures(details.get(row.feed_id)!).length&&<p>{row.operation?.reason&&row.state==='error'?'Original technical details are unavailable':'No original errors'}</p>}</>:!detailErrors.has(row.feed_id)&&<p>Loading details…</p>}</div>}</section>)}{!visible.length&&<p>{search?'No matches':'No unfinished sources'}</p>}</div></>}<p role="status" className="collection-copy-status">{copyStatus}</p><footer>Closing this window does not stop collection. AI processing is shown separately in Problems.</footer></dialog></>;
 }
 render();
 return {refresh,close:hide,notice(message:string){if(/stopped/i.test(message)){stopped=true;main?.abort();sequence++;connection='Oko stopped';render();return;}if(/connection|связ/i.test(message)){connection='No connection to Oko';render();}}};
}
