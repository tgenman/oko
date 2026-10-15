import type {ReaderError} from '../../frontend/src/api/errors';
interface TransportRequest {path?: RequestInfo | URL;url?: RequestInfo | URL;method?: string;cache?: RequestCache;keepalive?: boolean;authorization: string | null | undefined;contentType?: string;body?: BodyInit | null;signal?: boolean;}
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

test('generated SDK preserves binary media and its diagnostic HTTP failures', async () => {
  const r=await reader();
  try {
    await r.start([]);
    const reply=await r.page.evaluate(async()=>{
      const {createReaderApi,operations}=await import('/transport.js');
      const requests: TransportRequest[]=[];
      const api=createReaderApi({token:'binary-session',origin:location.origin,fetch:async(path,options)=>{
        requests.push({path,authorization:new Headers(options!.headers).get('Authorization')});
        if(String(path).endsWith('missing.txt'))return new Response('{"detail":"Медиа недоступно"}',{status:404,headers:{'Content-Type':'application/json','X-Oko-Error':'binary-diagnostic'}});
        return new Response(new Uint8Array([0,255,10]),{headers:{'Content-Type':'text/plain','Content-Disposition':'inline'}});
      }});
      const result=await operations.attachment({client:api.client,path:{item:'synthetic',name:'media-0.txt'}});
      let failure;
      try {await operations.attachment({client:api.client,path:{item:'synthetic',name:'missing.txt'}});}
      catch(caught){const error = caught as ReaderError;failure={message:error.message,confirmed:error.confirmed,diagnosticId:error.diagnosticId};}
      return {bytes:Array.from(new Uint8Array(await result.data.arrayBuffer())),disposition:result.response.headers.get('Content-Disposition'),requests,failure};
    });
    expect(reply.bytes).toEqual([0,255,10]);
    expect(reply.disposition).toBe('inline');
    expect(reply.requests).toHaveLength(2);
    expect(reply.requests.every(request=>request.authorization==='Bearer binary-session')).toBe(true);
    expect(reply.failure).toEqual({message:'Медиа недоступно',confirmed:true,diagnosticId:'binary-diagnostic'});
  } finally {await r.close();}
});

test('reader transport shares authorization and preserves JSON, media and closing request rules', async () => {
  const r = await reader();
  try {
    await r.start([]);
    const result = await r.page.evaluate(async () => {
      const {createTransport} = await import('/transport.js');
      const requests: TransportRequest[] = [];
      const controller = new AbortController();
      const transport = createTransport({token:'transport-session', origin:location.origin, fetch:async (url, options) => {
        requests.push({url, method:options!.method, cache:options!.cache, keepalive:options!.keepalive,
          authorization:new Headers(options?.headers).get('Authorization') ?? undefined, contentType:new Headers(options?.headers).get('Content-Type') || undefined, body:options!.body,
          signal:options!.signal === controller.signal});
        return new Response('{"saved":true}', {headers:{'Content-Type':'application/json'}});
      }});
      const saved = await transport.json('/api/settings/feed/f', 'PATCH', {theses:false}, controller.signal);
      await transport.media('/api/materials/a/media/image', controller.signal, 'no-store');
      await transport.media('https://other.test/image');
      await transport.close('/api/tabs/tab/close');
      const unavailable = createTransport({token:'session',origin:location.origin,fetch:async () => new Response('{\"detail\":\"Медиа недоступно\"}',{status:503,headers:{'X-Oko-Error':'media-diagnostic'}})});
      const media = await unavailable.media('/api/image');
      return {saved, requests, media:{ok:media.ok,status:media.status,diagnosticId:media.headers.get('X-Oko-Error')}};
    });
    expect(result.saved).toEqual({saved:true});
    expect(result.media).toEqual({ok:false,status:503,diagnosticId:'media-diagnostic'});
    expect(result.requests[0]).toEqual({url:'/api/settings/feed/f',method:'PATCH',cache:'no-store',authorization:'Bearer transport-session',contentType:'application/json',body:'{"theses":false}',signal:true});
    expect(result.requests[1]).toEqual({url:'/api/materials/a/media/image',cache:'no-store',authorization:'Bearer transport-session',signal:true});
    expect(result.requests[2].authorization).toBeUndefined();
    expect(result.requests[3]).toEqual({url:'/api/tabs/tab/close',method:'POST',keepalive:true,authorization:'Bearer transport-session',signal:false});
  } finally {await r.close();}
});

test('transport preserves error detail and diagnostic identity without retrying failed requests', async () => {
  const r = await reader();
  try {
    await r.start([]);
    const errors = await r.page.evaluate(async () => {
      const {createTransport} = await import('/transport.js');
      const detail = {message:'Подтвердите изменившийся состав',preview:{cards:2}};
      const replies = [
        () => new Response(JSON.stringify({detail}), {status:409,headers:{'X-Oko-Error':'diagnostic-1'}}),
        () => new Response('{"detail":"Операция отменена"}', {status:409,headers:{'X-Oko-Expected':'1'}}),
        () => new Response('<html>proxy failure</html>', {status:502,headers:{'X-Oko-Error':'diagnostic-2'}}),
        () => new Response('<html>unexpected success</html>'),
        () => {throw new TypeError('Network unavailable');},
        () => new Response('{"detail":[{"message":"invalid"}]}', {status:422}),
      ];
      let count = 0;
      const transport = createTransport({token:'session',origin:location.origin,fetch:async () => replies[count++]()});
      const errors: {name:string;message:string;detail?:unknown;diagnosticId?:string|null}[] = [];
      for (let i=0;i<6;i++) {
        try {await transport.json('/api/action','POST');}
        catch(caught) {const error = caught as ReaderError;errors.push({name:error.name,message:error.message,detail:error.detail,diagnosticId:error.diagnosticId});}
      }
      return {errors,count};
    });
    expect(errors.count).toBe(6);
    expect(errors.errors[0]).toEqual({name:'Error',message:'Связь с Око потеряна',detail:{message:'Подтвердите изменившийся состав',preview:{cards:2}},diagnosticId:'diagnostic-1'});
    expect(errors.errors[1].name).toBe('AbortError');
    expect(errors.errors[2].diagnosticId).toBe('diagnostic-2');
    expect(errors.errors[3].message).toBe('Некорректный ответ Око');
    expect(errors.errors[4].message).toBe('Network unavailable');
    expect(errors.errors[5].message).toBe('Проверьте значения настроек');
  } finally {await r.close();}
});

test('reader shows malformed responses and failed diagnostics retry only the original occurrence', async () => {
  const r = await reader();
  try {
    await r.start([material('one')]);
    await r.page.route('**/api/collect', route => route.fulfill({status:200,contentType:'text/html',body:'<html>broken reply</html>'}));
    await r.page.route('**/api/debug', route => route.request().method() === 'POST'
      ? route.fulfill({status:503,contentType:'text/html',body:'<html>offline</html>'}) : route.continue());
    await r.page.locator('#collect').click();
    await expect(r.page.locator('.header-command-error:not([hidden])')).toContainText('Некорректный ответ Око');
    await r.page.locator('#status-toggle').click();
    await r.page.locator('#status-journal-open').click();
    await expect(r.page.locator('#debug-errors')).toContainText('Некорректный ответ Око');
    await expect(r.page.locator('#debug-errors')).toContainText('не сохранено');
    await expect.poll(() => r.page.evaluate(() => readerTransport.requests.filter(request => request.url === '/api/debug' && request.method === 'POST').length)).toBeGreaterThan(1);
    const requests = await r.page.evaluate(() => readerTransport.requests.filter(request => request.url === '/api/debug' && request.method === 'POST').map(request => request.body));
    expect(new Set(requests.map(body => body!.event_id)).size).toBe(1);
    expect(requests.every(body => body!.message === 'Некорректный ответ Око')).toBe(true);
    expect(r.errors).toEqual([]);
  } finally {await r.close();}
});

test('reader closing sends an authorized keepalive and releases displayed media', async () => {
  const r = await reader();
  try {
    await r.page.addInitScript(() => {
      const fetch = window.fetch;
      window.closingRequests = [];
      window.fetch = (url, options) => {
        if (String(url).endsWith('/close')) window.closingRequests.push({url:String(url),method:options!.method,keepalive:options!.keepalive,authorization:new Headers(options!.headers).get('Authorization')});
        return fetch(url,options);
      };
    });
    await r.start([material('one')]);
    await r.page.locator('#cards button').click();
    await expect(r.page.locator('#title')).toHaveText('one');
    await r.page.evaluate(() => window.dispatchEvent(new PageTransitionEvent('pagehide')));
    const requests = await r.page.evaluate(() => window.closingRequests);
    expect(requests).toHaveLength(1);
    expect(requests[0].url).toMatch(/^\/api\/tabs\/[^/]+\/close$/);
    expect(requests[0].method).toBe('POST');
    expect(requests[0].keepalive).toBe(true);
    expect(requests[0].authorization).toBe('Bearer ' + r.token);
    await expect(r.page.locator('#title')).toHaveText('');
  } finally {await r.close();}
});
