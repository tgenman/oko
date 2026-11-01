import {material, reader, expect} from './support.ts';
import {artifactPath} from './artifacts.ts';
import {test} from 'node:test';

test('add URL uses one form and automatically shows YouTube connection', async () => {
  const r = await reader();
  try {
    await r.start([]);
    await r.page.evaluate(() => {
      const t = readerTransport;
      const feed = {id: 'youtube', title: '3Blue1Brown', kind: 'youtube', transport: 'youtube', folder_id: 'f', original_urls: ['https://www.youtube.com/feeds/videos.xml?channel_id=UCYO_jab_esuFRV4b17AJtAw'], importance: 'normal', initial_days: 1};
      t.replies.set('/api/v3/subscriptions', {body: {folders: [{id: 'f', title: 'Учёба'}], feeds: [feed]}});
      t.replies.set('/api/v3/feeds', {body: feed});
      t.replies.set('/api/v3/feeds/verify', {body: {url: feed.original_urls[0], title: '3Blue1Brown', kind: 'youtube', verification: 'checked', existing: null}});
      t.replies.set('/api/v3/settings/feed/youtube', {body: {...t.replies.get('/api/v3/settings/oko/global')!.body, feed}});
    });
    await r.page.locator('#add-open').click();
    await r.page.locator('#rss-url').fill('https://www.youtube.com/feeds/videos.xml?channel_id=UCYO_jab_esuFRV4b17AJtAw');
    await expect(r.page.locator('#rss-add')).toBeDisabled();
    await r.page.locator('#rss-check').click();
    await expect(r.page.locator('#rss-title')).toHaveValue('3Blue1Brown');
    await r.page.locator('#rss-folder').selectOption('Учёба');
    await r.page.locator('#rss-add').click();
    await expect(r.page.locator('#feeds-dialog')).toBeVisible();
    await expect(r.page.locator('#rss-url')).toHaveValue('');
    await expect(r.page.locator('#feed-options')).toContainText('YouTube connected');
    const request = await r.page.evaluate(() => readerTransport.requests.find(r => r.url === '/api/v3/feeds' && r.method === 'POST'));
    await r.page.keyboard.press('Escape');
    await r.page.locator('#add-open').click();
    await expect(r.page.locator('#rss-url')).toHaveValue('');
    await expect(r.page.locator('#rss-url')).toBeEnabled();
    await expect(r.page.locator('#rss-checked-fields')).toBeHidden();
    expect(request!.body).toEqual({url: 'https://www.youtube.com/feeds/videos.xml?channel_id=UCYO_jab_esuFRV4b17AJtAw', title: '3Blue1Brown', folder: 'Учёба', verification: 'checked', initial_history: {mode: 'count', count: '20'}});
  } finally { await r.close(); }
});

test('debug toggle shows server traceback and caught browser errors are sent while closed', async () => {
  const r = await reader();
  try {
    await r.start([]);
    await r.api('diagnostics', 'POST', {source:'collector', message:'broken source', traceback:'RuntimeError at discover', error_type:'RuntimeError', location:'discover:42'});
    await r.page.evaluate(() => readerTransport.replies.set('/api/v3/collect', {status: 500, body: {detail: 'collection failed'}}));
    await expect(r.page.locator('#debug-panel')).toBeHidden();
    await r.page.locator('#collect').click();
    await r.page.waitForFunction(() => readerTransport.requests.some(r => r.url === '/api/v3/diagnostics' && r.method === 'POST' && r.body!.message === 'collection failed'));
    await r.page.locator('#status-toggle').click();
    await r.page.locator('#status-journal-open').click();
    await expect(r.page.locator('#debug-panel')).toBeVisible();
    await expect(r.page.locator('#debug-errors')).toContainText('broken source');
    await expect(r.page.locator('#debug-errors')).toContainText('collection failed');
    await r.page.evaluate(() => window.dispatchEvent(new ErrorEvent('error', {message: 'JS exploded', error: new Error('JS exploded')})));
    await expect(r.page.locator('#debug-errors')).toContainText('JS exploded');
    await r.page.locator('#status-close').click();
    await expect(r.page.locator('#debug-panel')).toBeHidden();
  } finally { await r.close(); }
});

test('changing URL rejects late verification, errors retry and duplicate opens existing settings', async () => {
  const r = await reader();
  try {
    await r.start([]);
    await r.page.evaluate(() => {
      const t = readerTransport;
      t.replies.set('/api/v3/subscriptions', {body: {folders: [], feeds: []}});
      t.replies.set('/api/v3/processors/llamacpp', {body: null});
      t.held.set('/api/v3/feeds/verify', true);
    });
    await r.page.locator('#add-open').click();
    await r.page.locator('#rss-url').fill('https://source.test/old');
    await r.page.locator('#rss-check').click();
    await r.page.waitForFunction(() => readerTransport.pending.has('/api/v3/feeds/verify'));
    await r.page.locator('#rss-url').fill('https://source.test/new');
    await r.page.evaluate(() => readerTransport.release('/api/v3/feeds/verify', {url: 'https://source.test/old', title: 'Old', verification: 'old', existing: null}));
    await expect(r.page.locator('#rss-add')).toBeDisabled();
    await expect(r.page.locator('#rss-checked-fields')).toBeHidden();
    await r.page.evaluate(() => readerTransport.replies.set('/api/v3/feeds/verify', {status: 400, body: {detail: 'Неверный RSS/Atom'}}));
    await r.page.locator('#rss-check').click();
    await expect(r.page.locator('#rss-message')).toHaveText('Неверный RSS/Atom');
    await r.page.evaluate(() => {
      const feed = {id: 'existing', title: 'Старое имя', kind: 'rss', transport: 'rss', importance: 'high', initial_days: 7, folder_id: 'f'};
      readerTransport.replies.set('/api/v3/feeds/verify', {body: {existing: feed}});
      readerTransport.replies.set('/api/v3/subscriptions', {body: {feeds: [feed], folders: [{id: 'f', title: 'Старая папка'}]}});
      readerTransport.replies.set('/api/v3/settings/feed/existing', {body: {...readerTransport.replies.get('/api/v3/settings/oko/global')!.body, feed}});
    });
    await r.page.locator('#rss-check').click();
    await expect(r.page.locator('#feeds-dialog')).toBeVisible();
    await expect(r.page.locator('#owner-heading')).toHaveText('Feed: Старое имя');
    expect(await r.page.evaluate(() => readerTransport.requests.filter(r => r.url === '/api/v3/feeds' && r.method === 'POST').length)).toBe(0);
  } finally { await r.close(); }
});

test('offline browser errors group, retain material scroll, and retry the same delivery', async () => {
  const r = await reader();
  try {
    await r.start([material('one', {text: 'Длинный текст. '.repeat(4000)})]);
    await r.page.locator('#cards button').click();
    await expect(r.page.locator('#title')).toHaveText('one');
    await r.page.evaluate(() => {
      document.querySelector('article')!.scrollTop = 500;
      readerTransport.replies.set('POST /api/v3/diagnostics', {status: 503, body: {detail: 'offline'}});
      for (let i = 0; i < 3; i++) window.dispatchEvent(new ErrorEvent('error', {error: new Error('offline-js'), message: 'offline-js'}));
    });
    const scroll = await r.page.locator('article').evaluate(node => node.scrollTop);
    await r.page.locator('#status-toggle').click();
    await r.page.locator('#status-journal-open').click();
    await expect(r.page.locator('#debug-errors')).toContainText('offline-js');
    await expect(r.page.locator('#debug-errors')).toContainText('×3');
    await expect(r.page.locator('#debug-errors')).toContainText('not saved');
    await expect(r.page.locator('#title')).toHaveText('one');
    expect(await r.page.locator('article').evaluate(node => node.scrollTop)).toBe(scroll);
    const event = await r.page.evaluate(() => readerTransport.requests.find(r => r.url === '/api/v3/diagnostics' && r.method === 'POST')!.body!.event_id);
    await r.page.evaluate(() => readerTransport.replies.delete('POST /api/v3/diagnostics'));
    await expect(r.page.locator('#debug-errors')).not.toContainText('not saved', {timeout: 6000});
    await expect(r.page.locator('#debug-errors')).toContainText('×3');
    expect(await r.page.evaluate(id => readerTransport.requests.filter(r => r.url === '/api/v3/diagnostics' && r.method === 'POST' && r.body!.event_id === id).length, event)).toBeGreaterThan(1);
    await r.page.locator('#status-close').click();
    expect(await r.page.locator('article').evaluate(node => node.scrollTop)).toBe(scroll);
  } finally { await r.close(); }
});

test('browser pending diagnostics mask quoted passwords and Basic credentials', async () => {
  const r = await reader();
  try {
    await r.start([]);
    await r.page.evaluate(() => {
      readerTransport.replies.set('POST /api/v3/diagnostics', {status: 503, body: {detail: 'offline'}});
      const message = 'password="correct horse battery staple" Authorization: Basic dXNlcjpwYXNz';
      window.dispatchEvent(new ErrorEvent('error', {error: new Error(message), message}));
    });
    await r.page.locator('#status-toggle').click();
    await r.page.locator('#status-journal-open').click();
    await expect(r.page.locator('#debug-errors')).toContainText('[hidden]');
    await expect(r.page.locator('#debug-errors')).not.toContainText('horse');
    await expect(r.page.locator('#debug-errors')).not.toContainText('dXNlcjpwYXNz');
    const body = await r.page.evaluate(() => readerTransport.requests.find(r => r.url === '/api/v3/diagnostics' && r.method === 'POST')!.body);
    expect(JSON.stringify(body)).not.toContain('horse');
    expect(JSON.stringify(body)).not.toContain('dXNlcjpwYXNz');
  } finally { await r.close(); }
});
