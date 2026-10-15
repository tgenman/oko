import {feedOptions} from './settings-actions.ts';
import {reader, expect} from './support.ts';
import {test} from 'node:test';
import assert from 'node:assert/strict';

test('Telegram accounts are selectable and incomplete or pending connection is disabled', async () => {
  const r = await reader();
  try {
    await r.page.addInitScript(() => {
      const t = readerTransport;
      t.replies.set('/api/subscriptions', {body: {folders: [], feeds: []}});
      t.replies.set('/api/v3/processors/llamacpp', {body: null});
      t.replies.set('/api/v3/settings/oko/global', {body: {effective: {}, overrides: {}}});
      t.replies.set('/api/telegram/accounts', {body: ['personal', 'work']});
      t.held.set('/api/telegram/connect', true);
    });
    await r.start([]);
    await r.page.locator('#add-open').click();
    await r.page.getByRole('tab', {name: 'Telegram', exact: true}).click();
    await expect(r.page.locator('#telegram-account')).toHaveJSProperty('tagName', 'SELECT');
    await expect(r.page.locator('#telegram-account option')).toHaveCount(3);
    await expect(r.page.locator('#telegram-add')).toBeDisabled();
    await r.page.locator('#telegram-account').selectOption('work');
    await r.page.locator('#telegram-peer').fill('   ');
    await expect(r.page.locator('#telegram-add')).toBeDisabled();
    await r.page.locator('#telegram-peer').fill('@channel');
    await r.page.locator('#telegram-folder').selectOption('');
    await r.page.locator('#telegram-folder-new').fill(' ');
    await expect(r.page.locator('#telegram-add')).toBeDisabled();
    await r.page.locator('#telegram-folder-new').fill('Работа');
    await expect(r.page.locator('#telegram-add')).toBeEnabled();
    await r.page.locator('#telegram-add').click();
    await r.page.waitForFunction(() => readerTransport.pending.has('/api/telegram/connect'));
    await expect(r.page.locator('#telegram-add')).toBeDisabled();
    await r.page.evaluate(() => readerTransport.release('/api/telegram/connect', {detail: 'Нет доступа'}, 409));
    await expect(r.page.locator('#telegram-message')).toHaveText('Нет доступа');
    await expect(r.page.locator('#telegram-add')).toBeEnabled();
    await expect(r.page.locator('#telegram-peer')).toHaveValue('@channel');
    assert.deepEqual(r.errors, []);
  } finally {await r.close();}
});

test('forum is connected as one stream without topic selection', async () => {
  const r = await reader();
  try {
    await r.page.addInitScript(() => {
      readerTransport.replies.set('/api/subscriptions', {body:{folders:[], feeds:[]}});
      readerTransport.replies.set('/api/telegram/accounts', {body:['personal']});
      readerTransport.held.set('/api/telegram/connect', true);
    });
    await r.start([]);
    await r.page.locator('#add-open').click();
    await r.page.getByRole('tab', {name:'Telegram', exact:true}).click();
    await r.page.locator('#telegram-peer').fill('@forum');
    await expect(r.page.locator('#telegram-topics')).toHaveCount(0);
    await expect(r.page.locator('#telegram-topic')).toHaveCount(0);
    await expect(r.page.locator('#telegram-add')).toBeEnabled();
    if (process.env.OKO_BROWSER_SCREENSHOT) {
      await r.page.screenshot({path:process.env.OKO_BROWSER_SCREENSHOT});
      await r.page.setViewportSize({width:390,height:844});
      await r.page.screenshot({path:process.env.OKO_BROWSER_SCREENSHOT.replace('.png','-mobile.png')});
    }
    await r.page.locator('#telegram-add').click();
    await r.page.waitForFunction(() => readerTransport.pending.has('/api/telegram/connect'));
    const requests = await r.page.evaluate(() => readerTransport.requests.filter(r => r.url==='/api/telegram/connect'));
    assert.deepEqual(requests[0].body, {account:'personal',peer:'@forum',folder:'Telegram',auto_read:false,initial_history:{mode:'count',count:'20'}});
    await r.page.evaluate(() => readerTransport.release('/api/telegram/connect',{id:'forum'}));
    await expect(r.page.locator('#telegram-peer')).toHaveValue('');
    assert.deepEqual(r.errors,[]);
  } finally {await r.close();}
});

test('missing accounts and failed loading prevent connection until a successful retry', async () => {
  const r = await reader();
  try {
    await r.page.addInitScript(() => {
      const t = readerTransport;
      t.replies.set('/api/subscriptions', {body: {folders: [], feeds: []}});
      t.replies.set('/api/v3/processors/llamacpp', {body: null});
      t.replies.set('/api/telegram/accounts', {body: {detail: 'Клиент недоступен'}, status: 409});
    });
    await r.start([]);
    await r.page.locator('#add-open').click();
    await r.page.getByRole('tab', {name: 'Telegram', exact: true}).click();
    await expect(r.page.locator('#telegram-message')).toHaveText('Клиент недоступен');
    await r.page.locator('#telegram-peer').fill('@channel');
    await expect(r.page.locator('#telegram-add')).toBeDisabled();
    await r.page.evaluate(() => readerTransport.replies.set('/api/telegram/accounts', {body: []}));
    await r.page.locator('#telegram-accounts').click();
    await expect(r.page.locator('#telegram-message')).toHaveText('В клиенте Telegram нет настроенных аккаунтов.');
    await expect(r.page.locator('#telegram-add')).toBeDisabled();
    await r.page.evaluate(() => readerTransport.replies.set('/api/telegram/accounts', {body: ['personal']}));
    await r.page.locator('#telegram-accounts').click();
    await expect(r.page.locator('#telegram-account')).toHaveValue('personal');
    await expect(r.page.locator('#telegram-add')).toBeEnabled();
    assert.deepEqual(r.errors, []);
  } finally {await r.close();}
});

test('imported Telegram feed uses the same account selection and keeps its feed identity', async () => {
  const r = await reader();
  try {
    await r.page.addInitScript(() => {
      const t = readerTransport;
      t.replies.set('/api/subscriptions', {body: {folders: [{id: 'f', title: 'Работа'}], feeds: [{id: 'imported', kind: 'telegram', identity: 'telegram:fixture', title: 'Канал', folder_id: 'f', original_urls: ['https://t.me/fixture'], importance: 'normal', initial_days: 1, public_ai: false, auto_read: null, read_history: false, transport: null, connection: 'needs_connection'}]}});
      t.replies.set('/api/v3/processors/llamacpp', {body: null});
      t.replies.set('/api/telegram/accounts', {body: ['personal']});
      t.held.set('/api/telegram/connect', true);
    });
    await r.start([]);
    await r.page.evaluate(() => readerTransport.replies.set('/api/v3/settings/feed/imported', readerTransport.replies.get('/api/v3/settings/oko/global')!));
    await r.page.locator('#feeds-open').click();
    await r.page.locator('#feed-tree [data-owner="feed/imported"]').click();
    await feedOptions(r.page);
    await r.page.getByRole('button', {name: 'Настроить подключение Telegram-фида', exact: true}).click();
    await expect(r.page.locator('#reconnect-account')).toHaveValue('personal');
    await expect(r.page.locator('#reconnect-peer')).toHaveValue('@fixture');
    await expect(r.page.locator('#reconnect-folder')).toHaveValue('Работа');
    await expect(r.page.locator('#reconnect-folder')).toBeDisabled();
    await r.page.locator('#reconnect-add').click();
    await r.page.waitForFunction(() => readerTransport.pending.has('/api/telegram/connect'));
    const body = await r.page.evaluate(() => readerTransport.requests.find(r => r.url === '/api/telegram/connect')!.body);
    assert.equal(body!.feed_id, 'imported');
    assert.equal(body!.account, 'personal');
    await r.page.evaluate(() => readerTransport.release('/api/telegram/connect', {id: 'imported'}));
    await expect(r.page.locator('#reconnect-add')).toBeDisabled();
    assert.deepEqual(r.errors, []);
  } finally {await r.close();}
});

test('pending accounts populate Telegram after leaving and returning to its tab', async () => {
  const r = await reader();
  try {
    await r.start([]);
    await r.page.evaluate(() => {
      readerTransport.replies.set('/api/subscriptions', {body:{folders:[], feeds:[]}});
      readerTransport.held.set('/api/telegram/accounts', true);
    });
    await r.page.locator('#add-open').click();
    await r.page.locator('#add-telegram-tab').click();
    await r.page.waitForFunction(() => readerTransport.pending.has('/api/telegram/accounts'));
    await r.page.locator('#add-rss-tab').click();
    await r.page.locator('#add-telegram-tab').click();
    await r.page.evaluate(() => readerTransport.release('/api/telegram/accounts', ['personal']));
    await expect(r.page.locator('#telegram-account')).toHaveValue('personal');
    await r.page.locator('#telegram-peer').fill('@channel');
    await expect(r.page.locator('#telegram-add')).toBeEnabled();
    expect(r.errors).toEqual([]);
  } finally {await r.close();}
});

test('pending accounts populate Telegram after closing and reopening addition', async () => {
  const r = await reader();
  try {
    await r.start([]);
    await r.page.evaluate(() => {
      readerTransport.replies.set('/api/subscriptions', {body:{folders:[], feeds:[]}});
      readerTransport.held.set('/api/telegram/accounts', true);
    });
    await r.page.locator('#add-open').click();
    await r.page.locator('#add-telegram-tab').click();
    await r.page.waitForFunction(() => readerTransport.pending.has('/api/telegram/accounts'));
    await r.page.keyboard.press('Escape');
    await r.page.locator('#add-open').click();
    await r.page.evaluate(() => readerTransport.release('/api/telegram/accounts', ['personal']));
    await expect(r.page.locator('#telegram-account')).toHaveValue('personal');
    expect(r.errors).toEqual([]);
  } finally {await r.close();}
});
