const {test} = require('node:test');
const {objects} = require('./settings-fixtures.cjs');
const {reader, material, expect} = require('./support.cjs');
const {editSetting, settingsHome, modelPage, feedOptions} = require('./settings-actions.cjs');

test('three menus keep reading position and addition tabs, Escape restores opening focus', async () => {
  const r = await reader();
  try {
    await r.start([material('one', {text: 'Читать дальше. '.repeat(3000)})]);
    await r.page.evaluate(() => {
      readerTransport.replies.set('/api/subscriptions', {body: {folders: [], feeds: []}});
      readerTransport.replies.set('/api/processors/llamacpp', {body: null});
      readerTransport.replies.set('/api/telegram/accounts', {body: ['personal']});
    });
    await r.page.locator('#cards button').click();
    await expect(r.page.locator('#title')).toHaveText('one');
    await r.page.locator('main > article').evaluate(n => n.scrollTop = 450);
    await r.page.locator('#add-open').click();
    await expect(r.page.getByRole('dialog', {name: 'Добавить фид', exact: true})).toBeVisible();
    await r.page.locator('#rss-url').fill('https://example.org/draft');
    await r.page.getByRole('tab', {name: 'Telegram', exact: true}).click();
    await expect(r.page.locator('#rss-url')).toBeHidden();
    await r.page.locator('#telegram-peer').fill('@draft');
    await r.page.keyboard.press('Escape');
    await expect(r.page.locator('#add-open')).toBeFocused();
    await r.page.locator('#feeds-open').click();
    await expect(r.page.getByRole('dialog', {name: 'Фиды и папки', exact: true})).toBeVisible();
    await r.page.locator('#feeds-close').click();
    await r.page.locator('#add-open').click();
    await expect(r.page.locator('dialog[open]')).toHaveCount(1);
    await expect(r.page.locator('#telegram-peer')).toHaveValue('@draft');
    await r.page.getByRole('tab', {name: 'RSS / YouTube', exact: true}).click();
    await expect(r.page.locator('#rss-url')).toHaveValue('https://example.org/draft');
    await r.page.keyboard.press('Escape');
    await expect(r.page.locator('#add-open')).toBeFocused();
    await r.page.locator('#settings-open').click();
    await expect(r.page.getByRole('dialog', {name: 'Настройки Око', exact: true})).toBeVisible();
    await expect(r.page.getByRole('heading', {name:'Настройки',exact:true})).toBeVisible();
    await expect(r.page.getByRole('button',{name:/^Модели/})).toBeVisible();
    await r.page.keyboard.press('Escape');
    await expect(r.page.locator('#settings-open')).toBeFocused();
    await expect(r.page.locator('#title')).toHaveText('one');
    expect(await r.page.locator('main > article').evaluate(n => n.scrollTop)).toBe(450);
    expect(r.errors).toEqual([]);
  } finally { await r.close(); }
});


test('completed OPML import does not reopen a menu closed while import was pending', async () => {
  const r = await reader();
  try {
    await r.start([material('one', {text: 'Продолжить чтение. '.repeat(3000)})]);
    await objects(r);
    await r.page.locator('#cards button').click();
    await expect(r.page.locator('#title')).toHaveText('one');
    await r.page.locator('main > article').evaluate(n => n.scrollTop = 450);
    await r.page.locator('#add-open').click();
    await r.page.getByRole('tab', {name:'Импорт OPML',exact:true}).click();
    await r.page.evaluate(() => {
      readerTransport.replies.set('/api/import/preview',{body:{folders:[],feeds:[]}});
      readerTransport.held.set('/api/import',true);
    });
    await r.page.locator('#opml-file').setInputFiles({name:'example.opml',mimeType:'text/xml',buffer:Buffer.from('<opml><body/></opml>')});
    await r.page.getByRole('button',{name:'Импортировать',exact:true}).click();
    await r.page.waitForFunction(() => readerTransport.pending.has('/api/import'));
    await r.page.keyboard.press('Escape');
    await r.page.evaluate(() => readerTransport.release('/api/import',{}));
    await expect(r.page.locator('#import-plan')).toContainText('Подписки импортированы');
    await expect(r.page.locator('dialog[open]')).toHaveCount(0);
    await expect(r.page.locator('#add-open')).toBeFocused();
    await expect(r.page.locator('#title')).toHaveText('one');
    expect(await r.page.locator('main > article').evaluate(n => n.scrollTop)).toBe(450);
    await r.page.locator('#add-open').click();
    await expect(r.page.locator('#import-confirm')).toBeHidden();
    expect(await r.page.locator('#opml-file').evaluate(n => n.files.length)).toBe(0);
    expect(r.errors).toEqual([]);
  } finally {await r.close();}
});

test('completed OPML import preserves a newer settings menu, drafts and selected feed while updating subscriptions', async () => {
  const r = await reader();
  try {
    await r.start([]); await objects(r);
    await r.page.locator('#feeds-open').click();
    await r.page.locator('#feed-tree').getByRole('button', {name:'one',exact:true}).click();
    await editSetting(r.page, 'prompt_theses');
    await r.page.locator('#setting-prompt_theses').fill('Черновик фида');
    await r.page.locator('#feeds-close').click();
    await r.page.locator('#add-open').click();
    await r.page.getByRole('tab',{name:'Импорт OPML',exact:true}).click();
    await r.page.evaluate(() => {
      readerTransport.replies.set('/api/import/preview',{body:{folders:[],feeds:[]}});
      readerTransport.held.set('/api/import',true);
    });
    await r.page.locator('#opml-file').setInputFiles({name:'example.opml',mimeType:'text/xml',buffer:Buffer.from('<opml><body/></opml>')});
    await r.page.getByRole('button',{name:'Импортировать',exact:true}).click();
    await r.page.waitForFunction(() => readerTransport.pending.has('/api/import'));
    await r.page.keyboard.press('Escape');
    await r.page.locator('#settings-open').click();
    await editSetting(r.page, 'prompt_theses');
    await r.page.locator('#setting-prompt_theses').fill('Черновик Око');
    await r.page.evaluate(() => {
      const subscriptions = readerTransport.replies.get('/api/subscriptions').body;
      subscriptions.feeds.push({...subscriptions.feeds[0],id:'imported',title:'Импортированный'});
      readerTransport.release('/api/import',{});
    });
    await expect(r.page.locator('#import-plan')).toContainText('Подписки импортированы');
    await expect(r.page.locator('#settings-dialog')).toBeVisible();
    await expect(r.page.locator('dialog[open]')).toHaveCount(1);
    await expect(r.page.locator('#setting-prompt_theses')).toHaveValue('Черновик Око');
    await expect(r.page.locator('#setting-prompt_theses')).toBeFocused();
    await r.page.keyboard.press('Escape');
    await r.page.locator('#feeds-open').click();
    await expect(r.page.locator('#owner-heading')).toHaveText('Фид: one');
    await expect(r.page.locator('#setting-prompt_theses')).toHaveValue('Черновик фида');
    await expect(r.page.locator('#feed-tree').getByRole('button', {name:'Импортированный',exact:true})).toBeVisible();
    expect(r.errors).toEqual([]);
  } finally {await r.close();}
});

test('object and global drafts are independent, field save preserves other unsaved changes and close never saves', async () => {
  const r = await reader();
  try {
    await r.start([]); await objects(r);
    await r.page.locator('#feeds-open').click();
    await r.page.locator('#feed-tree').getByRole('button', {name:'one',exact:true}).click();
    await editSetting(r.page, 'prompt_theses');
    await r.page.locator('#setting-prompt_theses').fill('Черновик первого');
    await editSetting(r.page, 'prompt_digest');
    await r.page.locator('#setting-prompt_digest').fill('Обзор первого');
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
      t.replies.set('PATCH /api/settings/feed/one', {body:{}});
      t.replies.get('/api/settings/feed/one').body.effective.prompt_theses.value = 'Черновик первого';
    });
    await editSetting(r.page, 'prompt_theses');
    await r.page.locator('.setting-row').filter({has:r.page.locator('#setting-prompt_theses')}).getByRole('button',{name:'Сохранить',exact:true}).click();
    await expect(r.page.locator('#settings-message')).toHaveText('Настройка сохранена');
    await expect(r.page.locator('#setting-prompt_digest')).toHaveValue('Обзор первого');
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
    const writes = await r.page.evaluate(() => readerTransport.requests.filter(q => ['PATCH','PUT','POST'].includes(q.method) && !q.url.includes('/tabs/') && q.url !== '/api/debug'));
    expect(writes).toEqual([{url:'/api/settings/feed/one',method:'PATCH',body:{prompt_theses:'Черновик первого'}}]);
    expect(r.errors).toEqual([]);
  } finally {await r.close();}
});

test('reconnection drafts belong to each existing Telegram feed without changing the new-source form', async () => {
  const r = await reader();
  try {
    await r.start([]);
    await r.page.evaluate(() => {
      const t = readerTransport;
      const feeds = ['one','two'].map(id=>({id,title:id,kind:'telegram',identity:'telegram:'+id,folder_id:'f',original_urls:[],importance:'normal',auto_read:false,read_history:false,telegram_peer:'@'+id+'channel',telegram_account:'personal',transport:'telegram'}));
      t.replies.set('/api/subscriptions',{body:{folders:[{id:'f',title:'Telegram'}],feeds}});
      t.replies.set('/api/telegram/accounts',{body:['personal']});
      for (const feed of feeds) t.replies.set('/api/settings/feed/'+feed.id,t.replies.get('/api/settings/oko/global'));
    });
    await r.page.locator('#add-open').click();
    await r.page.getByRole('tab',{name:'Telegram',exact:true}).click();
    await r.page.locator('#telegram-peer').fill('@newchannel');
    await r.page.keyboard.press('Escape');
    await r.page.locator('#feeds-open').click();
    await r.page.locator('#feed-tree').getByRole('button', {name:'one',exact:true}).click();
    await feedOptions(r.page);
    await r.page.getByRole('button',{name:'Настроить подключение Telegram-фида',exact:true}).click();
    await r.page.locator('#reconnect-peer').fill('@draftone');
    await expect(r.page.locator('#reconnect-history')).toBeHidden();
    await r.page.locator('#feed-tree').getByRole('button', {name:'two',exact:true}).click();
    await feedOptions(r.page);
    await r.page.getByRole('button',{name:'Настроить подключение Telegram-фида',exact:true}).click();
    await r.page.locator('#reconnect-peer').fill('@drafttwo');
    await r.page.locator('#feed-tree').getByRole('button', {name:'one',exact:true}).click();
    await feedOptions(r.page);
    await r.page.getByRole('button',{name:'Настроить подключение Telegram-фида',exact:true}).click();
    await expect(r.page.locator('#reconnect-peer')).toHaveValue('@draftone');
    await r.page.keyboard.press('Escape');
    await r.page.locator('#add-open').click();
    await expect(r.page.locator('#telegram-peer')).toHaveValue('@newchannel');
    expect(r.errors).toEqual([]);
  } finally {await r.close();}
});

test('late subscription and settings responses cannot replace newer object selection; pending save retains later edits', async () => {
  const r = await reader();
  try {
    await r.start([]); await objects(r);
    await r.page.locator('#feeds-open').click();
    await r.page.locator('#feed-tree').getByRole('button', {name:'one',exact:true}).click();
    await expect(r.page.locator('#owner-heading')).toHaveText('Фид: one');
    await r.page.evaluate(() => {readerTransport.held.set('/api/settings/feed/one',true); readerTransport.held.set('/api/subscriptions',true);});
    await r.page.keyboard.press('Escape');
    await r.page.locator('#feeds-open').click();
    await r.page.waitForFunction(() => readerTransport.pending.has('/api/subscriptions'));
    await r.page.locator('#feed-tree').getByRole('button', {name:'two',exact:true}).click();
    await expect(r.page.locator('#owner-heading')).toHaveText('Фид: two');
    await r.page.evaluate(() => readerTransport.release('/api/subscriptions',readerTransport.replies.get('/api/subscriptions').body));
    await expect(r.page.locator('#owner-heading')).toHaveText('Фид: two');
    await editSetting(r.page, 'prompt_theses');
    await r.page.locator('#setting-prompt_theses').fill('Отправленный');
    await r.page.evaluate(() => readerTransport.held.set('/api/settings/feed/two',true));
    await editSetting(r.page, 'prompt_theses');
    await r.page.locator('.setting-row').filter({has:r.page.locator('#setting-prompt_theses')}).getByRole('button',{name:'Сохранить',exact:true}).click();
    await r.page.waitForFunction(() => readerTransport.pending.has('/api/settings/feed/two'));
    await editSetting(r.page, 'prompt_theses');
    await r.page.locator('#setting-prompt_theses').fill('Более новый черновик');
    await r.page.evaluate(() => {
      const result = structuredClone(readerTransport.replies.get('/api/settings/oko/global').body);
      result.effective.prompt_theses.value = 'Отправленный';
      readerTransport.replies.set('/api/settings/feed/two',{body:result});
      readerTransport.release('/api/settings/feed/two',{});
    });
    await expect(r.page.locator('#settings-message')).toHaveText('Настройка сохранена');
    await expect(r.page.locator('#setting-prompt_theses')).toHaveValue('Более новый черновик');
    await r.page.locator('#feed-tree').getByRole('button', {name:'one',exact:true}).click();
    await r.page.waitForFunction(() => readerTransport.pending.has('/api/settings/feed/one'));
    await r.page.locator('#feed-tree').getByRole('button', {name:'two',exact:true}).click();
    await r.page.evaluate(() => readerTransport.release('/api/settings/feed/one',readerTransport.replies.get('/api/settings/oko/global').body));
    await expect(r.page.locator('#owner-heading')).toHaveText('Фид: two');
    await expect(r.page.locator('#setting-prompt_theses')).toHaveValue('Более новый черновик');
    expect(r.errors).toEqual([]);
  } finally {await r.close();}
});

test('narrow feed menu navigates list and detail, OPML returns to the list without changing reading', async () => {
  const r = await reader();
  try {
    await r.page.setViewportSize({width:390,height:844});
    await r.start([]); await objects(r);
    await r.page.locator('#feeds-open').click();
    await expect(r.page.locator('#feed-list')).toBeVisible();
    await r.page.locator('#feed-tree').getByRole('button', {name:'one',exact:true}).click();
    await expect(r.page.locator('#feed-list')).toBeHidden();
    await editSetting(r.page, 'prompt_theses');
    await expect(r.page.locator('#setting-prompt_theses')).toBeVisible();
    await r.page.locator('#feeds-back').click();
    await expect(r.page.locator('#feed-list')).toBeVisible();
    await expect(r.page.locator('#feed-detail')).toBeHidden();
    await r.page.locator('#feeds-close').click();
    await r.page.locator('#add-open').click();
    await r.page.getByRole('tab',{name:'Импорт OPML',exact:true}).click();
    await r.page.evaluate(() => {
      readerTransport.replies.set('/api/import/preview',{body:{folders:[{title:'Учёба'}],feeds:[]}});
      readerTransport.replies.set('/api/import',{body:{}});
    });
    await r.page.locator('#opml-file').setInputFiles({name:'example.opml',mimeType:'text/xml',buffer:Buffer.from('<opml><body/></opml>')});
    await r.page.getByRole('button',{name:'Импортировать',exact:true}).click();
    await expect(r.page.locator('#feeds-dialog')).toBeVisible();
    await expect(r.page.locator('#feed-list')).toBeVisible();
    await expect(r.page.locator('#feed-detail')).toBeHidden();
    expect(r.errors).toEqual([]);
  } finally {await r.close();}
});

test('feed inherited fields show their owner and resetting only one override preserves other drafts', async () => {
  const r = await reader();
  try {
    await r.start([]); await objects(r);
    await r.page.evaluate(() => {
      const result = structuredClone(readerTransport.replies.get('/api/settings/oko/global').body);
      result.effective.prompt_theses = {value:'Свой промпт',scope:'feed',owner:'one'};
      result.effective.prompt_digest = {value:'Обзор папки',scope:'folder',owner:'f'};
      result.overrides = {prompt_theses:'Свой промпт'};
      readerTransport.replies.set('/api/settings/feed/one',{body:result});
      readerTransport.replies.set('PATCH /api/settings/feed/one',{body:{}});
    });
    await r.page.locator('#feeds-open').click();
    await r.page.locator('#feed-tree').getByRole('button', {name:'one',exact:true}).click();
    const theses = r.page.locator('.setting-row').filter({has:r.page.locator('#setting-prompt_theses')});
    const digest = r.page.locator('.setting-row').filter({has:r.page.locator('#setting-prompt_digest')});
    await expect(theses).toContainText('Фид: one');
    await expect(digest).toContainText('Папка: Учёба');
    await editSetting(r.page, 'prompt_digest');
    await r.page.locator('#setting-prompt_digest').fill('Несохранённый обзор');
    await r.page.evaluate(() => {
      const result=readerTransport.replies.get('/api/settings/feed/one').body;
      result.effective.prompt_theses={value:'Промпт папки',scope:'folder',owner:'f'};result.overrides={};
    });
    await editSetting(r.page, 'prompt_theses');
    await theses.getByRole('button',{name:'Сбросить',exact:true}).click();
    await expect(r.page.locator('#setting-prompt_theses')).toHaveValue('Промпт папки');
    await expect(theses).toContainText('Папка: Учёба');
    await expect(theses.getByRole('button',{name:'Сбросить',exact:true})).toBeDisabled();
    await expect(r.page.locator('#setting-prompt_digest')).toHaveValue('Несохранённый обзор');
    const patch=await r.page.evaluate(()=>readerTransport.requests.find(q=>q.method==='PATCH').body);
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
    await r.page.evaluate(() => readerTransport.held.set('/api/settings/feed/one', true));
    await editSetting(r.page, 'prompt_theses');
    await r.page.locator('.setting-row').filter({has:r.page.locator('#setting-prompt_theses')}).getByRole('button',{name:'Сохранить',exact:true}).click();
    await r.page.waitForFunction(() => readerTransport.pending.has('/api/settings/feed/one'));
    await r.page.keyboard.press('Escape');
    await r.page.evaluate(() => readerTransport.held.delete('/api/settings/feed/one'));
    await r.page.locator('#feeds-open').click();
    await editSetting(r.page, 'prompt_theses');
    await expect(r.page.locator('#setting-prompt_theses')).toBeVisible();
    await editSetting(r.page, 'prompt_theses');
    await r.page.locator('#setting-prompt_theses').fill('Новый черновик');
    await r.page.evaluate(() => readerTransport.release('/api/settings/feed/one', {detail:'Старая ошибка'}, 500));
    await expect.poll(() => r.api('debug').then(result => result.groups.some(g=>g.message==='Старая ошибка'))).toBe(true);
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
      readerTransport.held.set('/api/processors/llamacpp/check',true);
      readerTransport.replies.set('PATCH /api/settings/oko/global',{body:{}});
    });
    await r.page.locator('#llamacpp-check').click();
    await r.page.waitForFunction(() => readerTransport.pending.has('/api/processors/llamacpp/check'));
    await editSetting(r.page, 'prompt_theses');
    await r.page.locator('#setting-prompt_theses').fill('Сохранить параллельно');
    await editSetting(r.page, 'prompt_theses');
    await r.page.locator('.setting-row').filter({has:r.page.locator('#setting-prompt_theses')}).getByRole('button',{name:'Сохранить',exact:true}).click();
    await expect(r.page.locator('#settings-message')).toHaveText('Настройка сохранена');
    await r.page.evaluate(() => readerTransport.release('/api/processors/llamacpp/check',{mode:'Локальная',model:'working-model',models:['working-model']}));
    await expect(r.page.locator('#llamacpp-check-status')).not.toContainText('working-model');
    await modelPage(r.page);
    await r.page.evaluate(() => readerTransport.held.set('/api/processors/llamacpp/check',true));
    await r.page.locator('#llamacpp-check').click();
    await r.page.waitForFunction(() => readerTransport.pending.has('/api/processors/llamacpp/check'));
    await r.page.keyboard.press('Escape');
    await r.page.locator('#settings-open').click();
    await r.page.evaluate(() => readerTransport.release('/api/processors/llamacpp/check',{detail:'Старый сбой модели'},500));
    await expect.poll(() => r.api('debug').then(result => result.groups.some(g=>g.message==='Старый сбой модели'))).toBe(true);
    await expect(r.page.locator('#llamacpp-check-status')).not.toContainText('Старый сбой модели');
    expect(r.errors).toEqual([]);
  } finally {await r.close();}
});

test('RSS addition reconciles after closing and reopening but cannot navigate the new menu', async () => {
  const r = await reader();
  try {
    await r.start([]); await objects(r);
    await r.page.evaluate(() => {
      readerTransport.replies.set('/api/feeds/verify',{body:{url:'https://source.test/new',title:'Новый фид',kind:'rss',verification:'verified'}});
      readerTransport.held.set('/api/feeds', true);
    });
    await r.page.locator('#add-open').click();
    await r.page.locator('#rss-url').fill('https://source.test/new');
    await r.page.locator('#rss-check').click();
    await expect(r.page.locator('#rss-title')).toHaveValue('Новый фид');
    await r.page.locator('#rss-add').click();
    await r.page.waitForFunction(() => readerTransport.pending.has('/api/feeds'));
    await r.page.keyboard.press('Escape');
    await r.page.locator('#add-open').click();
    await r.page.evaluate(() => {
      const data = readerTransport.replies.get('/api/subscriptions').body;
      const feed = {...data.feeds[0],id:'new',title:'Новый фид'};
      data.feeds.push(feed);
      readerTransport.release('/api/feeds',feed);
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

test('pending Telegram accounts configure the newly selected feed', async () => {
  const r = await reader();
  try {
    await r.start([]); await objects(r);
    await r.page.evaluate(() => {
      for (const feed of readerTransport.replies.get('/api/subscriptions').body.feeds) {
        Object.assign(feed, {kind:'telegram', identity:'telegram:' + feed.id + 'channel', telegram_account:'personal', transport:'telegram', auto_read:false});
      }
      readerTransport.held.set('/api/telegram/accounts',true);
    });
    await r.page.locator('#feeds-open').click();
    await r.page.locator('#feed-tree').getByRole('button', {name:'one',exact:true}).click();
    await feedOptions(r.page);
    await r.page.getByRole('button',{name:'Настроить подключение Telegram-фида',exact:true}).click();
    await r.page.waitForFunction(() => readerTransport.pending.has('/api/telegram/accounts'));
    await r.page.locator('#feed-tree').getByRole('button', {name:'two',exact:true}).click();
    await feedOptions(r.page);
    await r.page.getByRole('button',{name:'Настроить подключение Telegram-фида',exact:true}).click();
    await r.page.evaluate(() => readerTransport.release('/api/telegram/accounts',['personal']));
    await expect(r.page.locator('#reconnect-peer')).toHaveValue('@twochannel');
    await expect(r.page.locator('#reconnect-account')).toHaveValue('personal');
    await expect(r.page.locator('#reconnect-add')).toBeEnabled();
    expect(r.errors).toEqual([]);
  } finally {await r.close();}
});

test('a saved feed updates reading even when navigation cancels its catalog refresh', async () => {
  const r = await reader();
  try {
    await r.start([material('before')]); await objects(r);
    await r.page.locator('#feeds-open').click();
    await r.page.locator('#feed-tree').getByRole('button', {name:'one',exact:true}).click();
    const subscriptions = await r.page.evaluate(() => readerTransport.replies.get('/api/subscriptions').body);
    let catalogRead = 0, statusRead = 0;
    let heldCatalog, heldStatus;
    await r.page.route('**/api/subscriptions', async route => {
      if (++catalogRead === 1) heldCatalog = route;
      else await route.fulfill({json:subscriptions});
    });
    await r.page.route('**/api/status', async route => {
      if (++statusRead === 1) heldStatus = route;
      else await route.fulfill({json:{stage:'Готово',errors:[],sources:[],ready_sources:1,total_sources:1}});
    });
    await r.page.evaluate(() => {
      readerTransport.native.add('/api/status');
      readerTransport.native.add('/api/subscriptions');
      readerTransport.replies.set('PATCH /api/feeds/one',{body:{}});
    });
    // An old background read is still pending when the server write completes.
    await expect.poll(() => Boolean(heldStatus)).toBe(true);
    await r.page.evaluate(() => {readerTransport.replies.get('/api/cards').body = [{id:'after',title:'После сохранения',published:100,folder_id:'f',feed_id:'one',url:'https://example.org/after',complete:true,published_known:true,feed_title:'one',importance:'high',snippet:''}];});
    await feedOptions(r.page);
    await r.page.locator('#feed-importance').selectOption('high');
    await r.page.getByRole('button',{name:'Сохранить фид',exact:true}).click();
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
      readerTransport.replies.set('/api/feeds/verify',{body:{url:'https://source.test/new',title:'Sent A',kind:'rss',verification:'verified'}});
      readerTransport.held.set('/api/feeds',true);
    });
    await r.page.locator('#add-open').click();
    await r.page.locator('#rss-url').fill('https://source.test/new');
    await r.page.locator('#rss-check').click();
    await expect(r.page.locator('#rss-title')).toHaveValue('Sent A');
    await r.page.locator('#rss-add').click();
    await r.page.waitForFunction(() => readerTransport.pending.has('/api/feeds'));
    await r.page.locator('#rss-title').fill('Draft B');
    await r.page.locator('#rss-history-count').fill('13');
    await r.page.keyboard.press('Escape');
    await r.page.locator('#add-open').click();
    const refreshes=await r.page.evaluate(() => readerTransport.requests.filter(request => request.url==='/api/subscriptions').length);
    await r.page.evaluate(() => {
      const catalog=readerTransport.replies.get('/api/subscriptions').body;
      const feed={...catalog.feeds[0],id:'new',title:'Sent A'};
      catalog.feeds.push(feed);readerTransport.release('/api/feeds',feed);
    });
    await r.page.waitForFunction(previous => readerTransport.requests.filter(request => request.url==='/api/subscriptions').length>previous,refreshes);
    await expect(r.page.locator('#rss-title')).toHaveValue('Draft B');
    await expect(r.page.locator('#rss-history-count')).toHaveValue('13');
    await expect(r.page.locator('#add-dialog')).toBeVisible();
    expect(r.errors).toEqual([]);
  } finally {await r.close();}
});
