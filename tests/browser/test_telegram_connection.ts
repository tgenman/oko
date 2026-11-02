import {reader, expect} from './support';
import {test} from 'node:test';
import assert from 'node:assert/strict';

async function setup() {
 const r=await reader();
 await r.page.addInitScript(()=>{
  readerTransport.replies.set('/api/v3/subscriptions',{body:{folders:[],feeds:[]}});
  readerTransport.held.set('/api/v3/telegram/connect',true);
 });
 await r.start([]);
 await r.page.locator('#add-open').click();
 await r.page.locator('#add-telegram-tab').click();
 await r.page.locator('#telegram-folder-new').fill('Telegram');
 return r;
}

test('Telegram defaults to web without account or read controls and sends exact history',async()=>{
 const r=await setup();try{
  await expect(r.page.locator('#telegram-access')).toHaveCount(0);
  await expect(r.page.locator('#telegram-auto')).toHaveCount(0);
  await r.page.locator('#telegram-peer').fill('https://t.me/s/channel/42');
  await expect(r.page.locator('#telegram-add')).toBeEnabled();
  await r.page.locator('#telegram-add').click();
  await r.page.waitForFunction(()=>readerTransport.pending.has('/api/v3/telegram/connect'));
  const request=await r.page.evaluate(()=>readerTransport.requests.find(r=>r.url==='/api/v3/telegram/connect'));
  assert.deepEqual(request?.body,{source_locator:'https://t.me/s/channel/42',folder:'Telegram',access:{kind:'web'},initial_history:{mode:'count',count:'20'}});
  await r.page.evaluate(()=>readerTransport.release('/api/v3/telegram/connect',{code:'source_unavailable',message:'Недоступен',details:{retry_at:null}},503));
  await expect(r.page.locator('#telegram-message')).toHaveText('Недоступен');
  assert.deepEqual(r.errors,[]);
 }finally{await r.close();}
});

test('duplicate public Telegram source remains visible',async()=>{
 const r=await setup();try{
  await r.page.locator('#telegram-peer').fill('@channel');
  await r.page.locator('#telegram-add').click();
  await r.page.waitForFunction(()=>readerTransport.pending.has('/api/v3/telegram/connect'));
  await r.page.evaluate(()=>readerTransport.release('/api/v3/telegram/connect',{code:'duplicate_source',message:'Already added',details:{existing_feeds:[{feed_id:'old',title:'Channel',access:{kind:'web',preview_url:'https://t.me/s/channel',username:'channel'}}]}},409));
  await expect(r.page.getByRole('dialog',{name:'Source already added'})).toContainText('Channel');
  await r.page.locator('#telegram-duplicate-close').click();
  await expect(r.page.locator('#telegram-peer')).toHaveValue('@channel');
  assert.deepEqual(r.errors,[]);
 }finally{await r.close();}
});

test('late Telegram connect failure cannot replace the new form context',async()=>{
 const r=await setup();try{
  await r.page.locator('#telegram-peer').fill('@first');
  await r.page.locator('#telegram-add').click();
  await r.page.waitForFunction(()=>readerTransport.pending.has('/api/v3/telegram/connect'));
  await r.page.locator('#add-rss-tab').click();
  await r.page.evaluate(()=>readerTransport.release('/api/v3/telegram/connect',{code:'source_unavailable',message:'Старый ответ',details:{retry_at:null}},503));
  await r.page.locator('#add-telegram-tab').click();
  await expect(r.page.locator('#telegram-message')).not.toContainText('Старый ответ');
  assert.deepEqual(r.errors,[]);
 }finally{await r.close();}
});

test('successful Telegram connect clears only the submitted draft after its menu closes',async()=>{
 const r=await setup();try{
  await r.page.locator('#telegram-peer').fill('@submitted');await r.page.locator('#telegram-add').click();
  await r.page.waitForFunction(()=>readerTransport.pending.has('/api/v3/telegram/connect'));
  await r.page.locator('#add-close').click();
  await r.page.evaluate(()=>readerTransport.release('/api/v3/telegram/connect',{feed_id:'created',access:{kind:'web',username:'submitted',preview_url:'https://t.me/s/submitted'}}));
  await expect(r.page.locator('#telegram-add')).toHaveText('Connect Source');
  await r.page.locator('#add-open').click();await r.page.locator('#add-telegram-tab').click();
  await expect(r.page.locator('#telegram-peer')).toHaveValue('');
  await expect(r.page.locator('#feeds-dialog')).not.toBeVisible();
  assert.deepEqual(r.errors,[]);
 }finally{await r.close();}
});
