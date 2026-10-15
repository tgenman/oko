import {test} from 'node:test';
import {reader, expect} from './support.ts';
import {editSetting, settingsHome} from './settings-actions.ts';

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


test('folder field resets preserve other overrides and new drafts', async () => {
  const r = await reader();
  try {
    const plan = await r.api('import','POST',{opml:'<opml><body><outline text="Наука"><outline text="Источник" xmlUrl="https://source.test/feed"/></outline></body></opml>'});
    const folder = plan.folders[0], feed = plan.feeds[0];
    await r.api('settings/oko/global','PATCH',{prompt_theses:'Global prompt'});
    await r.api(`settings/folder/${folder.id}`,'PATCH',{prompt_theses:'Folder prompt',originals:false});
    await r.start([]);
    await r.page.evaluate(({folder,feed})=>{
      readerTransport.native.add('/api/subscriptions');
      for (const path of ['oko/global','folder/'+folder.id,'feed/'+feed.id]) readerTransport.native.add('/api/settings/'+path);
    },{folder,feed});
    await r.page.locator('#feeds-open').click();
    await r.page.locator('#feed-tree').getByRole('button',{name:'Наука',exact:true}).click();
    await editSetting(r.page,'prompt_theses');
    await expect(r.page.locator('#setting-prompt_theses')).toHaveValue('Folder prompt');
    await r.page.getByRole('button',{name:'Сбросить',exact:true}).click();
    await expect(r.page.locator('#setting-prompt_theses')).toHaveValue('Global prompt');
    const result = await r.api(`settings/folder/${folder.id}`);
    expect(result.overrides.originals).toBe(false);
    expect(result.overrides.prompt_theses).toBeUndefined();
    await r.page.locator('#setting-prompt_theses').fill('Unsaved prompt');
    await r.page.locator('#feed-tree').getByRole('button',{name:'Источник',exact:true}).click();
    await expect(r.page.getByRole('button',{name:'Объединение событий',exact:true})).toHaveCount(0);
    await expect(r.page.locator('#feed-folder')).toBeHidden();
    await r.page.getByRole('button',{name:'Подключение и параметры',exact:true}).click();
    await expect(r.page.locator('#feed-folder')).toBeVisible();
    await r.page.locator('#feed-tree').getByRole('button',{name:'Наука',exact:true}).click();
    await editSetting(r.page,'prompt_theses');
    await expect(r.page.locator('#setting-prompt_theses')).toHaveValue('Unsaved prompt');
    expect((await r.api(`settings/folder/${folder.id}`)).effective.prompt_theses.value).toBe('Global prompt');
    expect(r.errors).toEqual([]);
  } finally {await r.close();}
});

test('prompts, empty filtering save via HTTP without saving adjacent drafts', async () => {
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
    await editSetting(r.page,'prompt_theses');
    await expect(r.page.locator('#setting-prompt_theses')).toHaveValue('Мой черновик');
    expect((await r.api('settings/oko/global')).effective.prompt_theses.value).not.toBe('Мой черновик');
    expect(r.errors).toEqual([]);
  } finally {await r.close();}
});
