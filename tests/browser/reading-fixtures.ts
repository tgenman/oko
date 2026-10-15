import type {FixtureMaterial} from './fixture-types';
import type {Navigation,Version,CompletionConflict} from '../../frontend/src/api/legacy-generated/types.gen';

// Convert old scenario data at the synthetic transport boundary only. The real
// HTTP scenarios exercise the canonical responses without this adapter.
export function installReadingFixtures() {
  const origin=(value:FixtureMaterial['provenance']):unknown=>!value||!('processor' in value)?{kind:'unknown',reason:'legacy'}:value.processor==='codex'&&'reasoning_effort' in value?{kind:'codex',model:value.model,reasoning_effort:value.reasoning_effort,fast:value.fast}:{kind:'local',processor_id:'llamacpp',model:value.model?{state:'known',name:value.model}:{state:'unknown'}};
  const incomplete=(value:{full?:boolean;reason?:string})=>value.full===false?{state:'incomplete',reason:'legacy',message:value.reason||''}:{state:'complete'};
  const attempt=(status:string|undefined,error?:string|null)=>({state:error?'waiting':({done:'ready',pending:'queued',off:'disabled'}[status||'off']||status),...(error?{reason:'processor_unavailable',retry_at:null}:{})});
  function reading(value:FixtureMaterial, opened:boolean) {
    const filter=value.filter,theses=value.theses;
    const common={id:value.id,title:value.title,feed_id:value.feed_id,feed_title:value.feed_title||'feed',folder_id:value.folder_id||'f',importance:value.importance||'normal',published_at:value.published_known===false?null:new Date(value.published*1000).toISOString(),source_url:value.url,source_position:value.source_order??null,snippet:value.snippet||'',originals:value.originals!==false,
      source:value.captions?{kind:'youtube',feed_id:value.feed_id,source_url:value.url,captions:value.captions}:{kind:'rss',feed_id:value.feed_id,source_url:value.url},
      filter:{attempt:attempt(filter?.status,filter?.error),diagnostic_message:filter?.error||null,result:filter?.status==='done'?{hidden:filter.hidden,restored:filter.restored,reason:filter.reason,removed:filter.removed,provenance:{kind:'unknown',reason:'legacy'}}:null},
      theses:{attempt:attempt(value.ai?.status||(theses?'done':'off'),value.ai?.error),diagnostic_message:value.ai?.error||null,result:theses?{points:theses.points.map(p=>({text:p.text,references:p.sources.map(r=>({material_id:r.id,source_url:r.url}))})),provenance:origin(value.provenance)}:null}};
    const content=value.complete?{availability:'available',loading:!!value.loading,completeness:incomplete(value),revision_id:value.version||'1',version:1,changed:!!value.changed,source_deleted:!!value.source_deleted,images:value.images||[],attachments:value.attachments||[],previous_versions:(value.previous||[]).map(v=>({number:v.number||Number(v.url.split('/').at(-1)),revision_id:v.version||'1',url:v.url})),...(opened?{text:value.text,display_text:value.display_text??value.text,description:value.description||''}:{})}:{availability:'pending',loading:value.loading!==false};
    return opened?{...common,availability:content.availability,content}:{...common,...content};
  }
  window.readingFixtureReply=(url,method,body,status)=>{
    const path=url.split('?')[0];
    if(!body||typeof body!=='object')return body;
    if(status===409&&path.includes('/completion')&&'detail' in body){const value=body.detail as CompletionConflict;return {code:'composition_changed',message:value.message,command_id:null,diagnostic_id:null,details:{preview:{cards_count:value.preview.cards,publications_count:value.preview.originals,token:value.preview.token}}};}
    if(status>=400)return body;
    if(path==='/api/v3/cards'&&Array.isArray(body))return body.map(v=>'availability' in v?v:reading(v,false));
    if(/^\/api\/v3\/materials\/[^/]+$/.test(path)&&!('availability' in body))return reading(body as FixtureMaterial,true);
    if(/^\/api\/v3\/materials\/[^/]+\/versions\/\d+$/.test(path)&&!('completeness' in body)){const v=body as Version;return {title:v.title,text:v.text,images:v.images,attachments:v.attachments,completeness:incomplete(v),number:Number(path.split('/').at(-1)),revision_id:'1'};}
    if(path==='/api/v3/reading-composition'&&'feeds' in body){const v=body as Navigation;if(v.feeds.length&&'feed' in v.feeds[0])return body;return {count:v.count,folders:v.folders.map(f=>({...f,position:f.position||0})),feeds:v.feeds.map(f=>({count:f.count,feed:{id:f.id,title:f.title,folder_id:f.folder_id,kind:f.kind,identity:f.identity||f.id,importance:f.importance||'normal',connection:f.kind==='telegram'?'needs_connection':'ready',history:{state:'absent',legacy_since:null},initial_days:f.initial_days||30,original_urls:f.original_urls||[],public_ai:f.public_ai??null,...(f.kind==='telegram'?{access:{kind:'unconfigured',source_locator:f.identity||f.id}}:{transport:f.kind==='youtube'?'youtube':'rss'})}}))};}
    if(path.endsWith('/completion/preview')&&'cards' in body&&'originals' in body&&'token' in body)return {cards_count:body.cards,publications_count:body.originals,token:body.token};
    if(method==='POST'&&('ok' in body)&&path.startsWith('/api/v3/'))return {...body,command_id:'fixture-command'};
    return body;
  };
}
