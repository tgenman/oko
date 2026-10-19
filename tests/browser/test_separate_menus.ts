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


test('object and global drafts are independent, field save preserves other unsaved changes and close never saves', async () => {
  const r = await reader();
  try {
    await r.start([]); await objects(r);
    await r.page.locator('#feeds-open').click();
    await r.page.locator('#feed-tree').getByRole('button', {name:'one',exact:true}).click();
    await editSetting(r.page, 'prompt_theses');
    await r.page.locator('#setting-prompt_theses').fill('Черновик первого');
    await editSetting(r.page, 'prompt_filters');
    await r.page.locator('#setting-prompt_filters').fill('Фильтр первого');
    await feedOptions(r.page);
    await r.page.locator('#feed-importance').selectOption('high');
    await r.page.locator('#feed-tree').getByRole('button', {name:'two',exact:true}).click();
    await expect(r.page.locator('#setting-prompt_theses')).toHaveValue('Тезисы');
    await editSetting(r.page, 'prompt_theses');
    await r.page.locator('#setting-prompt_theses').fill('Черновик второго');
    await r.page.locator('#feed-tree').getByRole('button', {name:'Учёба',exact:true}).click();
    await editSetting(r.page, 'prompt_theses');
    await r.page.locator('#setting-prompt_theses').fill('Черновик папки');
    await r.page.locator('#feed-tree').getByRole('button', {name:'one',exact:true}).click();
    await expect(r.page.locator('#setting-prompt_theses')).toHaveValue('Черновик первого');
    await expect(r.page.locator('#feed-importance')).toHaveValue('high');
    await r.page.evaluate(() => {
      const t = readerTransport;
      t.replies.set('PATCH /api/v3/settings/feed/one', {body:{}});
      t.replies.get('/api/v3/settings/feed/one')!.body!.effective.prompt_theses.value = 'Черновик первого';
    });
    await editSetting(r.page, 'prompt_theses');
    await r.page.locator('.setting-row').filter({has:r.page.locator('#setting-prompt_theses')}).getByRole('button',{name:'Save',exact:true}).click();
    await expect(r.page.locator('#settings-message')).toHaveText('Setting saved');
    await expect(r.page.locator('#setting-prompt_filters')).toHaveValue('Фильтр первого');
    await expect(r.page.locator('#feed-importance')).toHaveValue('high');
    await r.page.keyboard.press('Escape');
    await r.page.locator('#settings-open').click();
    await editSetting(r.page, 'prompt_theses');
    await r.page.locator('#setting-prompt_theses').fill('Черновик Око');
    await modelPage(r.page);
    await r.page.locator('#llamacpp-model').fill('Другая модель');
    await r.page.keyboard.press('Escape');
    await r.page.locator('#settings-open').click();
    await expect(r.page.locator('#setting-prompt_theses')).toHaveValue('Черновик Око');
    await expect(r.page.locator('#llamacpp-model')).toHaveValue('Другая модель');
    await r.page.keyboard.press('Escape');
    await r.page.locator('#feeds-open').click();
    await r.page.locator('#feed-tree').getByRole('button', {name:'two',exact:true}).click();
    await expect(r.page.locator('#setting-prompt_theses')).toHaveValue('Черновик второго');
    const writes = await r.page.evaluate(() => readerTransport.requests.filter(q => ['PATCH','PUT','POST'].includes(q.method) && !q.url.includes('/tabs/') && !/^\/api\/v3\/client-sessions(?:\/[^/?]+)?$/.test(q.url) && q.url !== '/api/v3/diagnostics'));
    expect(writes).toEqual([{url:'/api/v3/settings/feed/one',method:'PATCH',body:{prompt_theses:'Черновик первого'}}]);
    expect(r.errors).toEqual([]);
  } finally {await r.close();}
});

test('new Telegram source draft survives opening profile settings without a reconnection form', async () => {
 const r=await reader();try{
  await r.start([]);
  await r.page.locator('#add-open').click();await r.page.locator('#add-telegram-tab').click();
  await r.page.locator('#telegram-peer').fill('@newchannel');await r.page.keyboard.press('Escape');
  await r.page.locator('#settings-open').click();await r.page.locator('#settings-dialog [data-settings-target="telegram-profiles"]').click();
  await expect(r.page.locator('#reconnect-connection')).toHaveCount(0);
  await r.page.keyboard.press('Escape');await r.page.locator('#add-open').click();
  await expect(r.page.locator('#telegram-peer')).toHaveValue('@newchannel');
  expect(r.errors).toEqual([]);
 }finally{await r.close();}
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
      const result=readerTransport.replies.get('/api/v3/settings/feed/one')!.body;
      result!.effective.prompt_theses={value:'Промпт папки',scope:'folder',owner:'f'};result!.overrides={};
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

test('late failed save cannot replace messages after closing and reopening the same editor', async () => {
  const r = await reader();
  try {
    await r.start([]); await objects(r);
    await r.page.locator('#feeds-open').click();
    await r.page.locator('#feed-tree').getByRole('button', {name:'one',exact:true}).click();
    await editSetting(r.page, 'prompt_theses');
    await r.page.locator('#setting-prompt_theses').fill('Отправлено');
    await r.page.evaluate(() => readerTransport.held.set('/api/v3/settings/feed/one', true));
    await editSetting(r.page, 'prompt_theses');
    await r.page.locator('.setting-row').filter({has:r.page.locator('#setting-prompt_theses')}).getByRole('button',{name:'Save',exact:true}).click();
    await r.page.waitForFunction(() => readerTransport.pending.has('/api/v3/settings/feed/one'));
    await r.page.keyboard.press('Escape');
    await r.page.evaluate(() => readerTransport.held.delete('/api/v3/settings/feed/one'));
    await r.page.locator('#feeds-open').click();
    await editSetting(r.page, 'prompt_theses');
    await expect(r.page.locator('#setting-prompt_theses')).toBeVisible();
    await editSetting(r.page, 'prompt_theses');
    await r.page.locator('#setting-prompt_theses').fill('Новый черновик');
    await r.page.evaluate(() => readerTransport.release('/api/v3/settings/feed/one', {detail:'Старая ошибка'}, 500));
    await expect.poll(() => r.api('diagnostics').then(result => result.groups.some(g=>g.message==='Старая ошибка'))).toBe(true);
    await expect(r.page.locator('#settings-message')).not.toContainText('Старая ошибка');
    await expect(r.page.locator('#setting-prompt_theses')).toHaveValue('Новый черновик');
  } finally {await r.close();}
});

test('field save completes after leaving a model check; stale checks cannot report into another page or reopened menu', async () => {
  const r = await reader();
  try {
    await r.start([]); await objects(r);
    await r.page.locator('#settings-open').click();
    await modelPage(r.page);
    await r.page.evaluate(() => {
      readerTransport.held.set('/api/v3/processors/llamacpp/check',true);
      readerTransport.replies.set('PATCH /api/v3/settings/oko/global',{body:{}});
    });
    await r.page.locator('#llamacpp-check').click();
    await r.page.waitForFunction(() => readerTransport.pending.has('/api/v3/processors/llamacpp/check'));
    await editSetting(r.page, 'prompt_theses');
    await r.page.locator('#setting-prompt_theses').fill('Сохранить параллельно');
    await editSetting(r.page, 'prompt_theses');
    await r.page.locator('.setting-row').filter({has:r.page.locator('#setting-prompt_theses')}).getByRole('button',{name:'Save',exact:true}).click();
    await expect(r.page.locator('#settings-message')).toHaveText('Setting saved');
    await r.page.evaluate(() => readerTransport.release('/api/v3/processors/llamacpp/check',{mode:'Локальная',model:'working-model',models:['working-model']}));
    await expect(r.page.locator('#llamacpp-check-status')).not.toContainText('working-model');
    await modelPage(r.page);
    await r.page.evaluate(() => readerTransport.held.set('/api/v3/processors/llamacpp/check',true));
    await r.page.locator('#llamacpp-check').click();
    await r.page.waitForFunction(() => readerTransport.pending.has('/api/v3/processors/llamacpp/check'));
    await r.page.keyboard.press('Escape');
    await r.page.locator('#settings-open').click();
    await r.page.evaluate(() => readerTransport.release('/api/v3/processors/llamacpp/check',{detail:'Старый сбой модели'},500));
    await expect.poll(() => r.api('diagnostics').then(result => result.groups.some(g=>g.message==='Старый сбой модели'))).toBe(true);
    await expect(r.page.locator('#llamacpp-check-status')).not.toContainText('Старый сбой модели');
    expect(r.errors).toEqual([]);
  } finally {await r.close();}
});

test('RSS addition reconciles after closing and reopening but cannot navigate the new menu', async () => {
  const r = await reader();
  try {
    await r.start([]); await objects(r);
    await r.page.evaluate(() => {
      readerTransport.replies.set('/api/v3/feeds/verify',{body:{url:'https://source.test/new',title:'Новый фид',kind:'rss',verification:'verified'}});
      readerTransport.held.set('/api/v3/feeds', true);
    });
    await r.page.locator('#add-open').click();
    await r.page.locator('#rss-url').fill('https://source.test/new');
    await r.page.locator('#rss-check').click();
    await expect(r.page.locator('#rss-title')).toHaveValue('Новый фид');
    await r.page.locator('#rss-folder').selectOption({label:'Учёба'});
    await r.page.locator('#rss-add').click();
    await r.page.waitForFunction(() => readerTransport.pending.has('/api/v3/feeds'));
    await r.page.keyboard.press('Escape');
    await r.page.locator('#add-open').click();
    await r.page.evaluate(() => {
      const data = readerTransport.replies.get('/api/v3/subscriptions')!.body;
      const feed = {...data!.feeds[0],id:'new',title:'Новый фид'};
      data!.feeds.push(feed);
      readerTransport.release('/api/v3/feeds',feed);
    });
    await expect(r.page.locator('#rss-url')).toHaveValue('');
    await expect(r.page.locator('#add-dialog')).toBeVisible();
    await expect(r.page.locator('#rss-add')).toBeDisabled();
    await r.page.keyboard.press('Escape');
    await r.page.locator('#feeds-open').click();
    await expect(r.page.locator('#feed-tree').getByRole('button', {name:'Новый фид',exact:true})).toBeVisible();
    expect(r.errors).toEqual([]);
  } finally {await r.close();}
});

test('late profile catalog cannot overwrite a newer profile choice', async () => {
 const r=await reader();try{
  await r.start([]);await r.page.locator('#settings-open').click();
  await r.page.locator('#settings-dialog [data-settings-target="telegram-profiles"]').click();
  await expect(r.page.locator('#telegram-profile-personal option')).toHaveCount(3);
  await r.page.locator('#telegram-profile-personal').selectOption('two');
  await r.page.locator('#settings-dialog .settings-back').click();
  await r.page.locator('#settings-dialog [data-settings-target="telegram-profiles"]').click();
  await expect(r.page.locator('#telegram-profile-personal')).toHaveValue('two');
  expect(r.errors).toEqual([]);
 }finally{await r.close();}
});

test('a saved feed updates reading even when navigation cancels its catalog refresh', async () => {
  const r = await reader();
  try {
    await r.start([material('before')]); await objects(r);
    await r.page.locator('#feeds-open').click();
    await r.page.locator('#feed-tree').getByRole('button', {name:'one',exact:true}).click();
    const subscriptions = await r.page.evaluate(() => readerTransport.replies.get('/api/v3/subscriptions')!.body);
    const domainStatus=await r.api('v3/status');
    let catalogRead = 0, statusRead = 0;
    let heldCatalog: Route | undefined, heldStatus: Route | undefined;
    await r.page.route('**/api/v3/subscriptions', async route => {
      if (++catalogRead === 1) heldCatalog = route;
      else await route.fulfill({json:subscriptions});
    });
    await r.page.route('**/api/v3/status', async route => {
      if (++statusRead === 1) heldStatus = route;
      else await route.fulfill({json:domainStatus});
    });
    await r.page.evaluate(() => {
      readerTransport.native.add('/api/v3/status');
      readerTransport.native.add('/api/v3/subscriptions');
      readerTransport.replies.set('PATCH /api/v3/feeds/one',{body:{}});
    });
    // An old background read is still pending when the server write completes.
    await expect.poll(() => Boolean(heldStatus)).toBe(true);
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

test('a completed RSS addition preserves changes typed after submission', async () => {
  const r = await reader();
  try {
    await r.start([]); await objects(r);
    await r.page.evaluate(() => {
      readerTransport.replies.set('/api/v3/feeds/verify',{body:{url:'https://source.test/new',title:'Sent A',kind:'rss',verification:'verified'}});
      readerTransport.held.set('/api/v3/feeds',true);
    });
    await r.page.locator('#add-open').click();
    await r.page.locator('#rss-url').fill('https://source.test/new');
    await r.page.locator('#rss-check').click();
    await expect(r.page.locator('#rss-title')).toHaveValue('Sent A');
    await r.page.locator('#rss-folder').selectOption({label:'Учёба'});
    await r.page.locator('#rss-add').click();
    await r.page.waitForFunction(() => readerTransport.pending.has('/api/v3/feeds'));
    await r.page.locator('#rss-title').fill('Draft B');
    await r.page.locator('#rss-history-count').fill('13');
    await r.page.keyboard.press('Escape');
    await r.page.locator('#add-open').click();
    const refreshes=await r.page.evaluate(() => readerTransport.requests.filter(request => request.url==='/api/v3/subscriptions').length);
    await r.page.evaluate(() => {
      const catalog=readerTransport.replies.get('/api/v3/subscriptions')!.body;
      const feed={...catalog!.feeds[0],id:'new',title:'Sent A'};
      catalog!.feeds.push(feed);readerTransport.release('/api/v3/feeds',feed);
    });
    await r.page.waitForFunction(previous => readerTransport.requests.filter(request => request.url==='/api/v3/subscriptions').length>previous,refreshes);
    await expect(r.page.locator('#rss-title')).toHaveValue('Draft B');
    await expect(r.page.locator('#rss-history-count')).toHaveValue('13');
    await expect(r.page.locator('#add-dialog')).toBeVisible();
    for(const [width,height,name]of ([[1280,900,'react-rss-desktop.png']] as const)){
      await r.page.setViewportSize({width,height});
      expect(await r.page.locator('#add-dialog').evaluate(dialog=>dialog.scrollWidth<=dialog.clientWidth)).toBe(true);
      const screenshot=artifactPath(name);await r.page.screenshot({path:screenshot});console.log('RSS screenshot:',screenshot);
    }
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
