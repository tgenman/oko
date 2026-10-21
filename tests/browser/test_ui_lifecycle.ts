import type {Route} from 'playwright';
import {test} from 'node:test';
import {reader, expect} from './support.ts';
import {editSetting, settingsHome, feedOptions} from './settings-actions.ts';
import {objects, models} from './settings-fixtures.ts';
import assert from 'node:assert/strict';

test('a completed field write reconciles after Back without discarding a newer draft', async () => {
  const r = await reader(); let held: Route | null | undefined;
  try {
    await r.start([]);
    await r.page.evaluate(()=>{readerTransport.replies.set('/api/v3/subscriptions',{body:{folders:[],feeds:[]}});readerTransport.native.add('/api/v3/settings/oko/global');});
    await r.page.route('**/api/v3/settings/oko/global',async route=>{
      if (route.request().method()==='PATCH') held=route; else await route.continue();
    });
    await r.api('settings/oko/global','PATCH',{theses:true});
    await r.page.locator('#settings-open').click();await editSetting(r.page,'theses');
    await r.page.locator('#setting-theses').uncheck();
    await r.page.getByRole('button',{name:'Save',exact:true}).click();
    await expect.poll(()=>Boolean(held)).toBe(true);
    await r.page.locator('#setting-theses').check();
    await r.page.locator('#settings-dialog .settings-back').click();
    await held!.continue(); held=null;
    await expect(r.page.getByRole('button',{name:'Publication Summaries',exact:true})).toContainText('Disabled');
    await expect(r.page.getByRole('button',{name:'Publication Summaries',exact:true})).toBeFocused();
    await r.page.getByRole('button',{name:'Publication Summaries',exact:true}).click();
    await expect(r.page.locator('#setting-theses')).toBeChecked();
    await expect(r.page.locator('.setting-row:visible details summary')).toBeVisible();
    await r.page.locator('.setting-row:visible details summary').click();
    await expect(r.page.locator('.setting-row:visible details')).toContainText('Disabled');
    await expect(r.page.locator('#settings-message')).not.toContainText('Setting saved');
  } finally {if(held)await (held as Route).abort();await r.close();}
});

test('Back restores the current field and section links after a saved editor rerenders', async () => {
  const r=await reader();try{
    await r.start([]);await r.page.evaluate(()=>{readerTransport.replies.set('/api/v3/subscriptions',{body:{folders:[],feeds:[]}});readerTransport.native.add('/api/v3/settings/oko/global');});
    await r.api('settings/oko/global','PATCH',{theses:true});
    await r.page.locator('#settings-open').click();await editSetting(r.page,'theses');
    await r.page.locator('#setting-theses').uncheck();await r.page.getByRole('button',{name:'Save',exact:true}).click();
    await expect(r.page.locator('#settings-message')).toHaveText('Setting saved');
    await r.page.locator('#settings-dialog .settings-back').click();
    const field=r.page.getByRole('button',{name:'Publication Summaries',exact:true});
    await expect(field).toBeFocused();
    await expect(field).toHaveAccessibleDescription('Disabled');
    await r.page.locator('#settings-dialog .settings-back').click();
    await expect(r.page.getByRole('button',{name:'Reading',exact:true})).toBeFocused();
  }finally{await r.close();}
});

test('Back leaves Telegram profiles and rejects delayed profile state from the previous section', async () => {
 const r=await reader();try{
  await r.start([]);
  await r.page.evaluate(()=>readerTransport.held.set('/api/v3/telegram/profiles',true));
  await r.page.locator('#settings-open').click();
  await r.page.locator('#settings-dialog [data-settings-target="telegram-profiles"]').click();
  await r.page.waitForFunction(()=>readerTransport.pending.has('/api/v3/telegram/profiles'));
  await r.page.locator('#settings-dialog .settings-back').click();
  await r.page.evaluate(()=>readerTransport.release('/api/v3/telegram/profiles',{personal:{role:'personal',state:'unconfigured'},collection:{role:'collection',state:'unconfigured'},configurations:[]}));
  await expect(r.page.locator('[data-settings-page="telegram-profiles"]')).toBeHidden();
  await editSetting(r.page,'prompt_theses');
  await expect(r.page.locator('#setting-prompt_theses')).toBeVisible();
  expect(r.errors).toEqual([]);
 }finally{await r.close();}
});

test('returning to Codex retries a catalog load interrupted by section navigation', async () => {
  const r = await reader();
  try {
    await r.start([]);
    await r.page.evaluate(models => {
      readerTransport.replies.set('/api/v3/subscriptions',{body:{folders:[],feeds:[]}});
      readerTransport.replies.set('/api/v3/processors/llamacpp',{body:null});
      readerTransport.replies.set('/api/v3/processors/codex/models',{body:{models}});
      readerTransport.held.set('/api/v3/processors/codex/models',true);
    }, models);
    await r.page.locator('#settings-open').click();
    await r.page.locator('[data-settings-target="models"]').click();
    await r.page.locator('[data-settings-target="codex"]').click();
    await r.page.waitForFunction(()=>readerTransport.pending.has('/api/v3/processors/codex/models'));
    await r.page.locator('#settings-dialog .settings-back').click();
    await r.page.evaluate(()=>{
      window.interruptedCatalogReply = readerTransport.pending.get('/api/v3/processors/codex/models')!;
      readerTransport.pending.delete('/api/v3/processors/codex/models');
      readerTransport.held.delete('/api/v3/processors/codex/models');
    });
    await r.page.locator('[data-settings-target="codex"]').click();
    await expect(r.page.locator('#codex-theses-model')).toHaveValue('small');
    await r.page.evaluate(()=>window.interruptedCatalogReply(new Response(JSON.stringify({models:[]}),{headers:{'Content-Type':'application/json'}})));
    await expect(r.page.locator('#codex-theses-model')).toHaveValue('small');
    expect(r.errors).toEqual([]);
  } finally {await r.close();}
});

test('late settings replies do not overwrite a newer draft or reopen a closed form', async () => {
  const r = await reader();
  try {
    await r.start([]);
    await r.page.evaluate(() => {
      readerTransport.replies.set('/api/v3/subscriptions', {body:{folders:[], feeds:[]}});
      readerTransport.replies.set('/api/v3/processors/llamacpp', {body:null});
    });
    await r.page.locator('#settings-open').click();
    await r.page.locator('[data-settings-target="youtube"]').click();
    await expect(r.page.locator('#youtube-connection')).toHaveText('Direct connection');
    await r.page.locator('#youtube-mode').selectOption('proxy');
    await r.page.locator('#youtube-address').fill('http://first.test:8000');
    await r.page.locator('#youtube-password').fill('first-password');
    await r.page.evaluate(() => readerTransport.held.set('/api/v3/youtube', true));
    await r.page.locator('#youtube-save').click();
    await expect.poll(() => r.page.evaluate(() => readerTransport.pending.has('/api/v3/youtube'))).toBe(true);
    await r.page.locator('#youtube-address').fill('http://second.test:8000');
    await r.page.locator('#youtube-password').fill('second-password');
    await r.page.locator('#settings-close').click();
    await r.page.evaluate(() => readerTransport.release('/api/v3/youtube', {mode:'proxy', address:'http://first.test:8000', has_password:true, has_username:false, paused:false, next_at:0, strikes:0, reason:''}));
    await expect(r.page.locator('#settings-dialog')).not.toBeVisible();
    await r.page.locator('#settings-open').click();
    await r.page.locator('[data-settings-target="youtube"]').click();
    await expect(r.page.locator('#youtube-address')).toHaveValue('http://second.test:8000');
    await expect(r.page.locator('#youtube-password')).toHaveValue('second-password');
    await expect(r.page.locator('#youtube-message')).toHaveText('');
    assert.deepEqual(r.errors, []);
  } finally {await r.close();}
});
