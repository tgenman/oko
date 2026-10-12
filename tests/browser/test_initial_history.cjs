const {test} = require('node:test');
const {reader, expect} = require('./support.cjs');

test('RSS connection selects count or inclusive date and displays saved history progress', async () => {
  const r = await reader();
  try {
    await r.start([]);
    await r.page.evaluate(() => {
      const t = readerTransport;
      const feed = {id: 'rss', title: 'История', kind: 'rss', transport: 'rss', folder_id: 'f', original_urls: ['https://source.test/feed'], importance: 'normal', initial_days: 1,
        initial_history: {mode: 'date', date: '2025-10-01', timezone: 'UTC', since: 1759276800, loaded: 7, state: 'loading', frozen: true, available: 20, paused: false}};
      t.replies.set('/api/subscriptions', {body: {folders: [{id: 'f', title: 'Учёба'}], feeds: [feed]}});
      t.replies.set('/api/feeds', {body: feed});
      t.replies.set('/api/feeds/verify', {body: {url: feed.original_urls[0], title: feed.title, kind: 'rss', verification: 'checked', existing: null}});
      t.replies.set('/api/settings/feed/rss', {body: {...t.replies.get('/api/settings/oko/global').body, feed}});
    });
    await r.page.locator('#settings-open').click();
    await r.page.locator('#rss-connection summary').click();
    await r.page.locator('#rss-url').fill('https://source.test/feed');
    await r.page.locator('#rss-check').click();
    await expect(r.page.locator('#rss-history-count')).toHaveValue('20');
    await r.page.locator('#rss-history-mode').selectOption('date');
    await expect(r.page.locator('#rss-history-count')).toBeHidden();
    await r.page.locator('#rss-history-date').fill('2025-10-01');
    await r.page.locator('#rss-add').click();
    const request = await r.page.evaluate(() => readerTransport.requests.find(r => r.url === '/api/feeds' && r.method === 'POST'));
    expect(request.body.initial_history).toEqual({mode: 'date', count: null, date: '2025-10-01', timezone: await r.page.evaluate(() => Intl.DateTimeFormat().resolvedOptions().timeZone)});
    await expect(r.page.locator('#feed-options')).toContainText('Загружено 7 публикаций с 01.10.2025');
  } finally { await r.close(); }
});

test('Telegram connection sends default count and can switch to date with user timezone', async () => {
  const r = await reader();
  try {
    await r.page.addInitScript(() => {
      readerTransport.replies.set('/api/telegram/accounts', {body: ['fixture']});
      readerTransport.held.set('/api/telegram/connect', true);
    });
    await r.start([]);
    await r.page.locator('#settings-open').click();
    await r.page.locator('#telegram-connection summary').click();
    await r.page.locator('#telegram-peer').fill('@channel');
    await expect(r.page.locator('#telegram-history-count')).toHaveValue('20');
    await r.page.locator('#telegram-history-mode').selectOption('date');
    await expect(r.page.locator('#telegram-history-count')).toBeHidden();
    await r.page.locator('#telegram-history-date').fill('2025-10-01');
    await r.page.locator('#telegram-add').click();
    await r.page.waitForFunction(() => readerTransport.pending.has('/api/telegram/connect'));
    const request = await r.page.evaluate(() => readerTransport.requests.find(r => r.url === '/api/telegram/connect'));
    expect(request.body.initial_history).toEqual({mode:'date',count:null,date:'2025-10-01',timezone:await r.page.evaluate(() => Intl.DateTimeFormat().resolvedOptions().timeZone)});
    await expect(r.page.locator('#telegram-history-mode')).toBeDisabled();
  } finally {await r.close();}
});
