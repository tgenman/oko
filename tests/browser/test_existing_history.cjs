const {feedOptions} = require('./settings-actions.cjs');
const {test} = require('node:test');
const {reader, expect} = require('./support.cjs');

test('legacy feed shows only known history settings and saving does not change its boundary', async () => {
  const r = await reader();
  try {
    await r.start([]);
    await r.page.evaluate(() => {
      const t = readerTransport;
      const feed = {id: 'legacy', title: 'Старый фид', kind: 'rss', transport: 'rss', folder_id: 'f', original_urls: ['https://source.test/feed'], importance: 'normal', initial_days: 7, initial_history: null};
      t.replies.set('/api/subscriptions', {body: {folders: [{id: 'f', title: 'Учёба'}], feeds: [feed]}});
      t.replies.set('/api/feeds/verify', {body: {url: feed.original_urls[0], title: feed.title, kind: 'rss', verification: 'checked', existing: feed}});
      t.replies.set('/api/settings/feed/legacy', {body: {...t.replies.get('/api/settings/oko/global').body, feed}});
      t.replies.set('/api/feeds/legacy', {body: feed});
    });
    await r.page.locator('#add-open').click();
    await r.page.locator('#rss-url').fill('https://source.test/feed');
    await r.page.locator('#rss-check').click();
    await feedOptions(r.page);
    await expect(r.page.locator('#feed-options')).toContainText('Сохранённая настройка глубины: 7 дней; фактическая граница не сохранена');
    await expect(r.page.locator('#feed-options')).toContainText('Количество и прогресс начальной загрузки не сохранены');
    await expect(r.page.locator('#feed-options input[type=number]')).toHaveCount(0);
    await expect(r.page.locator('#feed-options')).not.toContainText('Загружено 0');
    await r.page.locator('#feed-options button').filter({hasText: 'Сохранить фид'}).click();
    await r.page.waitForFunction(() => readerTransport.requests.some(request => request.url === '/api/feeds/legacy' && request.method === 'PATCH'));
    const patch = await r.page.evaluate(() => readerTransport.requests.find(r => r.url === '/api/feeds/legacy' && r.method === 'PATCH'));
    expect(patch.body).not.toHaveProperty('initial_days');
  } finally { await r.close(); }
});

test('legacy settings show the actual frozen boundary instead of mutable days', async () => {
  const r = await reader();
  try {
    await r.start([]);
    await r.page.evaluate(() => {
      const t = readerTransport;
      const feed = {id:'legacy', title:'Старый фид', kind:'rss', transport:'rss', folder_id:'f', original_urls:['https://source.test/feed'], importance:'normal', initial_days:7, initial_history:null, legacy_since:1759352400};
      t.replies.set('/api/subscriptions', {body:{folders:[{id:'f',title:'Учёба'}], feeds:[feed]}});
      t.replies.set('/api/settings/feed/legacy', {body:{...t.replies.get('/api/settings/oko/global').body, feed}});
    });
    await r.page.locator('#feeds-open').click();
    await r.page.locator('#feed-tree [data-owner="feed/legacy"]').click();
    await feedOptions(r.page);
    await expect(r.page.locator('#feed-options')).toContainText('Зафиксированное начало истории:');
    await expect(r.page.locator('#feed-options')).not.toContainText('7 дней');
    await expect(r.page.locator('#feed-options')).not.toContainText('Загружено');
  } finally {await r.close();}
});
