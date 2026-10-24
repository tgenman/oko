import {installReadingFixtures} from './reading-fixtures';
import type {FixtureMaterial, ReplyMap} from './fixture-types.ts';
import {productionReader} from './http-reader.ts';
import {expect} from 'playwright/test';
import {contractFor, validate} from './contracts.ts';

async function reader() {
  const fixture = await productionReader();
  const {page, context, origin, token} = fixture;
  const schemaFailures: string[] = [];
  try {
    await context.exposeBinding('validateReaderReply', (_, url, method, status, body, alias, record = false) => {
      const name = alias || contractFor(url, method);
      if (name && status >= 200 && status < 300) {
        try {validate(name, body);} catch (error) {if (record) schemaFailures.push(error instanceof Error ? error.message : String(error)); throw error;}
      }
    });
    await context.addInitScript(installReadingFixtures);
    await context.addInitScript(() => {
      // External transport adapter deliberately ignores cancellation: a late
      // response must be harmless even when abort cannot stop its delivery.
      // Explicit synthetic baseline: scenarios opt into this fixture factory.
      window.readerFixtures = {history: values => ({mode:'count',count:20,count_text:null,date:null,timezone:'UTC',since:null,loaded:0,viewed:0,incomplete:0,available:20,paused:false,frozen:true,retrying:0,unavailable:0,errors:[],scan_complete:true,state:'done',...values})};
      const nativeFetch = window.fetch.bind(window);
      const replies: ReplyMap = new Map();
      const held = new Map<string, boolean>();
      const pending = new Map<string, (value: Response | Promise<Response>)=>void>();
      const aliases = new Map<string, string>();
      // Transitional v2 scenario snapshots are adapted only in this synthetic
      // transport. Production HTTP acceptance verifies the real v3 envelopes.
      const history=(value:unknown):unknown=>{
        if(!value||typeof value!=='object')return {state:'absent'};
        if('selection' in value||('state' in value&&value.state==='absent'))return value;
        const h=value as import('./scenario-data').History;
        const selection=h.mode==='date'?{mode:'date',date:h.date,timezone:h.timezone}:{mode:'count',count:h.count_text||String(h.count)};
        const progress={loaded:h.loaded||0,viewed:h.viewed||0,incomplete:h.incomplete||0,available:h.available||0,retrying:h.retrying||0,unavailable:h.unavailable||0,requests:h.requests||0,errors:(h.errors||[]).map(e=>({material_id:e.item,code:'source_unavailable',message:e.message})),scan_complete:!!h.scan_complete,manifest_complete:!!h.frozen,warnings:h.warnings||[],discovery_problem:h.discovery_error?{code:'source_unavailable',message:h.discovery_error,retry_at:h.discovery_retry_at?new Date(h.discovery_retry_at*1000).toISOString():null}:null};
        const state=h.state==='done'?'complete':h.state==='pending'?'not_started':h.state==='selecting'?'discovering':h.state==='loading'?'fetching':h.state;
        return {state,selection,progress,...(state==='not_started'?{}:{run:h.run||'fixture-run'}),...(state==='paused'?{paused_phase:h.frozen?'fetching':'discovering'}:{}),...(state==='waiting'?{reason:'retry_scheduled',retry_at:null}:{}),...(state==='cancelled'?{outcome:'cancelled'}:{})};
      };
      const youtube=(value:unknown)=>{const v=value as import('./scenario-data').YouTube|undefined;return {mode:v?.mode||'direct',address:v?.address||'',has_username:!!v?.has_username,has_password:!!v?.has_password,admission:v?.paused?'paused':v?.next_at?'waiting':'ready',retry_at:v?.next_at?new Date(v.next_at*1000).toISOString():null,strikes:v?.strikes||0,reason:v?.reason?'youtube_rate_limited':null};};
      const v3Reply=(url:string,method:string,body:unknown):unknown=>{
        if(url==='/api/v3/status'){
          if(body&&typeof body==='object'&&'api_version'in body&&'runtime_state'in body)return body;
          const v=body as import('./scenario-data').Status;
          const problem=(p:import('./scenario-data').Problem)=>{
            const codes:Record<string,string>={'codex-choice':'processor_not_configured','processor-config':'processor_not_configured','ai-permission':'public_ai_not_allowed','source-connection':'source_unavailable','material-incomplete':'material_incomplete','action-failed':'command_failed','action-unknown':'command_outcome_unknown','youtube-admission':'youtube_rate_limited','storage-unavailable':'storage_unavailable'};
            const code=codes[p.code]||'legacy_problem';
            let affected=p.objects.map<import('../../frontend/src/api/generated/types.gen').AffectedResource>(o=>o.kind==='material'?{kind:'material',material_id:o.id}:o.kind==='feed'?{kind:'feed',feed_id:o.id}:{kind:'runtime'});
            const recovery:import('../../frontend/src/api/generated/types.gen').RecoveryIntent[]=[];
            const action=p.action,ids=p.objects.filter(o=>o.kind==='material').map(o=>o.id);
            if(code!=='legacy_problem'&&action){
              if(action.kind==='settings'&&(action.page==='codex'||action.page==='llamacpp')){recovery.push({kind:'configure_processor',processor_id:action.page});affected=[{kind:'processor',processor_id:action.page}];}
              else if(action.kind==='settings'){
                const keys=['originals','theses','prompt_theses','prompt_filters','processor']as const;
                const key=keys.find(key=>'field/'+key===action.page);
                if(key)recovery.push({kind:'change_setting',target:action.scope==='feed'||action.scope==='folder'?{scope:action.scope,owner:action.id||'fixture'}:{scope:'oko',owner:'global'},keys:[key]});
              }
              else if(action.kind==='collect')recovery.push({kind:'retry_collection',target:{scope:'all'}});
              else if(action.kind==='retry'&&ids.length)recovery.push({kind:'retry_material',material_ids:ids});
              else if(action.kind==='theses-retry'&&ids.length)recovery.push({kind:'retry_theses',material_ids:ids});
              else if(action.kind==='filter-retry'&&ids.length)recovery.push({kind:'retry_filter',material_ids:ids});
              else if(action.kind==='objects'&&ids.length)recovery.push({kind:'inspect_materials',material_ids:ids});
            }
            return {id:p.id,code,...(code==='legacy_problem'?{raw_code:p.code}:{}),command_id:p.request_id||null,title:p.title,message:p.reason||p.next_step,consequence:p.consequence,affected,recovery:p.state==='finished'?[]:recovery,created_at:new Date(p.created_at*1000).toISOString(),state:p.state,...(p.state==='finished'?{finished_at:new Date((p.finished_at||p.created_at)*1000).toISOString(),outcome:p.outcome}:p.state==='retrying'?{retry_at:p.next_at?new Date(p.next_at*1000).toISOString():null}:{})};
          };
          const current=(v.problems?.current||[]).map(problem),recent=(v.problems?.recent||[]).map(problem);
          return {api_version:'3.0.0',runtime_state:v.stopped?'stopped':v.error?'unavailable':'running',work_allowed:!v.stopped,active_client_sessions:1,lifetime:{kind:'client_presence',lease_seconds:60,grace_seconds:60,renew_after_seconds:20},initial_histories:(v.initial_histories||[]).map(h=>({feed_id:h.feed_id,history:history(h)})),youtube:youtube(v.youtube),work:!v.jobs?.some(j=>j.status==='pending'||j.status==='running')?[{phase:'idle'}]:(v.jobs||[]).filter(j=>j.status==='pending'||j.status==='running').map(j=>j.kind==='filter'||j.kind==='theses'?{phase:'processing',operation:j.kind,progress:{completed:0,total:j.count}}:{phase:j.kind==='delete'?'deleting':'collecting',feed_id:null,progress:{completed:0,total:j.count}}),sources:(v.sources||[]).map(source=>({feed_id:source.feed_id,title:source.title,state:source.error?'unavailable':'ready',reason:source.error?'source_unavailable':null,message:source.error||null,gap:source.gap||null,changes:source.changes||null})),ready_sources:v.ready_sources||0,total_sources:v.total_sources||0,problems:{current,recent,count:current.length,updated_at:new Date((v.problems?.updated_at||0)*1000).toISOString()},diagnostic_count:v.debug_count||0,diagnostic_persistence_error:v.debug_persistence_error||null,viewed_ids:v.viewed_ids||[],telegram_reads:[]};
        }
        if(url==='/api/v3/youtube'&&body&&typeof body==='object'&&!('admission' in body))return youtube(body);
        if(url==='/api/v3/feeds/verify'&&body&&typeof body==='object'&&!('state' in body)){const v=body as import('./scenario-data').FeedVerification;return v.existing?{state:'existing',existing_feed:v.existing}:{state:'new',verification:v.verification,candidate:{url:v.url,title:v.title,kind:v.kind}};}
        if(url.startsWith('/api/v3/feeds/')&&url.endsWith('/history')&&body&&typeof body==='object'&&!('history' in body))return {feed_id:url.split('/')[4],history:history(body)};
        if(url==='/api/v3/processors/llamacpp'&&method==='GET'&&body===null)return {configuration:null};
        if(!body||typeof body!=='object')return body;
        if(url.startsWith('/api/v3/settings/')&&'effective' in body&&'overrides' in body&&!('scope' in body)){
          const [, , , ,scope,owner]=url.split('/');
          const effective=Object.fromEntries(Object.entries(body.effective||{}).map(([key,item])=>{
            if(!item||typeof item!=='object'||!('value' in item)||!('scope' in item))return [key,item];
            return [key,{value:item.value,origin:{scope:item.scope,...(item.scope==='default'?{}:{owner:'owner' in item?item.owner:'global'})}}];
          }));
          const feed=('feed' in body?body.feed:null)||replies.get('/api/v3/subscriptions')?.body?.feeds.find(f=>f.id===owner);
          return {scope,owner,effective,overrides:body.overrides,...(scope==='feed'&&feed&&typeof feed==='object'?{feed:{...feed,history:'initial_history' in feed&&feed.initial_history?history(feed.initial_history):{state:'absent'}}}:{})};
        }
        if(url==='/api/v3/processors/llamacpp'&&method==='GET'&&!('configuration' in body))return {configuration:body};
        if(url==='/api/v3/processors/llamacpp/check'&&'local' in body&&'model' in body&&'models' in body)return {locality:body.local?'local':'remote',model:body.model,models:body.models};
        if(url==='/api/v3/processors/codex'&&method==='GET'&&'config' in body)return {configuration:body.config,availability:{state:'unavailable',auth:'unchecked',version:null,local:null,limits_checked:false,quota_windows:[],reason:'unavailable',message:''}};
        if(url==='/api/v3/processors/codex/check'&&'available' in body)return {...body,state:body.available?'available':'unavailable',message:'reason' in body?body.reason:''};
        if(url==='/api/v3/diagnostics'){
          const entry=(value:unknown):unknown=>{if(!value||typeof value!=='object'||!('first_time' in value)||!('last_time' in value))return value;const {first_time,last_time,...rest}=value;return {...rest,first_at:first_time,last_at:last_time};};
          if(method==='GET'&&'groups' in body&&Array.isArray(body.groups))return {...body,groups:body.groups.map(entry)};
          if(method==='DELETE'&&'ok' in body)return body.ok?{state:'cleared'}:{state:'persistence_failure',message:'persistence_error' in body?body.persistence_error:''};
          if(method==='POST'&&'ok' in body)return body.ok===false?{state:'persistence_failure',message:'persistence_error' in body?body.persistence_error:''}:'entry' in body&&body.entry?{state:'accepted',entry:entry(body.entry)}:{state:'ignored',reason:'material_viewed'};
        }
        return body;
      };
      const response = async (url: string, method: string, body: unknown, status = 200) => {
        body=window.readingFixtureReply(url,method,body,status);
        if(status>=200&&status<300)body=v3Reply(url,method,body);
        await window.validateReaderReply(url, method, status, body, aliases.get(url), true);
        return new Response(JSON.stringify(body), {status, headers: {'Content-Type': 'application/json'}});
      };
      window.readerTransport = {
        replies, held, pending, aliases, native: new Set(), requests: [],
        release(url, body, status = 200) {
          const resolve = pending.get(url); if (!resolve) throw new Error('No pending reply: ' + url); pending.delete(url); held.delete(url);
          resolve(response(url, window.readerTransport.requests.filter(request=>request.url===url).at(-1)?.method||'GET', body, status));
        },
      };
      // Existing scenarios describe publication snapshots. Adapt those snapshots
      // to the new wire protocol here; requests still record only actual HTTP calls.
      const entityVersions=new Map<string,{version:number;json:string}>();
      async function fixtureRead(url:string) {
        if(held.has(url)){const result=await new Promise<Response>(resolve=>pending.set(url,resolve)),body=await result.json();if(!result.ok)throw new Error(body.message||body.detail||'Synthetic reading failure');return body;}
        const pathname=url.split('?')[0],reply=replies.get('GET '+url)||replies.get(url)||replies.get('GET '+pathname)||replies.get(pathname);
        if(!reply)throw new Error('Missing selective fixture: '+url);
        for(const [route,next] of reply.next||[])replies.set(route,next);
        const result=await response(url,'GET',reply.body,reply.status||200),body=await result.json();
        if(!result.ok)throw new Error(body.message||body.detail||'Synthetic reading failure');
        return body;
      }
      const storageId='3f003741-d0c7-4e62-8d12-55fb652f404a';
      async function selective(request:import('../../frontend/src/api/generated/types.gen').ReadingCheckRequest) {
        type Resource=import('../../frontend/src/api/generated/types.gen').ReadingResource;
        type Card=import('../../frontend/src/api/generated/types.gen').ReadingCard;
        type Material=import('../../frontend/src/api/generated/types.gen').ReadableMaterial;
        const resources:Resource[]=[],added=new Set<string>();
        const changed=(kind:Resource['kind'],id:string,data:unknown,known:string|null)=>{
          const key=kind+'/'+id;if(added.has(key))return;added.add(key);
          const json=JSON.stringify(data),old=entityVersions.get(key),version=old?(old.json===json?old.version:old.version+1):1;
          entityVersions.set(key,{version,json});
          resources.push(known===String(version)?{kind,id,state:'unchanged',version:String(version)}:{kind,id,state:'changed',version:String(version),data} as Resource);
        };
        let catalog:import('../../frontend/src/api/generated/types.gen').ReadingComposition|undefined;
        const navigation=async():Promise<import('../../frontend/src/api/generated/types.gen').ReadingComposition>=>catalog??=await fixtureRead('/api/v3/reading-composition');
        const opened=new Map<string,Material>();
        async function material(id:string){if(!opened.has(id))opened.set(id,await fixtureRead('/api/v3/materials/'+id));return opened.get(id)!;}
        const compact=(card:Card)=>({id:card.id,feed_id:card.feed_id,title:card.title,published_at:card.published_at,source_url:card.source_url,snippet:card.snippet,availability:card.availability,loading:card.loading,changed:card.availability==='available'&&card.changed,revision_id:card.availability==='available'?card.revision_id:null,preview_url:card.availability==='available'?(card.images[0]||card.attachments.find(a=>a.kind==='image')?.url||null):null,hidden:!!card.filter.result?.hidden,filter_state:card.filter.attempt.state,theses_state:card.theses.attempt.state});
        if(request.storage_id&&request.storage_id!==storageId)return {state:'reset',storage_id:storageId};
        for(const item of request.resources){
          const kind=item.kind;
          if(kind==='status')changed(kind,'runtime',await fixtureRead('/api/v3/status'),item.known_version);
          else if(kind==='navigation'){
            const nav=await navigation();
            changed(kind,'navigation',{...nav,folders:nav.folders.map(f=>({...f,version:'1'})),feeds:nav.feeds.map(f=>({id:f.feed.id,count:f.count,has_unviewed:f.has_unviewed}))},item.known_version);
            for(const ref of nav.feeds){const known=request.resources.find(r=>r.kind==='feed'&&r.id===ref.feed.id);changed('feed',ref.feed.id,ref.feed,known?.known_version??null);}
          }else if(kind==='feed'){
            const feed=(await navigation()).feeds.find(f=>f.feed.id===item.id)?.feed;
            if(feed)changed(kind,item.id,feed,item.known_version);
            else resources.push({kind,id:item.id,state:'unavailable',version:null,reason:'not_found'});
          }else if(kind==='list'){
            const o=item.options,p=new URLSearchParams({sort:o.sort});if(o.show_hidden)p.set('show_hidden','true');if(o.target.scope==='feed')p.set('feed',o.target.feed_id);if(o.target.scope==='folder')p.set('folder',o.target.folder_id);
            const cards:Card[]=await fixtureRead('/api/v3/cards?'+p);
            for(const card of cards)changed('card',card.id,compact(card),item.known_cards[card.id]??null);
            changed(kind,JSON.stringify([o.target.scope,o.target.scope==='feed'?o.target.feed_id:o.target.scope==='folder'?o.target.folder_id:'',o.sort,o.show_hidden]),{ids:cards.map(c=>c.id),options:o},item.known_version);
          }else if(kind==='card'){
            const card=(await fixtureRead('/api/v3/cards') as Card[]).find(c=>c.id===item.id);if(card)changed(kind,item.id,compact(card),item.known_version);
          }else{
            const m=await material(item.id),revision=m.availability==='available'?m.content.revision_id:null;
            if(kind==='filter')changed(kind,item.id,{revision_id:revision,filter:m.filter},item.known_version);
            else if(kind==='theses')changed(kind,item.id,{revision_id:revision,theses:m.theses},item.known_version);
            else if(kind==='media')changed(kind,item.id,{revision_id:revision,images:m.availability==='available'?m.content.images:[],attachments:m.availability==='available'?m.content.attachments:[]},item.known_version);
            else{
              const {feed_title,folder_id,importance,snippet,filter,theses,...text}=m;
              if(text.availability==='available'){const {images,attachments,display_text,...content}=text.content;text.content=content as typeof text.content;}
              changed(kind,item.id,text,item.known_version);
            }
          }
        }
        return {state:'current',storage_id:storageId,resources};
      }
      window.fetch = async (url, options) => {
        const key = String(url);
        window.readerTransport.requests.push({url: key, method: options?.method || 'GET', body: options?.body ? JSON.parse(String(options.body)) : null});
        if (window.readerTransport.native.has(key)) return nativeFetch(url, options);
        if(key==='/api/v3/reading/check')return response(key,'POST',await selective(JSON.parse(String(options?.body))));
        if(key.startsWith('/api/v3/reading/materials/')){const id=key.split('/')[5];return response(key,'GET',await selective({storage_id:null,resources:(['material','filter','theses','media'] as const).map(kind=>({kind,id,known_version:null}))}));}
        if (held.has(key)) return new Promise(resolve => pending.set(key, resolve));
        const method = options?.method || 'GET', pathname = key.split('?')[0];
        const reply = replies.get(method + ' ' + key) || replies.get(key) || replies.get(method + ' ' + pathname) || replies.get(pathname);
        if (reply) {
          for (const [route, next] of reply.next || []) replies.set(route, next);
          if (reply.base64) return new Response(Uint8Array.from(atob(reply.base64), c => c.charCodeAt(0)), {headers: {'Content-Type': reply.mime || 'application/octet-stream'}});
          return response(key, method, reply.body, reply.status || 200);
        }
        return nativeFetch(url, options);
      };
      const created = URL.createObjectURL.bind(URL), revoked = URL.revokeObjectURL.bind(URL);
      window.browserResources = {created: [], revoked: []};
      URL.createObjectURL = blob => { const url = created(blob); window.browserResources.created.push(url); return url; };
      URL.revokeObjectURL = url => { window.browserResources.revoked.push(url); revoked(url); };
    });
    const errors: string[] = []; page.on('pageerror', error => errors.push(error.message));
    async function start(materials: FixtureMaterial[], target = page) {
      await target.addInitScript(materials => {
        const t = window.readerTransport;
        t.replies.set('/api/v3/status', {body: {stage: 'Готово', errors: [], sources: [], ready_sources: 1, total_sources: 1}});
        t.replies.set('/api/v3/folders', {body: [{id: 'f', title: 'Тема', count: 0}]});
        t.replies.set('/api/v3/reading-composition', {body: {folders: [{id:'f',title:'Тема',count:0}], feeds:[...new Map(materials.map(m=>[m.feed_id,{id:m.feed_id,title:m.feed_title||m.feed_id,folder_id:m.folder_id||'f',kind:'rss',count:0}])).values()], count:0}});
        t.replies.set('/api/v3/processors/codex', {body:{config:null}});
        t.replies.set('/api/v3/youtube', {body:{mode:'direct', address:'', has_password:false, has_username:false, paused:false, next_at:0, strikes:0, reason:''}});
        t.replies.set('/api/v3/processors/codex/models', {body:{models:[]}});
        const settings = {originals: true, theses: true, prompt_theses: 'Тезисы', prompt_filters: '', processor: 'codex'};
        t.replies.set('/api/v3/settings/oko/global', {body: {overrides: {}, effective: Object.fromEntries(Object.entries(settings).map(([key, value]) => [key, {value, scope: 'oko', owner: 'global'}]))}});
        t.replies.set('/api/v3/cards', {body: materials.map(({text, description, display_text, reply_preview, ...card}) => ({folder_id:'f',published_known:true,feed_title:'feed',importance:'normal',snippet:'',...card}))});
        for (const material of materials) {
          const {snippet, feed_title, ...opened} = material;
          t.replies.set('/api/v3/materials/' + material.id, {body: opened});
        }
      }, materials);
      await target.goto(origin + '/#token=' + token);
      await expect(target.locator('#cards button')).toHaveCount(materials.length);
    }
  return {...fixture, errors, start, async close() {await fixture.close(); expect(schemaFailures).toEqual([]);}};
  } catch (error) {await fixture.close(); throw error;}
}
function material(id: string, extra: Partial<FixtureMaterial> = {}): FixtureMaterial { return {id, title: id, text: 'Текст ' + id, published: 100, published_known:true, importance:'normal', folder_id: 'f', feed_id: 'feed', url: 'https://example.org/' + id, complete: true, full: true, version: '1', images: [], attachments: [], ...extra}; }
export {reader, material, expect};

export type ReaderFixture = Awaited<ReturnType<typeof reader>>;
