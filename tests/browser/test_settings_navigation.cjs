const {test} = require('node:test');
const {reader, expect} = require('./support.cjs');

test('compact settings navigate to reading and persist only explicitly through HTTP', async () => {
  const r = await reader();
  try {
    await r.page.setViewportSize({width:390,height:844});
    await r.start([]);
    await r.page.evaluate(() => {
      readerTransport.replies.set('/api/subscriptions',{body:{folders:[],feeds:[]}});
      readerTransport.native.add('/api/settings/oko/global');
    });
    await r.page.locator('#settings-open').click();
    await expect(r.page.getByRole('button',{name:'Чтение',exact:true})).toBeVisible();
    await expect(r.page.locator('#setting-originals')).toBeHidden();
    await r.page.getByRole('button',{name:'Чтение',exact:true}).click();
    await r.page.getByRole('button',{name:/Показывать исходную публикацию/}).click();
    await r.page.getByRole('switch',{name:'Показывать исходную публикацию'}).uncheck();
    expect((await r.api('settings/oko/global')).effective.originals.value).toBe(true);
    await r.page.getByRole('button',{name:'Сохранить',exact:true}).click();
    await expect(r.page.locator('#settings-message')).toHaveText('Настройка сохранена');
    expect((await r.api('settings/oko/global')).effective.originals.value).toBe(false);
    await r.page.keyboard.press('Escape');
    await r.page.locator('#settings-open').click();
    await r.page.getByRole('button',{name:'Чтение',exact:true}).click();
    await expect(r.page.getByRole('button',{name:/Показывать исходную публикацию/})).toContainText('Выключено');
    expect(await r.page.locator('#settings-dialog').evaluate(n=>n.scrollWidth<=n.clientWidth)).toBe(true);
    expect(r.errors).toEqual([]);
  } finally {await r.close();}
});

const {editSetting, settingsHome} = require('./settings-actions.cjs');

test('events are folder scoped; individual resets preserve other overrides and new drafts', async () => {
  const r = await reader();
  try {
    const plan = await r.api('import','POST',{opml:'<opml><body><outline text="Наука"><outline text="Источник" xmlUrl="https://source.test/feed"/></outline></body></opml>'});
    const folder = plan.folders[0], feed = plan.feeds[0];
    await r.api('settings/oko/global','PATCH',{event_hours:48});
    await r.api('settings/folder/'+folder.id,'PATCH',{event_hours:72,events:false});
    await r.start([]);
    await r.page.evaluate(({folder,feed})=>{
      readerTransport.native.add('/api/subscriptions');
      for (const path of ['oko/global','folder/'+folder.id,'feed/'+feed.id]) readerTransport.native.add('/api/settings/'+path);
    },{folder,feed});
    await r.page.locator('#feeds-open').click();
    await r.page.locator('#feed-tree').getByRole('button',{name:'Наука',exact:true}).click();
    await editSetting(r.page,'event_hours');
    await expect(r.page.locator('#setting-event_hours')).toHaveValue('72');
    await r.page.getByRole('button',{name:'Сбросить',exact:true}).click();
    await expect(r.page.locator('#setting-event_hours')).toHaveValue('48');
    const result = await r.api('settings/folder/'+folder.id);
    expect(result.overrides.events).toBe(false);
    expect(result.overrides.event_hours).toBeUndefined();
    await r.page.locator('#setting-event_hours').fill('96');
    await r.page.locator('#feed-tree').getByRole('button',{name:'Источник',exact:true}).click();
    await expect(r.page.getByRole('button',{name:'Объединение событий',exact:true})).toHaveCount(0);
    await expect(r.page.locator('#feed-folder')).toBeHidden();
    await r.page.getByRole('button',{name:'Подключение и параметры',exact:true}).click();
    await expect(r.page.locator('#feed-folder')).toBeVisible();
    await r.page.locator('#feed-tree').getByRole('button',{name:'Наука',exact:true}).click();
    await editSetting(r.page,'event_hours');
    await expect(r.page.locator('#setting-event_hours')).toHaveValue('96');
    expect((await r.api('settings/folder/'+folder.id)).effective.event_hours.value).toBe(48);
    expect(r.errors).toEqual([]);
  } finally {await r.close();}
});

test('prompts, empty filtering and local schedule save via HTTP without saving adjacent drafts', async () => {
  const r = await reader();
  try {
    await r.start([]);
    await r.page.evaluate(()=>{
      readerTransport.replies.set('/api/subscriptions',{body:{folders:[],feeds:[]}});
      readerTransport.native.add('/api/settings/oko/global');
    });
    await r.api('settings/oko/global','PATCH',{prompt_filters:'Оставлять важное'});
    await r.page.locator('#settings-open').click();
    await editSetting(r.page,'prompt_theses');
    await r.page.locator('#setting-prompt_theses').fill('Мой черновик');
    await editSetting(r.page,'prompt_filters');
    await r.page.locator('#setting-prompt_filters').fill('');
    await r.page.getByRole('button',{name:'Сохранить',exact:true}).click();
    await expect(r.page.locator('#settings-message')).toHaveText('Настройка сохранена');
    expect((await r.api('settings/oko/global')).effective.prompt_filters.value).toBe('');
    await editSetting(r.page,'schedule');
    await r.page.getByRole('switch',{name:'Каждый день'}).check();
    await r.page.locator('#setting-schedule').fill('21:30');
    await r.page.getByRole('button',{name:'Сохранить',exact:true}).click();
    await expect(r.page.locator('#settings-message')).toHaveText('Настройка сохранена');
    expect((await r.api('settings/oko/global')).effective.schedule.value).toBe('21:30');
    await r.page.getByRole('switch',{name:'Каждый день'}).uncheck();
    await r.page.getByRole('button',{name:'Сохранить',exact:true}).click();
    await expect(r.page.locator('#settings-message')).toHaveText('Настройка сохранена');
    expect((await r.api('settings/oko/global')).effective.schedule.value).toBe('off');
    await editSetting(r.page,'prompt_theses');
    await expect(r.page.locator('#setting-prompt_theses')).toHaveValue('Мой черновик');
    expect((await r.api('settings/oko/global')).effective.prompt_theses.value).not.toBe('Мой черновик');
    expect(r.errors).toEqual([]);
  } finally {await r.close();}
});

test('a completed field write reconciles after Back without discarding a newer draft', async () => {
  const r = await reader(); let held;
  try {
    await r.start([]);
    await r.page.evaluate(()=>{readerTransport.replies.set('/api/subscriptions',{body:{folders:[],feeds:[]}});readerTransport.native.add('/api/settings/oko/global');});
    await r.page.route('**/api/settings/oko/global',async route=>{
      if (route.request().method()==='PATCH') held=route; else await route.continue();
    });
    await r.page.locator('#settings-open').click();await editSetting(r.page,'originals');
    await r.page.locator('#setting-originals').uncheck();
    await r.page.getByRole('button',{name:'Сохранить',exact:true}).click();
    await expect.poll(()=>Boolean(held)).toBe(true);
    await r.page.locator('#setting-originals').check();
    await r.page.locator('#settings-dialog .settings-back').click();
    await held.continue(); held=null;
    await expect(r.page.getByRole('button',{name:'Показывать исходную публикацию',exact:true})).toContainText('Выключено');
    await expect(r.page.getByRole('button',{name:'Показывать исходную публикацию',exact:true})).toBeFocused();
    await r.page.getByRole('button',{name:'Показывать исходную публикацию',exact:true}).click();
    await expect(r.page.locator('#setting-originals')).toBeChecked();
    await expect(r.page.locator('.setting-row:visible details summary')).toBeVisible();
    await r.page.locator('.setting-row:visible details summary').click();
    await expect(r.page.locator('.setting-row:visible details')).toContainText('Выключено');
    await expect(r.page.locator('#settings-message')).not.toContainText('Настройка сохранена');
  } finally {if(held)await held.abort();await r.close();}
});

test('Back restores the current field and section links after a saved editor rerenders', async () => {
  const r=await reader();try{
    await r.start([]);await r.page.evaluate(()=>{readerTransport.replies.set('/api/subscriptions',{body:{folders:[],feeds:[]}});readerTransport.native.add('/api/settings/oko/global');});
    await r.page.locator('#settings-open').click();await editSetting(r.page,'originals');
    await r.page.locator('#setting-originals').uncheck();await r.page.getByRole('button',{name:'Сохранить',exact:true}).click();
    await expect(r.page.locator('#settings-message')).toHaveText('Настройка сохранена');
    await r.page.locator('#settings-dialog .settings-back').click();
    const field=r.page.getByRole('button',{name:'Показывать исходную публикацию',exact:true});
    await expect(field).toBeFocused();
    await expect(field).toHaveAccessibleDescription('Выключено');
    await r.page.locator('#settings-dialog .settings-back').click();
    await expect(r.page.getByRole('button',{name:'Чтение',exact:true})).toBeFocused();
  }finally{await r.close();}
});
