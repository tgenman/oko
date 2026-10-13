const {productionReader} = require('./http-reader.cjs');
const {expect} = require('playwright/test');
const {contractFor, validate} = require('./contracts.cjs');

async function reader() {
  const fixture = await productionReader();
  const {page, context, origin, token} = fixture;
  const schemaFailures = [];
  try {
    await context.exposeBinding('validateReaderReply', (_, url, method, status, body, alias, record = false) => {
      const name = alias || contractFor(url, method);
      if (name && status >= 200 && status < 300) {
        try {validate(name, body);} catch (error) {if (record) schemaFailures.push(error.message); throw error;}
      }
    });
    await context.addInitScript(() => {
      // External transport adapter deliberately ignores cancellation: a late
      // response must be harmless even when abort cannot stop its delivery.
      const nativeFetch = window.fetch.bind(window);
      const replies = new Map();
      const held = new Map();
      const pending = new Map();
      const aliases = new Map();
      const response = async (url, method, body, status = 200) => {
        await window.validateReaderReply(url, method, status, body, aliases.get(url), true);
        return new Response(JSON.stringify(body), {status, headers: {'Content-Type': 'application/json'}});
      };
      window.readerTransport = {
        replies, held, pending, aliases, native: new Set(), requests: [],
        release(url, body, status = 200) {
          const resolve = pending.get(url); pending.delete(url); held.delete(url);
          resolve(response(url, 'GET', body, status));
        },
      };
      window.fetch = async (url, options) => {
        const key = String(url);
        window.readerTransport.requests.push({url: key, method: options?.method || 'GET', body: options?.body ? JSON.parse(options.body) : null});
        if (window.readerTransport.native.has(key)) return nativeFetch(url, options);
        if (held.has(key)) return new Promise(resolve => pending.set(key, resolve));
        const method = options?.method || 'GET', pathname = key.split('?')[0];
        const reply = replies.get(method + ' ' + key) || replies.get(key) || replies.get(method + ' ' + pathname) || replies.get(pathname);
        if (reply) {
          for (const [route, next] of reply.next || []) replies.set(route, next);
          if (reply.base64) return new Response(Uint8Array.from(atob(reply.base64), c => c.charCodeAt(0)), {headers: {'Content-Type': reply.mime}});
          return response(key, method, reply.body, reply.status || 200);
        }
        return nativeFetch(url, options);
      };
      const created = URL.createObjectURL.bind(URL), revoked = URL.revokeObjectURL.bind(URL);
      window.browserResources = {created: [], revoked: []};
      URL.createObjectURL = blob => { const url = created(blob); window.browserResources.created.push(url); return url; };
      URL.revokeObjectURL = url => { window.browserResources.revoked.push(url); revoked(url); };
    });
    const errors = []; page.on('pageerror', error => errors.push(error.message));
    async function start(materials, target = page) {
      await target.addInitScript(materials => {
        const t = window.readerTransport;
        t.replies.set('/api/status', {body: {stage: 'Готово', errors: [], sources: [], ready_sources: 1, total_sources: 1}});
        t.replies.set('/api/folders', {body: [{id: 'f', title: 'Тема', count: 0}]});
        t.replies.set('/api/navigation', {body: {folders: [{id:'f',title:'Тема',count:0}], feeds:[{id:'feed',title:'feed',folder_id:'f',kind:'rss',count:0}], count:0}});
        t.replies.set('/api/processors/codex', {body:{config:null}});
        t.replies.set('/api/youtube', {body:{mode:'direct', address:'', has_password:false, has_username:false, paused:false, next_at:0}});
        t.replies.set('/api/processors/codex/models', {body:{models:[]}});
        const settings = {events: true, event_hours: 24, originals: true, theses: true, digests: false, prompt_theses: 'Тезисы', prompt_digest: 'Темы', prompt_filters: '', schedule: 'off', digest_days: 1, processor: 'codex'};
        t.replies.set('/api/settings/oko/global', {body: {overrides: {}, effective: Object.fromEntries(Object.entries(settings).map(([key, value]) => [key, {value, scope: 'oko', owner: 'global'}]))}});
        t.replies.set('/api/cards', {body: materials.map(({text, description, display_text, sources, ...card}) => ({...card, ...(sources ? {sources:sources.map(({id,url,feed_id,title})=>({id,url,feed_id,title}))} : {})}))});
        for (const material of materials) {
          const {snippet, feed_title, ...opened} = material;
          t.replies.set('/api/materials/' + material.id, {body: opened});
          for (const version of [...(material.previous || []), ...(material.sources || []).flatMap(source => source.previous || [])]) t.aliases.set(version.url, 'version');
        }
      }, materials);
      await target.goto(origin + '/#token=' + token);
      await expect(target.locator('#cards button')).toHaveCount(materials.length);
    }
  return {...fixture, errors, start, async close() {await fixture.close(); expect(schemaFailures).toEqual([]);}};
  } catch (error) {await fixture.close(); throw error;}
}
function material(id, extra = {}) { return {id, title: id, text: 'Текст ' + id, published: 100, folder_id: 'f', feed_id: 'feed', url: 'https://example.org/' + id, complete: true, full: true, version: '1', images: [], attachments: [], ...extra}; }
module.exports = {reader, material, expect};
