const {chromium} = require('playwright');
const http = require('node:http');
const fs = require('node:fs/promises');
const path = require('node:path');
const {once} = require('node:events');
const {expect} = require('playwright/test');

async function reader() {
  const server = http.createServer(async (request, response) => {
    try {
      const name = request.url === '/' ? 'index.html' : request.url.slice(1);
      if (!['index.html', 'app.js', 'controls.js', 'ui-operations.js', 'menus.js', 'settings-editor.js', 'model-settings.js', 'opml-import.js', 'debug.js', 'feed-connection.js', 'initial-history.js', 'opened-material.js', 'telegram-connection.js', 'style.css'].includes(name)) {
        response.writeHead(404).end(); return;
      }
      const data = await fs.readFile(path.join(__dirname, '../../oko/web', name));
      response.setHeader('Content-Type', name.endsWith('.js') ? 'text/javascript' : name.endsWith('.css') ? 'text/css' : 'text/html');
      response.end(data);
    } catch { response.writeHead(404).end(); }
  });
  server.listen(0, '127.0.0.1'); await once(server, 'listening');
  let browser;
  try {
    browser = await chromium.launch({headless: true, executablePath: process.env.OKO_BROWSER_EXECUTABLE || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
    const context = await browser.newContext();
    await context.addInitScript(() => {
      // External transport adapter deliberately ignores cancellation: a late
      // response must be harmless even when abort cannot stop its delivery.
      const nativeFetch = window.fetch.bind(window);
      const replies = new Map();
      const held = new Map();
      const pending = new Map();
      window.readerTransport = {
        replies, held, pending, native: new Set(), cards: [], requests: [], debugGroups: [],
        release(url, body, status = 200) {
          const resolve = pending.get(url); pending.delete(url); held.delete(url);
          resolve(new Response(JSON.stringify(body), {status, headers: {'Content-Type': 'application/json'}}));
        },
      };
      window.fetch = async (url, options) => {
        const key = String(url);
        window.readerTransport.requests.push({url: key, method: options?.method || 'GET', body: options?.body ? JSON.parse(options.body) : null});
        if (window.readerTransport.native.has(key)) return nativeFetch(url, options);
        if (held.has(key)) return new Promise(resolve => pending.set(key, resolve));
        const replyKey = (options?.method || 'GET') + ' ' + key;
        if (replies.has(replyKey) || replies.has(key)) {
          const reply = replies.get(replyKey) || replies.get(key);
          if (reply.base64) return new Response(Uint8Array.from(atob(reply.base64), c => c.charCodeAt(0)), {headers: {'Content-Type': reply.mime}});
          return new Response(JSON.stringify(reply.body), {status: reply.status || 200, headers: {'Content-Type': 'application/json'}});
        }
        if (key === '/api/debug') {
          const t = window.readerTransport;
          if (options?.method === 'DELETE') {
            t.debugGroups = [];
            return new Response(JSON.stringify({ok: true, persistence_error: ''}));
          }
          if (options?.method === 'POST') {
            const body = JSON.parse(options.body);
            const old = t.debugGroups.find(g => g.source === body.source && g.location === body.location && g.message === body.message);
            const entry = {...body, id: old?.id || body.event_id, count: (old?.count || 0) + body.count,
              first_time: old?.first_time || body.first_at, last_time: body.occurred_at, streams: {...(old?.streams || {}), [body.stream_id]: body.sequence}};
            t.debugGroups = t.debugGroups.filter(g => g.id !== entry.id); t.debugGroups.unshift(entry);
            return new Response(JSON.stringify({ok: true, entry}));
          }
          return new Response(JSON.stringify({groups: t.debugGroups, persistence_error: ''}));
        }
        if (key.startsWith('/api/cards?')) {
          const folder = new URL(key, location.origin).searchParams.get('folder');
          return new Response(JSON.stringify(window.readerTransport.cards.filter(card => !folder || card.folder_id === folder)));
        }
        if (key.endsWith('/viewed')) {
          const id = key.split('/')[3];
          window.readerTransport.cards = window.readerTransport.cards.filter(card => card.id !== id && !(card.sources || []).some(source => source.id === id));
          return new Response('{}');
        }
        if (key.startsWith('/api/tabs/') || key === '/api/stop') return new Response('{}');
        return nativeFetch(url, options);
      };
      const created = URL.createObjectURL.bind(URL), revoked = URL.revokeObjectURL.bind(URL);
      window.browserResources = {created: [], revoked: []};
      URL.createObjectURL = blob => { const url = created(blob); window.browserResources.created.push(url); return url; };
      URL.revokeObjectURL = url => { window.browserResources.revoked.push(url); revoked(url); };
    });
    const origin = 'http://127.0.0.1:' + server.address().port;
    const page = await context.newPage();
    const errors = []; page.on('pageerror', error => errors.push(error.message));
    async function start(materials, target = page) {
      await target.addInitScript(materials => {
        const t = window.readerTransport;
        t.replies.set('/api/status', {body: {stage: 'Готово', errors: [], sources: [], ready_sources: 1, total_sources: 1}});
        t.replies.set('/api/folders', {body: [{id: 'f', title: 'Тема', count: materials.length}]});
        const defaults = {events: true, event_hours: 24, originals: true, theses: false, digests: false, prompt_theses: 'Тезисы', prompt_digest: 'Темы', prompt_filters: '', schedule: 'off', digest_days: 1, processor: 'codex'};
        t.replies.set('/api/settings/oko/global', {body: {overrides: {}, effective: Object.fromEntries(Object.entries(defaults).map(([key, value]) => [key, {value, scope: 'oko', owner: 'global'}]))}});
        t.cards = materials.map(({text, ...card}) => card);
        for (const material of materials) t.replies.set('/api/materials/' + material.id, {body: material});
      }, materials);
      await target.goto(origin);
      await expect(target.locator('#cards button')).toHaveCount(materials.length);
    }
    return {page, context, origin, errors, start, async close() {await browser.close(); server.close(); await once(server, 'close');}};
  } catch (error) { if (browser) await browser.close(); server.close(); throw error; }
}
function material(id, extra = {}) { return {id, title: id, text: 'Текст ' + id, published: 100, folder_id: 'f', feed_id: 'feed', url: 'https://example.org/' + id, complete: true, full: true, version: '1', images: [], attachments: [], ...extra}; }
module.exports = {reader, material, expect};
