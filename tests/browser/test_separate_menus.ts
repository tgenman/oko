import type {Route} from 'playwright';
import {artifactPath} from './artifacts.ts';
import {test} from 'node:test';
import {objects} from './settings-fixtures.ts';
import {reader, material, expect} from './support.ts';
import {editSetting, settingsHome, modelPage, feedOptions} from './settings-actions.ts';

test('three menus keep reading position and addition tabs, Escape restores opening focus', async () => {
  const r = await reader();
  try {
    await r.start([material('one', {text: 'Читать дальше. '.repeat(3000)})]);
    await r.page.evaluate(() => {
      readerTransport.replies.set('/api/v3/subscriptions', {body: {folders: [], feeds: []}});
      readerTransport.replies.set('/api/v3/processors/llamacpp', {body: null});
      readerTransport.replies.set('/api/v3/telegram/accounts', {body: ['personal']});
    });
    await r.page.locator('#cards button').click();
    await expect(r.page.locator('#title')).toHaveText('one');
    await r.page.locator('main > article').evaluate(n => n.scrollTop = 450);
    await r.page.locator('#add-open').click();
    await expect(r.page.getByRole('dialog', {name: 'Add Feed', exact: true})).toBeVisible();
    await r.page.locator('#rss-url').fill('https://example.org/draft');
    await r.page.getByRole('tab', {name: 'Telegram', exact: true}).click();
    await expect(r.page.locator('#rss-url')).toBeHidden();
    await r.page.locator('#telegram-peer').fill('@draft');
    await r.page.keyboard.press('Escape');
    await expect(r.page.locator('#add-open')).toBeFocused();
    await r.page.locator('#feeds-open').click();
    await expect(r.page.getByRole('dialog', {name: 'Feeds and Folders', exact: true})).toBeVisible();
    await r.page.locator('#feeds-close').click();
    await r.page.locator('#add-open').click();
    await expect(r.page.locator('dialog[open]')).toHaveCount(1);
    await expect(r.page.locator('#telegram-peer')).toHaveValue('@draft');
    await r.page.getByRole('tab', {name: 'RSS / YouTube', exact: true}).click();
    await expect(r.page.locator('#rss-url')).toHaveValue('https://example.org/draft');
    await r.page.keyboard.press('Escape');
    await expect(r.page.locator('#add-open')).toBeFocused();
    await r.page.locator('#settings-open').click();
    await expect(r.page.getByRole('dialog', {name: 'Oko Settings', exact: true})).toBeVisible();
    await expect(r.page.getByRole('heading', {name:'Settings',exact:true})).toBeVisible();
    await expect(r.page.getByRole('button',{name:/^Models/})).toBeVisible();
    await r.page.keyboard.press('Escape');
    await expect(r.page.locator('#settings-open')).toBeFocused();
    await expect(r.page.locator('#title')).toHaveText('one');
    expect(await r.page.locator('main > article').evaluate(n => n.scrollTop)).toBe(450);
    expect(r.errors).toEqual([]);
  } finally { await r.close(); }
});

test('late subscription and settings responses cannot replace newer object selection; pending save retains later edits', async () => {
  const r = await reader();
  try {
    await r.start([]); await objects(r);
    await r.page.locator('#feeds-open').click();
    await r.page.locator('#feed-tree').getByRole('button', {name:'one',exact:true}).click();
    await expect(r.page.locator('#owner-heading')).toHaveText('Feed: one');
    await r.page.evaluate(() => {readerTransport.held.set('/api/v3/settings/feed/one',true); readerTransport.held.set('/api/v3/subscriptions',true);});
    await r.page.keyboard.press('Escape');
    await r.page.locator('#feeds-open').click();
    await r.page.waitForFunction(() => readerTransport.pending.has('/api/v3/subscriptions'));
    await r.page.locator('#feed-tree').getByRole('button', {name:'two',exact:true}).click();
    await expect(r.page.locator('#owner-heading')).toHaveText('Feed: two');
    await r.page.evaluate(() => readerTransport.release('/api/v3/subscriptions',readerTransport.replies.get('/api/v3/subscriptions')!.body));
    await expect(r.page.locator('#owner-heading')).toHaveText('Feed: two');
    await editSetting(r.page, 'prompt_theses');
    await r.page.locator('#setting-prompt_theses').fill('Отправленный');
    await r.page.evaluate(() => readerTransport.held.set('/api/v3/settings/feed/two',true));
    await editSetting(r.page, 'prompt_theses');
    await r.page.locator('.setting-row').filter({has:r.page.locator('#setting-prompt_theses')}).getByRole('button',{name:'Save',exact:true}).click();
    await r.page.waitForFunction(() => readerTransport.pending.has('/api/v3/settings/feed/two'));
    await editSetting(r.page, 'prompt_theses');
    await r.page.locator('#setting-prompt_theses').fill('Более новый черновик');
    await r.page.evaluate(() => {
      const result = structuredClone(readerTransport.replies.get('/api/v3/settings/oko/global')!.body);
      result!.effective.prompt_theses.value = 'Отправленный';
      readerTransport.replies.set('/api/v3/settings/feed/two',{body:result});
      readerTransport.release('/api/v3/settings/feed/two',{});
    });
    await expect(r.page.locator('#settings-message')).toHaveText('Setting saved');
    await expect(r.page.locator('#setting-prompt_theses')).toHaveValue('Более новый черновик');
    await r.page.locator('#feed-tree').getByRole('button', {name:'one',exact:true}).click();
    await r.page.waitForFunction(() => readerTransport.pending.has('/api/v3/settings/feed/one'));
    await r.page.locator('#feed-tree').getByRole('button', {name:'two',exact:true}).click();
    await r.page.evaluate(() => readerTransport.release('/api/v3/settings/feed/one',readerTransport.replies.get('/api/v3/settings/oko/global')!.body));
    await expect(r.page.locator('#owner-heading')).toHaveText('Feed: two');
    await expect(r.page.locator('#setting-prompt_theses')).toHaveValue('Более новый черновик');
    expect(r.errors).toEqual([]);
  } finally {await r.close();}
});

test('feed inherited fields show their owner and resetting only one override preserves other drafts', async () => {
  const r = await reader();
  try {
    await r.start([]); await objects(r);
    await r.page.evaluate(() => {
      const result = structuredClone(readerTransport.replies.get('/api/v3/settings/oko/global')!.body);
      result!.effective.prompt_theses = {value:'Свой промпт',scope:'feed',owner:'one'};
      result!.effective.prompt_filters = {value:'Фильтр папки',scope:'folder',owner:'f'};
      result!.overrides = {prompt_theses:'Свой промпт'};
      readerTransport.replies.set('/api/v3/settings/feed/one',{body:result});
      readerTransport.replies.set('PATCH /api/v3/settings/feed/one',{body:{}});
    });
    await r.page.locator('#feeds-open').click();
    await r.page.locator('#feed-tree').getByRole('button', {name:'one',exact:true}).click();
    const theses = r.page.locator('.setting-row').filter({has:r.page.locator('#setting-prompt_theses')});
    const filter = r.page.locator('.setting-row').filter({has:r.page.locator('#setting-prompt_filters')});
    await expect(theses).toContainText('Feed: one');
    await expect(filter).toContainText('Folder: Учёба');
    await editSetting(r.page, 'prompt_filters');
    await r.page.locator('#setting-prompt_filters').fill('Несохранённый фильтр');
    await r.page.evaluate(() => {
      const result=structuredClone(readerTransport.replies.get('/api/v3/settings/feed/one')!.body);
      result!.effective.prompt_theses={value:'Промпт папки',scope:'folder',owner:'f'};result!.overrides={};
      readerTransport.replies.set('PATCH /api/v3/settings/feed/one',{body:{},next:[['/api/v3/settings/feed/one',{body:result}]]});
    });
    await editSetting(r.page, 'prompt_theses');
    await theses.getByRole('button',{name:'Reset',exact:true}).click();
    await expect(r.page.locator('#setting-prompt_theses')).toHaveValue('Промпт папки');
    await expect(theses).toContainText('Folder: Учёба');
    await expect(theses.getByRole('button',{name:'Reset',exact:true})).toBeDisabled();
    await expect(r.page.locator('#setting-prompt_filters')).toHaveValue('Несохранённый фильтр');
    const patch=await r.page.evaluate(()=>readerTransport.requests.find(q=>q.method==='PATCH')!.body);
    expect(patch).toEqual({prompt_theses:null});
    expect(r.errors).toEqual([]);
  } finally {await r.close();}
});

test('a saved feed updates reading even when navigation cancels its catalog refresh', async () => {
  const r = await reader();
  try {
    await r.start([material('before')]); await objects(r);
    await r.page.evaluate(()=>{const nav=readerTransport.replies.get('/api/v3/reading-composition')!.body!;nav.feeds.push({id:'one',title:'one',folder_id:'f',kind:'rss',count:1});});
    await r.page.locator('#feeds-open').click();
    await r.page.locator('#feed-tree').getByRole('button', {name:'one',exact:true}).click();
    const subscriptions = await r.page.evaluate(() => readerTransport.replies.get('/api/v3/subscriptions')!.body);
    const domainStatus=await r.api('v3/status/details');
    let catalogRead = 0, statusRead = 0;
    let heldCatalog: Route | undefined, heldStatus: Route | undefined;
    await r.page.route('**/api/v3/subscriptions', async route => {
      if (++catalogRead === 1) heldCatalog = route;
      else await route.fulfill({json:subscriptions});
    });
    await r.page.route('**/api/v3/status/details', async route => {
      if (++statusRead === 1) heldStatus = route;
      else await route.fulfill({json:domainStatus});
    });
    await r.page.evaluate(() => {
      readerTransport.native.add('/api/v3/status/details');
      readerTransport.native.add('/api/v3/subscriptions');
      readerTransport.replies.set('PATCH /api/v3/feeds/one',{body:{}});
    });
    // An old background read is still pending when the server write completes.
    await expect.poll(() => Boolean(heldStatus)).toBe(true);
    await r.page.evaluate(()=>readerTransport.native.delete('/api/v3/status/details'));
    await r.page.evaluate(() => {readerTransport.replies.get('/api/v3/cards')!.body = [{id:'after',title:'После сохранения',published:100,folder_id:'f',feed_id:'one',url:'https://example.org/after',complete:true,published_known:true,feed_title:'one',importance:'high',snippet:''}];});
    await feedOptions(r.page);
    await r.page.locator('#feed-importance').selectOption('high');
    await r.page.getByRole('button',{name:'Save Feed',exact:true}).click();
    await expect.poll(() => Boolean(heldCatalog)).toBe(true);
    await r.page.keyboard.press('Escape');
    await r.page.locator('#add-open').click();
    await expect.poll(() => catalogRead).toBe(2);
    await expect(r.page.locator('#cards button')).toContainText('После сохранения');
    await expect(r.page.locator('#add-dialog')).toBeVisible();
    expect(r.errors).toEqual([]);
  } finally {await r.close();}
});

test('confirmed feed removal wins over stale catalogue snapshots without deleting siblings',async()=>{
 const r=await reader();try{
  await r.start([]);await objects(r);
  await r.page.evaluate(()=>readerTransport.replies.set('DELETE /api/v3/feeds/one',{body:{feed_id:'one',state:'deleted'}}));
  await r.page.locator('#feeds-open').click();await r.page.locator('#feed-tree [data-owner="feed/one"]').click();await feedOptions(r.page);
  await r.page.locator('#feed-delete').click();await r.page.locator('#feed-delete-confirm-action').click();
  await expect(r.page.locator('#feed-tree [data-owner="feed/one"]')).toHaveCount(0);
  await expect(r.page.locator('#feed-tree [data-owner="feed/two"]')).toHaveCount(1);
  await r.page.locator('#feeds-close').click();await r.page.locator('#feeds-open').click();
  await expect(r.page.locator('#feed-tree [data-owner="feed/one"]')).toHaveCount(0);
  expect(r.errors).toEqual([]);
 }finally{await r.close();}
});
