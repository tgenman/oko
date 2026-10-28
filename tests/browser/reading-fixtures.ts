import type {FixtureMaterial} from './fixture-types';
import type {Navigation,CompletionConflict} from './scenario-data';

// Build canonical packets from compact scenario facts at the synthetic boundary.
export function installReadingFixtures() {
  const origin=(value:FixtureMaterial['provenance']):unknown=>!value||!('processor' in value)?{kind:'unknown',reason:'not_recorded'}:value.processor==='codex'&&'reasoning_effort' in value?{kind:'codex',model:value.model,reasoning_effort:value.reasoning_effort,fast:value.fast}:{kind:'local',processor_id:'llamacpp',model:value.model?{state:'known',name:value.model}:{state:'unknown'}};
  const incomplete=(value:{full?:boolean;reason?:string;post_web_unavailable?:boolean})=>value.full===false?{state:'incomplete',reason:value.post_web_unavailable?'post_web_unavailable':'source_incomplete',message:value.reason||''}:{state:'complete'};
  const attempt=(status:string|undefined,error?:string|null,hasResult=false)=>status==='running'?{state:'running'}:status==='done'&&hasResult?{state:'ready'}:({state:error?'waiting':({done:'queued',pending:'queued',off:'disabled'}[status||'off']||status),...(error?{reason:'processor_unavailable',retry_at:null}:{})});
  function reading(value:FixtureMaterial, opened:boolean) {
    const filter=value.filter,theses=value.theses;
    const common={id:value.id,title:value.title,feed_id:value.feed_id,feed_title:value.feed_title||'feed',folder_id:value.folder_id||'f',importance:value.importance||'normal',published_at:value.published_known===false?null:new Date(value.published*1000).toISOString(),source_url:value.url,source_position:value.source_order??null,snippet:value.snippet||'',originals:value.originals!==false,
      source:value.captions?{kind:'youtube',feed_id:value.feed_id,source_url:value.url,captions:value.captions}:{kind:'rss',feed_id:value.feed_id,source_url:value.url},
      filter:{attempt:attempt(filter?.status,filter?.error,filter?.status==='done'),diagnostic_message:filter?.error||null,result:filter?.status==='done'?{hidden:filter.hidden,reason:filter.reason,removed:filter.removed,provenance:{kind:'unknown',reason:'not_recorded'}}:null},
      theses:{attempt:attempt(value.ai?.status||(theses?'done':'off'),value.ai?.error,!!theses),diagnostic_message:value.ai?.error||null,result:theses?{points:theses.points.map(p=>({text:p.text,references:p.sources.map(r=>({material_id:r.id,source_url:r.url}))})),provenance:origin(value.provenance)}:null}};
    const content=value.complete?{availability:'available',post_web_unavailable:!!value.post_web_unavailable,loading:!!value.loading,completeness:incomplete(value),revision_id:value.version||'1',version:1,changed:!!value.changed,source_deleted:!!value.source_deleted,images:value.images||[],attachments:value.attachments||[],...(opened?{text:value.text,display_text:value.display_text??value.text,description:value.description||'',reply_preview:value.reply_preview||null}:{})}:{availability:'pending',loading:value.loading!==false};
    return opened?{...common,availability:content.availability,content}:{...common,...content};
  }
  window.readingFixtureReply=(url,method,body,status)=>{
    const path=url.split('?')[0];
    if(!body||typeof body!=='object')return body;
    if(status===409&&path.includes('/completion')&&'detail' in body){const value=body.detail as CompletionConflict;return {code:'composition_changed',message:value.message,command_id:null,diagnostic_id:null,details:{preview:{cards_count:value.preview.cards,publications_count:value.preview.originals,token:value.preview.token}}};}
    if(status>=400)return body;
    if(path==='/api/v3/cards'&&Array.isArray(body))return body.map(v=>'availability' in v?v:reading(v,false));
    if(/^\/api\/v3\/materials\/[^/]+$/.test(path)&&!('availability' in body))return reading(body as FixtureMaterial,true);
    if(path==='/api/v3/reading-composition'&&'feeds' in body){const v=body as Navigation;if(v.feeds.length&&'feed' in v.feeds[0])return body;return {count:v.count,folders:v.folders.map(f=>({...f,position:f.position||0})),feeds:v.feeds.map(f=>({count:f.count,has_unviewed:f.has_unviewed??true,feed:{id:f.id,title:f.title,folder_id:f.folder_id,kind:f.kind,identity:f.identity||f.id,importance:f.importance||'normal',connection:f.kind==='telegram'?'needs_connection':'ready',history:{state:'absent'},initial_days:f.initial_days||30,original_urls:f.original_urls||[],public_ai:f.public_ai??null,...(f.kind==='telegram'?{access:{kind:'unconfigured',source_locator:f.identity||f.id}}:{transport:f.kind==='youtube'?'youtube':'rss'})}}))};}
    if(path.endsWith('/completion/preview')&&'cards' in body&&'originals' in body&&'token' in body)return {cards_count:body.cards,publications_count:body.originals,token:body.token};
    if(method==='POST'&&('ok' in body)&&path.startsWith('/api/v3/'))return {...body,command_id:'fixture-command'};
    return body;
  };
}
