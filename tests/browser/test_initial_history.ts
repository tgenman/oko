import {feedOptions} from './settings-actions.ts';
import {test} from 'node:test';
import {reader, expect} from './support.ts';

test('RSS connection selects count or inclusive date and displays saved history progress', async () => {
  const r = await reader();
  try {
    await r.start([]);
    await r.page.evaluate(() => {
      const t = readerTransport;
      const feed = {id: 'rss', title: 'История', kind: 'rss', transport: 'rss', folder_id: 'f', original_urls: ['https://source.test/feed'], importance: 'normal' as const, initial_days: 1,
        initial_history: window.readerFixtures.history({mode: 'date', count:null, count_text:null, date: '2025-10-01', timezone: 'UTC', since: 1759276800, loaded: 7, state: 'loading', frozen: true, available: 20, paused: false})};
      t.replies.set('/api/subscriptions', {body: {folders: [{id: 'f', title: 'Учёба'}], feeds: [feed]}});
      t.replies.set('/api/feeds', {body: feed});
      t.replies.set('/api/feeds/verify', {body: {url: feed.original_urls[0], title: feed.title, kind: 'rss', verification: 'checked', existing: null}});
      t.replies.set('/api/settings/feed/rss', {body: {...t.replies.get('/api/settings/oko/global')!.body, feed}});
    });
    await r.page.locator('#add-open').click();
    await r.page.locator('#rss-url').fill('https://source.test/feed');
    await r.page.locator('#rss-check').click();
    await expect(r.page.locator('#rss-history-count')).toHaveValue('20');
    await r.page.locator('#rss-history-mode').selectOption('date');
    await expect(r.page.locator('#rss-history-count')).toBeHidden();
    await r.page.locator('#rss-history-date').fill('2025-10-01');
    await r.page.locator('#rss-add').click();
    await r.page.waitForFunction(() => readerTransport.requests.some(request => request.url === '/api/feeds' && request.method === 'POST'));
    const request = await r.page.evaluate(() => readerTransport.requests.find(r => r.url === '/api/feeds' && r.method === 'POST'));
    expect(request!.body!.initial_history).toEqual({mode: 'date', count: null, date: '2025-10-01', timezone: await r.page.evaluate(() => Intl.DateTimeFormat().resolvedOptions().timeZone)});
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
    await r.page.locator('#add-open').click();
    await r.page.getByRole('tab', {name: 'Telegram', exact: true}).click();
    for (const id of ['telegram-history-mode','telegram-history-count','telegram-history-date']) {
      await expect(r.page.locator('#'+id)).toHaveCount(1);
    }
    await r.page.locator('#telegram-peer').fill('@channel');
    await expect(r.page.locator('#telegram-history-count')).toHaveValue('20');
    await r.page.locator('#telegram-history-mode').selectOption('date');
    await expect(r.page.locator('#telegram-history-count')).toBeHidden();
    await r.page.locator('#telegram-history-date').fill('2025-10-01');
    await r.page.locator('#telegram-add').click();
    await r.page.waitForFunction(() => readerTransport.pending.has('/api/telegram/connect'));
    const request = await r.page.evaluate(() => readerTransport.requests.find(r => r.url === '/api/telegram/connect'));
    expect(request!.body!.initial_history).toEqual({mode:'date',count:null,date:'2025-10-01',timezone:await r.page.evaluate(() => Intl.DateTimeFormat().resolvedOptions().timeZone)});
    await expect(r.page.locator('#telegram-history-mode')).toBeDisabled();
  } finally {await r.close();}
});

test('finite history warning and unknown date are visible in settings and material', async () => {
  const r = await reader();
  try {
    const card = {id: 'unknown', feed_id: 'rss', title: 'Без даты', text: 'Текст', published: 0, published_known: false, url: 'https://source.test/post', complete: true, importance: 'normal' as const};
    await r.start([card]);
    await r.page.evaluate(card => {
      const t = readerTransport;
      const feed = {id: 'rss', title: 'История', kind: 'rss', transport: 'rss', folder_id: 'f', original_urls: ['https://source.test/feed'], importance: 'normal' as const, initial_days: 1,
        initial_history: window.readerFixtures.history({mode: 'count', count: 100, count_text: '100', loaded: 15, state: 'done', frozen: true, scan_complete: true, available: 15, paused: false, warnings: ['Порядок RSS неоднозначен: точность выбора последних N не подтверждена']})};
      t.replies.set('/api/subscriptions', {body: {folders: [{id: 'f', title: 'Учёба'}], feeds: [feed]}});
      t.replies.set('/api/settings/feed/rss', {body: {...t.replies.get('/api/settings/oko/global')!.body, feed}});
      t.replies.set('/api/materials/unknown', {body: {...card, originals: true, images: [], attachments: []}});
    }, card);
    await expect(r.page.locator('#cards')).toContainText('Дата неизвестна');
    await r.page.locator('#cards button').first().click();
    await expect(r.page.locator('#date')).toHaveText('Дата неизвестна');
    await r.page.locator('#feeds-open').click();
    await r.page.locator('#feed-tree [data-owner="feed/rss"]').click();
    await feedOptions(r.page);
    await expect(r.page.locator('#feed-options')).toContainText('Запрошено 100, доступно 15');
    await expect(r.page.locator('#feed-options')).toContainText('точность выбора последних N не подтверждена');
  } finally { await r.close(); }
});
