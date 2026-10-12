const test = require('node:test');
const assert = require('node:assert/strict');
const {reader, expect} = require('./support.cjs');

test('Telegram accounts are selectable and incomplete or pending connection is disabled', async () => {
  const r = await reader();
  try {
    await r.page.addInitScript(() => {
      const t = readerTransport;
      t.replies.set('/api/subscriptions', {body: {folders: [], feeds: []}});
      t.replies.set('/api/processors/llamacpp', {body: null});
      t.replies.set('/api/settings/oko/global', {body: {effective: {}, overrides: {}}});
      t.replies.set('/api/telegram/accounts', {body: ['personal', 'work']});
      t.held.set('/api/telegram/connect', true);
    });
    await r.start([]);
    await r.page.locator('#settings-open').click();
    await r.page.getByText('Подключить Telegram-канал или группу', {exact: true}).click();
    await expect(r.page.locator('#telegram-account')).toHaveJSProperty('tagName', 'SELECT');
    await expect(r.page.locator('#telegram-account option')).toHaveCount(3);
    await expect(r.page.locator('#telegram-add')).toBeDisabled();
    await r.page.locator('#telegram-account').selectOption('work');
    await r.page.locator('#telegram-peer').fill('   ');
    await expect(r.page.locator('#telegram-add')).toBeDisabled();
    await r.page.locator('#telegram-peer').fill('@channel');
    await r.page.locator('#telegram-folder').fill(' ');
    await expect(r.page.locator('#telegram-add')).toBeDisabled();
    await r.page.locator('#telegram-folder').fill('Работа');
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

test('forum selection resets on peer change and stale topics cannot attach to a new peer', async () => {
  const r = await reader();
  try {
    await r.page.addInitScript(() => {
      const t = readerTransport;
      t.replies.set('/api/subscriptions', {body: {folders: [], feeds: []}});
      t.replies.set('/api/processors/llamacpp', {body: null});
      t.replies.set('/api/telegram/accounts', {body: ['personal']});
      t.held.set('/api/telegram/topics', true);
    });
    await r.start([]);
    await r.page.locator('#settings-open').click();
    await r.page.getByText('Подключить Telegram-канал или группу', {exact: true}).click();
    await expect(r.page.locator('#telegram-account')).toHaveValue('personal');
    await r.page.locator('#telegram-peer').fill('@first');
    await r.page.locator('#telegram-topics').click();
    await r.page.waitForFunction(() => readerTransport.pending.has('/api/telegram/topics'));
    await expect(r.page.locator('#telegram-add')).toBeDisabled();
    await r.page.locator('#telegram-peer').fill('@second');
    await r.page.evaluate(() => readerTransport.release('/api/telegram/topics', [{id: 20, title: 'Старая тема'}]));
    await expect(r.page.locator('#telegram-topic-label')).toBeHidden();
    await r.page.evaluate(() => {
      readerTransport.replies.set('/api/telegram/topics', {body: [{id: 1, title: 'Общее'}, {id: 30, title: 'Новости'}]});
      readerTransport.held.set('/api/telegram/connect', true);
    });
    await r.page.locator('#telegram-topics').click();
    await expect(r.page.locator('#telegram-topic option')).toHaveCount(3);
    await expect(r.page.locator('#telegram-add')).toBeDisabled();
    await r.page.locator('#telegram-topic').selectOption('30');
    if (process.env.OKO_BROWSER_SCREENSHOT) await r.page.locator('#telegram-connection').screenshot({path: process.env.OKO_BROWSER_SCREENSHOT});
    await r.page.locator('#telegram-add').click();
    await r.page.waitForFunction(() => readerTransport.pending.has('/api/telegram/connect'));
    await expect(r.page.locator('#telegram-peer')).toBeDisabled();
    const request = await r.page.evaluate(() => readerTransport.requests.filter(r => r.url === '/api/telegram/connect'));
    assert.equal(request.length, 1);
    assert.deepEqual(request[0].body, {account: 'personal', peer: '@second', folder: 'Telegram', auto_read: false, topic_id: 30});
    await r.page.evaluate(() => readerTransport.release('/api/telegram/connect', {id: 'topic30'}));
    await expect(r.page.locator('#telegram-peer')).toHaveValue('');
    await expect(r.page.locator('#telegram-add')).toBeDisabled();
    await expect(r.page.locator('#telegram-topic-label')).toBeHidden();
    assert.deepEqual(r.errors, []);
  } finally {await r.close();}
});

test('ordinary channel stays connectable after checking its empty topic list', async () => {
  const r = await reader();
  try {
    await r.page.addInitScript(() => {
      const t = readerTransport;
      t.replies.set('/api/subscriptions', {body: {folders: [], feeds: []}});
      t.replies.set('/api/processors/llamacpp', {body: null});
      t.replies.set('/api/telegram/accounts', {body: ['personal']});
      t.replies.set('/api/telegram/topics', {body: []});
    });
    await r.start([]);
    await r.page.locator('#settings-open').click();
    await r.page.getByText('Подключить Telegram-канал или группу', {exact: true}).click();
    await r.page.locator('#telegram-peer').fill('@channel');
    await r.page.locator('#telegram-topics').click();
    await expect(r.page.locator('#telegram-message')).toHaveText('Это канал или обычная группа — тему выбирать не нужно.');
    await expect(r.page.locator('#telegram-topic-label')).toBeHidden();
    await expect(r.page.locator('#telegram-add')).toBeEnabled();
    assert.deepEqual(r.errors, []);
  } finally {await r.close();}
});

test('missing accounts and failed loading prevent connection until a successful retry', async () => {
  const r = await reader();
  try {
    await r.page.addInitScript(() => {
      const t = readerTransport;
      t.replies.set('/api/subscriptions', {body: {folders: [], feeds: []}});
      t.replies.set('/api/processors/llamacpp', {body: null});
      t.replies.set('/api/telegram/accounts', {body: {detail: 'Клиент недоступен'}, status: 409});
    });
    await r.start([]);
    await r.page.locator('#settings-open').click();
    await r.page.getByText('Подключить Telegram-канал или группу', {exact: true}).click();
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
      t.replies.set('/api/processors/llamacpp', {body: null});
      t.replies.set('/api/telegram/accounts', {body: ['personal']});
      t.held.set('/api/telegram/connect', true);
    });
    await r.start([]);
    await r.page.evaluate(() => readerTransport.replies.set('/api/settings/feed/imported', readerTransport.replies.get('/api/settings/oko/global')));
    await r.page.locator('#settings-open').click();
    await r.page.locator('#settings-owner').selectOption('feed/imported');
    await r.page.getByRole('button', {name: 'Настроить подключение Telegram-фида', exact: true}).click();
    await expect(r.page.locator('#telegram-account')).toHaveValue('personal');
    await expect(r.page.locator('#telegram-peer')).toHaveValue('@fixture');
    await expect(r.page.locator('#telegram-folder')).toHaveValue('Работа');
    await expect(r.page.locator('#telegram-folder')).toBeDisabled();
    await r.page.locator('#telegram-add').click();
    await r.page.waitForFunction(() => readerTransport.pending.has('/api/telegram/connect'));
    const body = await r.page.evaluate(() => readerTransport.requests.find(r => r.url === '/api/telegram/connect').body);
    assert.equal(body.feed_id, 'imported');
    assert.equal(body.account, 'personal');
    await r.page.evaluate(() => readerTransport.release('/api/telegram/connect', {id: 'imported'}));
    await expect(r.page.locator('#telegram-add')).toBeDisabled();
    assert.deepEqual(r.errors, []);
  } finally {await r.close();}
});
