import {addSources} from './connections';
import {test} from 'node:test';
import {expect} from 'playwright/test';
import {productionReader} from './http-reader.ts';
import assert from 'node:assert/strict';

async function isClosed(origin: string) {
  try {await fetch(origin,{signal:AbortSignal.timeout(1000)});return false;} catch {return true;}
}

test('HTTP scenarios isolate storage, settings, authentication and journals while overlapping', async t=>{
  const [a,b]=await Promise.all([productionReader(t),productionReader(t)]);
  assert.notEqual(a.origin,b.origin);
  assert.notEqual(a.token,b.token);
  assert.equal((await fetch(b.origin+'/api/v3/cards',{headers:{Authorization:'Bearer '+a.token}})).status,403);
  const originalSettings=await b.api('settings/oko/global');
  await a.api('settings/oko/global','PATCH',{theses:false,prompt_filters:'scenario A'});
  await a.api('diagnostics','POST',{source:'browser',message:'only A'});
  await addSources(a.api, [["https://a.test/feed", "A", "Без папки"]]);
  assert.deepEqual(await b.api('settings/oko/global'),originalSettings);
  assert.deepEqual((await b.api('cards')).items,[]);
  assert.deepEqual((await b.api('reading-composition')).feeds,[]);
  assert.deepEqual((await b.api('diagnostics')).groups,[]);
  assert.ok((await a.api('reading-composition')).feeds.length);
  assert.ok((await a.api('diagnostics')).groups.some(group=>group.message==='only A'));
  await a.close();
  await a.close();
  await expect.poll(()=>isClosed(a.origin)).toBe(true);
  await b.api('diagnostics','POST',{source:'browser',message:'B still works'});
  assert.equal((await b.api('diagnostics')).groups[0].message,'B still works');
  const c=await productionReader(t);
  assert.deepEqual((await c.api('diagnostics')).groups,[]);
  assert.deepEqual((await c.api('reading-composition')).feeds,[]);
  assert.deepEqual(await c.api('settings/oko/global'),originalSettings);
});

test('aborting one HTTP scenario closes its server and pages without stopping its neighbour',async t=>{
  const controller=new AbortController();
  const a=await productionReader({signal:controller.signal,after:callback=>t.after(callback)});
  const b=await productionReader(t);
  controller.abort();
  await expect.poll(()=>a.page.isClosed()).toBe(true);
  await expect.poll(()=>isClosed(a.origin)).toBe(true);
  assert.deepEqual((await b.api('cards')).items,[]);
  await a.close();
});

test('abort during HTTP setup rejects and leaves the next scenario usable',async t=>{
  const controller=new AbortController();
  const opening=productionReader({signal:controller.signal,after:callback=>t.after(callback)});
  controller.abort();
  await assert.rejects(opening,{name:'AbortError'});
  const next=await productionReader(t);
  assert.deepEqual((await next.api('cards')).items,[]);
});
