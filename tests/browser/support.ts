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
      const v3Reply=(url:string,method:string,body:unknown):unknown=>{
        if(url==='/api/v3/processors/llamacpp'&&method==='GET'&&body===null)return {configuration:null};
        if(!body||typeof body!=='object')return body;
        if(url.startsWith('/api/v3/settings/')&&'effective' in body&&'overrides' in body&&!('scope' in body)){
          const [, , , ,scope,owner]=url.split('/');
          const effective=Object.fromEntries(Object.entries(body.effective||{}).map(([key,item])=>{
            if(!item||typeof item!=='object'||!('value' in item)||!('scope' in item))return [key,item];
            return [key,{value:item.value,origin:{scope:item.scope,...(item.scope==='default'?{}:{owner:'owner' in item?item.owner:'global'})}}];
          }));
          return {scope,owner,effective,overrides:body.overrides};
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
      window.fetch = async (url, options) => {
        const key = String(url);
        window.readerTransport.requests.push({url: key, method: options?.method || 'GET', body: options?.body ? JSON.parse(String(options.body)) : null});
        if (window.readerTransport.native.has(key)) return nativeFetch(url, options);
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
        t.replies.set('/api/status', {body: {stage: 'Готово', errors: [], sources: [], ready_sources: 1, total_sources: 1}});
        t.replies.set('/api/folders', {body: [{id: 'f', title: 'Тема', count: 0}]});
        t.replies.set('/api/navigation', {body: {folders: [{id:'f',title:'Тема',count:0}], feeds:[{id:'feed',title:'feed',folder_id:'f',kind:'rss',count:0}], count:0}});
        t.replies.set('/api/v3/processors/codex', {body:{config:null}});
        t.replies.set('/api/youtube', {body:{mode:'direct', address:'', has_password:false, has_username:false, paused:false, next_at:0, strikes:0, reason:''}});
        t.replies.set('/api/v3/processors/codex/models', {body:{models:[]}});
        const settings = {originals: true, theses: true, prompt_theses: 'Тезисы', prompt_filters: '', processor: 'codex'};
        t.replies.set('/api/v3/settings/oko/global', {body: {overrides: {}, effective: Object.fromEntries(Object.entries(settings).map(([key, value]) => [key, {value, scope: 'oko', owner: 'global'}]))}});
        t.replies.set('/api/cards', {body: materials.map(({text, description, display_text, ...card}) => ({folder_id:'f',published_known:true,feed_title:'feed',importance:'normal',snippet:'',...card}))});
        for (const material of materials) {
          const {snippet, feed_title, ...opened} = material;
          t.replies.set('/api/materials/' + material.id, {body: opened});
          for (const version of material.previous || []) t.aliases.set(version.url, 'version');
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
