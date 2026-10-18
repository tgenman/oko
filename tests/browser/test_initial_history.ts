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
      t.replies.set('/api/v3/subscriptions', {body: {folders: [{id: 'f', title: 'Учёба'}], feeds: [feed]}});
      t.replies.set('/api/v3/feeds', {body: feed});
      t.replies.set('/api/v3/feeds/verify', {body: {url: feed.original_urls[0], title: feed.title, kind: 'rss', verification: 'checked', existing: null}});
      t.replies.set('/api/v3/settings/feed/rss', {body: {...t.replies.get('/api/v3/settings/oko/global')!.body, feed}});
    });
    await r.page.locator('#add-open').click();
    await r.page.locator('#rss-url').fill('https://source.test/feed');
    await r.page.locator('#rss-check').click();
    await expect(r.page.locator('#rss-history-count')).toHaveValue('20');
    await r.page.locator('#rss-history-mode').selectOption('date');
    await expect(r.page.locator('#rss-history-count')).toBeHidden();
    await r.page.locator('#rss-history-date').fill('2025-10-01');
    await r.page.locator('#rss-add').click();
    await r.page.waitForFunction(() => readerTransport.requests.some(request => request.url === '/api/v3/feeds' && request.method === 'POST'));
    const request = await r.page.evaluate(() => readerTransport.requests.find(r => r.url === '/api/v3/feeds' && r.method === 'POST'));
    expect(request!.body!.initial_history).toEqual({mode: 'date', date: '2025-10-01', timezone: await r.page.evaluate(() => Intl.DateTimeFormat().resolvedOptions().timeZone)});
    await expect(r.page.locator('#feed-options')).toContainText('Loaded 7 publications since 01.10.2025');
  } finally { await r.close(); }
});

test('Telegram connection sends default count and can switch to date with user timezone', async () => {
  const r = await reader();
  try {
    await r.page.addInitScript(() => {
      readerTransport.replies.set('/api/v3/telegram/accounts', {body: ['fixture']});
      readerTransport.held.set('/api/v3/telegram/connect', true);
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
    await r.page.waitForFunction(() => readerTransport.pending.has('/api/v3/telegram/connect'));
    const request = await r.page.evaluate(() => readerTransport.requests.find(r => r.url === '/api/v3/telegram/connect'));
    expect(request!.body!.initial_history).toEqual({mode:'date',date:'2025-10-01',timezone:await r.page.evaluate(() => Intl.DateTimeFormat().resolvedOptions().timeZone)});
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
      t.replies.set('/api/v3/subscriptions', {body: {folders: [{id: 'f', title: 'Учёба'}], feeds: [feed]}});
      t.replies.set('/api/v3/settings/feed/rss', {body: {...t.replies.get('/api/v3/settings/oko/global')!.body, feed}});
      t.replies.set('/api/v3/materials/unknown', {body: {...card, originals: true, images: [], attachments: []}});
    }, card);
    await expect(r.page.locator('#cards')).toContainText('Unknown date');
    await r.page.locator('#cards button').first().click();
    await expect(r.page.locator('#date')).toHaveText('Unknown date');
    await r.page.locator('#feeds-open').click();
    await r.page.locator('#feed-tree [data-owner="feed/rss"]').click();
    await feedOptions(r.page);
    await expect(r.page.locator('#feed-options')).toContainText('Requested 100, available 15');
    await expect(r.page.locator('#feed-options')).toContainText('the accuracy of the latest N selection is not confirmed');
  } finally { await r.close(); }
});
