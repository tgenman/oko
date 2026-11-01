import {feedOptions} from './settings-actions.ts';
import {test} from 'node:test';
import {reader, expect} from './support.ts';

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
      t.replies.set('/fixture/material/unknown', {body: {...card, originals: true, images: [], attachments: []}});
    }, card);
    await expect(r.page.locator('#cards')).toContainText('Unknown date');
    await r.page.locator('#cards button').first().click();
    await expect(r.page.locator('#date')).toHaveText('Unknown date');
    await r.page.locator('#feeds-open').click();
    await r.page.locator('#feed-tree [data-owner="feed/rss"]').click();
    await feedOptions(r.page);
    await expect(r.page.locator('#feed-options')).toContainText('Requested 100, available 15');
    await expect(r.page.locator('#feed-options')).toContainText('Порядок RSS неоднозначен: точность выбора последних N не подтверждена');
  } finally { await r.close(); }
});
