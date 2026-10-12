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
    await r.page.locator('#settings-open').click();
    await r.page.locator('#rss-connection summary').click();
    await r.page.locator('#rss-url').fill('https://source.test/feed');
    await r.page.locator('#rss-check').click();
    await expect(r.page.locator('#feed-options')).toContainText('Сохранённый первоначальный период: 7 дней');
    await expect(r.page.locator('#feed-options')).toContainText('Количество и прогресс начальной загрузки не сохранены');
    await expect(r.page.locator('#feed-options input[type=number]')).toHaveCount(0);
    await expect(r.page.locator('#feed-options')).not.toContainText('Загружено 0');
    await r.page.locator('#feed-options button').filter({hasText: 'Сохранить фид'}).click();
    const patch = await r.page.evaluate(() => readerTransport.requests.find(r => r.url === '/api/feeds/legacy' && r.method === 'PATCH'));
    expect(patch.body).not.toHaveProperty('initial_days');
  } finally { await r.close(); }
});
