import {reader, expect} from './support';
import {test} from 'node:test';
import assert from 'node:assert/strict';

async function setup() {
 const r=await reader();
 await r.page.addInitScript(()=>{
  readerTransport.replies.set('/api/v3/subscriptions',{body:{folders:[],feeds:[]}});
  readerTransport.replies.set('/api/v3/telegram/profiles',{body:{personal:{role:'personal',state:'ready',configuration_ref:'one',verified_user_id:'9007199254740993',checked_at:new Date().toISOString()},collection:{role:'collection',state:'unconfigured'},configurations:[{configuration_ref:'one',label:'Сессия один'},{configuration_ref:'two',label:'Сессия два'}]}});
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
  await expect(r.page.locator('#telegram-access')).toHaveValue('web');
  await expect(r.page.locator('#telegram-auto')).toBeHidden();
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

test('Telegram client requires verified selected role; duplicate popup describes all typed owners',async()=>{
 const r=await setup();try{
  await r.page.locator('#telegram-peer').fill('@channel');
  await r.page.locator('#telegram-access').selectOption('collection');
  await expect(r.page.locator('#telegram-add')).toBeDisabled();
  await r.page.locator('#telegram-access').selectOption('personal');
  await expect(r.page.locator('#telegram-add')).toBeEnabled();
  await expect(r.page.locator('#telegram-auto')).not.toBeChecked();
  await r.page.locator('#telegram-add').click();
  await r.page.waitForFunction(()=>readerTransport.pending.has('/api/v3/telegram/connect'));
  await r.page.evaluate(()=>readerTransport.release('/api/v3/telegram/connect',{code:'duplicate_source',message:'Уже добавлен',details:{existing_feeds:[{feed_id:'old',title:'Канал',access:{kind:'legacy_client',alias:'old-account',source_locator:'@channel',peer_id:null}},{feed_id:'other',title:'Другой владелец',access:{kind:'client',role:'personal',account_user_id:'9007199254740993',peer_id:'-10042'}}]}},409));
  await expect(r.page.getByRole('dialog',{name:'Source already added'})).toBeVisible();
  await expect(r.page.locator('#telegram-duplicate')).toContainText('old-account');
  await expect(r.page.locator('#telegram-duplicate')).toContainText('Personal Account');
  await expect(r.page.locator('#telegram-duplicate')).toContainText('delete');
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

test('legacy Telegram feed shows the saved alias and profile mapping without a reconnection form',async()=>{
 const r=await reader();try{
  const base=await r.api('v3/settings/oko/global');
  if(!base||typeof base!=='object')throw new Error('Missing HTTP settings snapshot');
  await r.start([]);
  await r.page.evaluate(snapshot=>{
   const common={id:'legacy',identity:'telegram-peer:old-account:-10042',title:'Старый канал',kind:'telegram',folder_id:'f',importance:'normal',initial_days:1,public_ai:false,original_urls:['@channel']};
   readerTransport.replies.set('/api/v3/subscriptions',{body:{folders:[{id:'f',title:'Прежние'}],feeds:[{...common,connection:'needs_profile_confirmation',access:{kind:'legacy_client',alias:'old-account',source_locator:'@channel',peer_id:'-10042'},history:{state:'absent',legacy_since:null}}]}});
   readerTransport.replies.set('/api/v3/settings/feed/legacy',{body:{...snapshot,scope:'feed',owner:'legacy',feed:{...common,connection:'needs_profile_confirmation',access:{kind:'legacy_client',alias:'old-account',source_locator:'@channel',peer_id:'-10042'},history:{state:'absent',legacy_since:null}}}});
  },base);
  await r.page.locator('#feeds-open').click();await r.page.locator('[data-owner="feed/legacy"]').click();
  await r.page.getByRole('button',{name:'Connection and Options',exact:true}).click();
  await expect(r.page.locator('#feed-options')).toContainText('old-account');
  await expect(r.page.locator('#feed-options')).not.toContainText('Personal Account');
  await expect(r.page.locator('#reconnect-connection')).toHaveCount(0);
  await r.page.getByRole('button',{name:'Configure Telegram Profiles',exact:true}).click();
  await expect(r.page.locator('[data-settings-page="telegram-profiles"]')).toBeVisible();
  assert.deepEqual(r.errors,[]);
 }finally{await r.close();}
});
