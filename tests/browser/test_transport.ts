import {test} from 'node:test';
import {reader, material, expect} from './support.ts';

test('cached-page restoration reloads the reader after its close event', async()=>{
  const r=await reader();
  try {
    await r.start([material('cached')]);
    await r.page.locator('#cards button').click();
    await expect(r.page.locator('#title')).toHaveText('cached');
    await r.page.evaluate(()=>window.dispatchEvent(new PageTransitionEvent('pagehide',{persisted:true})));
    const reload=r.page.waitForEvent('framenavigated',{timeout:2000});
    await r.page.evaluate(()=>window.dispatchEvent(new PageTransitionEvent('pageshow',{persisted:true})));
    await reload;
    await expect(r.page.locator('#cards button')).toHaveCount(1);
    await expect(r.page.locator('#title')).toHaveText('');
  } finally {await r.close();}
});

test('reader shows malformed responses and failed diagnostics retry only the original occurrence', async () => {
  const r = await reader();
  try {
    await r.start([material('one')]);
    await r.page.route('**/api/v3/collect', route => route.fulfill({status:200,contentType:'text/html',body:'<html>broken reply</html>'}));
    await r.page.route('**/api/v3/diagnostics', route => route.request().method() === 'POST'
      ? route.fulfill({status:503,contentType:'text/html',body:'<html>offline</html>'}) : route.continue());
    await r.page.locator('#collect').click();
    await expect(r.page.locator('.header-command-error:not([hidden])')).toContainText('Invalid response from Oko');
    await r.page.locator('#status-toggle').click();
    await r.page.locator('#status-journal-open').click();
    await expect(r.page.locator('#debug-errors')).toContainText('Invalid response from Oko');
    await expect(r.page.locator('#debug-errors')).toContainText('not saved');
    await expect.poll(() => r.page.evaluate(() => readerTransport.requests.filter(request => request.url === '/api/v3/diagnostics' && request.method === 'POST').length)).toBeGreaterThan(1);
    const requests = await r.page.evaluate(() => readerTransport.requests.filter(request => request.url === '/api/v3/diagnostics' && request.method === 'POST').map(request => request.body));
    expect(new Set(requests.map(body => body!.event_id)).size).toBe(1);
    expect(requests.every(body => body!.message === 'Invalid response from Oko')).toBe(true);
    expect(r.errors).toEqual([]);
  } finally {await r.close();}
});

test('reader closing sends an authorized keepalive and releases displayed media', {skip:'Временно отключён по просьбе пользователя 2026-10-15; проверка closing/keepalive отложена.'}, async () => {
  const r = await reader();
  try {
    await r.page.addInitScript(() => {
      const fetch = window.fetch;
      window.closingRequests = [];
      window.fetch = (url, options) => {
        if (String(url).startsWith('/api/v3/client-sessions/') && options?.method==='DELETE') window.closingRequests.push({url:String(url),method:options!.method,keepalive:options!.keepalive,authorization:new Headers(options!.headers).get('Authorization')});
        return fetch(url,options);
      };
    });
    await r.start([material('one')]);
    await r.page.locator('#cards button').click();
    await expect(r.page.locator('#title')).toHaveText('one');
    await r.page.evaluate(() => window.dispatchEvent(new PageTransitionEvent('pagehide')));
    const requests = await r.page.evaluate(() => window.closingRequests);
    expect(requests).toHaveLength(1);
    expect(requests[0].url).toMatch(/^\/api\/v3\/client-sessions\/[^/]+$/);
    expect(requests[0].method).toBe('DELETE');
    expect(requests[0].keepalive).toBe(true);
    expect(requests[0].authorization).toBe('Bearer ' + r.token);
    await expect(r.page.locator('#title')).toHaveText('');
  } finally {await r.close();}
});
