import {HistoryFields,selection,type Selection} from './connections';
import {FeedDetails} from './feed-details';
import {serviceText} from '../service-text';
import {useId, useState, useSyncExternalStore, type ReactNode} from 'react';
import type {SubscriptionFeed} from '../api/generated/types.gen';
import {accessLabel,TelegramAccessEditor} from './telegram';
import type {EffectiveSettingsSnapshot as EffectiveSettings,SettingsPatch} from '../api/generated/types.gen';
import {MenusOwner} from './owner';
import {historyProgress} from './history';
export function useOwner(owner:MenusOwner){useSyncExternalStore(owner.subscribe,owner.snapshot);return owner;}
export const fields={telegram_http_rps:['Telegram Request Limit','rps'],feed_refresh_minutes:['Feed Refresh Interval','minutes'],theses:['Publication Summaries','boolean'],prompt_theses:['Summary Prompt','text'],prompt_filters:['Filtering Rules','text'],processor:['Processor','processor']} as const;
type FieldName=keyof typeof fields;
const groups:[string,string,Exclude<FieldName,'feed_refresh_minutes'|'telegram_http_rps'>[],string,string][]=[['reading','Reading',['theses'],'green','▥'],['filters','Filtering',['prompt_filters'],'','▽'],['prompts','Prompts',['prompt_theses'],'purple','☷'],['processor','Processor',['processor'],'','▣']];
export function Page({owner,name,title,children}:{owner:MenusOwner;name:string;title?:string;children:ReactNode}){return <div data-settings-page={name} hidden={owner.page!==name}>{title&&<h2 className="settings-page-title" id={'settings-page-'+name.replaceAll('/','-')} tabIndex={-1}>{title}</h2>}{children}</div>;}
export function Link({owner,title,value='',page,color='',symbol='',describedBy}:{owner:MenusOwner;title:string;value?:string;page:string;color?:string;symbol?:string;describedBy?:string}){const descriptionId=useId();const description=describedBy||(value?descriptionId:undefined);return <button className="settings-link" aria-label={title} aria-describedby={description} data-settings-target={page} onClick={event=>owner.navigate(page,event.currentTarget)}>{symbol&&<span className={'settings-icon '+color} aria-hidden="true">{symbol}</span>}<span className="settings-label">{title}</span><span className="settings-value" id={description}>{value}</span><span aria-hidden="true">›</span></button>;}
function fieldValue(field:FieldName,value:unknown){if(value&&typeof value==='object'&&'kind' in value&&value.kind==='legacy'&&'value' in value)value=value.value;if(fields[field][1]==='boolean')return value?'Enabled':'Disabled';if(field==='processor')return value==='codex'?'Codex':value==='llamacpp'?'llama.cpp':String(value);if(fields[field][1]==='text')return value?'Configured':'Not Set';return String(value);}
function Field({owner,path,field,result}:{owner:MenusOwner;path:string;field:FieldName;result:EffectiveSettings}){
 const[expanded,setExpanded]=useState(false);const[title,type]=fields[field],effective=field==='telegram_http_rps'?(result.scope==='oko'?owner.telegramHttp:undefined):field==='feed_refresh_minutes'?(result.scope==='oko'?owner.feedRefresh:undefined):result.effective[field],id='setting-'+field;
 if(!effective)return null;
 const overridden=field==='feed_refresh_minutes'||field==='telegram_http_rps'?effective.origin.scope==='oko':field in result.overrides;
 const draft=owner.draft(path);const effectiveValue=typeof effective.value==='object'?effective.value.value:effective.value;const raw=String(id in draft?draft[id]:effectiveValue),saved=String(effectiveValue),busy=owner.saving.has(path+'/'+field);
 const changed=(value:string)=>owner.edit(path,id,value);const save=(reset=false)=>{let body:SettingsPatch;if(reset)body={[field]:null};else if(field==='telegram_http_rps')body={telegram_http_rps:Number(raw)};else if(field==='feed_refresh_minutes')body={feed_refresh_minutes:Number(raw)};else if(field==='theses')body={theses:raw==='true'};else if(field==='prompt_filters')body={prompt_filters:raw};else if(field==='prompt_theses')body={prompt_theses:raw};else if(raw==='codex'||raw==='llamacpp'||/^llamacpp:.+/.test(raw))body={processor:raw};else return;void owner.update(field,body,raw);};
 const control=type==='rps'?<input id={id} aria-label="Requests per second" type="number" min={0.1} max={10} step="any" required value={raw} onChange={event=>changed(event.target.value)}/>:type==='minutes'?<input id={id} aria-label="Interval (minutes)" type="number" min={1} max={1440} step={1} required value={raw} onChange={event=>changed(event.target.value)}/>:type==='boolean'?<input id={id} type="checkbox" role="switch" checked={raw==='true'} onChange={event=>changed(String(event.target.checked))}/>:
 type==='processor'?<select id={id} value={raw} onChange={event=>changed(event.target.value)}><option value="codex">Codex</option><option value="llamacpp">llama.cpp</option>{!['codex','llamacpp'].includes(String(effectiveValue))&&<option value={String(effectiveValue)}>{String(effectiveValue)}</option>}</select>:
 <textarea id={id} rows={8} value={raw} onChange={event=>changed(event.target.value)}/>;
 return <Page owner={owner} name={'field/'+field} title={title}><form className="setting-row settings-group" onSubmit={event=>{event.preventDefault();if(event.currentTarget.reportValidity())save();}}>
 <label className="settings-control">{title}{control}</label><small>{path.startsWith('oko/')&&!overridden?owner.describe(effective.origin.scope,'owner' in effective.origin?effective.origin.owner:null):(overridden?'Custom value · ':'Inherited · ')+owner.describe(effective.origin.scope,'owner' in effective.origin?effective.origin.owner:null)}</small>
 <details hidden={raw===saved} open={expanded} onToggle={event=>setExpanded(event.currentTarget.open)}><summary>Saved value</summary><p>{type==='text'?saved||'Empty field':fieldValue(field,effective.value)}</p></details>
 <button id={'setting-save-'+field} type="submit" className="settings-action" disabled={busy}>Save</button><button id={'setting-reset-'+field} type="button" className="settings-action" disabled={busy||!overridden} onClick={()=>save(true)}>Reset</button><button id={'setting-discard-'+field} type="button" className="settings-action" onClick={()=>void owner.discardField(path,field)}>Discard Change</button>
 </form>{field==='telegram_http_rps'&&<p>Enter 0.1–10 requests per second; default: 2. Applies to all Telegram web pages and separately to each media server. Changes apply without a restart and preserve waits requested by Telegram.</p>}{field==='feed_refresh_minutes'&&<p>Applies to all feeds. Enter 1–1440 minutes; default: 15. Manual refresh does not change the automatic timer.</p>}{field==='prompt_filters'&&<p>An empty field disables filtering. Hidden items remain available.</p>}</Page>;
}
export function Editor({owner}:{owner:MenusOwner}){
 useOwner(owner);
 const path=owner.currentPath,result=path&&owner.values.get(path);
 if(!path)return <div id="settings-editor" hidden/>;
 const feed=owner.subscriptions.feeds.find(feed=>'feed/'+feed.id===path);
 const feedResult=result&&result.scope==='feed'?result:undefined;
 return <div id="settings-editor">
 <div id="feed-options" hidden={!feed||!result}><Page owner={owner} name="feed-connection">{feed&&<FeedOptions key={feed.id} owner={owner} feed={feedResult?.feed}/>}</Page></div>
 <div id="setting-fields"><Page owner={owner} name="home" title={feed?undefined:'Settings'}>
 {result&&result.scope==='oko'&&owner.feedRefresh&&<div className="settings-group"><Link owner={owner} title="Feed Refresh Interval" page="field/feed_refresh_minutes" value={owner.feedRefresh.value+' min'} color="green" symbol="↻"/></div>}
 {result&&result.scope==='oko'&&owner.telegramHttp&&<div className="settings-group"><Link owner={owner} title="Telegram Request Limit" page="field/telegram_http_rps" value={owner.telegramHttp.value+' req/s'} color="green" symbol="⇄"/></div>}
 {result&&result.scope==='oko'&&<div className="settings-group"><Link owner={owner} title="Retry Delays" page="retry-delays" color="green" symbol="↻"/></div>}
 {feed&&<FeedDetails key={feed.id} owner={owner} feed={feedResult?.feed||feed} activity={feedResult?.activity}/>}
 {feed&&result&&<div className="settings-group"><Link owner={owner} title="Connection and Options" page="feed-connection" color="gray" symbol="▣"/></div>}
 {result&&<><h3 className="settings-group-title">Items</h3><div className="settings-group">{groups.map(([name,title,keys,color,symbol])=><Link key={name} owner={owner} title={title} page={name} color={color} symbol={symbol} value={name==='filters'?(result.effective.prompt_filters.value?'Configured':'Disabled'):name==='processor'?fieldValue(keys[0]!,result.effective[keys[0]!].value):''}/>)}</div></>}
 </Page>
 {result&&groups.map(([name,title,keys])=><Page key={name} owner={owner} name={name} title={title}><div className="settings-group">{keys.map(key=><Link key={key} owner={owner} title={fields[key][0]} value={fieldValue(key,result.effective[key].value)} page={'field/'+key}/>)}{name==='reading'&&<Link owner={owner} title="Summary Prompt" page="field/prompt_theses"/>}</div></Page>)}
 {result&&(Object.keys(fields)as FieldName[]).map(field=><Field key={path+'/'+field} owner={owner} path={path} field={field} result={result}/>)}
 {result&&result.scope==='oko'&&<RetryFields owner={owner} path={path}/>}
 </div></div>;
}
function RetryFields({owner,path}:{owner:MenusOwner;path:string}){
 const definitions=[['retry_initial_seconds','initial_seconds','Initial Retry Delay',60],['retry_max_seconds','max_seconds','Maximum Retry Delay',3600]] as const;
 return <><Page owner={owner} name="retry-delays" title="Retry Delays"><div className="settings-group">{definitions.map(([field,key,title])=><Link key={field} owner={owner} title={title} page={'retry/'+field} value={owner.retries?owner.retries[key]+' s':''}/>)}</div><p>Temporary failures retry with an increasing delay. Waits requested by a source remain in effect.</p></Page>
 {definitions.map(([field,key,title,defaultValue])=>{
  const id='setting-'+field,value=owner.retries?.[key],draft=owner.draft(path),raw=String(id in draft?draft[id]:value??''),busy=owner.saving.has(path+'/'+field);
  const save=(reset=false)=>{const number=reset?null:Number(raw);const body:SettingsPatch=field==='retry_initial_seconds'?{retry_initial_seconds:number}:{retry_max_seconds:number};void owner.updateRetry(field,body,raw);};
  return <Page key={field} owner={owner} name={'retry/'+field} title={title}>{value===undefined?<p>Loading…</p>:<form className="setting-row settings-group" onSubmit={event=>{event.preventDefault();if(event.currentTarget.reportValidity())save();}}>
  <label className="settings-control">Delay (seconds)<input id={id} type="number" min={1} max={86400} step={1} required value={raw} onChange={event=>owner.edit(path,id,event.target.value)}/></label>
  <small>Default: {defaultValue} seconds. Maximum delay must be at least the initial delay.</small>
  <button type="submit" className="settings-action" disabled={busy}>Save</button><button type="button" className="settings-action" disabled={busy} onClick={()=>save(true)}>Reset</button><button type="button" className="settings-action" onClick={()=>void owner.discardField(path,field)}>Discard Change</button>
  </form>}</Page>;
 })}</>;
}
function FeedOptions({owner,feed}:{owner:MenusOwner;feed:SubscriptionFeed|undefined}){
 const [confirm,setConfirm]=useState(false);if(!feed)return null;
 const transport=feed.kind==='telegram'?'telegram':feed.transport;
 const clientFeed=feed.kind==='telegram'&&'auto_read'in feed?feed:null;
 const deleting=feed.connection==='deleting'||owner.deletions.has(feed.id);
 const values=owner.draft('feed/'+feed.id);const value=(name:string,fallback:string|boolean)=>values['feed-'+name]??fallback;const edit=(name:string,value:string|boolean)=>owner.edit('feed/'+feed.id,'feed-'+name,value);
 const storedHistory=owner.getHistory(feed);const history=storedHistory?.state==='absent'?undefined:storedHistory;const active=history&&!['complete','cancelled'].includes(history.state);
 const help=owner.sources.find(source=>source.feed_id===feed.id)?.changes;
 const connection=feed.kind==='demo'?'Test source connected':feed.kind==='telegram'?accessLabel(feed.access):transport==='rss'?'RSS connected: collects while the tab is open':transport==='youtube'?'YouTube connected: collects while the tab is open':feed.kind==='youtube'?'YouTube disabled':feed.connection==='ambiguous'?'Ambiguous address: select a connection explicitly':'Collector connection required';
 const historyBusy=owner.historyBusy(feed.id);
 const count=String(values.youtubeHistoryCount??(history?.selection.mode==='count'?history.selection.count:60));
 return <><details hidden={!help}><summary>Update Detection Capabilities</summary><p id="source-help">{help&&serviceText(help)}</p></details><h2>Feed Connection</h2>
 {(history||feed.kind==='youtube'&&transport==='youtube')&&<><p id="history-progress" data-feed={feed.id} role="status" hidden={!history}>{historyProgress(history)}</p></>}
 {!history&&feed.kind!=='demo'&&<div id="history-legacy"><p>{storedHistory?.state==='absent'&&storedHistory.legacy_since?'Captured history start: '+new Date(storedHistory.legacy_since).toLocaleString():Number.isInteger(feed.initial_days)?'Saved history depth: '+feed.initial_days+' days; the actual boundary was not saved':'The actual history boundary was not saved'}</p><p>The initial download count and progress were not saved. The existing boundary and cursor are kept; history will not be downloaded again.</p></div>}
 {transport==='rss'&&<RSSHistoryControls owner={owner} feed={feed} active={Boolean(active)} busy={historyBusy}/>}
 {feed.kind==='youtube'&&transport==='youtube'&&<><p>History includes regular videos and recorded streams, excluding Shorts. Saved videos are not duplicated. Older videos may lack dates and descriptions.</p><label>Latest N Videos<input id="youtube-history-count" type="number" min={1} step={1} required value={count} onChange={event=>owner.edit('feed/'+feed.id,'youtubeHistoryCount',event.target.value)}/></label><button id="youtube-history-start" disabled={!owner.workAllowed||historyBusy||Boolean(active)} onClick={()=>{const input=document.getElementById('youtube-history-count')as HTMLInputElement;input.setCustomValidity(/^\d+$/.test(count)?'':'Enter a whole number, at least 1');if(input.reportValidity())void owner.history(feed,'start',count);}}>Load Latest N Videos</button><button id="youtube-history-stop" disabled={!owner.workAllowed||historyBusy} hidden={!active} onClick={()=>void owner.history(feed,'stop')}>Stop History Download</button></>}
 <p>{connection}</p><p>Original addresses: {feed.original_urls?.join(', ')}</p>{feed.kind==='telegram'&&<TelegramAccessEditor owner={owner} feed={feed}/>} {feed.kind==='youtube'&&<p>{transport==='youtube'?'YouTube: updates are collected while the tab is open. ':'YouTube connects automatically when you add a feed URL. '}Summaries are enabled by default; available YouTube subtitles are requested. High importance also enables subtitles and summaries. Videos are not downloaded.</p>}
 <label>Feed Importance<select id="feed-importance" value={String(value('importance',feed.importance||'normal'))} onChange={event=>edit('importance',event.target.value)}>{Object.entries({normal:'Normal',elevated:'Elevated',high:'High'}).map(([key,title])=><option key={key} value={key}>{title}</option>)}</select></label><label>Allow Public AI Processing<input id="feed-public-ai" type="checkbox" checked={Boolean(value('public-ai',Boolean(feed.public_ai)))} onChange={event=>edit('public-ai',event.target.checked)}/></label>
 {clientFeed!==null&&<><p>Oko only acknowledges the safe history boundary. Marking a later item as read may wait for earlier items.</p><label>Telegram Auto-Read<select id="feed-auto" value={String(value('auto',clientFeed?.auto_read===null?'':String(Boolean(clientFeed?.auto_read))))} onChange={event=>edit('auto',event.target.value)}><option value="">Choose Mode</option><option value="true">Mark as read after fully saving</option><option value="false">Mark as read only after “Mark as Read”</option></select></label><label>Mark history before the import boundary as read<input id="feed-read-history" type="checkbox" checked={Boolean(value('read-history',Boolean(clientFeed?.read_history)))} onChange={event=>edit('read-history',event.target.checked)}/></label><p>Without cleanup permission, older unread history may delay Telegram read acknowledgements. While Oko is running, this permission applies only to history before the captured import boundary.</p></>}
 {feed.kind==='youtube'&&<label>Collect YouTube Updates<input id="feed-youtube" type="checkbox" checked={Boolean(value('youtube',transport==='youtube'))} onChange={event=>edit('youtube',event.target.checked)}/></label>}
 {feed.kind==='unresolved'&&<label>Collection Method<select id="feed-transport" value={String(value('transport',transport||''))} onChange={event=>edit('transport',event.target.value)}><option value="">No Connection Selected</option><option value="rss">Limited RSS (no Telegram media or read acknowledgements)</option></select></label>}
 <button onClick={()=>{const importance=String(value('importance',feed.importance||'normal'));const selectedTransport=String(value('transport',transport||''));void owner.saveFeed(feed,{importance:importance==='high'?'high':importance==='elevated'?'elevated':'normal',public_ai:Boolean(value('public-ai',Boolean(feed.public_ai))),...(clientFeed!==null?{auto_read:value('auto',clientFeed?.auto_read===null?'':String(Boolean(clientFeed?.auto_read)))===''?null:value('auto',String(Boolean(clientFeed?.auto_read)))==='true',read_history:Boolean(value('read-history',Boolean(clientFeed?.read_history)))}:{}),...(feed.kind==='unresolved'?{transport:selectedTransport==='rss'?'rss':null}:{}),...(feed.kind==='youtube'?{transport:value('youtube',transport==='youtube')?'youtube':null}:{})});}}>Save Feed</button>
 {feed.kind==='telegram'&&feed.access.kind==='legacy_client'&&<><p>Previous alias: {feed.access.alias}. Map it to an existing profile configuration. Feed data and ID will be kept.</p><button onClick={event=>{void owner.open('settings',event.currentTarget).then(intent=>{if(intent?.current)owner.navigate('telegram-profiles');});}}>Configure Telegram Profiles</button></>}
 <p role="status">{deleting?'Deleting: local cleanup in progress':''}</p><button id="feed-delete" disabled={deleting||owner.stopped} onClick={()=>setConfirm(true)}>Delete Feed</button>
 {confirm&&<dialog id="feed-delete-confirm" aria-label="Delete Feed" ref={node=>{if(node&&!node.open)node.showModal();}} onCancel={()=>setConfirm(false)}><h2>Delete feed “{feed.title}”?</h2><p>The local subscription, items, versions, media, processing results and settings will be deleted. Telegram messages will not be deleted or marked as read; you will not leave the group. Adding it again will start a new collection.</p><button id="feed-delete-cancel" onClick={()=>setConfirm(false)}>Cancel</button><button id="feed-delete-confirm-action" onClick={()=>{setConfirm(false);void owner.removeFeed(feed);}}>Delete Local Data</button></dialog>}
 </>;
}

function RSSHistoryControls({owner,feed,active,busy}:{owner:MenusOwner;feed:SubscriptionFeed;active:boolean;busy:boolean}){
 const path='feed/'+feed.id, draft=owner.draft(path), saved=owner.getHistory(feed);
 const history=saved&&saved.state!=='absent'?saved:null;
 const value:Selection={mode:'count',count:String(draft.rssHistoryCount??(history?.selection.mode==='count'?history.selection.count:'20'))};
 return <><HistoryFields prefix="rss-load" value={value} disabled={!owner.workAllowed} onChange={next=>{
  owner.edit(path,'rssHistoryCount',next.count);
 }}/><p>History follows continuation links supplied by the source. The requested number may be unavailable.</p>
 <button id="rss-load-history-start" disabled={!owner.workAllowed||busy||active} onClick={()=>{const chosen=selection(value,'rss-load');if(chosen)void owner.history(feed,'start',chosen);}}>Load History</button>
 <button id="rss-load-history-stop" disabled={!owner.workAllowed||busy} hidden={!active} onClick={()=>void owner.history(feed,'stop')}>Stop History Download</button></>;
}
